"""Ingest textů návrhů zákonů a PN do schématu texty.

Postup `sync_texty` (každý krok commituje sám):
1. převzetí textů z tabulek PHP webu (tisk_text, pn_text), změna hashe = nové rozdělení
2. stažení chybějících textů tisků a PN z psp.cz (DOCX)
3. schválené znění tisků po 3. čtení (historie → stránka schváleného znění → originál DOCX)
4. rozdělení dokumentů na celky (texty.cel)

Kroky 2 a 3 sdílí jeden rozpočet `limit` dokumentů za běh (šetrnost k psp.cz).
Tabulky PHP webu se čtou bez schématu (podle search_path, v provozu public).
"""
from __future__ import annotations

import hashlib
import logging
import zipfile

import psycopg

from hlidac_rag.psp.http import BASE, Fetcher
from hlidac_rag.psp.parser import page_text
from hlidac_rag.texty.parser import rozdel
from hlidac_rag.texty.stahovani import (
    docx_to_text,
    odkaz_docx_pn,
    odkaz_docx_tisku,
    odkaz_originalu,
    odkaz_schvalene_stranky,
)

log = logging.getLogger("hlidac_rag")

JEN_PDF = "jen PDF"
VADNY_DOCX = "DOCX nelze přečíst"
DOCX_CHYBI = "DOCX nenalezen"
STRANKA_CHYBI = "stránka na psp.cz nenalezena"
SCHVALENE_NENI = "schválené znění zatím není"

# Kandidáti na schválené znění: tisk je vyhlášený, nebo má v historii krok 3. čtení či pozdější
# (typy stavů podle app/Analyzer.php: 5 = 3. čtení, STAV_PO_SNEMOVNE = 64, 12, 14, 63, 8, 13, 11).
# Fáze tisku sama nestačí: Analyzer ji po kroku KONEC nechá na předchozí hodnotě a názvy fází
# se mohou změnit, typy stavů z psp.cz ne. Zamítnuté a stažené tisky schválené znění nemají.
STAVY_PO_3_CTENI = (5, 64, 12, 14, 63, 8, 13, 11)
FAZE_BEZ_SCHVALENI = ("Zamítnuto", "Vzat zpět")

UPSERT = """
INSERT INTO texty.dokument (druh, ct, cislo_pn, verze, docx_url, zdroj_textu, text, hash, znaku,
                            zkontrolovano_at, chyba)
VALUES (%(druh)s, %(ct)s, %(cislo_pn)s, %(verze)s, %(docx_url)s, 'vlastni_stazeni', %(text)s, %(hash)s,
        %(znaku)s, now(), %(chyba)s)
ON CONFLICT (druh, ct, cislo_pn, verze) DO UPDATE SET
  docx_url = EXCLUDED.docx_url, zdroj_textu = EXCLUDED.zdroj_textu, text = EXCLUDED.text,
  hash = EXCLUDED.hash, znaku = EXCLUDED.znaku, zkontrolovano_at = EXCLUDED.zkontrolovano_at,
  chyba = EXCLUDED.chyba,
  rozdeleno_at = CASE WHEN texty.dokument.hash IS DISTINCT FROM EXCLUDED.hash
                      THEN NULL ELSE texty.dokument.rozdeleno_at END
"""

# převzetí z tabulky PHP webu; řádek se mění jen při jiném hashi textu
PREVZETI = """
INSERT INTO texty.dokument AS d (druh, ct, cislo_pn, verze, docx_url, zdroj_textu, text, hash, znaku)
SELECT %(druh)s, s.ct, {cislo}, 'puvodni', s.docx_url, %(zdroj)s, s.text,
       encode(sha256(convert_to(s.text, 'UTF8')), 'hex'), char_length(s.text)
FROM {tabulka} s
WHERE s.text IS NOT NULL AND btrim(s.text) <> ''
ON CONFLICT (druh, ct, cislo_pn, verze) DO UPDATE SET
  docx_url = EXCLUDED.docx_url, zdroj_textu = EXCLUDED.zdroj_textu, text = EXCLUDED.text,
  hash = EXCLUDED.hash, znaku = EXCLUDED.znaku, chyba = NULL, rozdeleno_at = NULL
WHERE d.hash IS DISTINCT FROM EXCLUDED.hash
"""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _uloz(conn: psycopg.Connection, *, druh: str, ct: int, cislo_pn: int, verze: str,
          docx_url: str | None = None, text: str | None = None, chyba: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(UPSERT, {
            "druh": druh, "ct": ct, "cislo_pn": cislo_pn, "verze": verze, "docx_url": docx_url,
            "text": text, "hash": _hash(text) if text is not None else None,
            "znaku": len(text) if text is not None else 0, "chyba": chyba,
        })
    conn.commit()


def _stranka(fetcher: Fetcher, url: str) -> str | None:
    raw = fetcher.get(url)
    return page_text(raw) if raw is not None else None


def _docx(fetcher: Fetcher, url: str) -> tuple[str | None, str | None]:
    """Stáhne DOCX a vrátí (text, chyba). Vadný soubor (HTML místo ZIPu, chybí document.xml)
    běh neshodí, jen se zapíše jako chyba dokumentu."""
    data = fetcher.get(url)
    if data is None:
        return None, DOCX_CHYBI
    try:
        text = docx_to_text(data)
    except (zipfile.BadZipFile, KeyError) as e:
        log.warning("vadný DOCX %s: %s", url, e)
        return None, VADNY_DOCX
    if not text.strip():
        return None, VADNY_DOCX
    return text, None


def _prevezmi(conn: psycopg.Connection) -> int:
    n = 0
    with conn.cursor() as cur:
        cur.execute(PREVZETI.format(cislo="0", tabulka="tisk_text"), {"druh": "tisk", "zdroj": "tisk_text"})
        n += cur.rowcount
        # pojistka: dva řádky se stejným (ct, cislo) by ON CONFLICT shodily celý krok
        pn_text = "(SELECT DISTINCT ON (ct, cislo) * FROM pn_text ORDER BY ct, cislo, id_dokument DESC)"
        cur.execute(PREVZETI.format(cislo="s.cislo", tabulka=pn_text), {"druh": "pn", "zdroj": "pn_text"})
        n += cur.rowcount
    conn.commit()
    return n


def _stahni_chybejici(conn: psycopg.Connection, fetcher: Fetcher, obdobi: int, limit: int, st: dict) -> int:
    """Tisky a PN bez dokumentu `puvodni`; nejdřív tisky (základ pro PN), novější dřív. Vrací počet pokusů."""
    if limit <= 0:
        return 0
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 'tisk', ct, 0 FROM (SELECT DISTINCT ct FROM tisky) t
            WHERE NOT EXISTS (SELECT 1 FROM texty.dokument d
                              WHERE d.druh = 'tisk' AND d.ct = t.ct AND d.cislo_pn = 0 AND d.verze = 'puvodni')
            ORDER BY ct DESC LIMIT %s""", (limit,))
        fronta = cur.fetchall()
        cur.execute("""
            SELECT 'pn', ct, cislo FROM (SELECT DISTINCT ct, cislo FROM pn) p
            WHERE NOT EXISTS (SELECT 1 FROM texty.dokument d
                              WHERE d.druh = 'pn' AND d.ct = p.ct AND d.cislo_pn = p.cislo AND d.verze = 'puvodni')
            ORDER BY cislo DESC LIMIT %s""", (limit,))
        fronta += cur.fetchall()
    conn.commit()
    fronta = fronta[:limit]
    for druh, ct, cislo in fronta:
        if druh == "tisk":
            url = f"{BASE}/sqw/text/tiskt.sqw?o={obdobi}&ct={ct}&ct1=0"
            najdi = odkaz_docx_tisku
        else:
            url = f"{BASE}/sqw/sd.sqw?cd={cislo}&o={obdobi}"
            najdi = odkaz_docx_pn
        stranka = _stranka(fetcher, url)
        if stranka is None:
            _uloz(conn, druh=druh, ct=ct, cislo_pn=cislo, verze="puvodni", chyba=STRANKA_CHYBI)
            st["chyby"] += 1
            continue
        odkaz = najdi(stranka)
        if not odkaz:
            _uloz(conn, druh=druh, ct=ct, cislo_pn=cislo, verze="puvodni", chyba=JEN_PDF)
            st["jen_pdf"] += 1
            continue
        text, chyba = _docx(fetcher, odkaz)
        _uloz(conn, druh=druh, ct=ct, cislo_pn=cislo, verze="puvodni", docx_url=odkaz, text=text, chyba=chyba)
        if text is None:
            st["chyby"] += 1
        else:
            st["stazeno"] += 1
    return len(fronta)


def _schvalene(conn: psycopg.Connection, fetcher: Fetcher, obdobi: int, limit: int, st: dict) -> int:
    """Schválené znění tisků po 3. čtení; bez odkazu se uloží jen čas kontroly (další za 24 h)."""
    if limit <= 0:
        return 0
    with conn.cursor() as cur:
        cur.execute("""
            SELECT t.ct FROM (SELECT ct, max(faze) AS faze FROM tisky GROUP BY ct) t
            LEFT JOIN texty.dokument d
              ON d.druh = 'tisk' AND d.ct = t.ct AND d.cislo_pn = 0 AND d.verze = 'schvalene'
            WHERE (t.faze = 'Vyhlášeno'
                   OR EXISTS (SELECT 1 FROM kroky k WHERE k.ct = t.ct AND k.typ_stavu = ANY(%s)))
              AND coalesce(t.faze, '') <> ALL(%s)
              AND (d.id IS NULL OR (d.text IS NULL
                   AND (d.zkontrolovano_at IS NULL OR d.zkontrolovano_at < now() - interval '24 hours')))
            ORDER BY d.zkontrolovano_at NULLS FIRST, t.ct DESC
            LIMIT %s""", (list(STAVY_PO_3_CTENI), list(FAZE_BEZ_SCHVALENI), limit))
        fronta = [r[0] for r in cur.fetchall()]
    conn.commit()
    for ct in fronta:
        historie = _stranka(fetcher, f"{BASE}/sqw/historie.sqw?o={obdobi}&t={ct}")
        odkaz_pz = odkaz_schvalene_stranky(historie) if historie else None
        stranka_pz = _stranka(fetcher, odkaz_pz) if odkaz_pz else None
        odkaz = odkaz_originalu(stranka_pz) if stranka_pz else None
        if not odkaz:
            _uloz(conn, druh="tisk", ct=ct, cislo_pn=0, verze="schvalene", chyba=SCHVALENE_NENI)
            continue
        text, chyba = _docx(fetcher, odkaz)
        _uloz(conn, druh="tisk", ct=ct, cislo_pn=0, verze="schvalene", docx_url=odkaz, text=text, chyba=chyba)
        if text is None:
            st["chyby"] += 1
        else:
            st["schvalene"] += 1
    return len(fronta)


def _rozdel_dokumenty(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id, text FROM texty.dokument WHERE text IS NOT NULL AND rozdeleno_at IS NULL ORDER BY id")
        dokumenty = cur.fetchall()
    conn.commit()
    for dokument_id, text in dokumenty:
        cely = rozdel(text)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM texty.cel WHERE dokument_id = %s", (dokument_id,))
            cur.executemany(
                """INSERT INTO texty.cel (dokument_id, poradi, typ, oznaceni, cesta, meni_zakon, text, znaku, od_znaku)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                [(dokument_id, c.poradi, c.typ, c.oznaceni, c.cesta, c.meni_zakon, c.text, len(c.text), c.od_znaku)
                 for c in cely],
            )
            cur.execute("UPDATE texty.dokument SET rozdeleno_at = now() WHERE id = %s", (dokument_id,))
        conn.commit()
    return len(dokumenty)


def sync_texty(conn: psycopg.Connection, fetcher: Fetcher, obdobi: int, limit: int = 40) -> dict:
    """Převezme, doplní a rozdělí texty. Vrací počty {prevzato, stazeno, schvalene, rozdeleno, chyby, jen_pdf}.

    Síťová chyba (psp.cz nedostupné) stahování v tomto běhu ukončí, převzaté texty se přesto rozdělí.
    """
    st = {"prevzato": 0, "stazeno": 0, "schvalene": 0, "rozdeleno": 0, "chyby": 0, "jen_pdf": 0}
    st["prevzato"] = _prevezmi(conn)
    try:
        zbyva = limit - _stahni_chybejici(conn, fetcher, obdobi, limit, st)
        _schvalene(conn, fetcher, obdobi, zbyva, st)
    except OSError as e:
        conn.rollback()
        log.warning("stahování textů z psp.cz přerušeno: %s", e)
        st["chyby"] += 1
    st["rozdeleno"] = _rozdel_dokumenty(conn)
    return st
