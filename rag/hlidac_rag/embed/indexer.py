"""Přírůstkové indexování embeddingů projevů a celků textů do rag.usek.

Kandidát = zdroj bez úseků, se změněným otiskem (text nebo údaje hlavičky a filtrů) nebo s úseky jiného modelu.
Otisk zdroje se počítá v SQL (md5 textu a údajů), takže hodinový běh nemusí číst a dělit všechny texty.
Zdroje se embedují po dávkách (víc zdrojů v jednom požadavku), ale zapisují po jednom v samostatné transakci
(smaž úseky zdroje + vlož nové): zdroj má vždy buď všechny úseky, nebo žádný. Strop tokenů se hlídá odhadem před
přidáním zdroje do dávky; zdroj, který se nevejde, zůstane na příští běh.
"""
from __future__ import annotations

import logging
import re

from .klient import EmbedChyba
from .useky import hlavicka_celku, hlavicka_projevu, rozdel, text_hash

log = logging.getLogger("hlidac_rag.embed")

DIM = 1024                     # dimenze sloupce rag.usek.embedding
MIN_ZNAKU_PROJEVU = 200        # kratší projevy (procedurální věty) se neindexují
CENA_USD_ZA_MILION = {"text-embedding-3-large": 0.13, "text-embedding-3-small": 0.02}
ZNAKU_NA_TOKEN = 3             # odhad pro strop (čeština v tokenizéru OpenAI ~2,9 znaku na token)
DAVKA_VSTUPU = 128             # vstupů v jednom požadavku na API
STRANKA = 500                  # kandidátů načtených najednou

# otisk projevu: text, údaje hlavičky (řečník, role, klub, datum, schůze, bod, tisky) a filtrů (osoba, druh, předsedající)
_OTISK_PROJEVU = """md5(concat_ws('|', md5(p.text), p.recnik, p.role, p.klub, p.datum::text, p.schuze::text, b.nazev,
        (SELECT string_agg(pt.ct::text, ',' ORDER BY pt.ct) FROM steno.projev_tisk pt WHERE pt.projev_id = p.id),
        p.id_osoba::text, p.druh, p.predsedajici::text))"""
_PROJEV_OK = f"p.zruseno IS NULL AND p.znaku >= {MIN_ZNAKU_PROJEVU} AND length(btrim(p.text)) > 0"

_NAZEV_TISKU = "(SELECT t.nazev FROM tisky t WHERE t.ct = d.ct ORDER BY t.id_tisk LIMIT 1)"
_OTISK_CELKU = f"""md5(concat_ws('|', md5(c.text), c.cesta, c.meni_zakon, c.typ, d.druh, d.ct::text, d.cislo_pn::text,
        d.verze, {_NAZEV_TISKU}))"""
_CEL_OK = ("d.text IS NOT NULL AND d.rozdeleno_at IS NOT NULL AND d.chyba IS NULL AND length(btrim(c.text)) > 0")


def _tabulka(t: str) -> str:
    if not re.fullmatch(r"[a-z_]+\.[a-z_0-9]+", t):
        raise ValueError(f"neplatná tabulka {t}")
    return t


def _ma_tisky(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('tisky') IS NOT NULL")
        return bool(cur.fetchone()[0])


def _dotaz_kandidatu(zdroj: str, tabulka: str, tisky: bool, jen_pocet: bool) -> str:
    """SQL kandidátů zdroje; parametry (model[, limit])."""
    if zdroj == "projev":
        otisk, kde, sloupec = _OTISK_PROJEVU, _PROJEV_OK, "projev_id"
        z = "steno.projev p LEFT JOIN steno.bod b ON b.id_bod = p.id_bod"
        idcol, poradi = "p.id", "k.datum DESC NULLS LAST, k.id DESC"
        vyber = """p.id, p.text, p.recnik, p.role, p.klub, p.datum, p.schuze, b.nazev AS bod_nazev, p.id_osoba,
                   p.druh, p.predsedajici,
                   ARRAY(SELECT pt.ct FROM steno.projev_tisk pt WHERE pt.projev_id = p.id ORDER BY pt.ct) AS tisky"""
    elif zdroj == "cel":
        otisk = _OTISK_CELKU if tisky else _OTISK_CELKU.replace(_NAZEV_TISKU, "NULL")
        kde, sloupec = _CEL_OK, "cel_id"
        z = "texty.cel c JOIN texty.dokument d ON d.id = c.dokument_id"
        idcol, poradi = "c.id", "k.ct DESC, k.cislo_pn, k.verze, k.poradi"
        nazev = _NAZEV_TISKU if tisky else "NULL"
        vyber = f"""c.id, c.poradi, c.text, c.typ, c.cesta, c.meni_zakon, d.druh, d.ct, d.cislo_pn, d.verze,
                    {nazev} AS nazev_tisku"""
    else:
        raise ValueError(f"neznámý zdroj {zdroj}")
    hotovo = f"""SELECT DISTINCT ON ({sloupec}) {sloupec} AS id, zdroj_hash, model FROM {tabulka}
                 WHERE {sloupec} IS NOT NULL ORDER BY {sloupec}, id"""
    base = f"""SELECT {vyber if not jen_pocet else idcol}, {otisk} AS otisk FROM {z} WHERE {kde}"""
    if jen_pocet:
        return f"""WITH k AS ({base}), h AS ({hotovo})
                   SELECT count(*) FROM k LEFT JOIN h ON h.id = k.id
                   WHERE h.id IS NULL OR h.zdroj_hash <> k.otisk OR h.model <> %s"""
    return f"""WITH h AS ({hotovo})
               SELECT k.* FROM ({base}) k LEFT JOIN h ON h.id = k.id
               WHERE h.id IS NULL OR h.zdroj_hash <> k.otisk OR h.model <> %s
               ORDER BY {poradi} LIMIT %s"""


def kandidati(conn, zdroj: str, limit: int, model: str = "text-embedding-3-large",
              tabulka: str = "rag.usek") -> list[dict]:
    """Zdroje k (pře)indexování: projevy (nezrušené, od 200 znaků) a celky dostupných dokumentů bez úseků,
    se změněným otiskem nebo s úseky jiného modelu. Projevy od nejnovějších, celky od nejvyšších tisků."""
    sql = _dotaz_kandidatu(zdroj, _tabulka(tabulka), _ma_tisky(conn), jen_pocet=False)
    with conn.cursor() as cur:
        cur.execute(sql, (model, limit))
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def pocet_kandidatu(conn, zdroj: str, model: str, tabulka: str = "rag.usek") -> int:
    with conn.cursor() as cur:
        cur.execute(_dotaz_kandidatu(zdroj, _tabulka(tabulka), _ma_tisky(conn), jen_pocet=True), (model,))
        return int(cur.fetchone()[0])


def uklid(conn, tabulka: str = "rag.usek") -> int:
    """Smaže úseky zdrojů, které se už neindexují (zrušený nebo zkrácený projev, nedostupný dokument)."""
    t = _tabulka(tabulka)
    with conn.cursor() as cur:
        cur.execute(f"""DELETE FROM {t} u USING steno.projev p
                        WHERE u.projev_id = p.id AND NOT ({_PROJEV_OK})""")
        n = cur.rowcount
        cur.execute(f"""DELETE FROM {t} u USING texty.cel c JOIN texty.dokument d ON d.id = c.dokument_id
                        WHERE u.cel_id = c.id AND NOT ({_CEL_OK})""")
        n += cur.rowcount
    conn.commit()
    return n


def _priprav(zdroj: str, r: dict) -> list[dict]:
    """Úseky zdroje s hlavičkou, vstupem embeddingu a kopií filtrů."""
    if zdroj == "projev":
        hlav = hlavicka_projevu(r)
        filtry = dict(projev_id=r["id"], cel_id=None, ct=r["tisky"][0] if r["tisky"] else None,
                      id_osoba=r["id_osoba"], klub=r["klub"], datum=r["datum"], predsedajici=r["predsedajici"],
                      druh=r["druh"], verze=None, vcetne_duvodove=None)
    else:
        hlav = hlavicka_celku(dict(r, pn=r["cislo_pn"]))
        filtry = dict(projev_id=None, cel_id=r["id"], ct=r["ct"], id_osoba=None, klub=None, datum=None,
                      predsedajici=None, druh=r["druh"], verze=r["verze"],
                      vcetne_duvodove=r["typ"] != "duvodova_zprava")
    return [dict(filtry, zdroj=zdroj, poradi=u.poradi, od_znaku=u.od_znaku, do_znaku=u.do_znaku, hlavicka=hlav,
                 text_hash=text_hash(hlav, u.telo), zdroj_hash=r["otisk"], vstup=f"{hlav}\n\n{u.telo}")
            for u in rozdel(r["text"])]


def _vektor_sql(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6g}" for x in v) + "]"


_SLOUPCE = ("zdroj", "projev_id", "cel_id", "poradi", "od_znaku", "do_znaku", "hlavicka", "text_hash", "zdroj_hash",
            "model", "embedding", "ct", "id_osoba", "klub", "datum", "predsedajici", "druh", "verze", "vcetne_duvodove")


def _zapis(conn, tabulka: str, zdroj: str, zdroj_id: int, useky: list[dict], model: str) -> None:
    """Úseky jednoho zdroje v jedné transakci: staré pryč, nové dovnitř."""
    sloupec = "projev_id" if zdroj == "projev" else "cel_id"
    hodnoty = ", ".join("%s::halfvec" if s == "embedding" else "%s" for s in _SLOUPCE)
    try:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {tabulka} WHERE {sloupec} = %s", (zdroj_id,))
            cur.executemany(f"INSERT INTO {tabulka} ({', '.join(_SLOUPCE)}) VALUES ({hodnoty})",
                            [tuple(_vektor_sql(u["vektor"]) if s == "embedding" else (model if s == "model" else u[s])
                                   for s in _SLOUPCE) for u in useky])
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def indexuj(conn, klient, max_tokenu: int = 3_000_000, zdroje: tuple[str, ...] = ("projev", "cel"),
            tabulka: str = "rag.usek", dim: int = DIM) -> dict:
    """Doindexuje kandidáty zdrojů do stropu tokenů. Chyba API běh nepřeruší výjimkou: vrátí se v klíči chyba
    a nezapsané zdroje zůstanou na příští běh."""
    t = _tabulka(tabulka)
    model = klient.model
    out = {"model": model, "zdroju": 0, "useku": 0, "tokenu": 0, "odhad_usd": 0.0, "zbyva_zdroju": 0,
           "preruseno_stropem": False}
    if klient.dim != dim:
        out["chyba"] = f"dimenze modelu {klient.dim} neodpovídá sloupci embedding ({dim}), nastavte EMBED_DIM={dim}"
        return out
    out["smazano"] = uklid(conn, t)
    odhad_celkem = 0          # odhad tokenů zdrojů, které se embedují nebo už embedovaly
    davka: list[tuple[str, int, list[dict]]] = []

    def odeslat() -> None:
        vstupy = [u["vstup"] for _, _, us in davka for u in us]
        vektory, tokenu = klient.embed(vstupy)
        it = iter(vektory)
        for zdroj, zid, us in davka:
            for u in us:
                u["vektor"] = next(it)
            _zapis(conn, t, zdroj, zid, us, model)
            out["zdroju"] += 1
            out["useku"] += len(us)
        out["tokenu"] += tokenu
        davka.clear()

    try:
        for zdroj in zdroje:
            videne: set[int] = set()
            while not out["preruseno_stropem"]:
                rows = [r for r in kandidati(conn, zdroj, STRANKA, model, t) if r["id"] not in videne]
                conn.commit()
                if not rows:
                    break
                for r in rows:
                    videne.add(r["id"])
                    useky = _priprav(zdroj, r)
                    if not useky:
                        continue
                    odhad = sum(len(u["vstup"]) for u in useky) // ZNAKU_NA_TOKEN + 1
                    if odhad_celkem + odhad > max_tokenu:
                        out["preruseno_stropem"] = True
                        break
                    odhad_celkem += odhad
                    davka.append((zdroj, r["id"], useky))
                    if sum(len(us) for _, _, us in davka) >= DAVKA_VSTUPU:
                        odeslat()
            if davka:
                odeslat()
    except EmbedChyba as e:
        log.warning("embeddingy: %s", e)
        out["chyba"] = str(e)[:300]
        davka.clear()
    out["odhad_usd"] = round(out["tokenu"] / 1e6 * CENA_USD_ZA_MILION.get(model, 0.13), 6)
    out["zbyva_zdroju"] = sum(pocet_kandidatu(conn, z, model, t) for z in zdroje)
    conn.commit()
    return out
