"""Úložiště chatbota ve schématu `chat` (spec 14.1, 11.1, 9.3, 15): relace, konverzace, zprávy, zdroje, volání modelu
a nástrojů, útrata, hodnocení, nahlášení a úklid.

Spojení má být v autocommit režimu (`db.connect(autocommit=True)`): každá funkce je pak jedna atomická operace
a dvě souběžné zprávy se nepotkají v otevřené transakci. Víceřádkové zápisy jsou v `conn.transaction()`.

Čas pro limity: den a měsíc jsou v Europe/Prague. IP se tu nikdy nevyskytuje čitelně, jen otisk (HMAC),
který spočítá volající.

Tah relace (spec 11.1, „jedna relace smí mít jen jeden běžící tah“): `zahaj_tah` podmíněným UPDATE zvedne příznak
`tah_od` na řádku relace, uloží do něj náhodný token vlastníka a teprve potom přečte útratu. Druhá zpráva téže relace
dostane None, dokud první nezapíše cenu (`zapis_volani_llm`) a nezavolá `skonci_tah` se svým tokenem. Příznak po pádu
vyprší sám (`drz_s`, ve službě `Nastaveni.drzeni_tahu_s`); převzatý tah pak starý vlastník se svým tokenem už neukončí.
Pořadí ve službě: `zahaj_tah` → kontrola stropů → `zaloz_odpoved` → volání modelu a zápis cen → `dokonci` →
`skonci_tah` (vždy, i při chybě). `drz_s` musí být delší než nejdelší tah (timeout SDK + nástroje).
"""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .historie import Tah
from .registr import Zdroj

TZ = "Europe/Prague"
KONCOVE_STAVY = ("ok", "nemam", "odmitnuto", "chyba", "strop", "preruseno")
# Do historie pro model patří jen dokončené odpovědi, na které model navázal věcně.
STAVY_DO_HISTORIE = ("ok", "nemam")
# bez 0/O, 1/l/I: kód se čte a opisuje
ABECEDA_KODU = "abcdefghjkmnpqrstuvwxyz23456789"
DELKA_KODU = 8
DRZENI_TAHU_S = 300   # výchozí; služba předá Nastaveni.drzeni_tahu_s (CHAT_DRZENI_TAHU_S)
UKLID_KONVERZACI = timedelta(days=30)
UKLID_IP = timedelta(hours=48)


@dataclass(frozen=True)
class Utrata:
    den: float
    hodina: float
    mesic: float
    relace: float
    ip: float
    zprav_min: int
    zprav_den: int


def _vyzaduj_cas(ted: datetime) -> datetime:
    if ted.tzinfo is None:
        raise ValueError("ted musí mít časové pásmo")
    return ted


def _jeden(conn: psycopg.Connection, q: str, params: tuple = ()) -> tuple | None:
    return conn.execute(q, params).fetchone()


# --- sůl dne pro otisk IP ---

DELKA_SOLI = 32


def sul_dne(conn: psycopg.Connection, den: date) -> bytes:
    """Náhodná sůl otisku IP pro pražský `den`; vznikne při prvním použití dne (souběžně vyhraje jedna)."""
    conn.execute("INSERT INTO chat.sul_dne (den, sul) VALUES (%s, %s) ON CONFLICT (den) DO NOTHING",
                 (den, secrets.token_bytes(DELKA_SOLI)))
    return bytes(_jeden(conn, "SELECT sul FROM chat.sul_dne WHERE den = %s", (den,))[0])


# --- relace ---

def zaloz_relaci(conn: psycopg.Connection, id: str | None = None) -> str:
    """Založí relaci s novým náhodným id, nebo s `id`, které služba vydala předem (podepsané, ještě bez řádku);
    existující řádek se nemění (souběžně vyhraje jeden)."""
    rid = id or secrets.token_hex(16)
    conn.execute("INSERT INTO chat.relace (id) VALUES (%s) ON CONFLICT (id) DO NOTHING", (rid,))
    return rid


def relace(conn: psycopg.Connection, id: str) -> dict | None:
    """Klíče: id, vytvoreno, posledni_aktivita, overeno_do, zprav_od_overeni, otisk (otisk IP při ověření)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""SELECT id, vytvoreno, posledni_aktivita, overeno_do, zprav_od_overeni, otisk_ip AS otisk
                       FROM chat.relace WHERE id = %s""", (id,))
        return cur.fetchone()


def zapis_overeni(conn: psycopg.Connection, relace_id: str, otisk_ip: str, do: datetime) -> None:
    """Turnstile: ověřeno do `do`; počet zpráv od ověření se vynuluje."""
    _vyzaduj_cas(do)
    conn.execute("""UPDATE chat.relace SET overeno_do = %s, otisk_ip = %s, zprav_od_overeni = 0, overeni_cas = now()
                    WHERE id = %s""", (do, otisk_ip, relace_id))


def pricti_zpravu_od_overeni(conn: psycopg.Connection, relace_id: str) -> int:
    """Atomicky zvýší počet zpráv od ověření a vrátí nový stav (0 u neznámé relace)."""
    r = _jeden(conn, "UPDATE chat.relace SET zprav_od_overeni = zprav_od_overeni + 1 WHERE id = %s "
                     "RETURNING zprav_od_overeni", (relace_id,))
    return r[0] if r else 0


# --- tah relace a útrata ---

class RelaceNeexistuje(LookupError):
    """Relace s tímto id není (např. smazaná úklidem)."""


def zahaj_tah(conn: psycopg.Connection, relace_id: str, otisk_ip: str, ted: datetime,
              drz_s: int = DRZENI_TAHU_S) -> tuple[str, Utrata] | None:
    """Zahájí tah relace: atomicky zvedne příznak a vrátí (token vlastníka, útrata přečtená až po zvednutí).

    None = relace už tah má. Neexistující relace vyhodí `RelaceNeexistuje`. Příznak starší než `drz_s` (pád
    procesu, příliš dlouhý tah) se přebírá. Po každém tahu musí následovat `skonci_tah(conn, relace_id, token)`.
    Vyžaduje spojení v autocommit režimu (jinak by příznak zůstal v neodevzdané transakci)."""
    _vyzaduj_cas(ted)
    if not conn.autocommit:
        raise RuntimeError("zahaj_tah vyžaduje spojení s autocommit=True")
    token = secrets.token_hex(16)
    r = _jeden(conn, """UPDATE chat.relace SET tah_od = %(ted)s, tah_token = %(token)s
                        WHERE id = %(id)s AND (tah_od IS NULL OR tah_od < %(ted)s - make_interval(secs => %(drz)s))
                        RETURNING 1""", {"ted": ted, "id": relace_id, "drz": drz_s, "token": token})
    if r is None:
        if _jeden(conn, "SELECT 1 FROM chat.relace WHERE id = %s", (relace_id,)) is None:
            raise RelaceNeexistuje(relace_id)
        return None
    return token, utrata(conn, relace_id, otisk_ip, ted)


def skonci_tah(conn: psycopg.Connection, relace_id: str, token: str) -> bool:
    """Uvolní příznak, jen když ho drží tento token. False = tah mezitím převzal jiný (starý vlastník nic nemaže)."""
    return conn.execute("UPDATE chat.relace SET tah_od = NULL, tah_token = NULL WHERE id = %s AND tah_token = %s",
                        (relace_id, token)).rowcount > 0


def utrata(conn: psycopg.Connection, relace_id: str, otisk_ip: str, ted: datetime) -> Utrata:
    """Součty z `volani_llm` (USD) a počet zpráv relace. Den a měsíc podle pražského kalendáře, hodina = posledních
    60 minut před `ted`, relace a IP jsou součty za pražský den. Počet zpráv nezahrnuje zprávu, kterou se teprve
    zakládá odpověď."""
    _vyzaduj_cas(ted)
    r = _jeden(conn, """
        WITH hr AS (
          SELECT (date_trunc('day', %(ted)s AT TIME ZONE %(tz)s)) AS d0,
                 (date_trunc('month', %(ted)s AT TIME ZONE %(tz)s)) AS m0
        ), b AS (
          SELECT d0 AT TIME ZONE %(tz)s AS den_od,
                 (d0 + interval '1 day') AT TIME ZONE %(tz)s AS den_do,
                 m0 AT TIME ZONE %(tz)s AS mesic_od,
                 (m0 + interval '1 month') AT TIME ZONE %(tz)s AS mesic_do
          FROM hr
        )
        SELECT
          coalesce((SELECT sum(v.cena_usd) FROM chat.volani_llm v, b
                    WHERE v.cas >= b.den_od AND v.cas < b.den_do), 0)::float8,
          coalesce((SELECT sum(v.cena_usd) FROM chat.volani_llm v
                    WHERE v.cas > %(ted)s - interval '60 minutes'), 0)::float8,
          coalesce((SELECT sum(v.cena_usd) FROM chat.volani_llm v, b
                    WHERE v.cas >= b.mesic_od AND v.cas < b.mesic_do), 0)::float8,
          coalesce((SELECT sum(v.cena_usd) FROM chat.volani_llm v, b
                    WHERE v.relace_id = %(rel)s AND v.cas >= b.den_od AND v.cas < b.den_do), 0)::float8,
          coalesce((SELECT sum(v.cena_usd) FROM chat.volani_llm v, b
                    WHERE v.otisk_ip = %(ip)s AND v.cas >= b.den_od AND v.cas < b.den_do), 0)::float8,
          (SELECT count(*) FROM chat.zprava_log z
           WHERE z.relace_id = %(rel)s AND z.cas > %(ted)s - interval '1 minute'),
          (SELECT count(*) FROM chat.zprava_log z, b
           WHERE z.relace_id = %(rel)s AND z.cas >= b.den_od AND z.cas < b.den_do)
        """, {"ted": ted, "tz": TZ, "rel": relace_id, "ip": otisk_ip})
    return Utrata(*r)


# --- konverzace a zprávy ---

def zaloz_konverzaci(conn: psycopg.Connection, relace_id: str, eval_: bool = False) -> str:
    kid = secrets.token_hex(16)
    conn.execute("INSERT INTO chat.konverzace (id, relace_id, eval) VALUES (%s, %s, %s)", (kid, relace_id, eval_))
    return kid


def konverzace(conn: psycopg.Connection, id: str, relace_id: str) -> dict | None:
    """Konverzace jen vlastní relace; cizí nebo neexistující = None."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""SELECT id, relace_id, vytvoreno, posledni_aktivita, eval FROM chat.konverzace
                       WHERE id = %s AND relace_id = %s""", (id, relace_id))
        return cur.fetchone()


def tahy(conn: psycopg.Connection, konverzace_id: str) -> list[Tah]:
    """Dokončené tahy (otázka + odpověď bota) v pořadí; rozpracované a neúspěšné se do historie nedávají."""
    radky = conn.execute("""SELECT u.text, u.kontext, b.text, b.citovane
                            FROM chat.zprava b JOIN chat.zprava u ON u.id = b.otazka_id
                            WHERE b.konverzace_id = %s AND b.role = 'bot' AND b.stav = ANY(%s)
                            ORDER BY b.id""", (konverzace_id, list(STAVY_DO_HISTORIE))).fetchall()
    return [Tah(text_uzivatele=t, znacka=(k or {}).get("znacka", ""), text_bota=o, zdroje=list(c)) for t, k, o, c in radky]


def _nahodny_kod() -> str:
    return "".join(secrets.choice(ABECEDA_KODU) for _ in range(DELKA_KODU))


def _rezervuj_kod(conn: psycopg.Connection) -> str:
    """Kód odpovědi se rezervuje v otisk_odpovedi, která přežije smazání konverzace: kód se tak nezopakuje."""
    for _ in range(50):
        kod = _nahodny_kod()
        if conn.execute("INSERT INTO chat.otisk_odpovedi (kod) VALUES (%s) ON CONFLICT DO NOTHING", (kod,)).rowcount:
            return kod
    raise RuntimeError("nepodařilo se vygenerovat unikátní kód odpovědi")


def zaloz_odpoved(conn: psycopg.Connection, konverzace_id: str, text_uzivatele: str, kontext: dict) -> tuple[int, str]:
    """Uloží zprávu uživatele a prázdnou odpověď bota ve stavu `probiha`. Vrací (id zprávy bota, kód odpovědi).
    Z `kontext` se čte klíč `znacka` pro historii; zapíše se celý. Započítá zprávu do limitů zpráv relace."""
    with conn.transaction():
        r = _jeden(conn, "UPDATE chat.konverzace SET posledni_aktivita = now() WHERE id = %s RETURNING relace_id",
                   (konverzace_id,))
        if r is None:
            raise LookupError(f"konverzace {konverzace_id} neexistuje")
        relace_id = r[0]
        conn.execute("UPDATE chat.relace SET posledni_aktivita = now() WHERE id = %s", (relace_id,))
        conn.execute("INSERT INTO chat.zprava_log (relace_id) VALUES (%s)", (relace_id,))
        otazka = _jeden(conn, """INSERT INTO chat.zprava (konverzace_id, role, text, kontext)
                                 VALUES (%s, 'uzivatel', %s, %s) RETURNING id""",
                        (konverzace_id, text_uzivatele, Jsonb(kontext)))[0]
        kod = _rezervuj_kod(conn)
        zid = _jeden(conn, """INSERT INTO chat.zprava (konverzace_id, role, otazka_id, kod, stav)
                              VALUES (%s, 'bot', %s, %s, 'probiha') RETURNING id""", (konverzace_id, otazka, kod))[0]
    return zid, kod


def prubezne(conn: psycopg.Connection, zprava_id: int, text: str) -> None:
    """Průběžný text rozpracované odpovědi (jen dokud je ve stavu `probiha`)."""
    conn.execute("UPDATE chat.zprava SET text = %s WHERE id = %s AND stav = 'probiha'", (text, zprava_id))


def dokonci(conn: psycopg.Connection, zprava_id: int, text: str, stav: str, model: str | None,
            citovane: list[int], overeni: dict) -> bool:
    """Dokončí rozpracovanou odpověď. False = už dokončená (druhé volání hotovou odpověď nepřepíše)."""
    if stav not in KONCOVE_STAVY:
        raise ValueError(f"stav {stav!r}: čekám jeden z {KONCOVE_STAVY}")
    with conn.transaction():
        r = _jeden(conn, """UPDATE chat.zprava SET text = %s, stav = %s, model = %s, citovane = %s, overeni = %s
                            WHERE id = %s AND role = 'bot' AND stav = 'probiha' RETURNING kod""",
                   (text, stav, model, list(citovane), Jsonb(overeni), zprava_id))
        if r is None:
            return False
        conn.execute("UPDATE chat.otisk_odpovedi SET hash = %s WHERE kod = %s",
                     (hashlib.sha256(text.encode("utf-8")).hexdigest(), r[0]))
    return True


def zapis_volani_llm(conn: psycopg.Connection, *, zprava_id: int | None, relace_id: str, otisk_ip: str | None,
                     model: str, vstup: int, vystup: int, cache_cteni: int, cache_zapis: int, cena_usd: float,
                     ms: int, stop_reason: str | None, cas: datetime | None = None) -> None:
    """Zapíše cenu volání. Musí proběhnout před `skonci_tah`, jinak druhý tah relace cenu prvního neuvidí."""
    conn.execute("""INSERT INTO chat.volani_llm (zprava_id, relace_id, otisk_ip, model, vstup, vystup, cache_cteni,
                        cache_zapis, cena_usd, ms, stop_reason, cas)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, coalesce(%s, now()))""",
                 (zprava_id, relace_id, otisk_ip, model, vstup, vystup, cache_cteni, cache_zapis, cena_usd, ms,
                  stop_reason, cas))


def zapis_volani_nastroje(conn: psycopg.Connection, *, zprava_id: int, nastroj: str, argumenty: dict, ms: int,
                          znaku: int, chyba: str | None) -> None:
    conn.execute("""INSERT INTO chat.volani_nastroje (zprava_id, nastroj, argumenty, ms, znaku, chyba)
                    VALUES (%s, %s, %s, %s, %s, %s)""",
                 (zprava_id, nastroj, Jsonb(argumenty), ms, znaku, chyba))


def uloz_zdroje(conn: psycopg.Connection, konverzace_id: str, zdroje: list[Zdroj]) -> None:
    """Upsert registru podle čísla; příznak `citovan` jednou nastavený se nevrací."""
    with conn.transaction():
        for z in zdroje:
            conn.execute("""INSERT INTO chat.zdroj (konverzace_id, n, klic, typ, titulek, meta, citovan)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (konverzace_id, n) DO UPDATE
                              SET titulek = excluded.titulek, meta = excluded.meta,
                                  citovan = chat.zdroj.citovan OR excluded.citovan""",
                         (konverzace_id, z.n, z.klic, z.typ, z.titulek, Jsonb(z.meta), z.citovan))


def zdroje(conn: psycopg.Connection, konverzace_id: str) -> list[Zdroj]:
    radky = conn.execute("SELECT n, klic, typ, titulek, meta, citovan FROM chat.zdroj WHERE konverzace_id = %s ORDER BY n",
                         (konverzace_id,)).fetchall()
    return [Zdroj(n, klic, typ, titulek, meta or {}, citovan) for n, klic, typ, titulek, meta, citovan in radky]


STAVY_PRO_OBNOVU = ("ok", "nemam", "preruseno")


def zpravy(conn: psycopg.Connection, konverzace_id: str) -> list[dict]:
    """Otázky a odpovědi konverzace pro obnovu panelu (klíče otazka, text, kod, stav, citovane), nejstarší první.
    Rozpracované, odmítnuté a neúspěšné odpovědi se neobnovují."""
    radky = conn.execute("""SELECT u.text, b.text, b.kod, b.stav, b.citovane
                            FROM chat.zprava b JOIN chat.zprava u ON u.id = b.otazka_id
                            WHERE b.konverzace_id = %s AND b.role = 'bot' AND b.stav = ANY(%s) ORDER BY b.id""",
                         (konverzace_id, list(STAVY_PRO_OBNOVU))).fetchall()
    return [{"otazka": o, "text": t, "kod": k, "stav": s, "citovane": list(c)} for o, t, k, s, c in radky]


def smaz_konverzaci(conn: psycopg.Connection, id: str, relace_id: str) -> bool:
    """Smaže konverzaci vlastní relace i se zprávami, zdroji a voláními nástrojů. Zůstává otisk odpovědi, ceny
    volání modelu a počítadlo zpráv (limity se smazáním nevynulují); nahlášené odpovědi mají vlastní kopii textu."""
    return conn.execute("DELETE FROM chat.konverzace WHERE id = %s AND relace_id = %s", (id, relace_id)).rowcount > 0


# --- kód odpovědi, hodnocení, nahlášení ---

def existuje_kod(conn: psycopg.Connection, kod: str) -> date | None:
    """Datum odpovědi s tímto kódem, jinak None (rozpracovaná odpověď bez textu se nepočítá)."""
    r = _jeden(conn, "SELECT datum FROM chat.otisk_odpovedi WHERE kod = %s AND hash IS NOT NULL", (kod,))
    return r[0] if r else None


def hodnoceni(conn: psycopg.Connection, kod: str, palec: int) -> bool:
    """Palec nahoru (1) nebo dolů (-1); poslední hodnocení kódu platí. False = neznámý kód."""
    if palec not in (-1, 1):
        raise ValueError("palec musí být -1 nebo 1")
    return conn.execute("""INSERT INTO chat.hodnoceni (kod, palec) SELECT kod, %s FROM chat.otisk_odpovedi WHERE kod = %s
                           ON CONFLICT (kod) DO UPDATE SET palec = excluded.palec, cas = now()""",
                        (palec, kod)).rowcount > 0


def nahlas(conn: psycopg.Connection, kod: str, duvod: str) -> bool:
    """Uloží nahlášení odpovědi s kopií otázky a odpovědi (konverzace se po 30 dnech smaže, nahlášení se drží
    do vyřízení). False = neznámý kód."""
    if existuje_kod(conn, kod) is None:
        return False
    r = _jeden(conn, """SELECT u.text, b.text FROM chat.zprava b LEFT JOIN chat.zprava u ON u.id = b.otazka_id
                        WHERE b.kod = %s""", (kod,))
    otazka, odpoved = r if r else (None, None)
    conn.execute("INSERT INTO chat.nahlaseni (kod, duvod, text_otazky, text_odpovedi) VALUES (%s, %s, %s, %s)",
                 (kod, duvod, otazka, odpoved))
    return True


# --- úklid ---

def uklid(conn: psycopg.Connection, ted: datetime) -> dict:
    """Retence (spec 15), spustitelná z cronu: `python -m hlidac_rag.chat.uloziste`.

    Konverzace a relace bez aktivity 30 dní (kaskádou zprávy, zdroje, volání nástrojů), otisky IP starší 48 h
    (volani_llm, relace) a soli dne starší včerejška (chat.sul_dne), otisk odpovědi (a hodnocení) starší 12 měsíců, počítadlo zpráv starší 2 dnů a vyřízená
    nahlášení starší 30 dní. Ceny volání modelu zůstávají (bez IP jsou to jen čísla)."""
    _vyzaduj_cas(ted)
    p = {"ted": ted, "tz": TZ}
    s = {}
    with conn.transaction():
        s["konverzace"] = conn.execute("DELETE FROM chat.konverzace WHERE posledni_aktivita < %s",
                                       (ted - UKLID_KONVERZACI,)).rowcount
        s["relace"] = conn.execute("DELETE FROM chat.relace WHERE posledni_aktivita < %s",
                                   (ted - UKLID_KONVERZACI,)).rowcount
        ip = conn.execute("UPDATE chat.volani_llm SET otisk_ip = NULL WHERE otisk_ip IS NOT NULL AND cas < %s",
                          (ted - UKLID_IP,)).rowcount
        ip += conn.execute("UPDATE chat.relace SET otisk_ip = NULL WHERE otisk_ip IS NOT NULL AND overeni_cas < %s",
                           (ted - UKLID_IP,)).rowcount
        s["otisky_ip"] = ip
        # sůl dne D se používá do půlnoci D, smaže se v D+2 (zůstává dnešní a včerejší)
        s["soli"] = conn.execute(
            "DELETE FROM chat.sul_dne WHERE den < (%(ted)s AT TIME ZONE %(tz)s)::date - 1", p).rowcount
        s["otisky_odpovedi"] = conn.execute(
            "DELETE FROM chat.otisk_odpovedi WHERE datum < ((%(ted)s AT TIME ZONE %(tz)s)::date - interval '12 months')",
            p).rowcount
        s["zpravy_log"] = conn.execute("DELETE FROM chat.zprava_log WHERE cas < %s", (ted - timedelta(days=2),)).rowcount
        s["nahlaseni"] = conn.execute("DELETE FROM chat.nahlaseni WHERE vyrizeno < %s",
                                      (ted - UKLID_KONVERZACI,)).rowcount
    return s


def main() -> int:
    from datetime import timezone

    from .. import db

    conn = db.connect(autocommit=True)
    try:
        print(json.dumps(uklid(conn, datetime.now(timezone.utc))))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
