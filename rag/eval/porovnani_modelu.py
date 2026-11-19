"""Porovnání embeddingových modelů na vzorku dat před plnou indexací (MCP fáze 4).

Spuštění z adresáře rag/ proti LOKÁLNÍ databázi s kopií dat (steno, texty, tisky), nikdy proti produkci:
    DATABASE_URL=postgresql://…/hlidac_kopie OPENAI_API_KEY=… python -m eval.porovnani_modelu --vzorek 1500 [--ano]

Vzorek = cíle otázek fáze 4 (pole cile v otazky.json) + náhodné projevy a celky (deterministicky). Každý model
se zaindexuje do vlastní tabulky ve schématu rag_porovnani (rag.usek zůstane netknutý), pak se pro každou otázku
spočítá recall@10 a @20 hustého hledání a hybridu (RRF s fulltextem nad týmž vzorkem), latence embeddingu dotazu
a cena. Před voláním API vypíše odhad ceny; nad --strop-usd (0,50) skončí, dokud nedostane --ano.
Výstup je markdown do stdout.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from psycopg.rows import dict_row

from hlidac_rag import db
from hlidac_rag.embed.hledani import rrf
from hlidac_rag.embed.indexer import (CENA_USD_ZA_MILION, ZNAKU_NA_TOKEN, _CEL_OK, _PROJEV_OK, _priprav, indexuj,
                                      kandidati)
from hlidac_rag.embed.klient import Klient
from hlidac_rag.tools import _tsquery

OTAZKY = Path(__file__).parent / "otazky.json"
SCHEMA = "rag_porovnani"
MODELY = [("large-1024", "text-embedding-3-large", 1024),
          ("small-1024", "text-embedding-3-small", 1024),
          ("small-1536", "text-embedding-3-small", 1536)]
TOP = 50
# celý korpus: ~65 mil. znaků textu + hlavičky a překryvy ≈ 80 mil. znaků, změřeno 2,2 znaku na token
PLNA_TOKENU = 36e6


def otazky_faze4(otazky: list[dict]) -> list[dict]:
    """Otázky fáze 4 s cíli a dotazem pro hledací nástroj."""
    out = []
    for o in otazky:
        if o.get("faze") != "4" or not o.get("cile"):
            continue
        v = next(v for v in o["volani"] if v["nastroj"] in ("hledej_v_projevech", "hledej_v_textech"))
        out.append({"id": o["id"], "zdroj": "projev" if v["nastroj"] == "hledej_v_projevech" else "cel",
                    "dotaz": v["argumenty"]["dotaz"], "cile": o["cile"]})
    return out


def rozloz_cil(cil: str) -> tuple[str, tuple]:
    """„10/34/297/3“ → ("projev", (obdobi, schuze, turn, aname)); „tisk:221:puvodni:12“ / „pn:2092:puvodni:7“
    → ("cel", (druh, cislo, verze, poradi))."""
    if ":" in cil:
        druh, cislo, verze, poradi = cil.split(":")
        if druh not in ("tisk", "pn") or verze not in ("puvodni", "schvalene"):
            raise ValueError(f"neplatný cíl {cil}")
        return "cel", (druh, int(cislo), verze, int(poradi))
    casti = [int(x) for x in cil.split("/")]
    if len(casti) != 4:
        raise ValueError(f"neplatný cíl {cil}")
    return "projev", tuple(casti)


def recall(cile: set, poradi: list, k: int) -> float:
    """Podíl cílů mezi prvními k výsledky."""
    return len(cile & set(poradi[:k])) / len(cile) if cile else 0.0


def _id_cile(conn, cil: str) -> int:
    zdroj, kl = rozloz_cil(cil)
    with conn.cursor() as cur:
        if zdroj == "projev":
            cur.execute("SELECT id FROM steno.projev WHERE obdobi=%s AND schuze=%s AND turn=%s AND aname=%s", kl)
        else:
            druh, cislo, verze, poradi = kl
            cur.execute(f"""SELECT c.id FROM texty.cel c JOIN texty.dokument d ON d.id = c.dokument_id
                            WHERE d.druh = %s AND {'d.ct' if druh == 'tisk' else 'd.cislo_pn'} = %s
                              AND {"d.cislo_pn = 0 AND" if druh == 'tisk' else ''} d.verze = %s AND c.poradi = %s""",
                        (druh, cislo, verze, poradi))
        r = cur.fetchone()
    if not r:
        raise ValueError(f"cíl {cil} v databázi není")
    return r[0]


def _nahodne(conn, zdroj: str, n: int, vynech: set[int]) -> list[int]:
    if zdroj == "projev":
        sql = f"""SELECT p.id FROM steno.projev p WHERE {_PROJEV_OK} AND NOT p.predsedajici
                  ORDER BY md5('vzorek' || p.id) LIMIT %s"""
    else:
        sql = f"""SELECT c.id FROM texty.cel c JOIN texty.dokument d ON d.id = c.dokument_id WHERE {_CEL_OK}
                  ORDER BY md5('vzorek' || c.id) LIMIT %s"""
    with conn.cursor() as cur:
        cur.execute(sql, (n + len(vynech),))
        return [r[0] for r in cur.fetchall() if r[0] not in vynech][:n]


def _tabulka(conn, jmeno: str, dim: int) -> str:
    t = f"{SCHEMA}.usek_{jmeno.replace('-', '_')}"
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        cur.execute(f"DROP TABLE IF EXISTS {t}")
        cur.execute(f"CREATE TABLE {t} (LIKE rag.usek INCLUDING DEFAULTS INCLUDING CONSTRAINTS)")
        cur.execute(f"ALTER TABLE {t} ALTER COLUMN embedding TYPE halfvec({dim})")
    conn.commit()
    return t


def _odhad_tokenu(conn, vzorek: dict[str, list[int]]) -> int:
    znaku = 0
    for zdroj, ids in vzorek.items():
        for r in kandidati(conn, zdroj, len(ids) + 1, "porovnani-odhad", "rag.usek", ids):
            znaku += sum(len(u["vstup"]) for u in _priprav(zdroj, r))
    conn.commit()
    return znaku // ZNAKU_NA_TOKEN


def _husty(conn, tabulka: str, zdroj: str, vektor: list[float]) -> list[int]:
    v = "[" + ",".join(f"{x:.6g}" for x in vektor) + "]"
    with conn.cursor() as cur:
        cur.execute(f"""SELECT coalesce(projev_id, cel_id) AS zid, min(embedding <=> %s::halfvec) AS d FROM {tabulka}
                        WHERE zdroj = %s GROUP BY 1 ORDER BY 2 LIMIT %s""", (v, zdroj, TOP))
        return [r[0] for r in cur.fetchall()]


def _lexikalni(conn, zdroj: str, dotaz: str, ids: list[int]) -> list[int]:
    tsq = _tsquery(dotaz)
    if zdroj == "projev":
        sql = """SELECT p.id FROM steno.projev p WHERE p.id = ANY(%s)
                   AND to_tsvector('simple', unaccent_simple(p.text)) @@ to_tsquery('simple', %s)
                 ORDER BY ts_rank(to_tsvector('simple', unaccent_simple(p.text)), to_tsquery('simple', %s)) DESC,
                          p.id LIMIT %s"""
    else:
        sql = """SELECT c.id FROM texty.cel c WHERE c.id = ANY(%s) AND c.tsv @@ to_tsquery('simple', %s)
                 ORDER BY ts_rank(c.tsv, to_tsquery('simple', %s)) DESC, c.id LIMIT %s"""
    with conn.cursor() as cur:
        cur.execute(sql, (ids, tsq, tsq, TOP))
        return [r[0] for r in cur.fetchall()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vzorek", type=int, default=1500, help="počet zdrojů ve vzorku (polovina projevy, polovina celky)")
    ap.add_argument("--strop-usd", type=float, default=0.50)
    ap.add_argument("--ano", action="store_true", help="spustit i nad stropem ceny")
    ap.add_argument("--modely", default=",".join(m[0] for m in MODELY), help="large-1024,small-1024,small-1536")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    otazky = otazky_faze4(json.loads(OTAZKY.read_text(encoding="utf-8")))
    if not otazky:
        print("Žádné otázky fáze 4 s cíli.")
        return 1
    conn = db.connect()
    jmeno_db = conn.execute("SELECT current_database()").fetchone()[0]
    print(f"Databáze: {jmeno_db}, otázek: {len(otazky)}", file=sys.stderr)
    db.migrate(conn)

    cile = {o["id"]: {_id_cile(conn, c) for c in o["cile"]} for o in otazky}
    vzorek: dict[str, list[int]] = {"projev": [], "cel": []}
    for o in otazky:
        vzorek[o["zdroj"]].extend(cile[o["id"]])
    for zdroj in vzorek:
        vzorek[zdroj] = sorted(set(vzorek[zdroj]))
        vzorek[zdroj] += _nahodne(conn, zdroj, max(0, args.vzorek // 2 - len(vzorek[zdroj])), set(vzorek[zdroj]))

    vybrane = [m for m in MODELY if m[0] in args.modely.split(",")]
    tokenu = _odhad_tokenu(conn, vzorek)
    odhad = sum(tokenu / 1e6 * CENA_USD_ZA_MILION[model] for _, model, _ in vybrane)
    print(f"Vzorek: {len(vzorek['projev'])} projevů, {len(vzorek['cel'])} celků, odhad {tokenu} tokenů na model, "
          f"cena celkem ~{odhad:.3f} USD", file=sys.stderr)
    if odhad > args.strop_usd and not args.ano:
        print(f"Odhad {odhad:.3f} USD je nad stropem {args.strop_usd} USD, přerušeno (spusťte s --ano).",
              file=sys.stderr)
        return 2

    radky, detail = [], {o["id"]: {} for o in otazky}
    celkem_usd = 0.0
    lex = {o["id"]: _lexikalni(conn, o["zdroj"], o["dotaz"], vzorek[o["zdroj"]]) for o in otazky}
    conn.commit()
    for jmeno, model, dim in vybrane:
        klient = Klient.z_prostredi()
        if klient is None:
            print("Chybí OPENAI_API_KEY.", file=sys.stderr)
            return 1
        klient.model, klient.dim = model, dim
        tabulka = _tabulka(conn, jmeno, dim)
        start = time.monotonic()
        r = indexuj(conn, klient, max_tokenu=10_000_000, tabulka=tabulka, dim=dim, omezeni=vzorek)
        if r.get("chyba"):
            print(f"{jmeno}: indexace selhala: {r['chyba']}", file=sys.stderr)
            return 1
        sekund = time.monotonic() - start
        latence, tokenu_dotazu = [], 0
        rec = {"h10": [], "h20": [], "y10": [], "y20": [], "l20": []}
        for o in otazky:
            t0 = time.monotonic()
            vektory, tk = klient.embed([o["dotaz"]])
            latence.append((time.monotonic() - t0) * 1000)
            tokenu_dotazu += tk
            husty = _husty(conn, tabulka, o["zdroj"], vektory[0])
            hybrid = [k for k, _ in rrf([lex[o["id"]], husty])]
            c = cile[o["id"]]
            rec["h10"].append(recall(c, husty, 10))
            rec["h20"].append(recall(c, husty, 20))
            rec["y10"].append(recall(c, hybrid, 10))
            rec["y20"].append(recall(c, hybrid, 20))
            rec["l20"].append(recall(c, lex[o["id"]], 20))
            mista = sorted(husty.index(x) + 1 for x in c if x in husty)
            detail[o["id"]][jmeno] = ", ".join(map(str, mista)) or "–"
        conn.commit()
        usd = (r["tokenu"] + tokenu_dotazu) / 1e6 * CENA_USD_ZA_MILION[model]
        celkem_usd += usd
        radky.append((jmeno, rec, statistics.median(latence), r["useku"], r["tokenu"], usd, sekund))
        print(f"{jmeno}: hotovo ({r['useku']} úseků, {r['tokenu']} tokenů, {usd:.4f} USD)", file=sys.stderr)

    print(f"## Porovnání modelů ({len(otazky)} otázek fáze 4, vzorek {len(vzorek['projev'])} projevů "
          f"+ {len(vzorek['cel'])} celků)\n")
    print("| model | husté R@10 | husté R@20 | hybrid R@10 | hybrid R@20 | latence dotazu (medián ms) | úseků | "
          "tokenů indexace | cena vzorku USD | cena plné indexace USD (odhad 36 mil. tokenů) |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for jmeno, rec, lat, useku, tk, usd, _ in radky:
        model = next(m for j, m, _ in MODELY if j == jmeno)
        plna = PLNA_TOKENU / 1e6 * CENA_USD_ZA_MILION[model]
        prumer = {k: statistics.mean(v) for k, v in rec.items()}
        print(f"| {jmeno} | {prumer['h10']:.2f} | {prumer['h20']:.2f} | {prumer['y10']:.2f} | {prumer['y20']:.2f} | "
              f"{lat:.0f} | {useku} | {tk} | {usd:.4f} | {plna:.2f} |")
    if radky:
        print(f"\nJen fulltext (stejný vzorek): R@20 = {statistics.mean(radky[0][1]['l20']):.2f}")
    print(f"\nCelkem utraceno: {celkem_usd:.4f} USD\n")
    print("### Pořadí cílů v hustém hledání (místa v top-50, – = mimo)\n")
    print("| otázka | dotaz | " + " | ".join(j for j, *_ in radky) + " |")
    print("|---|---|" + "---|" * len(radky))
    for o in otazky:
        print(f"| {o['id']} | {o['dotaz']} | " + " | ".join(detail[o['id']].get(j, "") for j, *_ in radky) + " |")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
