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
import urllib.error
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
    vycisti_kody_poli,
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

# kandidáti na převzetí z tabulky PHP webu: chybějící řádek nebo jiný hash SUROVÉHO textu PHP
PREVZETI_KANDIDATI = """
SELECT s.ct, {cislo} AS cislo_pn, s.docx_url, s.text,
       encode(sha256(convert_to(s.text, 'UTF8')), 'hex') AS hash
FROM {tabulka} s
LEFT JOIN texty.dokument d ON d.druh = %(druh)s AND d.ct = s.ct AND d.cislo_pn = {cislo}
                          AND d.verze = 'puvodni'
WHERE s.text IS NOT NULL AND btrim(s.text) <> ''
  AND (d.id IS NULL OR d.hash IS DISTINCT FROM encode(sha256(convert_to(s.text, 'UTF8')), 'hex'))
"""

# uložený text je očištěný od kódů polí, hash zůstává hashem surového textu PHP
PREVZETI = """
INSERT INTO texty.dokument AS d (druh, ct, cislo_pn, verze, docx_url, zdroj_textu, text, hash, znaku)
VALUES (%(druh)s, %(ct)s, %(cislo_pn)s, 'puvodni', %(docx_url)s, %(zdroj)s, %(text)s, %(hash)s, %(znaku)s)
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
        text = vycisti_kody_poli(docx_to_text(data))
    except (zipfile.BadZipFile, KeyError) as e:
        log.warning("vadný DOCX %s: %s", url, e)
        return None, VADNY_DOCX
    if not text.strip():
        return None, VADNY_DOCX
    return text, None


def _prevezmi(conn: psycopg.Connection) -> int:
    n = 0
    # pojistka: dva řádky se stejným (ct, cislo) by ON CONFLICT shodily celý krok
    pn_text = "(SELECT DISTINCT ON (ct, cislo) * FROM pn_text ORDER BY ct, cislo, id_dokument DESC)"
    davky = (("tisk", "tisk_text", "0", "tisk_text"), ("pn", "pn_text", "s.cislo", pn_text))
    with conn.cursor() as cur:
        for druh, zdroj, cislo, tabulka in davky:
            cur.execute(PREVZETI_KANDIDATI.format(cislo=cislo, tabulka=tabulka), {"druh": druh})
            for ct, cislo_pn, docx_url, raw, hash_ in cur.fetchall():
                text = vycisti_kody_poli(raw)
                cur.execute(PREVZETI, {
                    "druh": druh, "ct": ct, "cislo_pn": cislo_pn, "docx_url": docx_url, "zdroj": zdroj,
                    "text": text, "hash": hash_, "znaku": len(text),
                })
                n += cur.rowcount
    conn.commit()
    return n


def _chyba_dokumentu(e: Exception) -> str:
    """Krátký popis neočekávané chyby pro sloupec chyba (bez stack trace)."""
    return f"chyba zpracování: {type(e).__name__}: {str(e)[:150]}"


# Po tolika síťových chybách jednotlivých dokumentů za sebou (timeout, HTTP 5xx) stahování v běhu skončí,
# ať se psp.cz v potížích nezahltí; dokumenty, které chybu dostaly, se zkusí znovu podle svého rozvrhu.
MAX_SITOVYCH_ZA_SEBOU = 3
# Přechodná síťová chyba (timeout, spojení, HTTP 5xx) se opakuje po 24 h; HTTP 4xx (např. 403) po 7 dnech jako
# ostatní chyby. Zápis chyby viz _sitova_chyba („síť: <třída>: <text>“). %% kvůli parametrům psycopg.
SITOVA_PRECHODNA = "(d.chyba LIKE 'síť:%%' AND d.chyba NOT LIKE 'síť: HTTPError: HTTP Error 4%%')"


def _chyba_dokumentu_site(e: OSError) -> bool:
    """Síťová chyba patří jednomu dokumentu (odpověď s HTTP stavem jiným než 404, timeout jednoho požadavku),
    ne výpadku spojení s psp.cz (DNS, odmítnuté spojení), který by stejně postihl všechny další dokumenty."""
    if isinstance(e, urllib.error.HTTPError) or isinstance(e, TimeoutError):
        return True
    return isinstance(e, urllib.error.URLError) and isinstance(e.reason, TimeoutError)


def _sitova_chyba(conn: psycopg.Connection, e: OSError, st: dict, sit: list[int], **dokument) -> None:
    """Zapíše síťovou chybu k dokumentu (posune ho do rozvrhu opakování, aby neblokoval frontu).
    Výpadek spojení nebo několik chyb za sebou výjimku pošle dál: stahování v tomto běhu končí."""
    conn.rollback()
    _uloz(conn, **dokument, chyba=f"síť: {type(e).__name__}: {str(e)[:150]}")
    sit[0] += 1
    if not _chyba_dokumentu_site(e) or sit[0] >= MAX_SITOVYCH_ZA_SEBOU:
        raise e
    log.warning("text %s: síťová chyba %s, pokračuji dalším dokumentem", dokument, e)
    st["chyby"] += 1


def _stahni_chybejici(conn: psycopg.Connection, fetcher: Fetcher, obdobi: int, limit: int, st: dict,
                      sit: list[int]) -> int:
    """Tisky a PN bez dokumentu `puvodni`, nejdřív tisky (základ pro PN), novější dřív.
    Chybové řádky bez textu (kromě „jen PDF“) se zkusí znovu po 7 dnech, až po nových; síťové chyby
    (timeout, spojení, HTTP 5xx) už po 24 hodinách. Vrací počet pokusů."""
    if limit <= 0:
        return 0
    znovu = ("d.text IS NULL AND d.chyba IS DISTINCT FROM %s AND d.zkontrolovano_at < now() - CASE WHEN "
             f"{SITOVA_PRECHODNA} THEN interval '24 hours' ELSE interval '7 days' END")
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT 'tisk', t.ct, 0 FROM (SELECT DISTINCT ct FROM tisky) t
            LEFT JOIN texty.dokument d
              ON d.druh = 'tisk' AND d.ct = t.ct AND d.cislo_pn = 0 AND d.verze = 'puvodni'
            WHERE d.id IS NULL OR ({znovu})
            ORDER BY d.id IS NOT NULL, t.ct DESC LIMIT %s""", (JEN_PDF, limit))
        fronta = cur.fetchall()
        cur.execute(f"""
            SELECT 'pn', p.ct, p.cislo FROM (SELECT DISTINCT ct, cislo FROM pn) p
            LEFT JOIN texty.dokument d
              ON d.druh = 'pn' AND d.ct = p.ct AND d.cislo_pn = p.cislo AND d.verze = 'puvodni'
            WHERE d.id IS NULL OR ({znovu})
            ORDER BY d.id IS NOT NULL, p.cislo DESC LIMIT %s""", (JEN_PDF, limit))
        fronta += cur.fetchall()
    conn.commit()
    fronta = fronta[:limit]
    for druh, ct, cislo in fronta:
        try:
            _stahni_puvodni(conn, fetcher, obdobi, druh, ct, cislo, st)
            sit[0] = 0
        except OSError as e:
            _sitova_chyba(conn, e, st, sit, druh=druh, ct=ct, cislo_pn=cislo, verze="puvodni")
        except Exception as e:
            conn.rollback()
            log.exception("text %s %s/%s se nepodařilo zpracovat", druh, ct, cislo)
            _uloz(conn, druh=druh, ct=ct, cislo_pn=cislo, verze="puvodni", chyba=_chyba_dokumentu(e))
            st["chyby"] += 1
    return len(fronta)


def _stahni_puvodni(conn: psycopg.Connection, fetcher: Fetcher, obdobi: int, druh: str, ct: int, cislo: int,
                    st: dict) -> None:
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
        return
    odkaz = najdi(stranka)
    if not odkaz:
        _uloz(conn, druh=druh, ct=ct, cislo_pn=cislo, verze="puvodni", chyba=JEN_PDF)
        st["jen_pdf"] += 1
        return
    text, chyba = _docx(fetcher, odkaz)
    _uloz(conn, druh=druh, ct=ct, cislo_pn=cislo, verze="puvodni", docx_url=odkaz, text=text, chyba=chyba)
    if text is None:
        st["chyby"] += 1
    else:
        st["stazeno"] += 1


def _schvalene(conn: psycopg.Connection, fetcher: Fetcher, obdobi: int, limit: int, st: dict,
               sit: list[int]) -> int:
    """Schválené znění tisků po 3. čtení; bez odkazu se uloží jen čas kontroly (další za 24 h).
    Nečitelný DOCX (odkaz „Originál dokumentu“ nemusí být DOCX) se zkusí znovu až po 7 dnech."""
    if limit <= 0:
        return 0
    with conn.cursor() as cur:
        cur.execute("""
            SELECT t.ct FROM (SELECT ct, bool_or(faze = 'Vyhlášeno') AS vyhlaseno,
                                     bool_or(faze = ANY(%s)) AS bez_schvaleni
                              FROM tisky GROUP BY ct) t
            LEFT JOIN texty.dokument d
              ON d.druh = 'tisk' AND d.ct = t.ct AND d.cislo_pn = 0 AND d.verze = 'schvalene'
            WHERE (coalesce(t.vyhlaseno, false)
                   OR EXISTS (SELECT 1 FROM kroky k WHERE k.ct = t.ct AND k.typ_stavu = ANY(%s)))
              AND NOT coalesce(t.bez_schvaleni, false)
              AND (d.id IS NULL OR (d.text IS NULL
                   AND (d.zkontrolovano_at IS NULL
                        OR d.zkontrolovano_at < now() - CASE WHEN d.chyba = %s THEN interval '7 days'
                                                             ELSE interval '24 hours' END)))
            ORDER BY d.zkontrolovano_at NULLS FIRST, t.ct DESC
            LIMIT %s""", (list(FAZE_BEZ_SCHVALENI), list(STAVY_PO_3_CTENI), VADNY_DOCX, limit))
        fronta = [r[0] for r in cur.fetchall()]
    conn.commit()
    for ct in fronta:
        try:
            _stahni_schvalene(conn, fetcher, obdobi, ct, st)
            sit[0] = 0
        except OSError as e:
            _sitova_chyba(conn, e, st, sit, druh="tisk", ct=ct, cislo_pn=0, verze="schvalene")
        except Exception as e:
            conn.rollback()
            log.exception("schválené znění tisku %s se nepodařilo zpracovat", ct)
            _uloz(conn, druh="tisk", ct=ct, cislo_pn=0, verze="schvalene", chyba=_chyba_dokumentu(e))
            st["chyby"] += 1
    return len(fronta)


def _stahni_schvalene(conn: psycopg.Connection, fetcher: Fetcher, obdobi: int, ct: int, st: dict) -> None:
    historie = _stranka(fetcher, f"{BASE}/sqw/historie.sqw?o={obdobi}&t={ct}")
    odkaz_pz = odkaz_schvalene_stranky(historie) if historie else None
    stranka_pz = _stranka(fetcher, odkaz_pz) if odkaz_pz else None
    odkaz = odkaz_originalu(stranka_pz) if stranka_pz else None
    if not odkaz:
        _uloz(conn, druh="tisk", ct=ct, cislo_pn=0, verze="schvalene", chyba=SCHVALENE_NENI)
        return
    text, chyba = _docx(fetcher, odkaz)
    _uloz(conn, druh="tisk", ct=ct, cislo_pn=0, verze="schvalene", docx_url=odkaz, text=text, chyba=chyba)
    if text is None:
        st["chyby"] += 1
    else:
        st["schvalene"] += 1


def _rozdel_dokumenty(conn: psycopg.Connection, st: dict) -> int:
    """Rozdělí dokumenty na celky. Selhání jednoho dokumentu se zapíše do chyba a dokument
    dostane rozdeleno_at, aby neblokoval frontu; znovu se dělí až po změně textu (hashe).
    Celky předchozího textu se při selhání smažou: patří ke starému znění, ne k uloženému textu a docx_url."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, text FROM texty.dokument WHERE text IS NOT NULL AND rozdeleno_at IS NULL ORDER BY id")
        dokumenty = cur.fetchall()
    conn.commit()
    hotovo = 0
    for dokument_id, text in dokumenty:
        try:
            cely = rozdel(text)
            with conn.cursor() as cur:
                cur.execute("DELETE FROM texty.cel WHERE dokument_id = %s", (dokument_id,))
                cur.executemany(
                    """INSERT INTO texty.cel (dokument_id, poradi, typ, oznaceni, cesta, meni_zakon, text, znaku,
                                              od_znaku)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [(dokument_id, c.poradi, c.typ, c.oznaceni, c.cesta, c.meni_zakon, c.text, len(c.text),
                      c.od_znaku) for c in cely],
                )
                cur.execute("UPDATE texty.dokument SET rozdeleno_at = now(), chyba = NULL WHERE id = %s",
                            (dokument_id,))
            conn.commit()
            hotovo += 1
        except Exception as e:
            conn.rollback()
            log.exception("dokument %s se nepodařilo rozdělit", dokument_id)
            with conn.cursor() as cur:
                cur.execute("DELETE FROM texty.cel WHERE dokument_id = %s", (dokument_id,))
                cur.execute("UPDATE texty.dokument SET rozdeleno_at = now(), chyba = %s WHERE id = %s",
                            ("dělení selhalo: " + _chyba_dokumentu(e), dokument_id))
            conn.commit()
            st["chyby"] += 1
    return hotovo


def sync_texty(conn: psycopg.Connection, fetcher: Fetcher, obdobi: int, limit: int = 40) -> dict:
    """Převezme, doplní a rozdělí texty. Vrací počty {prevzato, stazeno, schvalene, rozdeleno, chyby, jen_pdf}.

    Síťová chyba jednoho dokumentu (HTTP 403/500/503, timeout) se zapíše do jeho sloupce chyba a běh pokračuje
    dalším; výpadek spojení s psp.cz nebo MAX_SITOVYCH_ZA_SEBOU chyb za sebou stahování v tomto běhu ukončí
    (dokument, u kterého k tomu došlo, chybu dostane také). Převzaté texty se přesto rozdělí.
    Jiná chyba u jednoho dokumentu se zapíše do jeho sloupce chyba a běh pokračuje dalším.
    """
    st = {"prevzato": 0, "stazeno": 0, "schvalene": 0, "rozdeleno": 0, "chyby": 0, "jen_pdf": 0}
    st["prevzato"] = _prevezmi(conn)
    sit = [0]   # síťové chyby za sebou (napříč oběma frontami)
    try:
        zbyva = limit - _stahni_chybejici(conn, fetcher, obdobi, limit, st, sit)
        _schvalene(conn, fetcher, obdobi, zbyva, st, sit)
    except OSError as e:
        conn.rollback()
        log.warning("stahování textů z psp.cz přerušeno: %s", e)
        st["chyby"] += 1
    st["rozdeleno"] = _rozdel_dokumenty(conn, st)
    return st
