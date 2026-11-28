"""Napojení služby chatu na PostgreSQL (spec 3, 21.1): úložiště pod rolí `chat_rw`, čtení dat pod rolí `chat_ro`.

`PgUloziste` nabízí funkce `uloziste` (a `relace.otisk_ip`) se stejnými podpisy bez prvního argumentu `conn`; každé
volání si vezme spojení z poolu v autocommit režimu. Služba je volá v thread poolu (`asyncio.to_thread`).

`PgData` čte data webu pro nástroje, kontext a kartu: každé volání v transakci `READ ONLY` se `statement_timeout`
(spec 8.2: pool 3 spojení). `jmena_osob` hledá v textu jména osob z `steno.osoba` (všechny osoby, které ve Sněmovně
10. období vystoupily: poslanci, členové vlády, senátoři, prezident). Kdo nevystoupil, v tabulce není a citace ho
pozná jen obecným pravidlem pro neznámé jméno (`citace._Kontext._nezname_jmeno`).
"""
from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime

from . import nahled as nahled_mod
from . import relace as relace_mod
from . import uloziste
from .citace import TYPY_S_TEXTEM, _bez_diakritiky, _tvary_prijmeni
from .karta import karta_kontextu
from .kontext import Kontext
from .kontext import over as over_kontext
from .nastroje import Vysledek, vykonej
from .registr import Zdroj
from .smycka import preloz_zdroj

log = logging.getLogger("hlidac_rag.chat.pg")

STATEMENT_TIMEOUT_MS = 5000
OSOBY_OBNOVA_S = 3600     # seznam osob se z DB načte znovu po hodině
MIN_DELKA_PRIJMENI = 3


# ---------------------------------------------------------------- úložiště (chat_rw)

_FUNKCE_ULOZISTE = (
    "zaloz_relaci", "relace", "zapis_overeni", "pricti_zpravu_od_overeni", "zahaj_tah", "skonci_tah", "utrata",
    "zaloz_konverzaci", "konverzace", "tahy", "zaloz_odpoved", "prubezne", "dokonci", "zapis_volani_llm",
    "zapis_volani_nastroje", "uloz_zdroje", "zdroje", "zpravy", "smaz_konverzaci", "existuje_kod", "hodnoceni", "nahlas",
)


class PgUloziste:
    """Úložiště nad poolem spojení (autocommit). Metody = funkce `uloziste` bez `conn`."""

    def __init__(self, pool):
        self._pool = pool

    def _s(self, fn, *a, **kw):
        with self._pool.connection() as conn:
            return fn(conn, *a, **kw)

    def __getattr__(self, jmeno: str):
        if jmeno in _FUNKCE_ULOZISTE:
            fn = getattr(uloziste, jmeno)
            return lambda *a, **kw: self._s(fn, *a, **kw)
        raise AttributeError(jmeno)

    def otisk_ip(self, ip: str, den) -> str:
        return self._s(relace_mod.otisk_ip, ip, den)

    def posledni_kontext(self, konverzace_id: str) -> dict | None:
        """Kontext poslední zprávy uživatele v konverzaci (pro značku „stránka se změnila“)."""
        with self._pool.connection() as conn:
            r = conn.execute("""SELECT kontext FROM chat.zprava WHERE konverzace_id = %s AND role = 'uzivatel'
                                ORDER BY id DESC LIMIT 1""", (konverzace_id,)).fetchone()
        return r[0] if r else None

    def souhrn_dne(self, ted: datetime) -> tuple[int, int]:
        """(počet zpráv, počet relací) za pražský den `ted` (pro hlášení do Telegramu)."""
        with self._pool.connection() as conn:
            r = conn.execute("""SELECT count(*), count(DISTINCT relace_id) FROM chat.zprava_log
                                WHERE (cas AT TIME ZONE %s)::date = (%s AT TIME ZONE %s)::date""",
                             (uloziste.TZ, ted, uloziste.TZ)).fetchone()
        return int(r[0]), int(r[1])


# ---------------------------------------------------------------- jména osob

_SLOVO = re.compile(r"\w+")


class IndexOsob:
    """Tvary příjmení (všechny pády a přivlastňovací tvary, `citace._tvary_prijmeni`) → celá jména osob."""

    def __init__(self, osoby: Iterable[tuple[str | None, str | None]]):
        self._tvary: dict[str, list[tuple[str, str]]] = {}
        for jmeno, prijmeni in osoby:
            if not prijmeni:
                continue
            cele = " ".join(f"{jmeno or ''} {prijmeni}".split())
            krestni = _bez_diakritiky((jmeno or "").split()[0]) if (jmeno or "").split() else ""
            if len(krestni) > 3 and krestni[-1] in "aeiouy":   # kmen kvůli skloňování (Alena → Aleny)
                krestni = krestni[:-1]
            for slovo in _SLOVO.findall(prijmeni):
                if len(slovo) < MIN_DELKA_PRIJMENI:
                    continue
                prvni, pady, priv, _ = _tvary_prijmeni(unicodedata.normalize("NFC", slovo.lower()))
                for tvar in prvni | pady | priv:
                    seznam = self._tvary.setdefault(tvar, [])
                    if (cele, krestni) not in seznam:
                        seznam.append((cele, krestni))

    def najdi(self, text: str) -> list[str]:
        """Jména osob, jejichž příjmení (s velkým písmenem, v libovolném tvaru) v textu stojí, v pořadí výskytu.
        Stojí-li před příjmením křestní jméno jedné z osob téhož příjmení, vrátí jen ji."""
        vysledek: list[str] = []
        predchozi = ""
        for m in _SLOVO.finditer(text):
            w = m.group()
            kandidati = self._tvary.get(unicodedata.normalize("NFC", w.lower())) if w[:1].isupper() else None
            if kandidati:
                pred = _bez_diakritiky(predchozi)
                presne = [c for c, k in kandidati if k and pred.startswith(k) and len(pred) - len(k) <= 4]
                for cele in presne or [c for c, _ in kandidati]:
                    if cele not in vysledek:
                        vysledek.append(cele)
            predchozi = w
        return vysledek


# ---------------------------------------------------------------- čtení dat (chat_ro)

class PgData:
    """Čtení dat webu pod rolí `chat_ro`: každé volání v transakci jen pro čtení s časovým limitem."""

    def __init__(self, pool, statement_timeout_ms: int = STATEMENT_TIMEOUT_MS):
        self._pool = pool
        self._timeout = int(statement_timeout_ms)
        self._osoby: IndexOsob | None = None
        self._osoby_cas = 0.0
        self._zamek = threading.Lock()

    @contextmanager
    def _cteni(self):
        with self._pool.connection() as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute(f"SET LOCAL statement_timeout = {self._timeout}")
            yield conn

    def over_kontext(self, k: Kontext) -> Kontext:
        try:
            with self._cteni() as conn:
                return over_kontext(conn, k)
        except Exception:
            log.warning("ověření kontextu: spojení nedostupné", exc_info=True)
            return Kontext("jina", uzke=k.uzke)

    def karta(self, k: Kontext, max_tokenu: int):
        try:
            with self._cteni() as conn:
                return karta_kontextu(conn, k, max_tokenu)
        except Exception:
            log.warning("karta kontextu: spojení nedostupné", exc_info=True)
            return None, []

    def nastroj(self, nazev: str, argumenty: dict) -> Vysledek:
        try:
            with self._cteni() as conn:
                return vykonej(conn, nazev, argumenty)
        except Exception:
            log.exception("nástroj %s: spojení nedostupné", nazev)
            return Vysledek("Data se právě aktualizují, zkuste to za chvíli.", [], True)

    def nahled(self, klic: str, zv: tuple[int, int] | None):
        """Náhled zdroje z DB (spec 6); chyba databáze se předá službě, ta odpoví 503."""
        with self._cteni() as conn:
            return nahled_mod.nahled(conn, klic, zv)

    def text_zdroje(self, z: Zdroj) -> str | None:
        """Text zdroje s textem (celek, projev) znovu načtený nástrojem; jiný typ nebo chyba = None."""
        if z.klic.split(":", 1)[0] not in TYPY_S_TEXTEM:
            return None
        preklad = preloz_zdroj(z)
        if preklad is None:
            return None
        v = self.nastroj(*preklad)
        if v.chyba:
            return None
        return next((p.text for p in v.polozky if p.klic == z.klic and p.text), None)

    def jmena_osob(self, text: str) -> list[str]:
        index = self._index_osob()
        return index.najdi(text) if index is not None else []

    def _index_osob(self) -> IndexOsob | None:
        with self._zamek:
            if self._osoby is not None and time.monotonic() - self._osoby_cas < OSOBY_OBNOVA_S:
                return self._osoby
            try:
                with self._cteni() as conn:
                    radky = conn.execute("SELECT jmeno, prijmeni FROM steno.osoba").fetchall()
                self._osoby = IndexOsob(radky)
            except Exception:
                log.warning("jména osob nejde načíst, přisouzení citací pozná jen jména mimo DB", exc_info=True)
                if self._osoby is None:
                    return None
            self._osoby_cas = time.monotonic()
            return self._osoby
