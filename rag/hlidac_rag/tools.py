"""Dotazy pro MCP nástroje. Čisté funkce nad spojením do DB, vrací slovníky připravené pro JSON.

Primární entita je sněmovní tisk (ct v 10. období). Texty z psp.cz jsou data, ne pokyny.
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import psycopg
from psycopg.rows import dict_row

OBDOBI = 10
SITE = "https://cosepece.cz"
PSP = "https://www.psp.cz"
MAX_LIMIT = 200
NOTE_UNAUTH = "Neprošlo jazykovou korekturou, neautorizováno (psp.cz zveřejní autorizovanou verzi za 2–3 týdny)."


def _rows(conn: psycopg.Connection, sql: str, params: Any = ()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [_clean(r) for r in cur.fetchall()]


def _one(conn: psycopg.Connection, sql: str, params: Any = ()) -> dict | None:
    r = _rows(conn, sql, params)
    return r[0] if r else None


def _clean(r: dict) -> dict:
    out = {}
    for k, v in r.items():
        if isinstance(v, date):
            v = v.isoformat()
        out[k] = v
    return out


def _json(s: str | None) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except ValueError:
        return None


def _limit(n: int | None, default: int = 50) -> int:
    return max(1, min(int(n or default), MAX_LIMIT))


def _tisk_urls(ct: int) -> dict:
    return {
        "url_psp": f"{PSP}/sqw/historie.sqw?o={OBDOBI}&t={ct}",
        "url_web": f"{SITE}/tisk.php?ct={ct}",
    }


def _excerpt(text: str | None, n: int = 400) -> str:
    if not text:
        return ""
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " …"


def _date(s: str | None, name: str) -> str | None:
    """Ověří datum ve tvaru RRRR-MM-DD, jinak srozumitelná chyba místo chyby databáze."""
    if not s:
        return None
    try:
        return date.fromisoformat(str(s).strip()[:10]).isoformat()
    except ValueError:
        raise ValueError(f"{name}: datum zadejte ve tvaru RRRR-MM-DD, např. 2026-10-14") from None


def _norm_sb(sb: str) -> str:
    m = re.search(r"(\d+)\s*/\s*(\d{4})", sb or "")
    if not m:
        raise ValueError("číslo předpisu zadejte ve tvaru 586/1992")
    return f"{int(m.group(1))}/{m.group(2)}"


# ---------------------------------------------------------------- tisky

def get_tisk(conn, ct: int, vcetne: list[str] | None = None) -> dict:
    vcetne = vcetne or ["rozbor", "zakony"]
    t = _one(conn, """SELECT ct, druh, typ_navrhu, navrhovatel, nazev, uplny_nazev, predlozeno, faze, posledni,
                             p90, p90ok, zl, pn_count, pn_last, zakony, sbirka, score, reasons
                      FROM tisky WHERE ct = %s""", (ct,))
    bod = _rows(conn, "SELECT DISTINCT ON (nazev) schuze, cislo, nazev, konec FROM steno.bod WHERE ct = %s ORDER BY nazev, schuze", (ct,))
    if not t and not bod:
        return {"chyba": f"Tisk {ct} v {OBDOBI}. období nesleduji. Zkuste seznam_tisku(q=…)."}
    out: dict = {"ct": ct, **_tisk_urls(ct)}
    if t:
        t["reasons"] = _json(t["reasons"])
        t["zakony"] = [z for z in (t["zakony"] or "").split(",") if z]
        out.update(t)
    else:
        out["poznamka"] = "Tisk není návrh zákona, web ho nesleduje; údaje jen z pořadu schůzí."
        out["nazev"] = bod[0]["nazev"]
    stats = _one(conn, """SELECT count(*) AS projevu, count(DISTINCT id_osoba) AS recniku,
                                 min(datum) AS prvni, max(datum) AS posledni
                          FROM steno.projev WHERE ct = %s AND zruseno IS NULL AND NOT predsedajici""", (ct,))
    out["projevy"] = stats
    if "rozbor" in vcetne:
        tt = _one(conn, "SELECT ai_json, ai_score, ai_model, signals, chars, docx_url FROM tisk_text WHERE ct = %s", (ct,))
        if tt:
            out["rozbor"] = {"ai": _json(tt["ai_json"]), "ai_skore": tt["ai_score"], "model": tt["ai_model"],
                             "priznaky": _json(tt["signals"]), "znaku_textu": tt["chars"], "docx": tt["docx_url"]}
    if "kroky" in vcetne:
        out["kroky"] = _rows(conn, "SELECT datum, stav, akce FROM kroky WHERE ct = %s ORDER BY datum, id_hist", (ct,))
    if "pn" in vcetne:
        out["pn"] = pn_k_tisku(conn, ct)["pn"]
    if "upozorneni" in vcetne:
        out["upozorneni"] = _rows(conn, "SELECT alert_key, kind, event_at, title, body, score FROM alerts WHERE ct = %s ORDER BY event_at", (ct,))
    if "body" in vcetne:
        out["body_poradu"] = bod
    if "zakony" in vcetne:
        pn_laws = _rows(conn, "SELECT laws FROM pn_text WHERE ct = %s AND laws <> ''", (ct,))
        out["zakony_z_pn"] = sorted({z for r in pn_laws for z in r["laws"].split(",") if z})
    return out


def seznam_tisku(conn, q: str = "", min_skore: int = 0, faze: str = "", typ_navrhu: str = "",
                 razeni: str = "skore", limit: int = 50) -> dict:
    order = {"skore": "score DESC, posledni DESC", "posledni": "posledni DESC NULLS LAST",
             "predlozeno": "predlozeno DESC"}.get(razeni, "score DESC")
    where, params = ["score >= %s"], [min_skore]
    if q:
        where.append("(nazev ILIKE %s OR uplny_nazev ILIKE %s OR navrhovatel ILIKE %s OR zakony LIKE %s)")
        params += [f"%{q}%"] * 4
    if faze:
        where.append("faze ILIKE %s")
        params.append(f"%{faze}%")
    if typ_navrhu:
        where.append("typ_navrhu ILIKE %s")
        params.append(f"%{typ_navrhu}%")
    rows = _rows(conn, f"""SELECT ct, typ_navrhu, nazev, navrhovatel, predlozeno, faze, posledni, pn_count, zakony,
                                  sbirka, score FROM tisky WHERE {' AND '.join(where)} ORDER BY {order} LIMIT %s""",
                 (*params, _limit(limit)))
    for r in rows:
        r.update(_tisk_urls(r["ct"]))
    return {"pocet": len(rows), "tisky": rows}


def tisky_menici_zakon(conn, sb: str) -> dict:
    sb = _norm_sb(sb)
    rows = _rows(conn, """SELECT ct, nazev, typ_navrhu, faze, sbirka, score, 'nazev' AS zdroj FROM tisky
                          WHERE ',' || zakony || ',' LIKE %s""", (f"%,{sb},%",))
    found = {r["ct"] for r in rows}
    for r in _rows(conn, """SELECT DISTINCT t.ct, t.nazev, t.typ_navrhu, t.faze, t.sbirka, t.score, 'pn' AS zdroj
                            FROM pn_text p JOIN tisky t ON t.ct = p.ct WHERE ',' || p.laws || ',' LIKE %s""", (f"%,{sb},%",)):
        if r["ct"] not in found:
            rows.append(r)
            found.add(r["ct"])
    for r in rows:
        r.update(_tisk_urls(r["ct"]))
    return {"zakon": sb, "pocet": len(rows), "tisky": rows,
            "poznamka": "zdroj nazev = zákon v názvu tisku, pn = zákon měněný pozměňovacím návrhem k tisku"}


def tisk_podle_sbirky(conn, sb: str) -> dict:
    sb = _norm_sb(sb)
    rows = _rows(conn, "SELECT ct, nazev, typ_navrhu, faze, sbirka, score FROM tisky WHERE sbirka = %s", (sb,))
    if not rows:
        return {"chyba": f"Žádný tisk {OBDOBI}. období nebyl vyhlášen pod číslem {sb} Sb."}
    for r in rows:
        r.update(_tisk_urls(r["ct"]))
    return {"sbirka": sb, "tisky": rows}


# ---------------------------------------------------------------- pozměňovací návrhy

def pn_k_tisku(conn, ct: int, id_osoba: int | None = None, limit: int = 100) -> dict:
    where, params = ["p.ct = %s"], [ct]
    if id_osoba:
        where.append("p.id_osoba = %s")
        params.append(id_osoba)
    rows = _rows(conn, f"""SELECT p.cislo, p.id_osoba, p.osoba, p.cas, t.ai_score, t.ai_json, t.laws, t.chars
                           FROM pn p LEFT JOIN pn_text t ON t.id_dokument = p.id_dokument
                           WHERE {' AND '.join(where)} ORDER BY p.cas LIMIT %s""", (*params, _limit(limit, 100)))
    for r in rows:
        ai = _json(r.pop("ai_json"))
        r["titulek"] = ai.get("titulek") if isinstance(ai, dict) else None
        r["url_psp"] = f"{PSP}/sqw/sd.sqw?cd={r['cislo']}&o={OBDOBI}"
    autori: dict = {}
    for r in rows:
        autori[r["osoba"]] = autori.get(r["osoba"], 0) + 1
    return {"ct": ct, "pocet": len(rows), "predkladatele": sorted(autori.items(), key=lambda x: -x[1]), "pn": rows}


def get_pn(conn, cislo: int, s_textem: bool = False, max_znaku: int = 8000) -> dict:
    r = _one(conn, """SELECT p.cislo, p.ct, p.id_osoba, p.osoba, p.cas, t.ai_score, t.ai_model, t.ai_json, t.signals,
                             t.laws, t.chars, t.docx_url, t.text
                      FROM pn p LEFT JOIN pn_text t ON t.id_dokument = p.id_dokument WHERE p.cislo = %s""", (cislo,))
    if not r:
        return {"chyba": f"Pozměňovací návrh {cislo} v {OBDOBI}. období neznám."}
    text = r.pop("text")
    r["ai"] = _json(r.pop("ai_json"))
    r["priznaky"] = _json(r.pop("signals"))
    r["url_psp"] = f"{PSP}/sqw/sd.sqw?cd={cislo}&o={OBDOBI}"
    r["url_web"] = f"{SITE}/tisk.php?ct={r['ct']}"
    if s_textem:
        m = max(500, min(int(max_znaku), 50000))
        r["text"] = text[:m] if text else None
        r["text_oriznut"] = bool(text and len(text) > m)
    return r


# ---------------------------------------------------------------- projevy

_SPEECH_COLS = """p.schuze, p.turn, p.aname, p.datum, p.cas, p.id_osoba, p.recnik, p.role, p.klub, p.predsedajici,
                  p.ct, p.znaku, p.autorizovano, p.verze, p.url, b.nazev AS bod"""


def _speech_meta(r: dict) -> dict:
    """Klíč projevu a poznámka o autorizaci; pole text nechá být."""
    r["klic"] = f"{OBDOBI}/{r.pop('schuze')}/{r.pop('turn')}/{r.pop('aname')}"
    if not r["autorizovano"]:
        r["poznamka"] = NOTE_UNAUTH
    return r


def _speech_rows(rows: list[dict], excerpt: int = 400) -> list[dict]:
    """Řádky seznamu projevů: text se zkrátí na úryvek."""
    for r in rows:
        _speech_meta(r)
        if "text" in r:
            text = r.pop("text")
            r["uryvek"] = _excerpt(text, excerpt)
            r["zkraceno"] = bool(text) and len(text) > excerpt
    return rows


def projevy_k_tisku(conn, ct: int, id_osoba: int | None = None, klub: str | None = None, od: str | None = None,
                    do: str | None = None, min_znaku: int = 200, vcetne_predsedajicich: bool = False,
                    limit: int = 50, offset: int = 0) -> dict:
    where, params = ["p.ct = %s", "p.zruseno IS NULL", "p.znaku >= %s"], [ct, min_znaku]
    if not vcetne_predsedajicich:
        where.append("NOT p.predsedajici")
    if id_osoba:
        where.append("p.id_osoba = %s")
        params.append(id_osoba)
    if klub:
        where.append("p.klub ILIKE %s")
        params.append(klub)
    if od:
        where.append("p.datum >= %s")
        params.append(_date(od, "od"))
    if do:
        where.append("p.datum <= %s")
        params.append(_date(do, "do"))
    w = " AND ".join(where)
    total = _one(conn, f"SELECT count(*) AS n FROM steno.projev p WHERE {w}", params)["n"]
    rows = _rows(conn, f"""SELECT {_SPEECH_COLS}, p.text FROM steno.projev p LEFT JOIN steno.bod b ON b.id_bod = p.id_bod
                           WHERE {w} ORDER BY p.datum, p.schuze, p.poradi LIMIT %s OFFSET %s""",
                 (*params, _limit(limit), max(0, int(offset))))
    return {"ct": ct, "celkem": total, "offset": offset, "projevy": _speech_rows(rows)}


def najdi_osobu(conn, jmeno: str) -> dict:
    rows = _rows(conn, """SELECT o.id_osoba, o.jmeno, o.prijmeni,
                                 (SELECT klub FROM steno.klub_clenstvi k WHERE k.id_osoba = o.id_osoba
                                  ORDER BY od DESC NULLS LAST LIMIT 1) AS klub,
                                 (SELECT count(*) FROM steno.projev p WHERE p.id_osoba = o.id_osoba AND p.zruseno IS NULL) AS projevu,
                                 (SELECT count(*) FROM pn WHERE pn.id_osoba = o.id_osoba) AS pn
                          FROM steno.osoba o
                          WHERE unaccent_simple(o.jmeno || ' ' || o.prijmeni) ILIKE unaccent_simple(%s)
                             OR unaccent_simple(o.prijmeni) ILIKE unaccent_simple(%s)
                          ORDER BY projevu DESC LIMIT 20""", (f"%{jmeno}%", f"%{jmeno}%"))
    rows = [r for r in rows if r["projevu"] or r["pn"] or r["klub"]] or rows
    return {"pocet": len(rows), "osoby": rows}


def _resolve_person(conn, osoba: int | str) -> tuple[int | None, dict | None]:
    if isinstance(osoba, int) or str(osoba).isdigit():
        return int(osoba), None
    found = najdi_osobu(conn, str(osoba))["osoby"]
    active = [o for o in found if o["projevu"]]
    if len(active) == 1:
        return active[0]["id_osoba"], None
    return None, {"chyba": "Jméno není jednoznačné, zopakujte dotaz s id_osoba.", "kandidati": found[:10]}


def projevy_recnika(conn, osoba: int | str, od: str | None = None, do: str | None = None, ct: int | None = None,
                    q: str | None = None, min_znaku: int = 0, limit: int = 50, offset: int = 0) -> dict:
    id_osoba, err = _resolve_person(conn, osoba)
    if err:
        return err
    where, params = ["p.id_osoba = %s", "p.zruseno IS NULL", "p.znaku >= %s"], [id_osoba, min_znaku]
    if od:
        where.append("p.datum >= %s")
        params.append(_date(od, "od"))
    if do:
        where.append("p.datum <= %s")
        params.append(_date(do, "do"))
    if ct:
        where.append("p.ct = %s")
        params.append(ct)
    if q:
        where.append("to_tsvector('simple', p.text) @@ plainto_tsquery('simple', %s)")
        params.append(q)
    w = " AND ".join(where)
    total = _one(conn, f"SELECT count(*) AS n FROM steno.projev p WHERE {w}", params)["n"]
    rows = _rows(conn, f"""SELECT {_SPEECH_COLS}, p.text FROM steno.projev p LEFT JOIN steno.bod b ON b.id_bod = p.id_bod
                           WHERE {w} ORDER BY p.datum DESC, p.schuze, p.poradi LIMIT %s OFFSET %s""",
                 (*params, _limit(limit), max(0, int(offset))))
    return {"id_osoba": id_osoba, "celkem": total, "projevy": _speech_rows(rows)}


def _parse_key(klic: str) -> tuple[int, int, int, int]:
    parts = [int(x) for x in re.findall(r"\d+", klic)]
    if len(parts) == 3:
        parts = [OBDOBI, *parts]
    if len(parts) != 4:
        raise ValueError("klíč projevu má tvar obdobi/schuze/turn/aname, např. 10/34/633/1")
    return parts[0], parts[1], parts[2], parts[3]


def get_projev(conn, klic: str, okoli: int = 0, max_znaku: int = 12000, offset_znaku: int = 0,
               okoli_max_znaku: int = 1500) -> dict:
    o, s, t, a = _parse_key(klic)
    r = _one(conn, f"""SELECT p.id, p.poradi, {_SPEECH_COLS}, p.text, p.turny, p.hlasovani, p.ct_zdroj, p.id_bod
                       FROM steno.projev p LEFT JOIN steno.bod b ON b.id_bod = p.id_bod
                       WHERE p.obdobi=%s AND p.schuze=%s AND p.turn=%s AND p.aname=%s""", (o, s, t, a))
    if not r:
        return {"chyba": f"Projev {klic} neznám."}
    text = r.pop("text") or ""
    m = max(500, min(int(max_znaku), 60000))
    start = max(0, min(int(offset_znaku), len(text)))
    r["text"] = text[start:start + m]
    r["znaku_celkem"] = len(text)
    r["offset_znaku"] = start
    r["pokracuje"] = start + m < len(text)
    r["dalsi_offset_znaku"] = start + m if r["pokracuje"] else None
    r["hlasovani"] = [f"{PSP}/sqw/hlasy.sqw?G={g}" for g in (r["hlasovani"] or [])]
    pid = r.pop("id")
    if okoli:
        n = max(1, min(int(okoli), 10))
        om = max(200, min(int(okoli_max_znaku), 6000))
        r["okoli"] = _speech_rows(_rows(conn, f"""
            SELECT p.poradi, {_SPEECH_COLS}, p.text FROM steno.projev p LEFT JOIN steno.bod b ON b.id_bod = p.id_bod
            WHERE p.obdobi=%s AND p.schuze=%s AND p.zruseno IS NULL
              AND p.poradi BETWEEN (SELECT poradi FROM steno.projev WHERE id=%s) - %s
                               AND (SELECT poradi FROM steno.projev WHERE id=%s) + %s
              AND p.id <> %s ORDER BY p.poradi""", (o, s, pid, n, pid, n, pid)), om)
    r["verze_historie"] = _rows(conn, "SELECT verze, autorizovano, platne_do FROM steno.projev_verze WHERE projev_id=%s ORDER BY verze", (pid,))
    return _speech_meta(r)


def hledej_v_projevech(conn, dotaz: str, ct: int | None = None, osoba: int | str | None = None,
                       klub: str | None = None, od: str | None = None, do: str | None = None,
                       vcetne_predsedajicich: bool = False, limit: int = 20) -> dict:
    """Fulltext (bez sémantiky, ta přijde s embeddingy): slova bez ohledu na diakritiku, předpony slov."""
    words = [w for w in re.findall(r"\w+", unaccent_py(dotaz.lower())) if len(w) > 1][:12]
    if not words:
        return {"chyba": "prázdný dotaz"}
    # zkrácení slov kvůli skloňování (heuristika z EpasDocsFetch)
    def stem(w: str) -> str:
        if any(ch.isdigit() for ch in w):
            return w
        return w[:-2] if len(w) >= 7 else w[:-1] if len(w) >= 5 else w
    tsq = " & ".join(stem(w) + ":*" for w in words)
    where, params = ["to_tsvector('simple', unaccent_simple(p.text)) @@ to_tsquery('simple', %s)", "p.zruseno IS NULL"], [tsq]
    if not vcetne_predsedajicich:
        where.append("NOT p.predsedajici")
    if ct:
        where.append("p.ct = %s")
        params.append(ct)
    if osoba:
        id_osoba, err = _resolve_person(conn, osoba)
        if err:
            return err
        where.append("p.id_osoba = %s")
        params.append(id_osoba)
    if klub:
        where.append("p.klub ILIKE %s")
        params.append(klub)
    if od:
        where.append("p.datum >= %s")
        params.append(_date(od, "od"))
    if do:
        where.append("p.datum <= %s")
        params.append(_date(do, "do"))
    rows = _rows(conn, f"""SELECT {_SPEECH_COLS},
                                  ts_headline('simple', p.text, to_tsquery('simple', %s),
                                              'MaxFragments=2, MaxWords=40, MinWords=15') AS uryvek_shoda,
                                  ts_rank(to_tsvector('simple', unaccent_simple(p.text)), to_tsquery('simple', %s)) AS rank
                           FROM steno.projev p LEFT JOIN steno.bod b ON b.id_bod = p.id_bod
                           WHERE {' AND '.join(where)} ORDER BY rank DESC, p.datum DESC LIMIT %s""",
                 (tsq, tsq, *params, _limit(limit, 20)))
    for r in rows:
        r.pop("rank", None)
    return {"dotaz": tsq, "pocet": len(rows), "projevy": _speech_rows(rows),
            "poznamka": "Fulltext bez ohledu na diakritiku; ts_headline zvýrazní jen slova s diakritikou shodnou s dotazem."}


def program_schuze(conn, schuze: int) -> dict:
    s = _one(conn, "SELECT schuze, od, \"do\" FROM steno.schuze WHERE obdobi=%s AND schuze=%s", (OBDOBI, schuze))
    if not s:
        return {"chyba": f"Schůze {schuze} v {OBDOBI}. období neznám."}
    s = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in s.items()}
    s["body"] = _rows(conn, """SELECT b.cislo, b.nazev, b.konec, b.ct,
                                      (SELECT count(*) FROM steno.projev p WHERE p.id_bod = b.id_bod AND p.zruseno IS NULL
                                       AND NOT p.predsedajici) AS projevu,
                                      (SELECT min(datum) FROM steno.projev p WHERE p.id_bod = b.id_bod) AS projednano
                               FROM steno.bod b WHERE b.obdobi=%s AND b.schuze=%s ORDER BY b.cislo NULLS LAST""",
                       (OBDOBI, schuze))
    s["url_psp"] = f"{PSP}/eknih/2025ps/stenprot/{schuze:03d}schuz/index.htm"
    return s


def zmeny_projevu(conn, od: str | None = None, ct: int | None = None, limit: int = 50) -> dict:
    """Projevy, jejichž text se po prvním zveřejnění změnil (jazyková korektura, autorizace)."""
    where, params = ["p.verze > 1"], []
    if od:
        where.append("p.zmeneno >= %s")
        params.append(_date(od, "od"))
    if ct:
        where.append("p.ct = %s")
        params.append(ct)
    rows = _rows(conn, f"""SELECT {_SPEECH_COLS}, p.zmeneno,
                                  (SELECT length(v.text) FROM steno.projev_verze v WHERE v.projev_id = p.id
                                   ORDER BY v.verze DESC LIMIT 1) AS znaku_predtim
                           FROM steno.projev p LEFT JOIN steno.bod b ON b.id_bod = p.id_bod
                           WHERE {' AND '.join(where)} ORDER BY p.zmeneno DESC LIMIT %s""", (*params, _limit(limit)))
    # zmeneno už převedl _clean na řetězec
    return {"pocet": len(rows), "projevy": _speech_rows(rows)}


def stav_dat(conn) -> dict:
    out = _one(conn, """SELECT
          (SELECT count(*) FROM steno.projev WHERE zruseno IS NULL) AS projevu,
          (SELECT count(*) FROM steno.projev WHERE zruseno IS NULL AND NOT autorizovano) AS projevu_neautorizovanych,
          (SELECT count(*) FROM steno.projev WHERE verze > 1) AS projevu_zmenenych,
          (SELECT count(*) FROM steno.cast WHERE html IS NOT NULL) AS casti_stazeno,
          (SELECT count(*) FROM steno.cast WHERE html IS NULL) AS casti_chybi,
          (SELECT max(schuze) FROM steno.cast WHERE html IS NOT NULL) AS posledni_schuze,
          (SELECT max(datum) FROM steno.projev) AS posledni_den,
          (SELECT count(*) FROM tisky) AS tisku,
          (SELECT count(*) FROM pn) AS pn""") or {}
    for k in ("steno_last_result", "last_sync"):
        r = _one(conn, "SELECT v FROM meta WHERE k = %s", (k,))
        out[k] = _json(r["v"]) if r and k.endswith("result") else (r["v"] if r else None)
    return out


_ACCENTS = str.maketrans("áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽäöüÄÖÜ", "acdeeinorstuuyzACDEEINORSTUUYZaouAOU")


def unaccent_py(s: str) -> str:
    return s.translate(_ACCENTS)
