"""Kontext obrazovky z prohlížeče (spec 5.2, 5.5 a 12): validace, ověření proti DB a značka serveru.

Widget posílá `{typ, ct?, pn?, hlasovani?, z?, pid?, uzke}`. Do modelu jde jen typ stránky ze seznamu a čísla
ověřená proti databázi, nikdy volný text z URL ani z DOM. `z_json` vezme z JSON jen povolená pole ve správném tvaru
(nikdy nevyhodí), `over` ověří čísla proti DB: PN a hlasování, které k tisku nepatří nebo neexistují, se zahodí
a tah pokračuje bez nich; neexistující tisk = stránka `jina`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import psycopg

from ..tools import _one

log = logging.getLogger("hlidac_rag.chat.kontext")

POVOLENE_TYPY: frozenset[str] = frozenset({
    "tisk", "zakon", "prehled", "seznam_tisku", "zakony", "hledani", "upozorneni", "pripravovane", "staticka", "jina"})
# celé číslo do rozsahu INTEGER v PostgreSQL (větší by dotaz shodil chybou)
MAX_CISLO = 2**31 - 1
_CISLO = re.compile(r"[0-9]{1,10}")
_ZAKON = re.compile(r"([0-9]{1,4})/([0-9]{4})")
_PID = re.compile(r"[A-Za-z0-9_-]{1,40}")


@dataclass(frozen=True)
class Kontext:
    """Ověřitelný kontext obrazovky: typ stránky a čísla. Hashovatelný (klíč cache karet)."""
    typ: str
    ct: int | None = None
    pn: int | None = None
    hlasovani: int | None = None
    z: str | None = None
    pid: str | None = None
    uzke: bool = False


def _cislo(v) -> int | None:
    """Kladné celé číslo z int nebo z řetězce číslic; cokoli jiného (bool, float, text, záporné) None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, str) and _CISLO.fullmatch(v.strip()):
        v = int(v.strip())
    return v if isinstance(v, int) and 0 < v <= MAX_CISLO else None


def _zakon(v) -> str | None:
    m = _ZAKON.fullmatch(v.strip()) if isinstance(v, str) else None
    return f"{int(m.group(1))}/{m.group(2)}" if m and int(m.group(1)) > 0 else None


def _pid(v) -> str | None:
    return v if isinstance(v, str) and _PID.fullmatch(v) else None


def z_json(d: object) -> Kontext:
    """Kontext z JSON od widgetu; nikdy nevyhodí. Neplatné pole = None, neznámý typ = `jina`, ostatní klíče se zahodí."""
    if not isinstance(d, dict):
        return Kontext("jina")
    typ = d.get("typ")
    typ = typ if isinstance(typ, str) and typ in POVOLENE_TYPY else "jina"
    uzke = d.get("uzke")
    return Kontext(typ=typ, ct=_cislo(d.get("ct")), pn=_cislo(d.get("pn")), hlasovani=_cislo(d.get("hlasovani")),
                   z=_zakon(d.get("z")), pid=_pid(d.get("pid")),
                   uzke=uzke is True or (isinstance(uzke, int) and not isinstance(uzke, bool) and uzke == 1))


def _rollback(conn) -> None:
    try:
        conn.rollback()
    except (psycopg.Error, AttributeError):
        pass


def _existuje_tabulka(conn, jmeno: str) -> bool:
    return bool(_one(conn, "SELECT to_regclass(%s) IS NOT NULL AS ok", (jmeno,))["ok"])


def _over_tisk(conn, k: Kontext) -> Kontext:
    # stránka /tisk/N existuje jen pro tisk, který web sleduje (tisk.php čte tabulku tisky)
    if not k.ct or not _one(conn, "SELECT 1 AS ok FROM tisky WHERE ct = %s LIMIT 1", (k.ct,)):
        return Kontext("jina", uzke=k.uzke)
    pn = k.pn
    if pn is not None:
        r = _one(conn, "SELECT ct FROM pn WHERE cislo = %s LIMIT 1", (pn,))
        pn = pn if r and r["ct"] == k.ct else None
    hlasovani = k.hlasovani
    if hlasovani is not None:
        r = (_one(conn, "SELECT ct FROM hlasovani WHERE id_hlasovani = %s", (hlasovani,))
             if _existuje_tabulka(conn, "hlasovani") else None)
        hlasovani = hlasovani if r and r["ct"] == k.ct else None
    return Kontext("tisk", ct=k.ct, pn=pn, hlasovani=hlasovani, uzke=k.uzke)


def over(conn, k: Kontext) -> Kontext:
    """Kontext s čísly ověřenými proti DB; nikdy nevyhodí.

    Každý typ si nechá jen svá pole (ct, PN a hlasování jen tisk, z jen zákon, pid jen připravované). PN a hlasování,
    které k tisku nepatří nebo neexistují, se zahodí; neexistující tisk = `jina`; pid, který v tabulce připravovaných
    není, se zahodí. Číslo zákona se jen kontroluje tvarem (stránka zákona existuje i bez tisků). Jakákoli chyba
    (databáze, spojení) = `jina`: neověřené číslo do modelu nejde."""
    try:
        if k.typ == "tisk":
            return _over_tisk(conn, k)
        if k.typ == "zakon":
            return Kontext("zakon", z=k.z, uzke=k.uzke) if k.z else Kontext("zakony", uzke=k.uzke)
        if k.typ == "pripravovane":
            pid = k.pid
            if pid is not None and not (_existuje_tabulka(conn, "pripravovane")
                                        and _one(conn, "SELECT 1 AS ok FROM pripravovane WHERE pid = %s", (pid,))):
                pid = None
            return Kontext("pripravovane", pid=pid, uzke=k.uzke)
        return Kontext(k.typ if k.typ in POVOLENE_TYPY else "jina", uzke=k.uzke)
    except psycopg.Error:
        _rollback(conn)
        log.warning("ověření kontextu obrazovky selhalo na chybě databáze", exc_info=True)
        return Kontext("jina", uzke=k.uzke)
    except Exception:      # cokoli jiného (rozbité spojení, chyba v kódu): tah pokračuje bez kontextu
        _rollback(conn)
        log.exception("ověření kontextu obrazovky selhalo")
        return Kontext("jina", uzke=k.uzke)


def _stranka(k: Kontext) -> tuple:
    # stránka webu = typ a její identifikátor; PN a hlasování jsou místa na stránce tisku, ne jiná stránka
    return k.typ, k.ct, k.z, k.pid


def popis(k: Kontext) -> str:
    """Typ stránky a čísla jedním řádkem („tisk 221, PN 2329“), bez volného textu."""
    out = k.typ
    for hodnota in (k.ct, k.z, k.pid):
        if hodnota:
            out += f" {hodnota}"
    if k.pn:
        out += f", PN {k.pn}"
    if k.hlasovani:
        out += f", hlasování {k.hlasovani}"
    return out


def znacka(k: Kontext, predchozi: Kontext | None) -> str:
    """Značka serveru do uživatelské zprávy (spec 5.5): `[stránka: tisk 221, PN 2329 · stranka_zmenena: ne]`.
    Stejný tvar jako eval/chat/f0.znacka. Bez předchozího kontextu (první zpráva) se stránka nezměnila."""
    zmena = predchozi is not None and _stranka(predchozi) != _stranka(k)
    return f"[stránka: {popis(k)} · stranka_zmenena: {'ano' if zmena else 'ne'}]"
