"""Služba chatu (app.vytvor): pořadí kontrol, tah se streamem, souběh, odpojení, skrytý režim, statické soubory.

Falešné LLM, falešné úložiště v paměti a falešný siteverify; Anthropic, OpenAI ani Cloudflare se nevolají.
Požadavky jdou přímo přes ASGI (vlastní ovladač), aby šlo stream číst průběžně a klienta odpojit uprostřed tahu.
Testy s `pg` běží s opravdovým úložištěm nad PostgreSQL."""
from __future__ import annotations

import asyncio
import itertools
import json
import os
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone

import httpx2 as httpx
import pytest

from hlidac_rag.chat import turnstile
from hlidac_rag.chat.app import Tajemstvi, vytvor
from hlidac_rag.chat.beh import Vystup
from hlidac_rag.chat.citace import Overeni
from hlidac_rag.chat.config import nacti
from hlidac_rag.chat.historie import Tah
from hlidac_rag.chat.llm import Odpoved, Usage
from hlidac_rag.chat.nastroje import Polozka, Vysledek
from hlidac_rag.chat.pg import IndexOsob
from hlidac_rag.chat.registr import Registr, Zdroj
from hlidac_rag.chat.relace import podepis_beta
from hlidac_rag.chat.uloziste import RelaceNeexistuje, Utrata

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def cista_pamet_turnstile():
    turnstile._vycisti_pamet()
    yield
    turnstile._vycisti_pamet()


PUVOD = "https://cosepece.cz"
EVAL_KLIC = "eval-klic-test"
BETA_KLIC = b"beta-klic-test"
MODEL = "claude-sonnet-5-5"
MALA = Usage(1000, 100, 0, 0)
_id = itertools.count(1)
_tok = itertools.count(1)


def ODP_TOOL(nazev, vstup, usage=MALA):
    return Odpoved([{"type": "tool_use", "id": f"tu_{next(_id)}", "name": nazev, "input": vstup}], "tool_use", usage,
                   MODEL, 5)


def ODP_TEXT(text, usage=MALA, stop="end_turn"):
    return Odpoved([{"type": "text", "text": text}] if text else [], stop, usage, MODEL, 5)


def U(den=0.0, hodina=0.0, relace=0.0, ip=0.0):
    return Utrata(den=den, hodina=hodina, mesic=den, relace=relace, ip=ip, zprav_min=0, zprav_den=0)


def N(**env):
    return nacti({"CHAT_ZAPNUTO": "1", "CHAT_REZIM": "verejne", **env})


TAJEMSTVI = Tajemstvi(turnstile="tajne-ts", beta_klic=BETA_KLIC, beta_kod="kod-bety", eval_klic=EVAL_KLIC,
                      telegram_token="tg-token", telegram_chat="42", turnstile_sitekey="site-key")


# ---------------------------------------------------------------- falešné části

class FalesnyLLM:
    """Připravené odpovědi; `brana` (asyncio.Event) pozdrží každé volání, dokud se nenastaví."""

    def __init__(self, odpovedi, brana: asyncio.Event | None = None):
        self.odpovedi = list(odpovedi)
        self.brana = brana
        self.volani = 0
        self.timeouty: list[float | None] = []

    async def volej(self, *, model, effort, system, tools, messages, max_tokens, na_text, tool_choice=None,
                    timeout_s=None):
        self.volani += 1
        self.timeouty.append(timeout_s)
        o = self.odpovedi.pop(0)
        if isinstance(o, BaseException):
            raise o
        if o == "visi":
            await asyncio.Event().wait()
        for b in o.content:
            if b.get("type") == "text":
                await na_text(b["text"])
        if self.brana is not None:
            await self.brana.wait()
        return o


class FalesneUloziste:
    """Úložiště v paměti se stejnými podpisy jako pg.PgUloziste (zahaj_tah drží jeden tah relace)."""

    def __init__(self, utrata: Utrata | None = None):
        self.pevna_utrata = utrata
        self.relace_: dict[str, dict] = {}
        self.konverzace_: dict[str, dict] = {}
        self.zpravy: list[dict] = []        # odpovědi bota
        self.otazky: list[dict] = []
        self.volani_llm: list[dict] = []
        self.volani_nastroje: list[dict] = []
        self.zdroje_: dict[str, dict[int, Zdroj]] = {}
        self.prubezne_: list[str] = []
        self._zamek = threading.Lock()

    def zaloz_relaci(self, id=None):
        rid = id or os.urandom(16).hex()
        if rid in self.relace_:
            return rid
        self.relace_[rid] = {"id": rid, "overeno_do": None, "zprav_od_overeni": 0, "otisk": None, "tah": None}
        return rid

    def relace(self, id):
        r = self.relace_.get(id)
        return dict(r) if r else None

    def zapis_overeni(self, rid, otisk, do):
        self.relace_[rid].update(overeno_do=do, otisk=otisk, zprav_od_overeni=0)

    def pricti_zpravu_od_overeni(self, rid):
        self.relace_[rid]["zprav_od_overeni"] += 1
        return self.relace_[rid]["zprav_od_overeni"]

    def zahaj_tah(self, rid, otisk, ted, drz_s=300):
        with self._zamek:
            if rid not in self.relace_:
                raise RelaceNeexistuje(rid)
            if self.relace_[rid]["tah"] is not None:
                return None
            token = os.urandom(8).hex()
            self.relace_[rid]["tah"] = token
        return token, self.utrata(rid, otisk, ted)

    def skonci_tah(self, rid, token):
        with self._zamek:
            if self.relace_[rid]["tah"] == token:
                self.relace_[rid]["tah"] = None
                return True
            return False

    def utrata(self, rid, otisk, ted):
        if self.pevna_utrata is not None:
            return self.pevna_utrata
        vse = sum(v["cena_usd"] for v in self.volani_llm)
        rel = sum(v["cena_usd"] for v in self.volani_llm if v["relace_id"] == rid)
        ip = sum(v["cena_usd"] for v in self.volani_llm if v["otisk_ip"] == otisk)
        return Utrata(den=vse, hodina=vse, mesic=vse, relace=rel, ip=ip, zprav_min=0, zprav_den=0)

    def zaloz_konverzaci(self, rid, eval_=False):
        kid = os.urandom(16).hex()
        self.konverzace_[kid] = {"id": kid, "relace_id": rid, "eval": eval_}
        return kid

    def konverzace(self, id, rid):
        k = self.konverzace_.get(id)
        return dict(k) if k and k["relace_id"] == rid else None

    def tahy(self, kid):
        return [Tah(o["text"], o["kontext"].get("znacka", ""), b["text"], list(b["citovane"]))
                for b in self.zpravy for o in self.otazky
                if b["konverzace_id"] == kid and b["otazka"] == o["id"] and b["stav"] in ("ok", "nemam")]

    def zaloz_odpoved(self, kid, text, kontext):
        oid = len(self.otazky) + 1
        self.otazky.append({"id": oid, "konverzace_id": kid, "text": text, "kontext": kontext})
        zid = len(self.zpravy) + 1
        kod = f"kod{zid:05d}"
        self.zpravy.append({"id": zid, "konverzace_id": kid, "otazka": oid, "text": "", "stav": "probiha",
                            "citovane": [], "kod": kod, "overeni": None, "model": None})
        return zid, kod

    def prubezne(self, zid, text):
        self.prubezne_.append(text)
        if self.zpravy[zid - 1]["stav"] == "probiha":
            self.zpravy[zid - 1]["text"] = text

    def dokonci(self, zid, text, stav, model, citovane, overeni):
        z = self.zpravy[zid - 1]
        if z["stav"] != "probiha":
            return False
        z.update(text=text, stav=stav, model=model, citovane=list(citovane), overeni=overeni)
        return True

    def zapis_volani_llm(self, **kw):
        self.volani_llm.append(kw)

    def zapis_volani_nastroje(self, **kw):
        self.volani_nastroje.append(kw)

    def uloz_zdroje(self, kid, zdroje):
        d = self.zdroje_.setdefault(kid, {})
        for z in zdroje:
            d[z.n] = replace(z, citovan=z.citovan or (d[z.n].citovan if z.n in d else False))

    def zdroje(self, kid):
        return [replace(z) for _, z in sorted(self.zdroje_.get(kid, {}).items())]

    def otisk_ip(self, ip, den):
        return "otisk-" + ip

    def posledni_kontext(self, kid):
        o = [x for x in self.otazky if x["konverzace_id"] == kid]
        return o[-1]["kontext"] if o else None

    def souhrn_dne(self, ted):
        return len(self.otazky), len(self.relace_)


class FalesnaData:
    """Čtení dat: kontext projde beze změny, karta žádná, nástroje z připravených výsledků."""

    def __init__(self, vysledky: dict | None = None, zpozdeni_s: float = 0.0):
        self.vysledky = vysledky or {}
        self.zpozdeni_s = zpozdeni_s
        self.volani: list[tuple[str, dict]] = []

    def over_kontext(self, k):
        return k

    def karta(self, k, max_tokenu):
        return None, []

    def nastroj(self, nazev, argumenty):
        self.volani.append((nazev, argumenty))
        if self.zpozdeni_s:
            time.sleep(self.zpozdeni_s)
        if nazev in self.vysledky:
            return self.vysledky[nazev]
        return Vysledek(f"Data nástroje {nazev}.", [Polozka("pn:2329", "pn", "PN 2329 (tisk 221)", None, {})], False)

    def text_zdroje(self, z):
        return None

    def jmena_osob(self, text):
        return []


class Http:
    """Falešný siteverify a Telegram (httpx MockTransport); zaznamenává požadavky."""

    def __init__(self, siteverify: dict | None = None, status: int = 200):
        self.siteverify = siteverify if siteverify is not None else {"success": True, "hostname": "cosepece.cz",
                                                                     "action": "chat"}
        self.status = status
        self.pozadavky: list[httpx.Request] = []
        self.klient = httpx.AsyncClient(transport=httpx.MockTransport(self._h))

    def _h(self, req):
        self.pozadavky.append(req)
        if req.url.host == "api.telegram.org":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(self.status, json=self.siteverify)

    def telegram(self):
        return [r for r in self.pozadavky if r.url.host == "api.telegram.org"]

    def overeni(self):
        return [r for r in self.pozadavky if r.url.host == "challenges.cloudflare.com"]


class Svet:
    def __init__(self, odpovedi=(), utrata=None, n=None, http=None, data=None, brana=None, **kw):
        self.llm = FalesnyLLM(odpovedi, brana)
        self.ulozit = FalesneUloziste(utrata)
        self.http = http or Http()
        self.data = data or FalesnaData()
        self.n = n or N()
        self.app = vytvor(self.n, self.llm, self.ulozit, self.http.klient, TAJEMSTVI, data=self.data, **kw)
        self.sluzba = self.app.state.sluzba

    async def dobehni(self, timeout=5.0):
        """Počká, až doběhnou všechny tahy na pozadí."""
        konec = time.monotonic() + timeout
        while self.sluzba._ulohy and time.monotonic() < konec:
            await asyncio.sleep(0.01)
        assert not self.sluzba._ulohy, "tah nedoběhl"


# ---------------------------------------------------------------- ovladač ASGI

class Odpoved_:
    def __init__(self):
        self.status: int | None = None
        self.hlavicky: dict[str, list[str]] = {}
        self.telo = b""
        self.zacala = asyncio.Event()
        self.zmena = asyncio.Event()

    def json(self):
        return json.loads(self.telo)

    @property
    def status_code(self):
        return self.status

    def hlavicka(self, jmeno):
        h = self.hlavicky.get(jmeno.lower())
        return h[0] if h else None

    def cookie(self, jmeno) -> str | None:
        for c in self.hlavicky.get("set-cookie", []):
            if c.startswith(jmeno + "="):
                return c
        return None


async def asgi(app, metoda, cesta, telo: bytes = b"", hlavicky: dict | None = None, odpojit=None,
               odpoved: Odpoved_ | None = None, timeout=5.0) -> Odpoved_:
    """Jeden požadavek přes ASGI. `odpojit(telo_dosud) -> bool`: True = klient zavře spojení."""
    o = odpoved or Odpoved_()
    odpoj = asyncio.Event()
    stav = {"telo": False}

    async def receive():
        if not stav["telo"]:
            stav["telo"] = True
            return {"type": "http.request", "body": telo, "more_body": False}
        await odpoj.wait()
        return {"type": "http.disconnect"}

    async def send(m):
        if m["type"] == "http.response.start":
            o.status = m["status"]
            for k, v in m.get("headers", []):
                o.hlavicky.setdefault(k.decode().lower(), []).append(v.decode())
            o.zacala.set()
        elif m["type"] == "http.response.body":
            o.telo += m.get("body", b"")
            o.zmena.set()
            if odpojit is not None and odpojit(o.telo):
                odpoj.set()

    path, _, qs = cesta.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": metoda, "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": qs.encode(), "root_path": "",
             "headers": [(k.lower().encode(), v.encode()) for k, v in (hlavicky or {}).items()],
             "client": ("127.0.0.1", 5000), "server": ("chat", 8000)}
    await asyncio.wait_for(app(scope, receive, send), timeout)
    return o


def zprava_telo(text="Prošel PN 2329?", turnstile_token="auto", konverzace_id=None, kontext=None) -> bytes:
    if turnstile_token == "auto":
        turnstile_token = f"token-{next(_tok)}"
    return json.dumps({"konverzace_id": konverzace_id, "text": text,
                       "kontext": kontext if kontext is not None else {"typ": "tisk", "ct": 221},
                       "turnstile": turnstile_token}).encode()


def hlavicky_zpravy(origin=PUVOD, ip="198.51.100.7", cookie=None, dalsi=None) -> dict:
    h = {"content-type": "application/json"}
    if origin is not None:
        h["origin"] = origin
    if ip is not None:
        h["x-forwarded-for"] = ip
    if cookie:
        h["cookie"] = cookie
    return {**h, **(dalsi or {})}


async def post(svet: Svet, telo: bytes | None = None, odpojit=None, odpoved=None, **kw) -> Odpoved_:
    hk = {k: kw.pop(k) for k in ("origin", "ip", "cookie", "dalsi") if k in kw}
    return await asgi(svet.app, "POST", "/chat-api/zprava", telo if telo is not None else zprava_telo(**kw),
                      hlavicky_zpravy(**hk), odpojit=odpojit, odpoved=odpoved)


def sse(telo: bytes) -> list[tuple[str, dict, int | None]]:
    out = []
    for blok in telo.decode().split("\n\n"):
        typ, data, i = None, None, None
        for radek in blok.split("\n"):
            if radek.startswith("event: "):
                typ = radek[7:]
            elif radek.startswith("data: "):
                data = json.loads(radek[6:])
            elif radek.startswith("id: "):
                i = int(radek[4:])
        if typ:
            out.append((typ, data, i))
    return out


def typy(telo: bytes, bez=("stav", "delta")) -> list[str]:
    return [t for t, _, _ in sse(telo) if t not in bez]


def sid_cookie(o: Odpoved_) -> str:
    return o.cookie("chat_sid").split(";")[0]


# ---------------------------------------------------------------- pořadí kontrol (žádná nesmí zavolat LLM)

async def test_cizi_origin():
    s = Svet([ODP_TEXT("x")])
    assert (await post(s, origin="https://evil.cz")).status_code == 403
    assert (await post(s, origin=None)).status_code == 403
    assert s.llm.volani == 0


async def test_dlouha_zprava():
    s = Svet([ODP_TEXT("x")])
    assert (await post(s, text="a" * 501)).status_code == 413
    assert (await post(s, telo=b'{"text": "' + b"a" * 17000 + b'"}')).status_code == 413
    assert s.llm.volani == 0 and not s.ulozit.relace_


async def test_spatne_telo_a_chybejici_ip_jsou_400():
    s = Svet([ODP_TEXT("x")])
    assert (await post(s, telo=b"{neni json")).json()["kod"] == "spatny_pozadavek"
    assert (await post(s, telo=json.dumps({"text": ""}).encode())).status_code == 400
    assert (await post(s, telo=json.dumps({"text": "a", "kontext": "x"}).encode())).status_code == 400
    r = await post(s, ip=None)
    assert (r.status_code, r.json()["kod"]) == (400, "spatny_pozadavek")
    assert (await post(s, ip="1.2.3.4, 5.6.7.8")).status_code == 400
    assert s.llm.volani == 0


async def test_bez_turnstile_je_428_pred_streamem():
    s = Svet([ODP_TEXT("x")])
    r = await post(s, turnstile_token=None)
    assert (r.status_code, r.json()["kod"]) == (428, "turnstile")
    assert s.llm.volani == 0 and not s.http.overeni()


async def test_neplatny_token_je_428():
    s = Svet([ODP_TEXT("x")], http=Http({"success": False, "error-codes": ["invalid-input-response"]}))
    r = await post(s)
    assert (r.status_code, r.json()["kod"]) == (428, "turnstile") and s.llm.volani == 0


async def test_vypadek_turnstile_je_503_a_nic_se_neplati():
    s = Svet([ODP_TEXT("x")], http=Http(status=500))
    r = await post(s)
    assert (r.status_code, r.json()["kod"]) == (503, "overeni_nedostupne")
    assert s.llm.volani == 0 and not s.ulozit.volani_llm and not s.ulozit.zpravy


async def test_limit_dne_vraci_kod_a_znovu_v():
    s = Svet([ODP_TEXT("x")], utrata=U(den=1.95))
    r = await post(s)
    assert (r.status_code, r.json()["kod"]) == (429, "limit_den") and r.json()["znovu_v"]
    assert datetime.fromisoformat(r.json()["znovu_v"]) > datetime.now(timezone.utc)
    assert s.llm.volani == 0 and not s.http.overeni()      # Turnstile až po limitech


async def test_limit_hodiny_ma_retry_after():
    s = Svet([ODP_TEXT("x")], utrata=U(den=0.5, hodina=0.45))
    r = await post(s)
    assert (r.status_code, r.json()["kod"]) == (429, "limit_hodina")
    assert int(r.hlavicka("retry-after")) == 3600 and r.json()["znovu_v"]
    assert s.llm.volani == 0


async def test_limit_relace_a_ip():
    assert (await post(Svet(utrata=U(relace=0.25)))).json()["kod"] == "limit_relace"
    assert (await post(Svet(utrata=U(ip=0.5)))).json()["kod"] == "limit_ip"


async def test_skryty_rezim_bez_cookie_je_404_krome_w_js_beta_zdravi(tmp_path):
    (tmp_path / "w.js").write_text("// __CHAT_HASH__", encoding="utf-8")
    s = Svet([ODP_TEXT("Ano.")], n=N(CHAT_REZIM="skryte"), static_dir=tmp_path)
    assert (await post(s)).status_code == 404
    assert (await asgi(s.app, "GET", "/chat-api/stav")).status_code == 404
    assert (await asgi(s.app, "GET", "/chat-api/tah/x/stream")).status_code == 404
    assert (await asgi(s.app, "GET", f"/chat-api/s/{s.sluzba.hash_static}/w.js")).status_code == 404
    assert (await asgi(s.app, "GET", "/chat-api/zdravi")).json() == {"ok": True}
    assert (await asgi(s.app, "GET", "/chat-api/w.js")).status_code == 200
    assert (await asgi(s.app, "POST", "/chat-api/beta", b'{"kod": "spatne"}', {"origin": PUVOD})).status_code == 404
    beta = f"chat_beta={podepis_beta(BETA_KLIC)}"
    assert (await asgi(s.app, "GET", "/chat-api/stav", hlavicky={"cookie": beta,
                                                                  "x-forwarded-for": "198.51.100.7"})).status_code == 200
    assert (await asgi(s.app, "GET", "/chat-api/stav", hlavicky={"cookie": "chat_beta=v1.9999999999." + "0" * 64})
            ).status_code == 404
    r = await post(s, cookie=beta)
    assert r.status_code == 200 and "konec" in typy(r.telo)
    assert s.llm.volani == 1


async def test_beta_spatny_kod_404_spravny_nastavi_cookie():
    s = Svet(n=N(CHAT_REZIM="skryte"))
    assert (await asgi(s.app, "POST", "/chat-api/beta", b'{"kod": "spatne"}', {"origin": PUVOD})).status_code == 404
    assert (await asgi(s.app, "POST", "/chat-api/beta", b'{"kod": 1}', {"origin": PUVOD})).status_code == 404
    assert (await asgi(s.app, "POST", "/chat-api/beta", b"x", {"origin": PUVOD})).status_code == 404
    assert (await asgi(s.app, "POST", "/chat-api/beta", b'{"kod": "kod-bety"}', {"origin": "https://evil.cz"})
            ).status_code == 404
    assert (await asgi(s.app, "POST", "/chat-api/beta", b'{"kod": "kod-bety"}')).status_code == 404
    assert (await asgi(s.app, "GET", "/chat-api/beta", hlavicky={"origin": PUVOD})).status_code == 404
    r = await asgi(s.app, "POST", "/chat-api/beta", b'{"kod": "kod-bety"}', {"origin": PUVOD})
    c = r.cookie("chat_beta")
    assert r.status_code == 204 and c and "Path=/;" in c and "HttpOnly" in c and "SameSite=Strict" in c
    assert "Max-Age=2592000" in c
    r = await asgi(s.app, "GET", "/chat-api/stav", hlavicky={"cookie": c.split(";")[0], "x-forwarded-for": "1.2.3.4"})
    assert r.status_code == 200


async def test_vypnuto_je_503_a_stav_rika_vypnuto():
    s = Svet([ODP_TEXT("x")], n=nacti({"CHAT_REZIM": "verejne"}))
    r = await post(s)
    assert (r.status_code, r.json()["kod"]) == (503, "vypnuto") and s.llm.volani == 0
    st = (await asgi(s.app, "GET", "/chat-api/stav", hlavicky={"x-forwarded-for": "1.2.3.4"})).json()
    assert st["dostupny"] is False and st["kod"] == "vypnuto"


async def test_stav_bez_limitu_a_po_limitu():
    s = Svet()
    st = (await asgi(s.app, "GET", "/chat-api/stav", hlavicky={"x-forwarded-for": "1.2.3.4"})).json()
    assert st == {"dostupny": True, "kod": None, "zbyva_otazek": 6, "znovu_v": None, "turnstile_sitekey": "site-key"}
    s = Svet(utrata=U(den=1.95))
    st = (await asgi(s.app, "GET", "/chat-api/stav")).json()      # bez IP: stav jde zjistit i tak
    assert st["dostupny"] is False and st["kod"] == "limit_den" and st["znovu_v"]
    assert not s.ulozit.relace_                                     # stav relaci nezakládá


async def test_eval_klic_obchazi_jen_turnstile():      # limity dál platí
    s = Svet([ODP_TEXT("Ano.")])
    r = await post(s, turnstile_token=None, dalsi={"X-Chat-Eval": EVAL_KLIC})
    assert r.status_code == 200 and not s.http.overeni()
    assert list(s.ulozit.konverzace_.values())[0]["eval"] is True
    r = await post(Svet(utrata=U(den=1.95)), turnstile_token=None, dalsi={"X-Chat-Eval": EVAL_KLIC})
    assert r.status_code == 429
    r = await post(Svet(), turnstile_token=None, dalsi={"X-Chat-Eval": "spatny"})
    assert r.status_code == 428


# ---------------------------------------------------------------- tah

async def test_cely_tah_ulozi_vse():
    s = Svet([ODP_TOOL("get_pn", {"cislo": 2329}), ODP_TEXT("Byl zamítnut [1].")])
    r = await post(s)
    await s.dobehni()
    ud = sse(r.telo)
    assert r.status_code == 200 and r.hlavicka("content-type").startswith("text/event-stream")
    assert [t for t, _, _ in ud if t not in ("stav", "delta")] == ["start", "zdroje", "konec"]
    assert [i for _, _, i in ud] == list(range(len(ud)))
    assert len(s.ulozit.volani_llm) == 2 and s.llm.volani == 2
    z = s.ulozit.zpravy[-1]
    assert z["stav"] == "ok" and z["citovane"] == [1] and z["text"] == "Byl zamítnut [1]." and z["model"] == MODEL
    assert "".join(d["text"] for t, d, _ in ud if t == "delta") == "Byl zamítnut [1]."
    start = ud[0][1]
    assert set(start) == {"konverzace_id", "zprava_id", "tah_id"} and start["zprava_id"] == z["id"]
    zdroje = next(d for t, d, _ in ud if t == "zdroje")["zdroje"]
    assert zdroje == [{"n": 1, "typ": "pn", "titulek": "PN 2329 (tisk 221)", "znacky": []}]
    konec = ud[-1][1]
    assert konec["kod"] == z["kod"] and konec["stav"] == "ok" and isinstance(konec["zbyva_otazek"], int)
    assert s.ulozit.volani_nastroje[0]["nastroj"] == "get_pn"
    assert s.ulozit.zdroje(start["konverzace_id"])[0].citovan is True
    rid = next(iter(s.ulozit.relace_))
    assert s.ulozit.relace_[rid]["tah"] is None and not s.sluzba.bezici_relace and s.sluzba.soubeh == 0
    otazka = s.ulozit.otazky[-1]
    assert otazka["kontext"]["typ"] == "tisk" and otazka["kontext"]["znacka"].startswith("[stránka: tisk 221")


async def test_odstavec_s_citatem_jde_ven_az_po_overeni():
    projev = Polozka("projev:10/1/2/3", "projev", "Jan Novák (ANO), 1. 2. 2026",
                     "Tento zákon přinese lidem levnější energie a nižší účty.", {"recnik": "Jan Novák", "klub": "ANO"})
    s = Svet([ODP_TOOL("get_projev", {"klic": "10/1/2/3"}),
              ODP_TEXT("Úvodní věta bez citace.\n\nV projevu zaznělo „přinese lidem levnější energie“ [1].\n\nKonec.")],
             data=FalesnaData({"get_projev": Vysledek("Projev.", [projev], False)}))
    r = await post(s)
    await s.dobehni()
    ud = sse(r.telo)
    i_overeni = next(i for i, (t, _, _) in enumerate(ud) if t == "overeni")
    s_citatem = [i for i, (t, d, _) in enumerate(ud) if t == "delta" and "„" in d["text"]]
    bez = [i for i, (t, d, _) in enumerate(ud) if t == "delta" and "Úvodní" in d["text"]]
    assert s_citatem and min(s_citatem) > i_overeni and max(bez) < i_overeni
    citat = ud[i_overeni][1]["citaty"][0]
    assert citat["n"] == 1 and citat["shoda"] == "presne" and citat["zaznelo_v"]["klic"] == "projev:10/1/2/3"
    text = "".join(d["text"] for t, d, _ in ud if t == "delta")
    assert text == s.ulozit.zpravy[-1]["text"] and text.endswith("Konec.") and "\n\nV projevu" in text
    assert s.ulozit.zpravy[-1]["overeni"]["citaty"][0]["shoda"] == "presne"


async def test_vymyslena_citace_jde_ven_bez_uvozovek():
    projev = Polozka("projev:10/1/2/3", "projev", "Jan Novák (ANO), 1. 2. 2026", "Úplně jiný text projevu o rozpočtu.",
                     {"recnik": "Jan Novák", "klub": "ANO"})
    s = Svet([ODP_TOOL("get_projev", {"klic": "10/1/2/3"}),
              ODP_TEXT("V projevu zaznělo „zrušíme všechny daně do konce roku“ [1].")],
             data=FalesnaData({"get_projev": Vysledek("Projev.", [projev], False)}))
    r = await post(s)
    await s.dobehni()
    text = "".join(d["text"] for t, d, _ in sse(r.telo) if t == "delta")
    assert "„" not in text and "(neověřeno ve zdroji)" in text


async def test_vymyslene_n_se_odstrani():
    s = Svet([ODP_TOOL("get_pn", {"cislo": 2329}), ODP_TEXT("Zamítnut [1], viz také [7] a [12].")])
    r = await post(s)
    await s.dobehni()
    text = "".join(d["text"] for t, d, _ in sse(r.telo) if t == "delta")
    assert text == "Zamítnut [1], viz také a." == s.ulozit.zpravy[-1]["text"]
    assert s.ulozit.zpravy[-1]["citovane"] == [1] and s.ulozit.zpravy[-1]["overeni"]["odstranenych_odkazu"] == 2


async def test_cookie_sid_vznikne_prvni_zpravou():
    s = Svet([ODP_TEXT("Ano."), ODP_TEXT("Ano znovu.")])
    r = await post(s)
    await s.dobehni()
    c = r.cookie("chat_sid")
    assert c and "Path=/chat-api" in c and "HttpOnly" in c and "Secure" in c and "SameSite=Strict" in c
    rid = sid_cookie(r).split("=")[1]
    assert rid in s.ulozit.relace_
    r2 = await post(s, cookie=sid_cookie(r))
    await s.dobehni()
    assert sid_cookie(r2) == sid_cookie(r) and len(s.ulozit.relace_) == 1
    # druhá zpráva téže relace v ověřeném okně Turnstile znovu neověřuje
    assert len(s.http.overeni()) == 1


async def test_428_nastavi_cookie_a_opakovani_s_tokenem_projde():
    s = Svet([ODP_TEXT("Ano.")])
    r = await post(s, turnstile_token=None)
    assert r.status_code == 428 and r.cookie("chat_sid")
    assert not s.ulozit.relace_                       # bez Turnstile žádný řádek relace
    assert (await post(s, turnstile_token=None, cookie=sid_cookie(r))).status_code == 428
    assert not s.ulozit.relace_
    r2 = await post(s, cookie=sid_cookie(r))
    await s.dobehni()
    assert r2.status_code == 200 and list(s.ulozit.relace_) == [sid_cookie(r).split("=")[1]]
    assert sid_cookie(r2) == sid_cookie(r)


async def test_podstrcene_id_relace_se_nepouzije():
    s = Svet([ODP_TEXT("Ano.")])
    podstrcene = "ab" * 16
    r = await post(s, cookie=f"chat_sid={podstrcene}")
    await s.dobehni()
    rid = sid_cookie(r).split("=")[1]
    assert r.status_code == 200 and rid != podstrcene and list(s.ulozit.relace_) == [rid]
    # id vydané jiným procesem (jiný klíč) taky ne
    r = await post(Svet([ODP_TEXT("Ano.")]), cookie=sid_cookie(r), turnstile_token=None)
    assert sid_cookie(r).split("=")[1] != rid


async def test_cizi_konverzace_je_400():
    s = Svet([ODP_TEXT("Ano.")])
    cizi = s.ulozit.zaloz_konverzaci(s.ulozit.zaloz_relaci())
    r = await post(s, konverzace_id=cizi)
    assert (r.status_code, r.json()["kod"]) == (400, "spatny_pozadavek") and s.llm.volani == 0
    assert all(v["tah"] is None for v in s.ulozit.relace_.values()) and s.sluzba.soubeh == 0
    assert (await post(s, konverzace_id="neexistuje")).status_code == 400


async def test_sedmy_tah_konverzace_je_409_limit_konverzace():
    s = Svet([ODP_TEXT("Odpověď.")])
    r = await post(s)
    await s.dobehni()
    kid = sse(r.telo)[0][1]["konverzace_id"]
    for i in range(5):
        zid, _ = s.ulozit.zaloz_odpoved(kid, f"otázka {i}", {"znacka": ""})
        s.ulozit.dokonci(zid, "odpověď", "ok", MODEL, [], {})
    r = await post(s, cookie=sid_cookie(r), konverzace_id=kid)
    assert (r.status_code, r.json()["kod"]) == (409, "limit_konverzace") and s.llm.volani == 1
    assert s.sluzba.soubeh == 0 and all(v["tah"] is None for v in s.ulozit.relace_.values())


async def test_druha_zprava_teze_relace_behem_tahu_je_409():     # Review Focus 2
    brana = asyncio.Event()
    s = Svet([ODP_TEXT("Nultá."), ODP_TEXT("První."), ODP_TEXT("Druhá.")], brana=brana)
    brana.set()
    r0 = await post(s)                         # relace a ověřené okno Turnstile
    await s.dobehni()
    brana.clear()
    cookie = sid_cookie(r0)
    prvni = Odpoved_()
    uloha = asyncio.ensure_future(post(s, cookie=cookie, odpoved=prvni))
    while b"event: start" not in prvni.telo:
        await asyncio.wait_for(prvni.zmena.wait(), 2)
        prvni.zmena.clear()
    rid = cookie.split("=")[1]
    zprav = s.ulozit.relace_[rid]["zprav_od_overeni"]
    druha = await post(s, cookie=cookie)
    assert (druha.status_code, druha.json()["kod"]) == (409, "tah_bezi")
    assert s.ulozit.relace_[rid]["zprav_od_overeni"] == zprav      # odmítnutá zpráva se do Turnstile nepočítá
    # zámek v DB platí i bez čítače v procesu (víc procesů): druhý zahaj_tah dostane None
    assert s.ulozit.zahaj_tah(rid, "o", datetime.now(timezone.utc)) is None
    brana.set()
    await uloha
    await s.dobehni()
    assert typy(prvni.telo)[-1] == "konec" and s.llm.volani == 2
    assert s.sluzba.soubeh == 0 and s.ulozit.relace_[rid]["tah"] is None and len(s.ulozit.zpravy) == 2


async def test_dve_relace_z_jedne_ip_soubeh_ip_je_503():
    brana = asyncio.Event()
    s = Svet([ODP_TEXT("První."), ODP_TEXT("Druhá.")], brana=brana)
    prvni = Odpoved_()
    uloha = asyncio.ensure_future(post(s, odpoved=prvni))
    while b"event: start" not in prvni.telo:
        await asyncio.wait_for(prvni.zmena.wait(), 2)
        prvni.zmena.clear()
    druha = await post(s)                      # nová relace, stejná IP, CHAT_SOUBEH_IP = 1
    assert (druha.status_code, druha.json()["kod"]) == (503, "obsazeno") and druha.hlavicka("retry-after")
    brana.set()
    await uloha
    await s.dobehni()
    assert s.llm.volani == 1 and s.sluzba.soubeh == 0 and not s.sluzba.soubeh_ip


async def test_soubeh_celkem_vraci_503_obsazeno():
    brana = asyncio.Event()
    s = Svet([ODP_TEXT("První."), ODP_TEXT("Druhá.")], brana=brana, n=N(CHAT_SOUBEH_CELKEM="1"))
    prvni = Odpoved_()
    uloha = asyncio.ensure_future(post(s, odpoved=prvni, ip="198.51.100.1"))
    while b"event: start" not in prvni.telo:
        await asyncio.wait_for(prvni.zmena.wait(), 2)
        prvni.zmena.clear()
    druha = await post(s, ip="203.0.113.9")
    assert (druha.status_code, druha.json()["kod"]) == (503, "obsazeno")
    brana.set()
    await uloha
    await s.dobehni()
    treti = await post(s, ip="203.0.113.9")    # po konci tahu je místo znovu volné
    await s.dobehni()
    assert treti.status_code == 200 and s.llm.volani == 2


async def test_odpojeny_klient_ukonci_tah_a_zapise_cenu():       # Review Focus 3
    s = Svet([ODP_TOOL("get_pn", {"cislo": 2329}), ODP_TEXT("Nemělo by se zavolat.")],
             data=FalesnaData(zpozdeni_s=0.3), odpojeni_s=0.05)
    r = await post(s, odpojit=lambda telo: b"event: start" in telo)
    assert r.status_code == 200 and b"event: konec" not in r.telo
    await s.dobehni()
    z = s.ulozit.zpravy[-1]
    assert z["stav"] == "preruseno" and s.llm.volani == 1
    assert len(s.ulozit.volani_llm) == 1 and s.ulozit.volani_llm[0]["cena_usd"] > 0
    assert len(s.ulozit.volani_nastroje) == 1          # rozběhnuté kolo doběhlo
    assert all(v["tah"] is None for v in s.ulozit.relace_.values()) and s.sluzba.soubeh == 0


async def test_odpojeny_klient_ktery_se_vrati_tah_nepreruší():
    brana = asyncio.Event()
    s = Svet([ODP_TEXT("Celá odpověď.")], brana=brana, odpojeni_s=0.2)
    r = await post(s, odpojit=lambda telo: b"event: start" in telo)
    tah_id = sse(r.telo)[0][1]["tah_id"]
    druha = Odpoved_()
    uloha = asyncio.ensure_future(asgi(s.app, "GET", f"/chat-api/tah/{tah_id}/stream?od=1",
                                       hlavicky={"cookie": sid_cookie(r)}, odpoved=druha))
    await asyncio.sleep(0.4)        # déle než odpojeni_s: připojený posluchač časovač zrušil
    brana.set()
    await uloha
    await s.dobehni()
    assert s.ulozit.zpravy[-1]["stav"] == "ok"


async def test_znovupripojeni_dostane_udalosti_od_offsetu():
    brana = asyncio.Event()
    s = Svet([ODP_TEXT("Odpověď po kouscích.")], brana=brana, odpojeni_s=5)
    r = await post(s, odpojit=lambda telo: b"event: delta" in telo)
    prvni = sse(r.telo)
    tah_id = prvni[0][1]["tah_id"]
    od = prvni[-1][2] + 1
    cizi = await asgi(s.app, "GET", f"/chat-api/tah/{tah_id}/stream?od={od}",
                      hlavicky={"cookie": "chat_sid=" + "a" * 32})
    assert cizi.status_code == 404
    druha = Odpoved_()
    uloha = asyncio.ensure_future(asgi(s.app, "GET", f"/chat-api/tah/{tah_id}/stream?od={od}",
                                       hlavicky={"cookie": sid_cookie(r)}, odpoved=druha))
    await asyncio.sleep(0.05)
    brana.set()
    await uloha
    await s.dobehni()
    ud = sse(druha.telo)
    assert ud[0][2] == od and ud[-1][0] == "konec" and "start" not in [t for t, _, _ in ud]
    # po konci tahu se dá připojit znovu od začátku (události drží proces DRZENI_UDALOSTI_S)
    cela = await asgi(s.app, "GET", f"/chat-api/tah/{tah_id}/stream?od=0", hlavicky={"cookie": sid_cookie(r)})
    assert [i for _, _, i in sse(cela.telo)] == list(range(len(sse(cela.telo))))
    assert (await asgi(s.app, "GET", "/chat-api/tah/neni/stream", hlavicky={"cookie": sid_cookie(r)})
            ).status_code == 404


async def test_udalosti_po_drzeni_zmizi():
    s = Svet([ODP_TEXT("Ano.")], drzeni_udalosti_s=0.05)
    r = await post(s)
    await s.dobehni()
    tah_id = sse(r.telo)[0][1]["tah_id"]
    await asyncio.sleep(0.1)
    assert tah_id not in s.sluzba.tahy


async def test_stop_ukonci_tah():
    brana = asyncio.Event()
    s = Svet([ODP_TOOL("get_pn", {"cislo": 2329}), ODP_TEXT("x")], brana=brana)
    prvni = Odpoved_()
    uloha = asyncio.ensure_future(post(s, odpoved=prvni))
    while b"event: start" not in prvni.telo:
        await asyncio.wait_for(prvni.zmena.wait(), 2)
        prvni.zmena.clear()
    tah_id = sse(prvni.telo)[0][1]["tah_id"]
    cookie = sid_cookie(prvni)
    assert (await asgi(s.app, "POST", f"/chat-api/tah/{tah_id}/stop", hlavicky={"cookie": cookie})).status_code == 403
    r = await asgi(s.app, "POST", f"/chat-api/tah/{tah_id}/stop", hlavicky={"cookie": cookie, "origin": PUVOD})
    assert r.status_code == 204
    brana.set()
    await uloha
    await s.dobehni()
    assert s.ulozit.zpravy[-1]["stav"] == "preruseno" and s.llm.volani == 1


async def test_ping_kazdych_n_sekund():
    brana = asyncio.Event()
    s = Svet([ODP_TEXT("Ano.")], brana=brana, ping_s=0.02)
    uloha = asyncio.ensure_future(post(s))
    await asyncio.sleep(0.15)
    brana.set()
    r = await uloha
    await s.dobehni()
    assert b": ping\n\n" in r.telo and typy(r.telo)[-1] == "konec"


async def test_refusal_je_systemova_hlaska_ne_text_bota():
    s = Svet([ODP_TEXT("Začínám odpovídat", stop="refusal")])
    r = await post(s)
    await s.dobehni()
    ud = sse(r.telo)
    assert ("chyba", {"kod": "odmitnuto"}) in [(t, d) for t, d, _ in ud]
    assert "zdroje" not in [t for t, _, _ in ud] and ud[-1][1]["stav"] == "odmitnuto"
    z = s.ulozit.zpravy[-1]
    assert z["stav"] == "odmitnuto" and z["text"] == "" and len(s.ulozit.volani_llm) == 1


async def test_refusal_po_nekolika_deltach_stahne_cely_text():
    class PoKouscich(FalesnyLLM):
        async def volej(self, *, na_text, **kw):
            self.volani += 1
            for kus in ("Tohle je první odstavec odpovědi", " s dalším textem.\n\n", "Druhý odstavec", " pokračuje"):
                await na_text(kus)
            return Odpoved([{"type": "text", "text": "…"}], "refusal", MALA, MODEL, 5)
    s = Svet()
    s.llm.__class__ = PoKouscich
    r = await post(s)
    await s.dobehni()
    ud = sse(r.telo)
    delty = [i for i, (t, _, _) in enumerate(ud) if t == "delta"]
    chyba = next(i for i, (t, d, _) in enumerate(ud) if t == "chyba")
    assert len(delty) >= 2 and max(delty) < chyba and ud[chyba][1] == {"kod": "odmitnuto"}
    assert "".join(d["text"] for t, d, _ in ud if t == "delta").startswith("Tohle je první odstavec odpovědi")
    assert ud[-1][0] == "konec" and ud[-1][1]["stav"] == "odmitnuto" and "zdroje" not in [t for t, _, _ in ud]
    z = s.ulozit.zpravy[-1]
    assert z["stav"] == "odmitnuto" and z["text"] == ""


async def test_chyba_uloziste_pri_priprave_uvolni_relaci_a_soubeh():      # recenze bod 1
    s = Svet([ODP_TEXT("Ano."), ODP_TEXT("Ano.")])

    def pada(*a, **kw):
        raise RuntimeError("DB nedostupná")
    puvodni_odpoved, puvodni_konec = s.ulozit.zaloz_odpoved, s.ulozit.skonci_tah
    s.ulozit.zaloz_odpoved = pada
    s.ulozit.skonci_tah = pada
    for _ in range(3):      # i opakovaně: čítače se nesmí nasčítat (CHAT_SOUBEH_CELKEM = 2)
        with pytest.raises(RuntimeError):
            await post(s, ip=f"198.51.100.{next(_tok) % 200}")
        assert s.sluzba.soubeh == 0 and not s.sluzba.bezici_relace and not s.sluzba.soubeh_ip
    assert s.llm.volani == 0
    s.ulozit.zaloz_odpoved, s.ulozit.skonci_tah = puvodni_odpoved, puvodni_konec
    for r in s.ulozit.relace_.values():
        r["tah"] = None          # příznak by v DB vypršel po drzeni_tahu_s
    r = await post(s, ip="203.0.113.50")
    await s.dobehni()
    assert r.status_code == 200 and s.ulozit.zpravy[-1]["stav"] == "ok"


async def test_timeout_volani_ze_zbyvajiciho_casu_a_odhad_ceny():           # recenze bod 2
    from hlidac_rag.chat.llm import CasVyprsel
    s = Svet([ODP_TOOL("get_pn", {"cislo": 2329}), CasVyprsel("časový limit")])
    r = await post(s)
    await s.dobehni()
    t1, t2 = s.llm.timeouty
    assert 30 <= t1 <= (75 - 5) / 2 and t2 <= t1          # (LIMIT_TAHU_S − rezerva) / (1 + MAX_OPAKOVANI)
    assert len(s.ulozit.volani_llm) == 2
    odhad = s.ulozit.volani_llm[-1]
    assert odhad["stop_reason"] == "odhad_timeout" and odhad["vystup"] == s.n.max_tokenu_odpovedi
    assert odhad["vstup"] >= MALA.vstup + MALA.vystup and odhad["cena_usd"] > s.ulozit.volani_llm[0]["cena_usd"]
    assert s.ulozit.zpravy[-1]["stav"] == "chyba" and ("chyba", {"kod": "chyba"}) in [(t, d) for t, d, _ in sse(r.telo)]
    assert all(v["tah"] is None for v in s.ulozit.relace_.values()) and s.sluzba.soubeh == 0


async def test_tvrdy_limit_tahu_zapise_odhad_zruseneho_volani(monkeypatch):
    from hlidac_rag.chat import app as app_mod
    monkeypatch.setattr(app_mod, "LIMIT_TAHU_S", 0.2)
    s = Svet(["visi"])
    r = await post(s)
    await s.dobehni()
    assert [v["stop_reason"] for v in s.ulozit.volani_llm] == ["odhad_timeout"]
    assert s.ulozit.volani_llm[0]["cena_usd"] > 0 and s.ulozit.zpravy[-1]["stav"] == "chyba"
    assert s.llm.timeouty == [1.0]          # zbývající čas pod rezervou: nejmenší timeout
    assert typy(r.telo)[-1] == "konec" and s.sluzba.soubeh == 0


async def test_anthropic_llm_posila_timeout_a_cas_vyprsel():
    import anthropic

    from hlidac_rag.chat.llm import MAX_OPAKOVANI, AnthropicLLM, CasVyprsel
    assert MAX_OPAKOVANI <= 1 and AnthropicLLM.z_prostredi.__func__ is not None
    pouzite = {}

    class Klient:
        def with_options(self, **kw):
            pouzite.update(kw)
            return self

        @property
        def beta(self):
            raise anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))

    async def na_text(t):
        pass
    with pytest.raises(CasVyprsel):
        await AnthropicLLM(Klient()).volej(model=MODEL, effort="low", system="s", tools=[], messages=[
            {"role": "user", "content": "x"}], max_tokens=10, na_text=na_text, timeout_s=12.5)
    assert pouzite == {"timeout": 12.5}


async def test_chyba_modelu_je_udalost_chyba_a_cena_zustane():
    class Padajici(FalesnyLLM):
        async def volej(self, **kw):
            self.volani += 1
            raise RuntimeError("API 400")
    s = Svet()
    s.llm.__class__ = Padajici
    r = await post(s)
    await s.dobehni()
    assert ("chyba", {"kod": "chyba"}) in [(t, d) for t, d, _ in sse(r.telo)]
    assert s.ulozit.zpravy[-1]["stav"] == "chyba" and s.sluzba.soubeh == 0


async def test_telegram_jednou_pri_100_procentech():
    s = Svet(utrata=U(den=1.95))
    assert (await post(s)).status_code == 429
    assert (await post(s)).status_code == 429
    await s.dobehni()
    tg = s.http.telegram()
    assert len(tg) == 1 and "vyčerpaný" in json.loads(tg[0].content)["text"]
    assert json.loads(tg[0].content)["chat_id"] == "42"


async def test_telegram_pri_60_procentech_po_tahu():
    s = Svet([ODP_TEXT("Ano."), ODP_TEXT("Ano.")], utrata=U(den=1.3))
    await post(s)
    await s.dobehni()
    await post(s)
    await s.dobehni()
    tg = s.http.telegram()
    assert len(tg) == 1 and "60 %" in json.loads(tg[0].content)["text"]


async def test_w_js_se_servi_s_cache_300_a_chat_js_immutable(tmp_path):
    (tmp_path / "w.js").write_text("import('/chat-api/s/__CHAT_HASH__/chat.mjs')", encoding="utf-8")
    (tmp_path / "chat.mjs").write_text("export const x = 1;", encoding="utf-8")
    s = Svet(static_dir=tmp_path)
    h = s.sluzba.hash_static
    w = await asgi(s.app, "GET", "/chat-api/w.js")
    assert w.status_code == 200 and w.hlavicka("cache-control") == "public, max-age=300"
    assert w.telo.decode() == f"import('/chat-api/s/{h}/chat.mjs')"
    assert w.hlavicka("content-type").startswith("text/javascript")
    c = await asgi(s.app, "GET", f"/chat-api/s/{h}/chat.mjs")
    assert c.status_code == 200 and "immutable" in c.hlavicka("cache-control") and c.telo == b"export const x = 1;"
    assert (await asgi(s.app, "GET", "/chat-api/s/starahash/chat.mjs")).status_code == 404
    assert (await asgi(s.app, "GET", f"/chat-api/s/{h}/../test_chat_app.py")).status_code == 404
    assert (await asgi(s.app, "GET", f"/chat-api/s/{h}/neni.js")).status_code == 404
    # změna obsahu = nový hash (po redeployi se stáhne nový widget)
    (tmp_path / "chat.mjs").write_text("export const x = 2;", encoding="utf-8")
    assert Svet(static_dir=tmp_path).sluzba.hash_static != h


async def test_zdravi():
    s = Svet()
    assert (await asgi(s.app, "GET", "/chat-api/zdravi")).json() == {"ok": True}


def test_tajemstvi_z_prostredi_a_bez_repr():
    t = Tajemstvi.z_prostredi({"CHAT_TURNSTILE_SECRET": " a ", "CHAT_BETA_KLIC": "k", "CHAT_BETA_KOD": "b",
                               "CHAT_EVAL_KLIC": "e", "CHAT_TELEGRAM_TOKEN": "t", "CHAT_TELEGRAM_CHAT": "c",
                               "CHAT_TURNSTILE_SITEKEY": "s"})
    assert (t.turnstile, t.beta_klic, t.beta_kod, t.eval_klic, t.telegram_token, t.telegram_chat,
            t.turnstile_sitekey) == ("a", b"k", "b", "e", "t", "c", "s")
    assert "a" not in repr(t).replace("Tajemstvi", "")


# ---------------------------------------------------------------- výstup po odstavcích a jména osob

async def _projdi(text: str, kusy: int, registr: Registr | None = None):
    registr = registr or Registr([Zdroj(1, "pn:1", "pn", "PN 1")])
    overeno = []

    async def over(odst):
        overeno.append(odst)
        return Overeni(odst.replace("„", "").replace("“", ""), [{"n": 1, "shoda": "presne"}])

    v = Vystup(registr, over)
    out = []
    krok = max(1, len(text) // kusy)
    for i in range(0, len(text), krok):
        out += await v.pridej(text[i:i + krok])
    out += await v.konec()
    return v, out, overeno


@pytest.mark.parametrize("kusy", [1, 3, 17, 200])
async def test_vystup_skladani_po_kouscich(kusy):
    text = "První odstavec [1] a [9].\n\nDruhý „citát“ [1].\n\n> blok\n\nTřetí [1"
    v, out, overeno = await _projdi(text, kusy)
    delty = "".join(d["text"] for t, d in out if t == "delta")
    # rozepsané „[1“ se drží jen během streamu; na konci je text hotový a jde ven celý
    assert delty == v.text == "První odstavec [1] a.\n\nDruhý citát [1].\n\n> blok\n\nTřetí [1"
    assert overeno == ["Druhý „citát“ [1].", "> blok"]
    poradi = [(t, d.get("text", "")) for t, d in out]
    i = next(k for k, (t, _) in enumerate(poradi) if t == "overeni")
    assert all("citát" not in txt for _, txt in poradi[:i])


def test_index_osob_sklonovani_a_krestni_jmeno():
    ix = IndexOsob([("Jan", "Hrnčíř"), ("Alena", "Schillerová"), ("Petr", "Malý"), ("Karel", "Novák"),
                    ("Jan", "Novák"), (None, None), ("Eva", "Pivoňka Vaňková")])
    assert ix.najdi("Citovala poslance Hrnčíře.") == ["Jan Hrnčíř"]
    assert ix.najdi("Podle Schillerové a Malého") == ["Alena Schillerová", "Petr Malý"]
    assert ix.najdi("hrnčíře malými písmeny") == []
    assert ix.najdi("Novákovi") == ["Karel Novák", "Jan Novák"]
    assert ix.najdi("Jan Novák řekl") == ["Jan Novák"]
    assert ix.najdi("Řekla to Vaňková.") == ["Eva Pivoňka Vaňková"]


# ---------------------------------------------------------------- s opravdovým úložištěm (PostgreSQL)

@pytest.fixture
def pg_uloziste():
    from psycopg_pool import ConnectionPool

    from hlidac_rag import db
    from hlidac_rag.chat.pg import PgUloziste

    c = db.connect()
    db.migrate(c)
    c.close()
    pool = ConnectionPool(db.dsn(), min_size=1, max_size=4, kwargs={"autocommit": True}, open=True)
    u = PgUloziste(pool)
    yield u
    with pool.connection() as c:
        rel = [r[0] for r in c.execute("SELECT DISTINCT relace_id FROM chat.volani_llm WHERE otisk_ip = 'pgtest'")]
        rel += [r[0] for r in c.execute("SELECT id FROM chat.relace WHERE otisk_ip = 'pgtest'")]
        for rid in set(rel):
            kody = [r[0] for r in c.execute("""SELECT z.kod FROM chat.zprava z JOIN chat.konverzace k
                                               ON k.id = z.konverzace_id WHERE k.relace_id = %s AND z.kod IS NOT NULL""",
                                            (rid,))]
            c.execute("DELETE FROM chat.volani_llm WHERE relace_id = %s", (rid,))
            c.execute("DELETE FROM chat.zprava_log WHERE relace_id = %s", (rid,))
            c.execute("DELETE FROM chat.relace WHERE id = %s", (rid,))
            c.execute("DELETE FROM chat.otisk_odpovedi WHERE kod = ANY(%s)", (kody,))
    pool.close()


class PgSvet(Svet):
    def __init__(self, uloziste, odpovedi, **kw):
        self.llm = FalesnyLLM(odpovedi)
        self.ulozit = _PevnyOtisk(uloziste)
        self.http = Http()
        self.data = kw.pop("data", FalesnaData())
        self.n = N(CHAT_ROZPOCET_DEN_USD="100000", CHAT_ROZPOCET_MESIC_USD="1000000", CHAT_PODIL_HODINA="1")
        self.app = vytvor(self.n, self.llm, self.ulozit, self.http.klient, TAJEMSTVI, data=self.data, **kw)
        self.sluzba = self.app.state.sluzba


class _PevnyOtisk:
    """Opravdové úložiště, jen otisk IP je pevný (úklid testu ho najde)."""

    def __init__(self, u):
        self._u = u

    def __getattr__(self, jmeno):
        return getattr(self._u, jmeno)

    def otisk_ip(self, ip, den):
        return "pgtest"


@pytest.mark.pg
@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)")
async def test_pg_cely_tah_ulozi_zpravu_ceny_a_uvolni_tah(pg_uloziste):
    s = PgSvet(pg_uloziste, [ODP_TOOL("get_pn", {"cislo": 2329}), ODP_TEXT("Byl zamítnut [1].")])
    r = await post(s)
    await s.dobehni()
    assert typy(r.telo) == ["start", "zdroje", "konec"]
    start = sse(r.telo)[0][1]
    pool = pg_uloziste._pool
    with pool.connection() as c:
        stav, text, citovane = c.execute("SELECT stav, text, citovane FROM chat.zprava WHERE id = %s",
                                         (start["zprava_id"],)).fetchone()
        ceny = c.execute("SELECT count(*), sum(cena_usd) FROM chat.volani_llm WHERE zprava_id = %s",
                         (start["zprava_id"],)).fetchone()
        rid = c.execute("SELECT relace_id FROM chat.konverzace WHERE id = %s", (start["konverzace_id"],)).fetchone()[0]
        tah = c.execute("SELECT tah_od, tah_token, otisk_ip FROM chat.relace WHERE id = %s", (rid,)).fetchone()
        zdroj = c.execute("SELECT klic, citovan FROM chat.zdroj WHERE konverzace_id = %s", (start["konverzace_id"],)
                          ).fetchall()
    assert (stav, text, citovane) == ("ok", "Byl zamítnut [1].", [1])
    assert ceny[0] == 2 and float(ceny[1]) > 0
    assert tah == (None, None, "pgtest") and zdroj == [("pn:2329", True)]
    # druhá zpráva téže relace navazuje na historii a registr
    s.llm.odpovedi = [ODP_TEXT("Ano, viz [1].")]
    r2 = await post(s, cookie=sid_cookie(r), konverzace_id=start["konverzace_id"])
    await s.dobehni()
    assert typy(r2.telo) == ["start", "zdroje", "konec"] and sse(r2.telo)[-1][1]["stav"] == "ok"


@pytest.mark.pg
@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)")
async def test_pg_odpojeny_klient_zapise_cenu_a_skonci_tah(pg_uloziste):
    s = PgSvet(pg_uloziste, [ODP_TOOL("get_pn", {"cislo": 2329}), ODP_TEXT("x")],
               data=FalesnaData(zpozdeni_s=0.3), odpojeni_s=0.05)
    r = await post(s, odpojit=lambda telo: b"event: start" in telo)
    await s.dobehni()
    start = sse(r.telo)[0][1]
    with pg_uloziste._pool.connection() as c:
        stav = c.execute("SELECT stav FROM chat.zprava WHERE id = %s", (start["zprava_id"],)).fetchone()[0]
        ceny = c.execute("SELECT count(*) FROM chat.volani_llm WHERE zprava_id = %s", (start["zprava_id"],)
                         ).fetchone()[0]
        tah = c.execute("""SELECT r.tah_token FROM chat.relace r JOIN chat.konverzace k ON k.relace_id = r.id
                           WHERE k.id = %s""", (start["konverzace_id"],)).fetchone()[0]
    assert (stav, ceny, tah, s.llm.volani) == ("preruseno", 1, None, 1)


@pytest.mark.pg
@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)")
def test_pg_jmena_osob_ze_steno_osoba():
    from psycopg_pool import ConnectionPool

    from hlidac_rag import db
    from hlidac_rag.chat.pg import PgData

    c = db.connect(autocommit=True)
    db.migrate(c)
    c.execute("DELETE FROM steno.osoba WHERE id_osoba IN (990001, 990002)")
    c.execute("INSERT INTO steno.osoba (id_osoba, jmeno, prijmeni) VALUES (990001, 'Testoslav', 'Zkouškář'), "
              "(990002, 'Testa', 'Pokusná')")
    pool = ConnectionPool(db.dsn(), min_size=1, max_size=1, open=True)
    try:
        d = PgData(pool)
        assert d.jmena_osob("Citovala poslance Zkouškáře a paní Pokusnou.") == ["Testoslav Zkouškář", "Testa Pokusná"]
        assert d.jmena_osob("Nikdo takový.") == []
    finally:
        pool.close()
        c.execute("DELETE FROM steno.osoba WHERE id_osoba IN (990001, 990002)")
        c.close()
