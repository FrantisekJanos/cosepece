"""Náhledy zdrojů, odkazy na originál a obslužné adresy služby chat (Task 15, spec 6, 9, 12, 15).

Bez databáze: odkazy, klíče, omezení odchozích spojení, adresy služby s falešným úložištěm a daty.
S PostgreSQL (`pg`, bez DATABASE_URL se přeskočí): náhledy z tabulek (texty, steno, tabulky webu v dočasném schématu)."""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections import deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx2 as httpx
import pytest
from test_chat_app import (EVAL_KLIC, N, ODP_TEXT, PUVOD, TAJEMSTVI, FalesnaData, Http, Svet, asgi,  # noqa: F401
                           hlavicky_zpravy)

from hlidac_rag.chat import nahled as nahled_mod
from hlidac_rag.chat.app import POVOLENE_HOSTY, NepovolenyHost, Tajemstvi, _zdroj_pro_widget, vytvor
from hlidac_rag.chat.citace import _zaznelo_v
from hlidac_rag.chat.config import nacti
from hlidac_rag.chat.nahled import klic_nahledu, ma_tabulku, nahled, odkazy, platny_klic
from hlidac_rag.chat.registr import Zdroj

pytestmark = pytest.mark.anyio
KOREN = Path(__file__).resolve().parents[2]
SID = "0123456789abcdef0123456789abcdef"
CIZI_SID = "fedcba9876543210fedcba9876543210"
URL_STENO = "https://www.psp.cz/eknih/2025ps/stenprot/034schuz/s034001.htm#r2"
URL_DOCX = "https://www.psp.cz/sqw/text/orig2.sqw?idd=1"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def pg(test):
    test = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)")(test)
    return pytest.mark.pg(test)


# ---------------------------------------------------------------- odkazy

PRIKLADY = [
    ("tisk:221", {"ct": 221}), ("pn:2329", {"ct": 221}), ("pn:2329", {}),
    ("cel:tisk:221:schvalene:§ 17c", {"ct": 221}), ("cel:tisk:221:puvodni:poradi 14", {}),
    ("cel:pn:2329:bod 3", {"ct": 221}), ("projev:10/34/633/1", {"url": URL_STENO}), ("projev:10/34/633/1", {}),
    ("hlas:90001", {"ct": 221}), ("hlas:90001", {}), ("zakon:159/2006", {}), ("rozbor:tisk:221", {}),
    ("rozbor:pn:2329", {"ct": 221}), ("upoz:senate:221:2026-11-01", {"url": "https://evil.example/orig2.sqw"}),
    ("neplatny", {"url": "https://evil.example/orig2.sqw?idd=1", "ct": 5}),
]


def test_odkazy_nikdy_na_orig2_pdf_ani_kopii():
    for klic, meta in PRIKLADY:
        o = odkazy(klic, meta)
        assert set(o) == {"web", "psp", "docx"}
        assert all(v is None or v.startswith(("https://www.psp.cz/", "/")) for v in o.values()), (klic, o)
        assert "/dok/" not in str(o) and "#page" not in str(o) and "orig2" not in str(o), (klic, o)
        assert "pdf" not in str(o).lower() and "search=" not in str(o)


def test_odkazy_tisk_pn_hlasovani():
    assert odkazy("tisk:221", {})["psp"] == "https://www.psp.cz/sqw/historie.sqw?o=10&t=221"
    assert odkazy("tisk:221", {})["web"] == "/tisk/221"
    assert odkazy("pn:2329", {"ct": 221}) == {"web": "/tisk/221#pn-2329",
                                              "psp": "https://www.psp.cz/sqw/sd.sqw?cd=2329&o=10", "docx": None}
    assert odkazy("hlas:90001", {"ct": 221})["psp"] == "https://www.psp.cz/sqw/hlasy.sqw?g=90001"
    assert odkazy("hlas:90001", {"ct": 221})["web"] == "/tisk/221"
    assert odkazy("hlas:90001", {})["web"] is None             # nespárované hlasování nemá stránku tisku
    assert odkazy("pn:2329", {})["web"] is None                # bez tisku odkaz na web nevznikne
    assert odkazy("cel:tisk:221:schvalene:§ 1", {})["psp"] == "https://www.psp.cz/sqw/historie.sqw?o=10&t=221"
    assert odkazy("cel:pn:2329:bod 3", {"ct": 221})["psp"] == "https://www.psp.cz/sqw/sd.sqw?cd=2329&o=10"
    assert odkazy("zakon:159/2006", {})["web"] == "/zakon/159/2006"


def test_odkaz_projevu_jen_ze_steno_na_psp():
    assert odkazy("projev:10/34/633/1", {"url": URL_STENO})["psp"] == URL_STENO
    for zly in ("https://evil.cz/s034001.htm", "http://www.psp.cz/eknih/s.htm", "javascript:alert(1)",
                "https://www.psp.cz/sqw/text/orig2.sqw?idd=5", "https://www.psp.cz.evil.cz/x.htm",
                "https://www.psp.cz/a b.htm", None, 5):
        assert odkazy("projev:10/34/633/1", {"url": zly})["psp"] is None, zly


def test_docx_jen_z_meta():
    assert odkazy("cel:tisk:221:schvalene:§ 1", {"docx_url": URL_DOCX})["docx"].endswith("idd=1")
    assert odkazy("cel:tisk:221:schvalene:§ 1", {"url_docx": URL_DOCX})["docx"] == URL_DOCX
    assert odkazy("cel:tisk:221:schvalene:§ 1", {})["docx"] is None
    for zly in ("https://evil.cz/sqw/text/orig2.sqw?idd=1", "https://www.psp.cz/sqw/text/orig2.sqw?idd=1&x=2",
                "http://www.psp.cz/sqw/text/orig2.sqw?idd=1", "https://www.psp.cz/dok/x.pdf#page=3"):
        assert odkazy("cel:tisk:221:schvalene:§ 1", {"docx_url": zly})["docx"] is None, zly


def test_orig2_jen_v_klici_docx_a_jen_z_dat():
    for klic, meta in PRIKLADY:
        o = odkazy(klic, {**meta, "docx_url": URL_DOCX})
        assert all("orig2" not in v for k, v in o.items() if k != "docx" and v), (klic, o)
        if o["docx"]:
            assert o["docx"] == URL_DOCX and klic.split(":", 1)[0] in ("tisk", "pn", "cel")


def test_povoleny_odkaz_je_whitelist_domen():
    assert nahled_mod.povoleny_odkaz("https://www.psp.cz/eknih/x.htm")
    assert nahled_mod.povoleny_odkaz("https://odok.gov.cz/portal/veklep/material/1/")
    assert nahled_mod.povoleny_odkaz("https://cosepece.cz/tisk/221")
    for zly in ("http://www.psp.cz/x", "https://evil.cz/psp.cz", "https://psp.cz.evil.cz/x", "https://notpsp.cz/x",
                "https://www.psp.cz/sqw/text/orig2.sqw?idd=1", "//www.psp.cz/x", None):
        assert not nahled_mod.povoleny_odkaz(zly), zly


# ---------------------------------------------------------------- služba nic nestahuje

def test_sluzba_nic_nestahuje_z_psp():          # robots.txt psp.cz: chat jen odkazuje
    zdroj = "".join(p.read_text(encoding="utf-8") for p in (KOREN / "rag/hlidac_rag/chat").rglob("*.py"))
    for volani in re.findall(r"(?:http|client|httpx)\.(?:get|post|stream|request)\([^)]*\)", zdroj):
        assert "psp.cz" not in volani
    assert POVOLENE_HOSTY == {"api.anthropic.com", "api.openai.com", "challenges.cloudflare.com", "api.telegram.org"}
    assert isinstance(POVOLENE_HOSTY, frozenset)


async def test_sdileny_klient_odmitne_jiny_host_i_pri_presmerovani():
    videno: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        videno.append(req.url.host)
        if req.url.path == "/presmeruj":
            return httpx.Response(302, headers={"location": "https://www.psp.cz/sqw/text/orig2.sqw?idd=1"})
        return httpx.Response(200, json={"ok": True})

    klient = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    Svet(http=type("H", (), {"klient": klient})())      # vytvor() klientovi přidá omezení hostů
    assert (await klient.get("https://api.telegram.org/bot/x")).status_code == 200
    assert (await klient.post("https://challenges.cloudflare.com/turnstile/v0/siteverify")).status_code == 200
    for adresa in ("https://www.psp.cz/sqw/historie.sqw", "https://evil.example/", "https://api.telegram.org.evil.cz/",
                   "http://127.0.0.1:8000/"):
        with pytest.raises(NepovolenyHost):
            await klient.get(adresa)
    with pytest.raises(NepovolenyHost):                 # povolený host přesměruje jinam
        await klient.get("https://api.openai.com/presmeruj")
    assert videno == ["api.telegram.org", "challenges.cloudflare.com", "api.openai.com"]
    assert isinstance(NepovolenyHost("x"), httpx.HTTPError)     # turnstile a Telegram ho chytají jako chybu spojení
    # klient se nezdvojuje při opakovaném vytvoření služby
    Svet(http=type("H", (), {"klient": klient})())
    assert len(klient.event_hooks["request"]) == 1


# ---------------------------------------------------------------- klíče a tabulky

def test_platny_klic_jen_typy_ze_spec():
    for dobry in ("tisk:221", "pn:2329", "cel:tisk:221:schvalene:§ 17c", "cel:pn:2329:bod 3", "projev:10/34/633/1",
                  "hlas:90001", "zakon:159/2006", "rozbor:tisk:221"):
        assert platny_klic(dobry), dobry
    for zly in ("", "tisk:", "tisk:abc", "tisk:221 ", "tisk:1234567", "tisk:221\n", "pn:-1", "tisk:٣٣", "../etc/passwd",
                "cel:tisk:221:jina:§ 1", "cel:tisk:221:schvalene:", "cel:tisk:221:schvalene:" + "x" * 81,
                "cel:pn:2329:a\nb", "projev:10/34/633", "projev:10/34/633/1/2", "hlas:1;DROP", "zakon:159",
                "rozbor:zakon:1", "upoz:x", "prip:1", None, 5, "tisk:221" + "9" * 200):
        assert not platny_klic(zly), zly


def test_klic_nahledu_pouzije_poradi_u_celku():
    assert klic_nahledu("cel:tisk:221:schvalene:bod 1", {"poradi": 7}) == "cel:tisk:221:schvalene:poradi 7"
    assert klic_nahledu("cel:pn:2329:bod 1", {"poradi": 0}) == "cel:pn:2329:poradi 0"
    assert klic_nahledu("cel:pn:2329:bod 1", {}) == "cel:pn:2329:bod 1"
    assert klic_nahledu("pn:2329", {"poradi": 7}) == "pn:2329"
    assert _zdroj_pro_widget(Zdroj(1, "cel:tisk:221:schvalene:bod 1", "cel", "Tisk 221, bod 1", {"poradi": 7}))[
        "klic"] == "cel:tisk:221:schvalene:poradi 7"


def test_ma_tabulku_podle_bunek_a_tabulatoru():
    assert not ma_tabulku("§ 17c\nLhůta pro vymáhání činí 4 roky ode dne porušení povinnosti.\nDruhý odstavec.")
    assert ma_tabulku("Sazba\tOd\tDo")
    assert ma_tabulku("Úvod\n\nSazba\nOd\nDo\n10 %\n15 %\n21 %\n\nZávěr")
    assert not ma_tabulku("a\nb\nc\n\nd\ne\nf\n\ng\nh\ni")        # prázdné řádky řadu přerušují
    assert not ma_tabulku("Čl. I\n§ 5\nbod 1\nText delší než buňka tabulky, aby řadu přerušil.\nA\nB")


# ---------------------------------------------------------------- adresy služby

def hl(ip="198.51.100.7", cookie=None, origin=PUVOD, post=True):
    h = {"x-forwarded-for": ip}
    if cookie:
        h["cookie"] = cookie
    if post and origin is not None:
        h["origin"] = origin
    return h


def j(**kw) -> bytes:
    return json.dumps(kw).encode()


class Hodiny:
    def __init__(self):
        self.t = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t


def svet_s_nahledem(vysledek=None, vyjimka=None, **kw):
    s = Svet(**kw)
    s.volani_nahledu = []

    def nahled_(klic, zv):
        s.volani_nahledu.append((klic, zv))
        if vyjimka:
            raise vyjimka
        return vysledek

    s.data.nahled = nahled_
    return s


async def test_neplatny_klic_je_404_bez_dotazu_do_db():
    s = svet_s_nahledem({"typ": "tisk"})
    for k in ("", "neco", "tisk:", "tisk:221;DROP", "cel:tisk:221:x:y", "upoz:a", "tisk:" + "9" * 40):
        r = await asgi(s.app, "GET", "/chat-api/nahled?k=" + k.replace(";", "%3B"), hlavicky=hl(post=False))
        assert (r.status_code, r.json()) == (404, {"kod": "nenalezeno"}), k
    assert (await asgi(s.app, "GET", "/chat-api/nahled", hlavicky=hl(post=False))).status_code == 404
    assert s.volani_nahledu == [] and s.data.volani == []


async def test_nahled_vraci_data_s_cache_a_zv():
    data = {"typ": "cel", "text": "<script>alert(1)</script> & <b>tučně</b>", "zdroj": "Poslanecká sněmovna PČR, psp.cz"}
    s = svet_s_nahledem(data)
    r = await asgi(s.app, "GET", "/chat-api/nahled?k=cel:tisk:221:schvalene:%C2%A7%2017c&zv=3-9",
                   hlavicky=hl(post=False))
    assert r.status_code == 200 and r.json() == data                    # text beze změny, jako data
    assert r.hlavicka("cache-control") == "public, max-age=300"
    assert r.hlavicka("content-type").startswith("application/json") and r.hlavicka("x-content-type-options") == "nosniff"
    assert s.volani_nahledu == [("cel:tisk:221:schvalene:§ 17c", (3, 9))]
    for zv in ("", "9", "a-b", "-3-9", "1-2-3", "1-" + "9" * 20):          # špatné zvýraznění se ignoruje
        await asgi(s.app, "GET", "/chat-api/nahled?k=tisk:221&zv=" + zv, hlavicky=hl(post=False))
    assert [v[1] for v in s.volani_nahledu[1:]] == [None] * 6


async def test_nahled_neexistujici_zdroj_404_a_vypadek_db_503():
    s = svet_s_nahledem(None)
    r = await asgi(s.app, "GET", "/chat-api/nahled?k=tisk:221", hlavicky=hl(post=False))
    assert (r.status_code, r.json()["kod"], r.hlavicka("cache-control")) == (404, "nenalezeno", "no-store")
    s = svet_s_nahledem(vyjimka=RuntimeError("db"))
    r = await asgi(s.app, "GET", "/chat-api/nahled?k=tisk:221", hlavicky=hl(post=False))
    assert (r.status_code, r.json()["kod"]) == (503, "data_nedostupna") and "db" not in r.telo.decode()
    assert (await asgi(s.app, "GET", "/chat-api/nahled?k=tisk:221", hlavicky={})).status_code == 400     # bez IP


async def test_nahled_limit_60_za_minutu_na_otisk_ip():
    hodiny = Hodiny()
    s = svet_s_nahledem({"typ": "tisk"}, hodiny=hodiny)
    for _ in range(60):
        assert (await asgi(s.app, "GET", "/chat-api/nahled?k=tisk:221", hlavicky=hl(post=False))).status_code == 200
    r = await asgi(s.app, "GET", "/chat-api/nahled?k=tisk:221", hlavicky=hl(post=False))
    assert (r.status_code, r.json()["kod"], r.hlavicka("retry-after")) == (429, "limit_pozadavku", "60")
    assert len(s.volani_nahledu) == 60
    # jiná adresa má vlastní okno, stejná sít IPv6 /64 sdílí jedno
    assert (await asgi(s.app, "GET", "/chat-api/nahled?k=tisk:221", hlavicky=hl("198.51.100.8", post=False))
            ).status_code == 200
    hodiny.t += timedelta(seconds=30)
    assert (await asgi(s.app, "GET", "/chat-api/nahled?k=tisk:221", hlavicky=hl(post=False))).status_code == 429
    hodiny.t += timedelta(seconds=31)
    assert (await asgi(s.app, "GET", "/chat-api/nahled?k=tisk:221", hlavicky=hl(post=False))).status_code == 200


async def test_limit_pamet_se_cisti():
    hodiny = Hodiny()
    s = svet_s_nahledem({"typ": "tisk"}, hodiny=hodiny)
    s.sluzba._okna_limitu.update({f"x{i}": deque([hodiny().timestamp() - 500])
                                  for i in range(10001)})
    await asgi(s.app, "GET", "/chat-api/nahled?k=tisk:221", hlavicky=hl(post=False))
    assert len(s.sluzba._okna_limitu) == 1


async def test_overit_nevraci_text_odpovedi():
    s = Svet()
    s.ulozit.existuje_kod = lambda kod: date(2026, 3, 5) if kod == "abcd2345" else None
    s.ulozit.zpravy_text = "TAJNY TEXT ODPOVEDI"
    r = await asgi(s.app, "GET", "/chat-api/overit?kod=ABCD2345", hlavicky=hl(post=False))
    telo = r.telo.decode()
    assert r.status_code == 200 and r.hlavicka("content-type").startswith("text/html")
    assert "Odpověď s tímto kódem existuje, vznikla 5. 3. 2026" in telo and "TAJNY TEXT" not in telo
    assert "default-src 'none'" in r.hlavicka("content-security-policy") and r.hlavicka("cache-control") == "no-store"
    telo = (await asgi(s.app, "GET", "/chat-api/overit?kod=zzzzzzzz", hlavicky=hl(post=False))).telo.decode()
    assert "Odpověď s tímto kódem neexistuje" in telo and "existuje, vznikla" not in telo
    for zly in ("", "<script>alert(1)</script>", "abc", "abcd2345x"):          # neplatný kód se nehledá ani nevrací
        telo = (await asgi(s.app, "GET", "/chat-api/overit?kod=" + zly, hlavicky=hl(post=False))).telo.decode()
        assert "neexistuje" in telo and "<script>alert" not in telo


async def test_hodnoceni():
    s = Svet()
    videno = []
    s.ulozit.hodnoceni = lambda kod, palec: videno.append((kod, palec)) or kod == "abcd2345"
    ok = await asgi(s.app, "POST", "/chat-api/hodnoceni", j(kod="ABCD2345", palec=-1), hl())
    assert ok.status_code == 204 and videno == [("abcd2345", -1)]
    assert (await asgi(s.app, "POST", "/chat-api/hodnoceni", j(kod="zzzzzzzz", palec=1), hl())).status_code == 404
    for telo in (j(kod="abcd2345", palec=0), j(kod="abcd2345", palec=True), j(kod="abcd2345", palec="1"),
                 j(kod="abcd2345"), j(kod="x", palec=1), b"[1]", b"{"):
        assert (await asgi(s.app, "POST", "/chat-api/hodnoceni", telo, hl())).status_code == 400, telo
    assert (await asgi(s.app, "POST", "/chat-api/hodnoceni", j(kod="abcd2345", palec=1),
                       hl(origin="https://evil.cz"))).status_code == 403
    assert (await asgi(s.app, "POST", "/chat-api/hodnoceni", j(kod="abcd2345", palec=1), hl(origin=None))
            ).status_code == 403
    assert len(videno) == 2          # ostatní požadavky se do úložiště nedostaly


async def test_nahlaseni_dlouhy_duvod_413():
    s = Svet()
    videno = []
    s.ulozit.pocet_nahlaseni_dne = lambda ted: 0
    s.ulozit.nahlas = lambda kod, duvod, otisk: videno.append((kod, duvod)) or kod == "abcd2345"
    r = await asgi(s.app, "POST", "/chat-api/nahlasit", j(kod="abcd2345", duvod="x" * 1001), hl())
    assert (r.status_code, r.json()["kod"]) == (413, "prilis_dlouhy_duvod") and videno == []
    r = await asgi(s.app, "POST", "/chat-api/nahlasit", j(kod="abcd2345", duvod="č" * 1000), hl())
    assert r.status_code == 204 and videno == [("abcd2345", "č" * 1000)]
    assert (await asgi(s.app, "POST", "/chat-api/nahlasit", j(kod="zzzzzzzz", duvod="x"), hl())).status_code == 404
    assert (await asgi(s.app, "POST", "/chat-api/nahlasit", j(kod="abcd2345", duvod=5), hl())).status_code == 400
    assert (await asgi(s.app, "POST", "/chat-api/nahlasit", b"x" * 20000, hl())).status_code == 413
    assert (await asgi(s.app, "POST", "/chat-api/nahlasit", j(kod="abcd2345", duvod="x"), hl(origin="https://x.cz"))
            ).status_code == 403


async def test_smazat_cizi_konverzaci_je_404():
    s = Svet()
    rid = s.ulozit.zaloz_relaci(SID)
    vlastni = s.ulozit.zaloz_konverzaci(rid)
    cizi = s.ulozit.zaloz_konverzaci(s.ulozit.zaloz_relaci(CIZI_SID))
    smazano = []
    s.ulozit.smaz_konverzaci = lambda kid, r: smazano.append((kid, r)) or s.ulozit.konverzace(kid, r) is not None
    cookie = f"chat_sid={SID}"
    r = await asgi(s.app, "POST", "/chat-api/smazat", j(konverzace_id=cizi), hl(cookie=cookie))
    assert (r.status_code, r.json()["kod"]) == (404, "nenalezeno")
    assert (await asgi(s.app, "POST", "/chat-api/smazat", j(konverzace_id="0" * 32), hl(cookie=cookie))).status_code == 404
    assert (await asgi(s.app, "POST", "/chat-api/smazat", j(konverzace_id=vlastni), hl())).status_code == 404   # bez cookie
    assert smazano == [(cizi, SID), ("0" * 32, SID)]
    assert (await asgi(s.app, "POST", "/chat-api/smazat", j(konverzace_id="x"), hl(cookie=cookie))).status_code == 400
    assert (await asgi(s.app, "POST", "/chat-api/smazat", j(konverzace_id=vlastni), hl(cookie=cookie, origin="https://x.cz")
                       )).status_code == 403
    s.sluzba.bezici_relace.add(SID)         # pod běžícím tahem se nemaže
    assert (await asgi(s.app, "POST", "/chat-api/smazat", j(konverzace_id=vlastni), hl(cookie=cookie))).status_code == 409
    s.sluzba.bezici_relace.discard(SID)
    assert (await asgi(s.app, "POST", "/chat-api/smazat", j(konverzace_id=vlastni), hl(cookie=cookie))).status_code == 204


async def test_konverzace_obnova_jen_vlastni_a_jen_citovane_zdroje():
    s = Svet()
    rid = s.ulozit.zaloz_relaci(SID)
    kid = s.ulozit.zaloz_konverzaci(rid)
    cizi = s.ulozit.zaloz_konverzaci(s.ulozit.zaloz_relaci(CIZI_SID))
    s.ulozit.zpravy = lambda k: [{"otazka": "Prošel PN?", "text": "Ano [1].", "kod": "abcd2345", "stav": "ok",
                                  "citovane": [1]}]
    s.ulozit.uloz_zdroje(kid, [Zdroj(1, "pn:2329", "pn", "PN 2329 (tisk 221)", {}, True),
                               Zdroj(2, "tisk:221", "tisk", "Tisk 221", {}, False)])
    r = await asgi(s.app, "GET", f"/chat-api/konverzace/{kid}", hlavicky=hl(cookie=f"chat_sid={SID}", post=False))
    assert r.status_code == 200 and r.hlavicka("cache-control") == "no-store"
    assert r.json() == {"konverzace_id": kid, "zpravy": [
        {"role": "uzivatel", "text": "Prošel PN?"},
        {"role": "bot", "text": "Ano [1].", "kod": "abcd2345", "stav": "ok", "citovane": [1]}],
        "zdroje": [{"n": 1, "typ": "pn", "titulek": "PN 2329 (tisk 221)", "znacky": [], "klic": "pn:2329"}]}
    for url, cookie in ((f"/chat-api/konverzace/{cizi}", f"chat_sid={SID}"), (f"/chat-api/konverzace/{kid}", None),
                        (f"/chat-api/konverzace/{kid}", f"chat_sid={CIZI_SID}"), ("/chat-api/konverzace/x", f"chat_sid={SID}")):
        assert (await asgi(s.app, "GET", url, hlavicky=hl(cookie=cookie, post=False))).status_code == 404, url


async def test_zasady_nahradi_provozovatele_a_escapuje_ho():
    s = Svet(n=N(CHAT_PROVOZOVATEL="Provozovatel <b>Test</b> & spol., kontakt: test@example.org"))
    r = await asgi(s.app, "GET", "/chat-api/zasady", hlavicky=hl(post=False))
    telo = r.telo.decode()
    assert r.status_code == 200 and r.hlavicka("content-type").startswith("text/html")
    assert "Provozovatel &lt;b&gt;Test&lt;/b&gt; &amp; spol., kontakt: test@example.org" in telo
    assert "{{PROVOZOVATEL}}" not in telo and "<b>Test" not in telo and "<script" not in telo
    assert "umělá inteligence" in telo and "30 dnech" in telo and "12 měsíců" in telo and "/chat-api/overit" in telo
    bez = (await asgi(Svet().app, "GET", "/chat-api/zasady", hlavicky=hl(post=False))).telo.decode()
    assert "{{PROVOZOVATEL}}" not in bez and "zatím nejsou zveřejněny" in bez
    assert nacti({}).provozovatel == "" and nacti({"CHAT_PROVOZOVATEL": " X "}).provozovatel == "X"


async def test_obsluzne_adresy_ve_skrytem_rezimu_bez_cookie_jsou_404():
    s = Svet(n=N(CHAT_REZIM="skryte"))
    for metoda, url in (("GET", "/chat-api/nahled?k=tisk:221"), ("GET", "/chat-api/overit?kod=abcd2345"),
                        ("GET", "/chat-api/zasady"), ("POST", "/chat-api/hodnoceni"), ("POST", "/chat-api/nahlasit"),
                        ("POST", "/chat-api/smazat"), ("GET", f"/chat-api/konverzace/{'0' * 32}")):
        assert (await asgi(s.app, metoda, url, b"{}", hl())).status_code == 404, url


# ---------------------------------------------------------------- náhledy z DB

CT = 99901
PN = 99902
SCHUZE = 998
SCHEMA = "test_chat_nahled_php"
PHP = f"""
CREATE SCHEMA {SCHEMA};
CREATE TABLE {SCHEMA}.tisky (id_tisk int PRIMARY KEY, ct int NOT NULL, druh int NOT NULL, typ_navrhu varchar(20),
  navrhovatel varchar(255), nazev varchar(255), uplny_nazev text, predlozeno varchar(19), faze varchar(60),
  zakony text, score int);
CREATE TABLE {SCHEMA}.tisk_text (ct int PRIMARY KEY, ai_json text);
CREATE TABLE {SCHEMA}.pn (id_dokument int PRIMARY KEY, cislo int NOT NULL, ct int NOT NULL, id_osoba int,
  osoba varchar(120), klub varchar(40), cas varchar(19));
CREATE TABLE {SCHEMA}.pn_text (id_dokument int PRIMARY KEY, ct int NOT NULL, cislo int NOT NULL, docx_url varchar(255),
  ai_json text);
CREATE TABLE {SCHEMA}.pn_vysledek (ct int NOT NULL, cislo int NOT NULL, stav varchar(20) NOT NULL,
  PRIMARY KEY (ct, cislo));
CREATE TABLE {SCHEMA}.kroky (id_hist int PRIMARY KEY, id_tisk int NOT NULL, ct int NOT NULL, datum varchar(19),
  typ_stavu int, stav varchar(120), akce_id int, akce varchar(120), id_hlasovani int);
CREATE TABLE {SCHEMA}.hlasovani (id_hlasovani int PRIMARY KEY, ct int NOT NULL, schuze int, cislo int,
  datum varchar(16), pro int, proti int, zdrzel int, nehlasoval int, prihlaseno int, kvorum int, vysledek varchar(1),
  nazev varchar(255), krok int NOT NULL DEFAULT 0);
CREATE TABLE {SCHEMA}.hlasovani_kluby (id_hlasovani int NOT NULL, klub varchar(40) NOT NULL, pro int, proti int,
  zdrzel int, nepritomen int);
"""

TEXT_17C = "§ 17c\nLhůta pro vymáhání činí 4 roky ode dne porušení povinnosti."
TABULKA = "Příloha\nSazba\nOd\nDo\n10 %\n15 %\n21 %"
CELKY = [
    ("clanek", "Čl. I", "Čl. I", "Čl. I\nV zákoně č. 218/2000 Sb. se mění takto:"),
    ("paragraf", "§ 17c", "Čl. I / § 17c", TEXT_17C),
    ("bod", "bod 1", "Čl. II / bod 1", "1. V § 5 se slovo „dva“ nahrazuje slovem „tři“."),
    ("bod", "bod 1", "Čl. III / bod 1", "1. V § 9 se doplňuje odstavec 4."),
    ("priloha", "Příloha", "Příloha (1/2)", TABULKA),
]
DLOUHY = "Úvod projevu. " + ("slovo " * 3000) + "Zde zaznělo důležité tvrzení o dotacích. " + ("dál " * 500)


def nahraj_dokument(conn, druh, ct, pn, verze, celky, docx="https://www.psp.cz/sqw/text/orig2.sqw?idd=77"):
    text = "\n".join(c[3] for c in celky)
    did = conn.execute("""INSERT INTO texty.dokument (druh, ct, cislo_pn, verze, zdroj_textu, text, znaku, docx_url,
                                                      rozdeleno_at)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now()) RETURNING id""",
                       (druh, ct, pn, verze, "tisk_text" if druh == "tisk" else "pn_text", text, len(text), docx)
                       ).fetchone()[0]
    od = 0
    for i, (typ, ozn, cesta, t) in enumerate(celky, 1):
        conn.execute("""INSERT INTO texty.cel (dokument_id, poradi, typ, oznaceni, cesta, text, znaku, od_znaku)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""", (did, i, typ, ozn, cesta, t, len(t), od))
        od += len(t) + 1


@pytest.fixture
def conn():
    from hlidac_rag import db

    c = db.connect()
    jmeno = c.execute("SELECT current_database()").fetchone()[0]
    if not jmeno.endswith("_test"):
        c.close()
        pytest.skip(f"databáze {jmeno} nekončí na _test")
    db.migrate(c)
    c.commit()
    with c.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        cur.execute(PHP)
        cur.execute(f"SET search_path TO {SCHEMA}, public")
    c.commit()
    yield c
    c.rollback()
    c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    c.commit()
    c.close()


def projev(conn, aname=2, text="Navrhuji zamítnout. Druhá věta.", autorizovano=False, schuze=SCHUZE, klub="XYZ"):
    conn.execute("""INSERT INTO steno.projev (obdobi, schuze, turn, aname, datum, poradi, recnik, klub, text, znaku, hash,
                                              autorizovano, url, id_bod)
                    VALUES (10, %s, 1, %s, '2026-11-18', %s, 'Poslanec Test', %s, %s, %s, 'h', %s, %s, NULL)""",
                 (schuze, aname, aname, klub, text, len(text), autorizovano,
                  f"https://www.psp.cz/eknih/2025ps/stenprot/{schuze}schuz/s{schuze}001.htm#r{aname}"))


@pg
def test_nahled_celku_ma_cestu_sousedy_a_zvyrazneni(conn):
    nahraj_dokument(conn, "tisk", CT, 0, "puvodni", CELKY)
    od = TEXT_17C.index("4 roky")
    n = nahled(conn, f"cel:tisk:{CT}:puvodni:§ 17c", (od, od + 6))
    assert n["typ"] == "cel" and n["titulek"] == f"Tisk {CT}, § 17c" and n["cesta"] == ["Čl. I", "§ 17c"]
    assert n["text"] == TEXT_17C and n["zv"] == [od, od + 6] and n["text"][od:od + 6] == "4 roky"
    assert n["predchozi"] == f"cel:tisk:{CT}:puvodni:poradi 1" and n["dalsi"] == f"cel:tisk:{CT}:puvodni:poradi 3"
    assert n["tabulka"] is False and n["verze"] == "puvodni" and n["znacky"] == [] and n["zkraceno"] is False
    assert n["kluby"] is None and n["zdroj"] == "Poslanecká sněmovna PČR, psp.cz"
    assert n["odkazy"] == {"web": f"/tisk/{CT}", "psp": f"https://www.psp.cz/sqw/historie.sqw?o=10&t={CT}",
                           "docx": "https://www.psp.cz/sqw/text/orig2.sqw?idd=77"}
    assert set(n) == {"typ", "titulek", "cesta", "text", "zv", "znacky", "predchozi", "dalsi", "odkazy", "tabulka",
                      "kluby", "verze", "zkraceno", "projev", "zdroj"}
    # sousedé jdou následovat, první a poslední celek mají jednoho
    prvni = nahled(conn, n["predchozi"], None)
    assert prvni["predchozi"] is None and prvni["dalsi"] == f"cel:tisk:{CT}:puvodni:poradi 2" and prvni["zv"] is None
    # neplatné zvýraznění se zahodí
    for zv in ((5, 5), (9, 3), (-1, 4), (0, 99999), (True, 5), (1,)):
        assert nahled(conn, f"cel:tisk:{CT}:puvodni:§ 17c", zv)["zv"] is None, zv
    # celek PN se jmenuje podle PN a odkazuje na stránku dokumentu
    nahraj_dokument(conn, "pn", 231, PN, "puvodni", CELKY[:2], docx=None)
    pn = nahled(conn, f"cel:pn:{PN}:§ 17c", None)
    assert pn["titulek"] == f"PN {PN}, § 17c" and pn["verze"] is None and pn["odkazy"]["psp"].endswith(f"cd={PN}&o=10")
    assert pn["odkazy"]["web"] == f"/tisk/231#pn-{PN}" and pn["odkazy"]["docx"] is None


@pg
def test_nahled_celku_nejednoznacne_oznaceni_jde_pres_poradi(conn):
    nahraj_dokument(conn, "tisk", CT, 0, "puvodni", CELKY)
    assert nahled(conn, f"cel:tisk:{CT}:puvodni:bod 1", None) is None            # dva celky se stejným označením
    n = nahled(conn, f"cel:tisk:{CT}:puvodni:poradi 4", None)
    assert n["cesta"] == ["Čl. III", "bod 1"] and "§ 9" in n["text"]
    assert nahled(conn, f"cel:tisk:{CT}:puvodni:poradi 99", None) is None         # chybějící celek
    assert nahled(conn, f"cel:tisk:{CT}:schvalene:§ 17c", None) is None            # schválené znění není
    assert nahled(conn, f"cel:tisk:{CT + 1}:puvodni:§ 17c", None) is None          # tisk bez textu


@pg
def test_celek_s_tabulkou_nabidne_original(conn):
    nahraj_dokument(conn, "tisk", CT, 0, "puvodni", CELKY)
    n = nahled(conn, f"cel:tisk:{CT}:puvodni:poradi 5", None)
    assert n["tabulka"] is True and n["cesta"] == ["Příloha"]          # „(1/2)“ za cestou kusu se neukazuje
    assert nahled(conn, f"cel:tisk:{CT}:puvodni:poradi 2", None)["tabulka"] is False


@pg
def test_nahled_projevu_ma_znacku_neautorizovano(conn):
    projev(conn)
    n = nahled(conn, f"projev:10/{SCHUZE}/1/2", (0, 8))
    assert n["typ"] == "projev" and n["titulek"] == "Poslanec Test (XYZ), 18. 11. 2026"
    assert n["znacky"] == ["neautorizovano"] and n["text"].startswith("Navrhuji zamítnout") and n["zv"] == [0, 8]
    assert n["cesta"] == [f"{SCHUZE}. schůze"] and n["predchozi"] is None and n["dalsi"] is None and n["tabulka"] is False
    assert n["odkazy"] == {"web": None, "docx": None,
                           "psp": f"https://www.psp.cz/eknih/2025ps/stenprot/{SCHUZE}schuz/s{SCHUZE}001.htm#r2"}
    # metadata sedí s `zaznelo_v` z ověření citací (recnik, klic, datum, url)
    zdroj = Zdroj(1, f"projev:10/{SCHUZE}/1/2", "projev", n["titulek"], {"datum": "2026-11-18", "url": n["odkazy"]["psp"]})
    assert n["projev"] == _zaznelo_v(zdroj, n["projev"]["recnik"])
    assert set(n["projev"]) == {"recnik", "klic", "datum", "url"} and n["projev"]["recnik"] == "Poslanec Test (XYZ)"
    projev(conn, aname=3, autorizovano=True)
    assert nahled(conn, f"projev:10/{SCHUZE}/1/3", None)["znacky"] == []
    assert nahled(conn, f"projev:10/{SCHUZE}/1/9", None) is None


@pg
def test_nahled_dlouheho_projevu_otevre_okno_u_zvyrazneni(conn):
    projev(conn, text=DLOUHY)
    od = DLOUHY.index("důležité tvrzení")
    assert od > 12000
    n = nahled(conn, f"projev:10/{SCHUZE}/1/2", (od, od + 16))
    assert n["zkraceno"] is True and n["zv"] is not None and n["text"][n["zv"][0]:n["zv"][1]] == "důležité tvrzení"
    assert nahled(conn, f"projev:10/{SCHUZE}/1/2", None)["text"].startswith("Úvod projevu.")


@pg
def test_nahled_hlasovani_ma_vysledek_a_kluby(conn):
    conn.execute("INSERT INTO hlasovani (id_hlasovani, ct, schuze, cislo, datum, pro, proti, zdrzel, nehlasoval, "
                 "prihlaseno, kvorum, vysledek, nazev) VALUES (88876, 231, 34, 287, '2026-10-24 04:39', 81, 96, 0, 0, "
                 "177, 89, 'R', 'Novela zákona')")
    for klub, *h in (("ODS", 23, 0, 0, 11), ("ANO2011", 0, 69, 0, 11)):
        conn.execute("INSERT INTO hlasovani_kluby VALUES (88876, %s, %s, %s, %s, %s)", (klub, *h))
    n = nahled(conn, "hlas:88876", None)
    assert n["typ"] == "hlasovani" and n["titulek"] == "Hlasování č. 287, 24. 10. 2026"
    assert n["text"].startswith("Zamítnuto: pro 81, proti 96") and "kvórum 89" in n["text"]
    assert n["kluby"] == [{"klub": "ANO2011", "pro": 0, "proti": 69, "zdrzel": 0, "nepritomen": 11},
                          {"klub": "ODS", "pro": 23, "proti": 0, "zdrzel": 0, "nepritomen": 11}]
    assert n["cesta"] == ["34. schůze", "Tisk 231"]
    assert n["odkazy"] == {"web": "/tisk/231", "psp": "https://www.psp.cz/sqw/hlasy.sqw?g=88876", "docx": None}
    assert nahled(conn, "hlas:1", None) is None


@pg
def test_nahled_tisku_pn_zakona_a_rozboru(conn):
    conn.execute("INSERT INTO tisky (id_tisk, ct, druh, typ_navrhu, navrhovatel, nazev, uplny_nazev, predlozeno, faze, "
                 "zakony, score) VALUES (1, 221, 1, 'poslanecký', 'Poslanec Test', 'Novela', 'Návrh novely zákona', "
                 "'2026-10-01', '2. čtení', '159/2006,218/2000', 60)")
    n = nahled(conn, "tisk:221", None)
    assert n["titulek"] == "Tisk 221" and "Návrh novely zákona" in n["text"] and "Předkladatel: Poslanec Test" in n["text"]
    assert "Fáze: 2. čtení" in n["text"] and "Předloženo: 1. 10. 2026" in n["text"] and n["odkazy"]["web"] == "/tisk/221"
    assert nahled(conn, "tisk:222", None) is None
    conn.execute("INSERT INTO pn (id_dokument, cislo, ct, id_osoba, osoba, klub, cas) VALUES (5, 2329, 221, 1, "
                 "'Jan Test', 'ODS', '2026-10-02 10:00:00')")
    conn.execute("INSERT INTO pn_text (id_dokument, ct, cislo, docx_url, ai_json) VALUES (5, 221, 2329, "
                 "'https://www.psp.cz/sqw/text/orig2.sqw?idd=5', %s)", (json.dumps({"titulek": "Zkrácení lhůty",
                                                                                   "shrnuti": "PN zkracuje lhůtu."}),))
    conn.execute("INSERT INTO pn_vysledek (ct, cislo, stav) VALUES (221, 2329, 'zamitnut')")
    p = nahled(conn, "pn:2329", None)
    assert p["titulek"] == "PN 2329 (tisk 221)" and "Autor: Jan Test (ODS)" in p["text"] and "zamítnut" in p["text"]
    assert p["odkazy"] == {"web": "/tisk/221#pn-2329", "psp": "https://www.psp.cz/sqw/sd.sqw?cd=2329&o=10",
                           "docx": "https://www.psp.cz/sqw/text/orig2.sqw?idd=5"}
    z = nahled(conn, "zakon:159/2006", None)
    assert z["titulek"] == "Zákon 159/2006 Sb." and z["text"] == "Tisk 221: Novela" and z["odkazy"]["web"] == "/zakon/159/2006"
    assert nahled(conn, "zakon:1/2000", None) is None
    r = nahled(conn, "rozbor:pn:2329", None)
    assert r["znacky"] == ["odvozeny_ai"] and r["text"] == "Zkrácení lhůty\nPN zkracuje lhůtu."
    assert r["odkazy"]["web"] == "/tisk/221#pn-2329"
    assert nahled(conn, "rozbor:tisk:221", None) is None              # rozbor tisku v datech není


@pg
def test_pgdata_nahled_cte_v_transakci_jen_pro_cteni():
    from psycopg_pool import ConnectionPool

    from hlidac_rag import db
    from hlidac_rag.chat.pg import PgData

    c = db.connect(autocommit=True)
    jmeno = c.execute("SELECT current_database()").fetchone()[0]
    if not jmeno.endswith("_test"):
        c.close()
        pytest.skip(f"databáze {jmeno} nekončí na _test")
    db.migrate(c)
    c.execute("DELETE FROM steno.projev WHERE obdobi = 10 AND schuze = 997")
    projev(c, schuze=997)
    pool = ConnectionPool(db.dsn(), min_size=1, max_size=1, open=True)
    try:
        d = PgData(pool)
        assert d.nahled("projev:10/997/1/2", None)["titulek"].startswith("Poslanec Test")
        assert d.nahled("projev:10/997/1/9", None) is None
        assert d.nahled("tisk:abc", None) is None
    finally:
        pool.close()
        c.execute("DELETE FROM steno.projev WHERE obdobi = 10 AND schuze = 997")
        c.close()


@pg
def test_uloziste_zpravy_pro_obnovu_panelu():
    from hlidac_rag import db
    from hlidac_rag.chat import uloziste
    from hlidac_rag.chat.pg import _FUNKCE_ULOZISTE

    assert {"zpravy", "smaz_konverzaci", "existuje_kod", "hodnoceni", "nahlas"} <= set(_FUNKCE_ULOZISTE)
    c = db.connect(autocommit=True)
    db.migrate(c)
    rid = uloziste.zaloz_relaci(c)
    try:
        kid = uloziste.zaloz_konverzaci(c, rid)
        z1, kod1 = uloziste.zaloz_odpoved(c, kid, "První otázka", {})
        uloziste.dokonci(c, z1, "První odpověď [1].", "ok", "m", [1], {})
        z2, _ = uloziste.zaloz_odpoved(c, kid, "Rozpracovaná", {})
        z3, _ = uloziste.zaloz_odpoved(c, kid, "Odmítnutá", {})
        uloziste.dokonci(c, z3, "", "odmitnuto", "m", [], {})
        assert uloziste.zpravy(c, kid) == [{"otazka": "První otázka", "text": "První odpověď [1].", "kod": kod1,
                                            "stav": "ok", "citovane": [1]}]
        assert z2 and uloziste.zpravy(c, "0" * 32) == []
    finally:
        kody = [r[0] for r in c.execute("""SELECT z.kod FROM chat.zprava z JOIN chat.konverzace k ON k.id = z.konverzace_id
                                           WHERE k.relace_id = %s AND z.kod IS NOT NULL""", (rid,)).fetchall()]
        c.execute("DELETE FROM chat.relace WHERE id = %s", (rid,))
        c.execute("DELETE FROM chat.zprava_log WHERE relace_id = %s", (rid,))
        c.execute("DELETE FROM chat.otisk_odpovedi WHERE kod = ANY(%s)", (kody,))
        c.close()


async def test_nahlaseni_denni_strop_vraci_429():
    s = Svet(n=N(CHAT_MAX_NAHLASENI_DEN="2"))
    videno = []
    pocet = [1]
    s.ulozit.pocet_nahlaseni_dne = lambda ted: pocet[0]
    s.ulozit.nahlas = lambda kod, duvod, otisk: videno.append(otisk) or True
    r = await asgi(s.app, "POST", "/chat-api/nahlasit", j(kod="abcd2345", duvod="x"), hl())
    assert r.status_code == 204 and videno == ["otisk-198.51.100.7"]     # otisk IP jde do úložiště
    pocet[0] = 2
    r = await asgi(s.app, "POST", "/chat-api/nahlasit", j(kod="abcd2345", duvod="x"), hl())
    assert (r.status_code, r.json()["kod"]) == (429, "limit_nahlaseni") and len(videno) == 1
    assert nacti({}).max_nahlaseni_den == 200


async def test_ipv6_sit_64_sdili_okno_limitu():
    s = svet_s_nahledem({"typ": "tisk"})
    for i in range(60):
        r = await asgi(s.app, "GET", "/chat-api/nahled?k=tisk:221", hlavicky=hl(f"2001:db8:1:2::{i + 1:x}", post=False))
        assert r.status_code == 200
    assert (await asgi(s.app, "GET", "/chat-api/nahled?k=tisk:221",
                       hlavicky=hl("2001:db8:1:2:ffff::9", post=False))).status_code == 429
    assert (await asgi(s.app, "GET", "/chat-api/nahled?k=tisk:221",
                       hlavicky=hl("2001:db8:1:3::1", post=False))).status_code == 200


async def test_klic_zdroje_je_null_bez_nahledu():
    assert _zdroj_pro_widget(Zdroj(1, "upoz:senate:221:2026", "upozorneni", "U", {}))["klic"] is None
    assert _zdroj_pro_widget(Zdroj(1, "prip:123", "pripravovane", "P", {}))["klic"] is None
    assert _zdroj_pro_widget(Zdroj(1, "tisk:221", "tisk", "T", {}))["klic"] == "tisk:221"


@pg
def test_nahled_rozboru_tisku(conn):
    conn.execute("INSERT INTO tisk_text (ct, ai_json) VALUES (221, %s)",
                 (json.dumps({"titulek": "Zkrácení lhůty", "shrnuti": "Návrh zkracuje lhůtu."}),))
    r = nahled(conn, "rozbor:tisk:221", None)
    assert r["typ"] == "rozbor" and r["znacky"] == ["odvozeny_ai"] and r["titulek"] == "Rozbor AI, tisk 221"
    assert r["text"] == "Zkrácení lhůty\nNávrh zkracuje lhůtu."
    assert r["odkazy"]["web"] == "/tisk/221" and r["odkazy"]["psp"].endswith("historie.sqw?o=10&t=221")
    assert nahled(conn, "rozbor:tisk:222", None) is None


@pg
def test_nahlaseni_stejneho_kodu_z_jednoho_otisku_je_jeden_radek():
    from hlidac_rag import db
    from hlidac_rag.chat import uloziste

    c = db.connect(autocommit=True)
    db.migrate(c)
    rid = uloziste.zaloz_relaci(c)
    try:
        kid = uloziste.zaloz_konverzaci(c, rid)
        z, kod = uloziste.zaloz_odpoved(c, kid, "Otázka", {})
        uloziste.dokonci(c, z, "Odpověď", "ok", "m", [], {})
        ted = datetime.now(timezone.utc)
        before = uloziste.pocet_nahlaseni_dne(c, ted)
        for duvod in ("a", "b", "c"):
            assert uloziste.nahlas(c, kod, duvod, "otisk1") is True
        assert uloziste.nahlas(c, kod, "d", "otisk2") is True
        radky = c.execute("SELECT otisk_ip, duvod FROM chat.nahlaseni WHERE kod = %s ORDER BY otisk_ip", (kod,)).fetchall()
        assert radky == [("otisk1", "c"), ("otisk2", "d")]
        assert uloziste.pocet_nahlaseni_dne(c, ted) == before + 2
        uloziste.uklid(c, ted + timedelta(hours=49))
        assert c.execute("SELECT count(*) FROM chat.nahlaseni WHERE kod = %s AND otisk_ip IS NOT NULL", (kod,)
                         ).fetchone()[0] == 0
    finally:
        c.execute("DELETE FROM chat.nahlaseni WHERE kod = %s", (kod,))
        c.execute("DELETE FROM chat.relace WHERE id = %s", (rid,))
        c.execute("DELETE FROM chat.zprava_log WHERE relace_id = %s", (rid,))
        c.execute("DELETE FROM chat.otisk_odpovedi WHERE kod = %s", (kod,))
        c.close()
