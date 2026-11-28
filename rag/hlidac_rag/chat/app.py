"""HTTP služba chatbota (spec 3, 4.2, 11, 12, 14): Starlette pod uvicornem s jedním procesem.

Adresy (vše pod `/chat-api`): `zdravi`, `stav`, `beta`, `zprava` (SSE), `tah/{id}/stream` (opětovné připojení),
`tah/{id}/stop`, `w.js` a `s/<hash>/<soubor>` (statické soubory widgetu z `chat/static/`).

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
import hmac
import json
import logging
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx2 as httpx
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request, cookie_parser
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from . import hlaseni, rozpocet, turnstile
from .beh import BehTahu, ProudTahu, RegistrTahu, Vystup
from .cenik import CENY, Cena
from .citace import TYPY_S_TEXTEM, over_odstavec
from .config import Nastaveni
from .historie import ZNAKU_NA_TOKEN, sestav
from .kontext import Kontext, z_json, znacka
from .llm import LLM
from .nastroje import Vysledek
from .relace import (COOKIE_BETA, COOKIE_SID, BETA_PLATNOST, ChybiIP, cookie_sid, den_prahy, ip_z_hlavicky, over_beta,
                     platne_sid, podepis_beta)
from .smycka import tah as smycka_tah
from .uloziste import RelaceNeexistuje, Utrata

log = logging.getLogger("hlidac_rag.chat.app")

PREFIX = "/chat-api"
MAX_TELO = 16 * 1024                # spec 11.1: tělo do 16 KB
LIMIT_TAHU_S = 75                   # spec 8.2: tvrdý strop tahu (Cloudflare drží spojení 100 s)
MEKKY_LIMIT_S = 60                  # potom se tah po rozběhnutém kole ukončí sám
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


def _json(data: dict, status: int = 200, hlavicky: dict | None = None) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store", **(hlavicky or {})})


def _chyba(status: int, kod: str, znovu_v: datetime | None = None, hlavicky: dict | None = None) -> JSONResponse:
    data: dict = {"kod": kod}
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


class Sluzba:
    def __init__(self, n: Nastaveni, llm: LLM, ulozit, http: httpx.AsyncClient, tajemstvi: Tajemstvi, data,
                 cenik: dict[str, Cena] | None, static_dir: Path, hodiny: Callable[[], datetime],
                 odpojeni_s: float, ping_s: float, drzeni_udalosti_s: float):
        self.n, self.llm, self.ulozit, self.http, self.tajemstvi, self.data = n, llm, ulozit, http, tajemstvi, data
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
        self._ulohy: set[asyncio.Task] = set()
        from concurrent.futures import ThreadPoolExecutor
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
        return _chyba(429, kod, znovu, hlavicky)

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
                      "turnstile_sitekey": self.tajemstvi.turnstile_sitekey})

    async def beta(self, request: Request) -> Response:
        if request.headers.get("origin") != self.puvod:
            return _chyba(403, "puvod")
        try:
            kod = json.loads(await request.body() or b"{}").get("kod")
        except (ValueError, AttributeError):
            kod = None
        ocekavany = self.tajemstvi.beta_kod
        if not (ocekavany and self.tajemstvi.beta_klic and isinstance(kod, str)
                and hmac.compare_digest(kod.encode(), ocekavany.encode())):
            return PlainTextResponse("Not Found", 404)
        cookie = (f"{COOKIE_BETA}={podepis_beta(self.tajemstvi.beta_klic)}; Path=/; HttpOnly; Secure; SameSite=Strict; "
                  f"Max-Age={int(BETA_PLATNOST.total_seconds())}")
        return Response(status_code=204, headers={"Set-Cookie": cookie, "Cache-Control": "no-store"})

    async def w_js(self, request: Request) -> Response:
        soubor = self.static_dir / "w.js"
        if not soubor.is_file():
            return PlainTextResponse("Not Found", 404)
        obsah = soubor.read_text(encoding="utf-8").replace(ZNACKA_HASHE, self.hash_static)
        return Response(obsah, media_type="text/javascript", headers={"Cache-Control": "public, max-age=300"})

    async def soubor(self, request: Request) -> Response:
        if request.path_params["hash"] != self.hash_static:
            return PlainTextResponse("Not Found", 404)
        koren = self.static_dir.resolve()
        cesta = (koren / request.path_params["soubor"]).resolve()
        if koren not in cesta.parents or not cesta.is_file() or cesta.suffix not in TYPY_SOUBORU:
            return PlainTextResponse("Not Found", 404)
        return Response(cesta.read_bytes(), media_type=TYPY_SOUBORU[cesta.suffix],
                        headers={"Cache-Control": CACHE_IMMUTABLE})

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

    # --- zpráva ---

    async def _telo(self, request: Request) -> dict:
        delka = request.headers.get("content-length", "")
        if delka.isdigit() and int(delka) > MAX_TELO:
            raise _Odmitnuti(_chyba(413, "prilis_velke"))
        telo = b""
        async for kus in request.stream():
            telo += kus
            if len(telo) > MAX_TELO:
                raise _Odmitnuti(_chyba(413, "prilis_velke"))
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
        rid = sid if platne_sid(sid) and await self._u("relace", sid) else await self._u("zaloz_relaci")
        cookie = {"Set-Cookie": cookie_sid(rid)}       # i klouzavé prodloužení existující relace
        otisk = await self._u("otisk_ip", ip, den_prahy(ted))
        u = await self._u("utrata", rid, otisk, ted)
        kod = rozpocet.zkontroluj(n, u)
        if kod:
            return self._odmitni_limit(kod, u, ted, cookie)
        je_eval = self._je_eval(request)
        if not je_eval:
            rel = await self._u("relace", rid)
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
                await self._u("zapis_overeni", rid, otisk, turnstile.platnost_overeni(n, ted))
            await self._u("pricti_zpravu_od_overeni", rid)

        # jeden tah relace a kapacita procesu (před zámkem v DB, spec 11.1)
        if rid in self.bezici_relace:
            return _chyba(409, "tah_bezi", hlavicky=cookie)
        if self.soubeh >= n.soubeh_celkem or self.soubeh_ip.get(otisk, 0) >= n.soubeh_ip:
            return _chyba(503, "obsazeno", hlavicky={**cookie, "Retry-After": str(RETRY_OBSAZENO_S)})
        self._obsad(rid, otisk)
        token, predano = None, False
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
            self._na_pozadi(self._bez(p))
            predano = True                          # úklid (skonci_tah, čítače) převzal běh tahu
            return ProudTahu(p.beh, 0, self.ping_s, cookie)
        finally:
            if not predano:
                if token is not None:
                    await self._u("skonci_tah", rid, token)
                self._uvolni(rid, otisk)

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
        return _Priprava(beh, rid, token, otisk, kid, zid, kod, messages, registr, rozpocet.vyber_model(n, u))

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
        konec: dict = {"stav": "chyba", "model": p.model, "text": ""}
        mekky = asyncio.get_running_loop().call_later(MEKKY_LIMIT_S, beh.prerusit.set)
        try:
            beh.pridej("start", {"konverzace_id": p.konverzace_id, "zprava_id": p.zprava_id, "tah_id": beh.id})
            ulozeno = time.monotonic()
            try:
                async with asyncio.timeout(LIMIT_TAHU_S):
                    gen = smycka_tah(self.llm, self._spust_nastroj, p.messages, p.registr, n, p.model, beh.prerusit,
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
            await self._dokonci(p, vystup, konec)
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

    async def _dokonci(self, p: _Priprava, vystup: Vystup, konec: dict) -> None:
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
        beh.pridej("konec", {"kod": p.kod, "stav": stav, "zbyva_otazek": rozpocet.zbyva_otazek(self.n, u)})


def _zdroj_pro_widget(z) -> dict:
    znacky = []
    if z.meta.get("autorizovano") is False:
        znacky.append("neautorizovano")
    if z.meta.get("odvozeny"):
        znacky.append("odvozeny_ai")
    return {"n": z.n, "typ": z.typ, "titulek": z.titulek, "znacky": znacky}


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
                return await PlainTextResponse("Not Found", 404)(scope, receive, send)
        await self.app(scope, receive, send)


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
        Route(f"{PREFIX}/beta", s.beta, methods=["POST"]),
        Route(f"{PREFIX}/zprava", s.zprava, methods=["POST"]),
        Route(f"{PREFIX}/tah/{{tah_id}}/stream", s.tah_stream, methods=["GET"]),
        Route(f"{PREFIX}/tah/{{tah_id}}/stop", s.tah_stop, methods=["POST"]),
        Route(f"{PREFIX}/w.js", s.w_js, methods=["GET"]),
        Route(f"{PREFIX}/s/{{hash}}/{{soubor:path}}", s.soubor, methods=["GET"]),
    ]
    app = Starlette(routes=trasy, middleware=[Middleware(SkrytyRezim, sluzba=s)])
    app.state.sluzba = s
    return app
