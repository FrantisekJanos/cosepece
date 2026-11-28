"""HTTP služba chatbota (spec 3, 4.2, 11, 12, 14): Starlette pod uvicornem s jedním procesem.

Adresy (vše pod `/chat-api`): `zdravi`, `stav`, `beta`, `zprava` (SSE), `tah/{id}/stream` (opětovné připojení),
`tah/{id}/stop`, `w.js` a `s/<hash>/<soubor>` (statické soubory widgetu z `chat/static/`) a obslužné adresy
(spec 6, 9, 15): `nahled` (náhled zdroje z DB), `hodnoceni`, `nahlasit`, `smazat`, `overit` (kód odpovědi), `zasady`
(HTML) a `konverzace/{id}` (zprávy a zdroje pro obnovu panelu).

`POST /chat-api/zprava` ověří všechno PŘED otevřením streamu a prvním placeným voláním, v tomto pořadí:
skrytý režim (`chat_beta`) → `Origin` → velikost těla a zprávy → relace (`chat_sid`) → útrata → stropy v USD →
Turnstile (eval klíč obchází jen tento krok) → běžící tah relace a čítače souběhu (v procesu) → `zahaj_tah` v DB →
konverzace a strop tahů → kontext obrazovky a karta → `zaloz_odpoved` → stream. Odmítnutí = JSON `{kod, znovu_v?}`.

Tah běží jako samostatná úloha (`beh.BehTahu`); odpojení klienta ho nezruší. Cena každého volání modelu se zapíše
hned, `skonci_tah` proběhne vždy ve `finally`. Události tahu drží proces `drzeni_udalosti_s` po konci.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import html
import hmac
import json
import logging
import math
import re
import secrets
import time
from collections import deque
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx2 as httpx
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request, cookie_parser
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from . import hlaseni, nahled, rozpocet, turnstile
from .beh import BehTahu, ProudTahu, RegistrTahu, Vystup
from .cenik import CENY, Cena
from .citace import TYPY_S_TEXTEM, over_odstavec
from .config import Nastaveni
from .historie import ZNAKU_NA_TOKEN, odhad_tokenu, sestav
from .kontext import Kontext, z_json, znacka
from .llm import LLM, MAX_OPAKOVANI, mozna_uctovano
from .nastroje import Vysledek
from .relace import (COOKIE_BETA, COOKIE_SID, BETA_PLATNOST, ChybiIP, _sit, cookie_sid, den_prahy, ip_z_hlavicky,
                     over_beta, platne_sid, podepis_beta)
from .smycka import _ocen
from .smycka import tah as smycka_tah
from .uloziste import RelaceNeexistuje, Utrata

log = logging.getLogger("hlidac_rag.chat.app")

PREFIX = "/chat-api"
MAX_TELO = 16 * 1024                # spec 11.1: tělo do 16 KB
LIMIT_TAHU_S = 75                   # spec 8.2: tvrdý strop tahu (Cloudflare drží spojení 100 s)
MEKKY_LIMIT_S = 60                  # potom se tah po rozběhnutém kole ukončí sám
REZERVA_VOLANI_S = 5                # timeout volání modelu skončí aspoň tolik před LIMIT_TAHU_S
STOP_ODHAD = "odhad_timeout"        # stop_reason ve volani_llm: cena je horní odhad nedoběhlého volání
PRUBEZNE_S = 2.0                    # rozepsaný text se ukládá po ~2 s
PING_S = 10.0
ODPOJENI_S = 15.0                   # tak dlouho tah čeká na návrat odpojeného klienta
DRZENI_UDALOSTI_S = 60.0
RETRY_OBSAZENO_S = 5
SOUBEH_NASTROJU_DB = 3              # spec 8.2: pool DB 3 spojení
ZNACKA_HASHE = "__CHAT_HASH__"      # ve w.js se nahradí hashem statických souborů
VOLNE_ADRESY = frozenset({f"{PREFIX}/w.js", f"{PREFIX}/beta", f"{PREFIX}/zdravi"})
STATIC = Path(__file__).parent / "static"
TYPY_SOUBORU = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".html": "text/html",
                ".svg": "image/svg+xml", ".json": "application/json", ".txt": "text/plain"}
CACHE_IMMUTABLE = "public, max-age=31536000, immutable"
# skrytý režim: soubory widgetu jen pro beta, sdílená cache (Cloudflare) je nesmí držet
CACHE_IMMUTABLE_SKRYTE = "private, max-age=31536000, immutable"
ZDRAVI_TIMEOUT_S = 3        # /zdravi: nejdéle tak dlouho čeká na SELECT 1 přes oba pooly
HASH_STATIC = re.compile(r"[0-9a-f]{12}")     # tvar výstupu _hash_static
# spec 6 a 12: služba volá ven jen tyto servery (Anthropic přes SDK vlastním klientem, ostatní přes sdílený klient)
POVOLENE_HOSTY = frozenset({"api.anthropic.com", "api.openai.com", "challenges.cloudflare.com", "api.telegram.org"})
LIMIT_POZADAVKU = 60                # spec 6: náhledy a obslužné adresy, požadavků za okno na otisk IP
OKNO_LIMITU_S = 60
MAX_KLICU_LIMITU = 10000            # po překročení se z paměti smažou prošlá okna
MAX_DUVOD = 1000                    # spec 15: důvod nahlášení do 1 000 znaků
KOD_ODPOVEDI = re.compile(r"[a-z0-9]{8}")
ID_KONVERZACE = re.compile(r"[0-9a-f]{32}")
ZV = re.compile(r"([0-9]{1,7})-([0-9]{1,7})")
HLAVICKY_HTML = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
                 "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
ZASTUPCE_PROVOZOVATELE = "{{PROVOZOVATEL}}"
PROVOZOVATEL_CHYBI = "provozovatel a kontakt zatím nejsou zveřejněny"


@dataclass(frozen=True, repr=False)
class Tajemstvi:
    turnstile: str
    beta_klic: bytes
    beta_kod: str
    eval_klic: str
    telegram_token: str
    telegram_chat: str
    turnstile_sitekey: str = ""

    def __repr__(self) -> str:     # tajemství do logu nesmí
        return "Tajemstvi(…)"

    @classmethod
    def z_prostredi(cls, env: Mapping[str, str]) -> "Tajemstvi":
        g = lambda k: (env.get(k) or "").strip()   # noqa: E731
        return cls(turnstile=g("CHAT_TURNSTILE_SECRET"), beta_klic=g("CHAT_BETA_KLIC").encode(),
                   beta_kod=g("CHAT_BETA_KOD"), eval_klic=g("CHAT_EVAL_KLIC"), telegram_token=g("CHAT_TELEGRAM_TOKEN"),
                   telegram_chat=g("CHAT_TELEGRAM_CHAT"), turnstile_sitekey=g("CHAT_TURNSTILE_SITEKEY"))


class _Odmitnuti(Exception):
    def __init__(self, odpoved: Response):
        self.odpoved = odpoved


class NepovolenyHost(httpx.TransportError):
    """Odchozí požadavek mimo `POVOLENE_HOSTY` (spec 6: chat nic nestahuje z psp.cz ani odjinud)."""


async def _jen_povolene_hosty(request: httpx.Request) -> None:
    if request.url.host not in POVOLENE_HOSTY:
        raise NepovolenyHost(f"odchozí spojení na {request.url.host} není povoleno")


def omez_hosty(http: httpx.AsyncClient) -> httpx.AsyncClient:
    """Klientovi přidá hook událostí `request`, který odmítne jiný host než z `POVOLENE_HOSTY` (i při přesměrování)."""
    hooky = http.event_hooks
    if _jen_povolene_hosty not in hooky["request"]:
        http.event_hooks = {**hooky, "request": [*hooky["request"], _jen_povolene_hosty]}
    return http


def _json(data: dict, status: int = 200, hlavicky: dict | None = None) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store", **(hlavicky or {})})


def _chyba(status: int, kod: str, znovu_v: datetime | None = None, hlavicky: dict | None = None,
           **navic) -> JSONResponse:
    data: dict = {"kod": kod, **navic}
    if znovu_v is not None:
        data["znovu_v"] = znovu_v.isoformat()
    return _json(data, status, hlavicky)


def _hash_static(slozka: Path) -> str:
    h = hashlib.sha256()
    if slozka.is_dir():
        for f in sorted(p for p in slozka.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
            h.update(f.relative_to(slozka).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()[:12]


@dataclass
class _Priprava:
    beh: BehTahu
    relace_id: str
    token: str
    otisk: str
    konverzace_id: str
    zprava_id: int
    kod: str
    messages: list[dict]
    registr: RegistrTahu
    model: str
    je_eval: bool = False


class Sluzba:
    def __init__(self, n: Nastaveni, llm: LLM, ulozit, http: httpx.AsyncClient, tajemstvi: Tajemstvi, data,
                 cenik: dict[str, Cena] | None, static_dir: Path, hodiny: Callable[[], datetime],
                 odpojeni_s: float, ping_s: float, drzeni_udalosti_s: float):
        self.n, self.llm, self.ulozit, self.tajemstvi, self.data = n, llm, ulozit, tajemstvi, data
        self.http = omez_hosty(http)
        self.cenik = cenik if cenik is not None else CENY
        self.static_dir = static_dir
        self.hash_static = _hash_static(static_dir)
        self.hodiny = hodiny
        self.odpojeni_s, self.ping_s, self.drzeni_udalosti_s = odpojeni_s, ping_s, drzeni_udalosti_s
        self.puvod = f"https://{n.turnstile_hostname}"
        self.tahy: dict[str, BehTahu] = {}
        self.bezici_relace: set[str] = set()
        self.soubeh = 0
        self.soubeh_ip: dict[str, int] = {}
        self.hlidac = hlaseni.Hlidac()
        self._klic_sid = secrets.token_bytes(32)
        self._ulohy: set[asyncio.Task] = set()
        self._klic_limitu = secrets.token_bytes(32)
        self._okna_limitu: dict[str, deque] = {}
        self._uklid_limitu = 0.0
        self._vlakna = ThreadPoolExecutor(max_workers=SOUBEH_NASTROJU_DB, thread_name_prefix="chat-nastroj")

    # --- pomocné ---

    async def _u(self, jmeno: str, *a, **kw):
        """Volání úložiště ve vlákně (psycopg je synchronní)."""
        return await asyncio.to_thread(getattr(self.ulozit, jmeno), *a, **kw)

    async def _d(self, jmeno: str, *a):
        """Čtení dat ve vlastním thread poolu (3 vlákna = 3 spojení `chat_ro`)."""
        return await asyncio.get_running_loop().run_in_executor(self._vlakna, getattr(self.data, jmeno), *a)

    def _na_pozadi(self, coro) -> None:
        t = asyncio.ensure_future(coro)
        self._ulohy.add(t)
        t.add_done_callback(self._ulohy.discard)

    def _je_eval(self, request: Request) -> bool:
        klic = self.tajemstvi.eval_klic
        hodnota = request.headers.get("x-chat-eval", "")
        return bool(klic) and hmac.compare_digest(hodnota.encode(), klic.encode())

    def _odmitni_limit(self, kod: str, u: Utrata, ted: datetime, hlavicky: dict) -> JSONResponse:
        if kod == "vypnuto":
            return _chyba(503, "vypnuto", hlavicky=hlavicky)
        znovu = rozpocet.znovu_v(self.n, u, ted)
        if kod == "limit_den":
            self._hlid_rozpocet(u, ted, vycerpano=True)
        if kod == "limit_hodina" and znovu is not None:
            hlavicky = {**hlavicky, "Retry-After": str(max(1, int((znovu - ted).total_seconds())))}
        # u limitu relace počet dnešních otázek pro hlášku „Dnes jste položili N otázek“ (spec 11.3)
        navic = {"otazek": u.zprav_den} if kod == "limit_relace" else {}
        return _chyba(429, kod, znovu, hlavicky, **navic)

    def _hlid_rozpocet(self, u: Utrata, ted: datetime, vycerpano: bool = False) -> None:
        """Telegram při prvním překročení 60 % a 100 % dne (každý práh jednou za den v procesu)."""
        if self.n.rozpocet_den <= 0:
            return
        vycerpano = vycerpano or rozpocet.zkontroluj(self.n, u) == "limit_den"
        prahy = self.hlidac.prekrocene(den_prahy(ted), 100 * u.den / self.n.rozpocet_den, vycerpano)
        if prahy:      # překročí-li se oba prahy najednou, stačí hlásit vyšší
            self._na_pozadi(self._hlas([max(prahy)], u, ted))

    async def _hlas(self, prahy: list[int], u: Utrata, ted: datetime) -> None:
        try:
            dotazu, relaci = await self._u("souhrn_dne", ted)
        except Exception:
            log.warning("souhrn dne pro hlášení nejde načíst", exc_info=True)
            dotazu = relaci = 0
        for prah in prahy:
            await hlaseni.posli(self.http, self.tajemstvi,
                                hlaseni.text_hlaseni(prah, u.den, self.n.rozpocet_den, dotazu, relaci))

    # --- adresy ---

    async def zdravi(self, request: Request) -> Response:
        """200 jen když odpoví obě spojení do DB (chat_rw i chat_ro); špatné heslo start služby neshodí."""
        try:
            await asyncio.wait_for(asyncio.gather(self._u("zdravi"), self._d("zdravi")), ZDRAVI_TIMEOUT_S)
        except Exception:
            log.warning("zdraví: DB neodpovídá", exc_info=True)
            return _json({"ok": False}, 503, {"Cache-Control": "no-store"})
        return _json({"ok": True})

    async def stav(self, request: Request) -> Response:
        ted = self.hodiny()
        sid = request.cookies.get(COOKIE_SID)
        rid = sid if platne_sid(sid) and await self._u("relace", sid) else ""
        try:
            otisk = await self._u("otisk_ip", ip_z_hlavicky(request.headers.get("x-forwarded-for")), den_prahy(ted))
        except (ChybiIP, ValueError):
            otisk = ""
        u = await self._u("utrata", rid, otisk, ted)
        kod = rozpocet.zkontroluj(self.n, u)
        znovu = rozpocet.znovu_v(self.n, u, ted)
        return _json({"dostupny": kod is None, "kod": kod,
                      "zbyva_otazek": rozpocet.zbyva_otazek(self.n, u) if kod != "vypnuto" else None,
                      "znovu_v": znovu.isoformat() if znovu else None,
                      "otazek": u.zprav_den if kod == "limit_relace" else None,
                      "turnstile_sitekey": self.tajemstvi.turnstile_sitekey,
                      "max_znaku_zpravy": self.n.max_znaku_zpravy})

    async def beta(self, request: Request) -> Response:
        # cizí původ i jiná metoda vypadají stejně jako špatný kód: adresa nic neprozradí
        if request.method != "POST" or request.headers.get("origin") != self.puvod:
            return PlainTextResponse("Not Found", 404)
        try:
            kod = json.loads(await request.body() or b"{}").get("kod")
        except (ValueError, AttributeError):
            kod = None
        ocekavany = self.tajemstvi.beta_kod
        if not (ocekavany and self.tajemstvi.beta_klic and isinstance(kod, str)
                and hmac.compare_digest(kod.encode(), ocekavany.encode())):
            return PlainTextResponse("Not Found", 404)
        cookie = (f"{COOKIE_BETA}={podepis_beta(self.tajemstvi.beta_klic)}; Path=/; HttpOnly; Secure; SameSite=Lax; "
                  f"Max-Age={int(BETA_PLATNOST.total_seconds())}")
        return Response(status_code=204, headers={"Set-Cookie": cookie, "Cache-Control": "no-store"})

    async def w_js(self, request: Request) -> Response:
        soubor = self.static_dir / "w.js"
        if not soubor.is_file():
            return PlainTextResponse("Not Found", 404)
        obsah = soubor.read_text(encoding="utf-8").replace(ZNACKA_HASHE, self.hash_static)
        return Response(obsah, media_type="text/javascript", headers={"Cache-Control": "public, max-age=300"})

    async def soubor(self, request: Request) -> Response:
        """Statický soubor widgetu. Aktuální hash = `immutable`. Starý hash správného tvaru (w.js v cache prohlížeče
        až 5 min po nasazení) dostane aktuální obsah s `no-cache`: 404 by si prohlížeč zapamatoval u `import()`
        a widget by do obnovení stránky nešel otevřít."""
        h = request.path_params["hash"]
        if not HASH_STATIC.fullmatch(h):
            return PlainTextResponse("Not Found", 404)
        koren = self.static_dir.resolve()
        cesta = (koren / request.path_params["soubor"]).resolve()
        if koren not in cesta.parents or not cesta.is_file() or cesta.suffix not in TYPY_SOUBORU:
            return PlainTextResponse("Not Found", 404)
        immutable = CACHE_IMMUTABLE_SKRYTE if self.n.rezim == "skryte" else CACHE_IMMUTABLE
        cache = immutable if h == self.hash_static else "no-cache"
        return Response(cesta.read_bytes(), media_type=TYPY_SOUBORU[cesta.suffix], headers={"Cache-Control": cache})

    def _beh_relace(self, request: Request) -> BehTahu | None:
        beh = self.tahy.get(request.path_params["tah_id"])
        sid = request.cookies.get(COOKIE_SID)
        if beh is None or not sid or not hmac.compare_digest(sid.encode(), beh.relace_id.encode()):
            return None
        return beh

    async def tah_stream(self, request: Request) -> Response:
        beh = self._beh_relace(request)
        if beh is None:
            return _chyba(404, "tah_neexistuje")
        od = request.query_params.get("od", "0")
        return ProudTahu(beh, int(od) if od.isdigit() else 0, self.ping_s)

    async def tah_stop(self, request: Request) -> Response:
        if request.headers.get("origin") != self.puvod:
            return _chyba(403, "puvod")
        beh = self._beh_relace(request)
        if beh is None:
            return _chyba(404, "tah_neexistuje")
        beh.prerusit.set()
        return Response(status_code=204)

    # --- náhledy a obslužné adresy (spec 6, 9, 15) ---

    def _omez(self, request: Request) -> Response | None:
        """Limit požadavků na otisk IP, čítač jen v paměti procesu (spec 6). None = smí."""
        try:
            ip = ip_z_hlavicky(request.headers.get("x-forwarded-for"))
        except ChybiIP:
            return _chyba(400, "spatny_pozadavek")
        otisk = hmac.new(self._klic_limitu, _sit(ip).encode(), hashlib.sha256).hexdigest()[:32]
        ted = self.hodiny().timestamp()
        okna = self._okna_limitu
        okno = okna.setdefault(otisk, deque())
        while okno and okno[0] <= ted - OKNO_LIMITU_S:
            okno.popleft()
        if len(okno) >= LIMIT_POZADAVKU:
            znovu = max(1, math.ceil(okno[0] + OKNO_LIMITU_S - ted))
            return _chyba(429, "limit_pozadavku", hlavicky={"Retry-After": str(znovu)})
        okno.append(ted)
        if len(okna) > MAX_KLICU_LIMITU and ted - self._uklid_limitu >= OKNO_LIMITU_S:    # jednou za okno
            self._uklid_limitu = ted
            for k in [k for k, o in okna.items() if o[-1] <= ted - OKNO_LIMITU_S]:
                del okna[k]
        return None

    async def _obsluzny_json(self, request: Request) -> dict:
        """Tělo POST obslužné adresy: shodný `Origin`, tělo do 16 KB, JSON objekt."""
        if request.headers.get("origin") != self.puvod:
            raise _Odmitnuti(_chyba(403, "puvod"))
        try:
            d = json.loads(await self._surove_telo(request))
        except ValueError:
            raise _Odmitnuti(_chyba(400, "spatny_pozadavek")) from None
        if not isinstance(d, dict):
            raise _Odmitnuti(_chyba(400, "spatny_pozadavek"))
        return d

    @staticmethod
    def _kod(hodnota) -> str | None:
        kod = hodnota.strip().lower() if isinstance(hodnota, str) else ""
        return kod if KOD_ODPOVEDI.fullmatch(kod) else None

    async def nahled_zdroje(self, request: Request) -> Response:
        if (o := self._omez(request)) is not None:
            return o
        klic = request.query_params.get("k", "")
        if not nahled.platny_klic(klic):         # neplatný klíč se do DB nedostane
            return _chyba(404, "nenalezeno")
        m = ZV.fullmatch(request.query_params.get("zv", ""))
        zv = (int(m[1]), int(m[2])) if m else None
        try:
            data = await self._d("nahled", klic, zv)
        except Exception:
            log.warning("náhled %s: data nejsou dostupná", klic, exc_info=True)
            return _chyba(503, "data_nedostupna")
        if data is None:
            return _chyba(404, "nenalezeno")
        return _json(data, hlavicky={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"})

    async def hodnoceni(self, request: Request) -> Response:
        if (o := self._omez(request)) is not None:
            return o
        d = await self._obsluzny_json(request)
        kod, palec = self._kod(d.get("kod")), d.get("palec")
        if kod is None or isinstance(palec, bool) or not isinstance(palec, int) or palec not in (-1, 1):
            return _chyba(400, "spatny_pozadavek")
        if not await self._u("hodnoceni", kod, palec):
            return _chyba(404, "nenalezeno")
        return Response(status_code=204)

    async def nahlasit(self, request: Request) -> Response:
        if (o := self._omez(request)) is not None:
            return o
        d = await self._obsluzny_json(request)
        kod, duvod = self._kod(d.get("kod")), d.get("duvod")
        if kod is None or not isinstance(duvod, str):
            return _chyba(400, "spatny_pozadavek")
        if len(duvod) > MAX_DUVOD:
            return _chyba(413, "prilis_dlouhy_duvod")
        ted = self.hodiny()
        if await self._u("pocet_nahlaseni_dne", ted) >= self.n.max_nahlaseni_den:
            return _chyba(429, "limit_nahlaseni")
        try:
            otisk = await self._u("otisk_ip", ip_z_hlavicky(request.headers.get("x-forwarded-for")), den_prahy(ted))
        except ChybiIP:
            return _chyba(400, "spatny_pozadavek")
        if not await self._u("nahlas", kod, duvod.strip(), otisk):
            return _chyba(404, "nenalezeno")
        return Response(status_code=204)

    async def smazat(self, request: Request) -> Response:
        if (o := self._omez(request)) is not None:
            return o
        d = await self._obsluzny_json(request)
        kid, sid = d.get("konverzace_id"), request.cookies.get(COOKIE_SID)
        if not isinstance(kid, str) or not ID_KONVERZACE.fullmatch(kid):
            return _chyba(400, "spatny_pozadavek")
        if not platne_sid(sid):
            return _chyba(404, "nenalezeno")
        if sid in self.bezici_relace:           # mazání pod běžícím tahem by tah rozbilo
            return _chyba(409, "tah_bezi")
        if not await self._u("smaz_konverzaci", kid, sid):      # cizí i neexistující konverzace vypadají stejně
            return _chyba(404, "nenalezeno")
        return Response(status_code=204)

    async def konverzace_obnova(self, request: Request) -> Response:
        if (o := self._omez(request)) is not None:
            return o
        kid, sid = request.path_params["id"], request.cookies.get(COOKIE_SID)
        if not ID_KONVERZACE.fullmatch(kid) or not platne_sid(sid) or not await self._u("konverzace", kid, sid):
            return _chyba(404, "nenalezeno")
        zpravy = []
        for t in await self._u("zpravy", kid):
            zpravy.append({"role": "uzivatel", "text": t["otazka"]})
            zpravy.append({"role": "bot", "text": t["text"], "kod": t["kod"], "stav": t["stav"],
                           "citovane": list(t["citovane"])})
        zdroje = [_zdroj_pro_widget(z) for z in await self._u("zdroje", kid) if z.citovan]
        return _json({"konverzace_id": kid, "zpravy": zpravy, "zdroje": zdroje})

    @staticmethod
    def _html(telo: str, cache: str = "no-store") -> Response:
        return Response(telo, media_type="text/html", headers={**HLAVICKY_HTML, "Cache-Control": cache})

    async def overit(self, request: Request) -> Response:
        """Řekne jen, zda odpověď s kódem existuje, a datum; text odpovědi nevrací (spec 15)."""
        if (o := self._omez(request)) is not None:
            return o
        kod = self._kod(request.query_params.get("kod"))
        datum = await self._u("existuje_kod", kod) if kod else None
        if datum is not None:
            veta = f"Odpověď s tímto kódem existuje, vznikla {datum.day}. {datum.month}. {datum.year}."
        else:
            veta = "Odpověď s tímto kódem neexistuje."
        kod_html = f"<p>Kód: <code>{html.escape(kod)}</code></p>" if kod else ""
        return self._html(
            '<!doctype html><html lang="cs"><head><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">'
            "<title>Ověření kódu odpovědi</title>"
            "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:36rem;margin:2rem auto;padding:0 1rem}</style>"
            f"</head><body><h1>Ověření kódu odpovědi</h1>{kod_html}<p><strong>{veta}</strong></p>"
            "<p>Stránka neukazuje text odpovědi, jen to, zda ji chatbot cosepece.cz vytvořil.</p></body></html>")

    async def zasady(self, request: Request) -> Response:
        soubor = self.static_dir / "zasady.html"
        if not soubor.is_file():
            return PlainTextResponse("Not Found", 404)
        kdo = html.escape(self.n.provozovatel or PROVOZOVATEL_CHYBI)
        telo = soubor.read_text(encoding="utf-8").replace(ZASTUPCE_PROVOZOVATELE, kdo)
        return self._html(telo, "public, max-age=300")

    # --- zpráva ---

    async def _surove_telo(self, request: Request) -> bytes:
        delka = request.headers.get("content-length", "")
        if delka.isdigit() and int(delka) > MAX_TELO:
            raise _Odmitnuti(_chyba(413, "prilis_velke"))
        telo = b""
        async for kus in request.stream():
            telo += kus
            if len(telo) > MAX_TELO:
                raise _Odmitnuti(_chyba(413, "prilis_velke"))
        return telo

    async def _telo(self, request: Request) -> dict:
        telo = await self._surove_telo(request)
        try:
            d = json.loads(telo)
        except ValueError:
            raise _Odmitnuti(_chyba(400, "spatny_pozadavek")) from None
        if (not isinstance(d, dict) or not isinstance(d.get("text"), str) or not d["text"].strip()
                or not isinstance(d.get("konverzace_id"), (str, type(None)))
                or not isinstance(d.get("kontext"), (dict, type(None)))
                or not isinstance(d.get("turnstile"), (str, type(None)))):
            raise _Odmitnuti(_chyba(400, "spatny_pozadavek"))
        if len(d["text"]) > self.n.max_znaku_zpravy:
            raise _Odmitnuti(_chyba(413, "prilis_dlouha_zprava"))
        return d

    async def zprava(self, request: Request) -> Response:
        try:
            return await self._zprava(request)
        except _Odmitnuti as e:
            return e.odpoved

    async def _zprava(self, request: Request) -> Response:
        n = self.n
        if request.headers.get("origin") != self.puvod:
            return _chyba(403, "puvod")
        d = await self._telo(request)
        try:
            ip = ip_z_hlavicky(request.headers.get("x-forwarded-for"))
        except ChybiIP:
            log.warning("požadavek bez IP od Caddy, odmítnut")
            return _chyba(400, "spatny_pozadavek")
        ted = self.hodiny()
        sid = request.cookies.get(COOKIE_SID)
        if platne_sid(sid) and await self._u("relace", sid):
            rid, nova = sid, False
        else:
            # řádek relace vznikne až po Turnstile; do té doby nese cookie id podepsané klíčem procesu
            rid, nova = (sid if self._predbezne_sid(sid) else self._nove_sid()), True
        cookie = {"Set-Cookie": cookie_sid(rid)}       # i klouzavé prodloužení existující relace
        otisk = await self._u("otisk_ip", ip, den_prahy(ted))
        u = await self._u("utrata", rid, otisk, ted)
        kod = rozpocet.zkontroluj(n, u)
        if kod:
            return self._odmitni_limit(kod, u, ted, cookie)
        je_eval = self._je_eval(request)
        if je_eval and nova:
            await self._u("zaloz_relaci", rid)
        if not je_eval:
            rel = await self._u("relace", rid) if not nova else None
            if turnstile.potreba_overeni(n, rel, otisk, rozpocet.prisny_rezim(n, u), ted):
                if not d.get("turnstile"):
                    return _chyba(428, "turnstile", hlavicky=cookie)
                try:
                    ok = await turnstile.over_token(self.http, d["turnstile"], ip, self.tajemstvi.turnstile,
                                                    hostname=n.turnstile_hostname)
                except turnstile.OvereniNedostupne:
                    return _chyba(503, "overeni_nedostupne", hlavicky=cookie)
                if not ok:
                    return _chyba(428, "turnstile", hlavicky=cookie)
                if nova:
                    await self._u("zaloz_relaci", rid)
                await self._u("zapis_overeni", rid, otisk, turnstile.platnost_overeni(n, ted))

        # jeden tah relace a kapacita procesu (před zámkem v DB, spec 11.1)
        if rid in self.bezici_relace:
            return _chyba(409, "tah_bezi", hlavicky=cookie)
        if self.soubeh >= n.soubeh_celkem or self.soubeh_ip.get(otisk, 0) >= n.soubeh_ip:
            return _chyba(503, "obsazeno", hlavicky={**cookie, "Retry-After": str(RETRY_OBSAZENO_S)})
        self._obsad(rid, otisk)
        token, predano, p = None, False, None
        try:
            try:
                z = await self._u("zahaj_tah", rid, otisk, ted, n.drzeni_tahu_s)
            except RelaceNeexistuje:
                return _chyba(400, "spatny_pozadavek", hlavicky=cookie)
            if z is None:
                return _chyba(409, "tah_bezi", hlavicky=cookie)
            token, u = z
            kod = rozpocet.zkontroluj(n, u)        # útrata přečtená až pod zámkem relace
            if kod:
                return self._odmitni_limit(kod, u, ted, cookie)
            p = await self._priprav(d, rid, token, otisk, u, je_eval, cookie)
            if not je_eval:     # počítá se jen zpráva, která opravdu jde do tahu (ne 409, 503)
                await self._u("pricti_zpravu_od_overeni", rid)
            self._na_pozadi(self._bez(p))
            predano = True                          # úklid (skonci_tah, čítače) převzal běh tahu
            return ProudTahu(p.beh, 0, self.ping_s, cookie)
        finally:
            if not predano:
                if p is not None:      # odpověď už založená, tah se ale nespustil
                    self.tahy.pop(p.beh.id, None)
                    try:
                        await self._u("dokonci", p.zprava_id, "", "chyba", p.model, [], {})
                    except Exception:
                        log.exception("založenou odpověď %s nejde označit jako chybu", p.zprava_id)
                try:
                    if token is not None:
                        await self._u("skonci_tah", rid, token)
                except Exception:
                    log.exception("skonci_tah po odmítnutí selhal (příznak vyprší po %s s)", n.drzeni_tahu_s)
                finally:
                    self._uvolni(rid, otisk)

    def _podpis_sid(self, nonce: str) -> str:
        return hmac.new(self._klic_sid, nonce.encode(), hashlib.sha256).hexdigest()[:16]

    def _nove_sid(self) -> str:
        """Id relace, která ještě nemá řádek v DB: 16 hex náhodně + 16 hex podpis klíčem procesu (32 hex jako id
        z `zaloz_relaci`). Bot tak bez Turnstile nezaloží v `chat.relace` nic a cizí id si nepodstrčí."""
        nonce = secrets.token_hex(8)
        return nonce + self._podpis_sid(nonce)

    def _predbezne_sid(self, sid: str | None) -> bool:
        return platne_sid(sid) and hmac.compare_digest(sid[16:], self._podpis_sid(sid[:16]))

    def _obsad(self, rid: str, otisk: str) -> None:
        self.bezici_relace.add(rid)
        self.soubeh += 1
        self.soubeh_ip[otisk] = self.soubeh_ip.get(otisk, 0) + 1

    def _uvolni(self, rid: str, otisk: str) -> None:
        if rid not in self.bezici_relace:
            return
        self.bezici_relace.discard(rid)
        self.soubeh -= 1
        zbyva = self.soubeh_ip.get(otisk, 1) - 1
        if zbyva > 0:
            self.soubeh_ip[otisk] = zbyva
        else:
            self.soubeh_ip.pop(otisk, None)

    async def _priprav(self, d: dict, rid: str, token: str, otisk: str, u: Utrata, je_eval: bool,
                       cookie: dict) -> _Priprava:
        n = self.n
        kid = d.get("konverzace_id")
        if kid is not None:
            if not await self._u("konverzace", kid, rid):
                raise _Odmitnuti(_chyba(400, "spatny_pozadavek", hlavicky=cookie))
        else:
            kid = await self._u("zaloz_konverzaci", rid, je_eval)
        tahy = await self._u("tahy", kid)
        if len(tahy) >= n.max_tahu_konverzace:
            raise _Odmitnuti(_chyba(409, "limit_konverzace", hlavicky=cookie))
        k = await self._d("over_kontext", z_json(d.get("kontext")))
        karta, polozky = await self._d("karta", k, n.karta_max_tokenu)
        predchozi = await self._u("posledni_kontext", kid)
        pole = {f.name for f in dataclasses.fields(Kontext)}
        predchozi_k = Kontext(**{a: b for a, b in predchozi.items() if a in pole}) if predchozi and predchozi.get(
            "typ") else None
        zn = znacka(k, predchozi_k)
        registr = RegistrTahu(await self._u("zdroje", kid))
        for pol in polozky:
            registr.pridej(pol)
        messages = sestav(tahy, d["text"].strip(), zn, karta, registr, n.historie_max_tokenu, n.karta_jako)
        zid, kod = await self._u("zaloz_odpoved", kid, d["text"].strip(), {"znacka": zn, **dataclasses.asdict(k)})
        beh = BehTahu(secrets.token_urlsafe(16), rid, self.odpojeni_s)
        self.tahy[beh.id] = beh
        return _Priprava(beh, rid, token, otisk, kid, zid, kod, messages, registr, rozpocet.vyber_model(n, u), je_eval)

    # --- běh tahu ---

    def _text_zdroje(self, registr: RegistrTahu) -> Callable[[int], str | None]:
        def text(cislo: int) -> str | None:
            z = registr.podle_n(cislo)
            if z is None or z.klic.split(":", 1)[0] not in TYPY_S_TEXTEM:
                return None
            if z.klic not in registr.texty:
                t = self.data.text_zdroje(z)
                if t is None:
                    return None
                registr.texty[z.klic] = t
            return registr.texty[z.klic]
        return text

    async def _spust_nastroj(self, nazev: str, argumenty: dict) -> Vysledek:
        return await self._d("nastroj", nazev, argumenty)

    async def _bez(self, p: _Priprava) -> None:
        beh, n = p.beh, self.n
        text_zdroje = self._text_zdroje(p.registr)

        async def over(odstavec: str):
            return await asyncio.get_running_loop().run_in_executor(
                self._vlakna, over_odstavec, odstavec, p.registr, text_zdroje, self.data.jmena_osob)

        vystup = Vystup(p.registr, over)
        llm = _LLMVTahu(self.llm, time.monotonic() + LIMIT_TAHU_S, lambda r: self._zapis_odhad(p, r))
        konec: dict = {"stav": "chyba", "model": p.model, "text": ""}
        nastroje: list[str] = []      # jen pro eval (konec.eval)
        mekky = asyncio.get_running_loop().call_later(MEKKY_LIMIT_S, beh.prerusit.set)
        try:
            beh.pridej("start", {"konverzace_id": p.konverzace_id, "zprava_id": p.zprava_id, "tah_id": beh.id})
            ulozeno = time.monotonic()
            try:
                async with asyncio.timeout(LIMIT_TAHU_S):
                    gen = smycka_tah(llm, self._spust_nastroj, p.messages, p.registr, n, p.model, beh.prerusit,
                                     self.cenik)
                    try:
                        async for u in gen:
                            if u.typ == "stav":
                                beh.pridej("stav", {"text": u.data["text"]})
                            elif u.typ == "text":
                                for typ, data in await vystup.pridej(u.data["text"]):
                                    beh.pridej(typ, data)
                                if time.monotonic() - ulozeno >= PRUBEZNE_S:
                                    await self._u("prubezne", p.zprava_id, vystup.text)
                                    ulozeno = time.monotonic()
                            elif u.typ == "volani_llm":
                                await self._zapis_llm(p, u.data)
                            elif u.typ == "volani_nastroje":
                                nastroje.append(u.data["nazev"] or "")
                                await self._u("zapis_volani_nastroje", zprava_id=p.zprava_id,
                                              nastroj=u.data["nazev"] or "", argumenty=u.data["argumenty"],
                                              ms=u.data["ms"], znaku=round(u.data["tokenu"] * ZNAKU_NA_TOKEN),
                                              chyba="chyba" if u.data["chyba"] else None)
                            elif u.typ == "konec":
                                konec = u.data
                    finally:
                        await gen.aclose()
            except TimeoutError:
                log.error("tah %s překročil %s s", beh.id, LIMIT_TAHU_S)
                konec = {**konec, "stav": "chyba"}
                if llm.rozbehnute is not None:      # zrušené volání mohlo být účtováno
                    await self._zapis_odhad(p, llm.rozbehnute)
            await self._dokonci(p, vystup, konec, nastroje)
        except Exception:
            log.exception("tah %s selhal", beh.id)
            try:
                await self._u("dokonci", p.zprava_id, vystup.text, "chyba", konec.get("model"), [], {})
            except Exception:
                log.exception("tah %s: chybu nejde zapsat", beh.id)
            beh.pridej("chyba", {"kod": "chyba"})
            beh.pridej("konec", {"kod": p.kod, "stav": "chyba", "zbyva_otazek": None})
        finally:
            mekky.cancel()
            try:
                await self._u("skonci_tah", p.relace_id, p.token)
            except Exception:
                log.exception("tah %s: skonci_tah selhal (příznak vyprší po %s s)", beh.id, n.drzeni_tahu_s)
            self._uvolni(p.relace_id, p.otisk)
            beh.skonci()
            asyncio.get_running_loop().call_later(self.drzeni_udalosti_s, self.tahy.pop, beh.id, None)

    async def _zapis_llm(self, p: _Priprava, d: dict) -> None:
        await self._u("zapis_volani_llm", zprava_id=p.zprava_id, relace_id=p.relace_id, otisk_ip=p.otisk,
                      model=d["model"], vstup=d["vstup"], vystup=d["vystup"], cache_cteni=d["cache_cteni"],
                      cache_zapis=d["cache_zapis"], cena_usd=d["cena_usd"], ms=d["ms"], stop_reason=d["stop_reason"])

    async def _zapis_odhad(self, p: _Priprava, r: dict) -> None:
        """Horní odhad ceny volání, které nedoběhlo (vstup bez cache + celé max_tokens výstupu)."""
        # vstup jako zápis do cache: nejdražší sazba vstupu, odhad je tedy opravdu horní mez
        cena = _ocen(r["model"], 0, r["vystup"], 0, r["vstup"], self.cenik)
        log.warning("tah %s: volání modelu nedoběhlo, zapisuji horní odhad ceny %.4f USD", p.beh.id, cena)
        await self._u("zapis_volani_llm", zprava_id=p.zprava_id, relace_id=p.relace_id, otisk_ip=p.otisk,
                      model=r["model"], vstup=0, vystup=r["vystup"], cache_cteni=0, cache_zapis=r["vstup"],
                      cena_usd=cena, ms=int((time.monotonic() - r["start"]) * 1000), stop_reason=STOP_ODHAD)

    async def _dokonci(self, p: _Priprava, vystup: Vystup, konec: dict, nastroje: list[str]) -> None:
        beh, stav = p.beh, konec["stav"]
        if stav == "odmitnuto":
            # už odeslaný text se stahuje (widget ho nahradí systémovou hláškou) a do historie nic nejde
            text = ""
            beh.pridej("chyba", {"kod": "odmitnuto"})
        elif stav == "chyba":
            text = vystup.text
            beh.pridej("chyba", {"kod": "chyba"})
        else:
            for typ, data in await vystup.konec():
                beh.pridej(typ, data)
            text = vystup.text
        citovane = p.registr.oznac_citovane(text) if text else []
        ulozit = {z.n: z for z in p.registr.nove()}
        for c in citovane:
            ulozit[c] = p.registr.podle_n(c)
        if ulozit:
            await self._u("uloz_zdroje", p.konverzace_id, list(ulozit.values()))
        if stav not in ("odmitnuto", "chyba"):
            beh.pridej("zdroje", {"zdroje": [_zdroj_pro_widget(p.registr.podle_n(c)) for c in citovane]})
        await self._u("dokonci", p.zprava_id, text, stav, konec.get("model"), citovane,
                      {"citaty": vystup.citaty, "odstranenych_odkazu": vystup.odstraneno})
        ted = self.hodiny()
        u = await self._u("utrata", p.relace_id, p.otisk, ted)
        self._hlid_rozpocet(u, ted)
        zaver = {"kod": p.kod, "stav": stav, "zbyva_otazek": rozpocet.zbyva_otazek(self.n, u)}
        if p.je_eval:       # chat-eval (úloha 19) potřebuje cenu a volané nástroje; veřejnému widgetu je nedáváme
            zaver["eval"] = {"cena_usd": konec.get("cena_usd", 0.0), "nastroje": nastroje}
        beh.pridej("konec", zaver)


class _LLMVTahu:
    """LLM pro jeden tah: timeout každého volání ze zbývajícího času tahu (SDK opakuje nejvýš MAX_OPAKOVANI)
    a evidence rozběhnutého volání. Volání, které po odeslání požadavku skončí chybou API nebo výpadkem
    (`llm.mozna_uctovano`: timeout čtení, přerušené spojení, chyba ve streamu), dostane do útraty horní odhad."""

    def __init__(self, llm: LLM, konec_tahu: float, pri_odhadu):
        self.llm, self.konec_tahu, self.pri_odhadu = llm, konec_tahu, pri_odhadu
        self.posledni_vstup = 0
        self.rozbehnute: dict | None = None

    async def volej(self, *, model, system, tools, messages, max_tokens, **kw):
        zbyva = self.konec_tahu - time.monotonic() - REZERVA_VOLANI_S
        timeout_s = max(1.0, zbyva / (1 + MAX_OPAKOVANI))
        text = system + json.dumps(tools, ensure_ascii=False) + json.dumps(messages, ensure_ascii=False, default=str)
        self.rozbehnute = {"model": model, "vstup": max(self.posledni_vstup, odhad_tokenu(text)),
                           "vystup": max_tokens, "start": time.monotonic()}
        try:
            o = await self.llm.volej(model=model, system=system, tools=tools, messages=messages,
                                     max_tokens=max_tokens, timeout_s=timeout_s, **kw)
        except Exception as e:
            r, self.rozbehnute = self.rozbehnute, None
            if mozna_uctovano(e):      # chyba API nebo výpadek po odeslání požadavku: mohlo se účtovat
                await self.pri_odhadu(r)
            elif not getattr(e, "pred_streamem", False):
                # chyba v kódu: bez odhadu (opakovaná chyba by jinak vyčerpala rozpočet), výstraha do Sentry
                log.exception("volání modelu selhalo mimo API, cena se neodhaduje")
            raise
        self.rozbehnute = None
        u = o.usage
        self.posledni_vstup = u.vstup + u.cache_cteni + u.cache_zapis + u.vystup
        return o


def _zdroj_pro_widget(z) -> dict:
    znacky = []
    if z.meta.get("autorizovano") is False:
        znacky.append("neautorizovano")
    if z.meta.get("odvozeny"):
        znacky.append("odvozeny_ai")
    # klic = klíč pro GET /chat-api/nahled (adresy od modelu se nepřebírají, odkazy staví server z klíče a dat)
    return {"n": z.n, "typ": z.typ, "titulek": z.titulek, "znacky": znacky, "klic": nahled.klic_nahledu(z.klic, z.meta) if nahled.platny_klic(z.klic) else None}


class SkrytyRezim:
    """Ve skrytém režimu bez platné `chat_beta` vrací 404 na všech adresách kromě `VOLNE_ADRESY` (spec 4.2)."""

    def __init__(self, app, sluzba: Sluzba):
        self.app, self.sluzba = app, sluzba

    async def __call__(self, scope, receive, send):
        s = self.sluzba
        if scope["type"] == "http" and s.n.rezim == "skryte" and scope.get("path") not in VOLNE_ADRESY:
            hlavicky = dict((k.decode("latin-1").lower(), v.decode("latin-1")) for k, v in scope.get("headers", []))
            beta = cookie_parser(hlavicky.get("cookie", "")).get(COOKIE_BETA)
            if not over_beta(beta, s.tajemstvi.beta_klic):
                # no-store: Cloudflare cachuje .js/.css podle přípony a 404 by pak dostali i beta uživatelé
                return await PlainTextResponse("Not Found", 404, headers={"Cache-Control": "no-store"})(
                    scope, receive, send)
        await self.app(scope, receive, send)


def _chraneno(handler):
    """Obslužná adresa, která odmítá výjimkou `_Odmitnuti` (špatný `Origin`, příliš velké tělo)."""
    async def obal(request: Request) -> Response:
        try:
            return await handler(request)
        except _Odmitnuti as e:
            return e.odpoved
    return obal


def vytvor(n: Nastaveni, llm: LLM, ulozit, http: httpx.AsyncClient, tajemstvi: Tajemstvi, *, data=None,
           cenik: dict[str, Cena] | None = None, static_dir: Path | None = None,
           hodiny: Callable[[], datetime] | None = None, odpojeni_s: float = ODPOJENI_S, ping_s: float = PING_S,
           drzeni_udalosti_s: float = DRZENI_UDALOSTI_S) -> Starlette:
    """Aplikace chatbota. `ulozit` = úložiště (`pg.PgUloziste`), `data` = čtení dat (`pg.PgData`)."""
    if data is None:
        raise ValueError("chybí data (pg.PgData nad poolem role chat_ro)")
    s = Sluzba(n, llm, ulozit, http, tajemstvi, data, cenik, static_dir or STATIC,
               hodiny or (lambda: datetime.now(timezone.utc)), odpojeni_s, ping_s, drzeni_udalosti_s)
    trasy = [
        Route(f"{PREFIX}/zdravi", s.zdravi, methods=["GET"]),
        Route(f"{PREFIX}/stav", s.stav, methods=["GET"]),
        Route(f"{PREFIX}/beta", s.beta, methods=["GET", "POST"]),
        Route(f"{PREFIX}/zprava", s.zprava, methods=["POST"]),
        Route(f"{PREFIX}/tah/{{tah_id}}/stream", s.tah_stream, methods=["GET"]),
        Route(f"{PREFIX}/tah/{{tah_id}}/stop", s.tah_stop, methods=["POST"]),
        Route(f"{PREFIX}/nahled", s.nahled_zdroje, methods=["GET"]),
        Route(f"{PREFIX}/hodnoceni", _chraneno(s.hodnoceni), methods=["POST"]),
        Route(f"{PREFIX}/nahlasit", _chraneno(s.nahlasit), methods=["POST"]),
        Route(f"{PREFIX}/smazat", _chraneno(s.smazat), methods=["POST"]),
        Route(f"{PREFIX}/overit", s.overit, methods=["GET"]),
        Route(f"{PREFIX}/zasady", s.zasady, methods=["GET"]),
        Route(f"{PREFIX}/konverzace/{{id}}", s.konverzace_obnova, methods=["GET"]),
        Route(f"{PREFIX}/w.js", s.w_js, methods=["GET"]),
        Route(f"{PREFIX}/s/{{hash}}/{{soubor:path}}", s.soubor, methods=["GET"]),
    ]
    app = Starlette(routes=trasy, middleware=[Middleware(SkrytyRezim, sluzba=s)])
    app.state.sluzba = s
    return app
