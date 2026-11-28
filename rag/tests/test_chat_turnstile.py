"""Turnstile: kdy ověřit znovu a ověření tokenu přes siteverify (spec 11.2). Cloudflare se nikdy nevolá."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx2 as httpx
import pytest

from hlidac_rag.chat import turnstile
from hlidac_rag.chat.config import nacti
from hlidac_rag.chat.turnstile import OvereniNedostupne, over_token, potreba_overeni

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def cista_pamet(monkeypatch):
    """Paměť použitých tokenů je v procesu; každý test začíná prázdnou a bez přepisu hostname."""
    monkeypatch.delenv("CHAT_TURNSTILE_HOSTNAME", raising=False)
    turnstile._vycisti_pamet()
    yield
    turnstile._vycisti_pamet()


# Veřejné testovací klíče Cloudflare (https://developers.cloudflare.com/turnstile/troubleshooting/testing/),
# tady jen jako konstanty: falešný klient je nikam neposílá.
TEST_SECRET_PROJDE = "1x0000000000000000000000000000000AA"
TEST_SECRET_SELZE = "2x0000000000000000000000000000000AA"
TEST_TOKEN = "XXXX.DUMMY.TOKEN.XXXX"

# --- potreba_overeni ---

N = nacti({"CHAT_ZAPNUTO": "1", "CHAT_TURNSTILE_ZPRAV": "5", "CHAT_TURNSTILE_HODIN": "6"})
TED = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
ZA_HODINU = TED + timedelta(hours=1)
PRED_MINUTOU = TED - timedelta(minutes=1)


def R(overeno_do=None, zprav_od_overeni=0, otisk=None):
    return {"id": "a" * 32, "overeno_do": overeno_do, "zprav_od_overeni": zprav_od_overeni, "otisk": otisk}


@pytest.mark.parametrize("rel, prisny, ocekavam", [
    (R(overeno_do=None), False, True),                                   # první zpráva
    (R(overeno_do=ZA_HODINU, zprav_od_overeni=4, otisk="a"), False, False),
    (R(overeno_do=ZA_HODINU, zprav_od_overeni=5, otisk="a"), False, True),   # CHAT_TURNSTILE_ZPRAV
    (R(overeno_do=PRED_MINUTOU, zprav_od_overeni=0, otisk="a"), False, True),# CHAT_TURNSTILE_HODIN
    (R(overeno_do=ZA_HODINU, zprav_od_overeni=1, otisk="b"), False, True),   # jiná síť
    (R(overeno_do=ZA_HODINU, zprav_od_overeni=2, otisk="a"), True, True),    # přísný režim: každé 2 zprávy
])
def test_kdy_overovat(rel, prisny, ocekavam):
    assert potreba_overeni(N, rel, "a", prisny, TED) is ocekavam


def test_prisny_rezim_po_jedne_zprave_jeste_ne():
    assert potreba_overeni(N, R(overeno_do=ZA_HODINU, zprav_od_overeni=1, otisk="a"), "a", True, TED) is False


def test_otisk_smazany_uklidem_je_jiny_otisk():
    """Úklid po 48 h maže otisk (None); bez něj nejde ověřit, že relace je ze stejné sítě."""
    assert potreba_overeni(N, R(overeno_do=ZA_HODINU, zprav_od_overeni=0, otisk=None), "a", False, TED) is True


def test_presne_na_hranici_platnosti_overit():
    assert potreba_overeni(N, R(overeno_do=TED, zprav_od_overeni=0, otisk="a"), "a", False, TED) is True


def test_zadna_relace_overit():
    assert potreba_overeni(N, None, "a", False, TED) is True


def test_cas_bez_pasma_je_chyba():
    with pytest.raises(ValueError):
        potreba_overeni(N, R(overeno_do=ZA_HODINU, otisk="a"), "a", False, TED.replace(tzinfo=None))


def test_platnost_overeni():
    assert turnstile.platnost_overeni(N, TED) == TED + timedelta(hours=6)


# --- over_token ---

def _klient(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def http(odpoved: dict, zachyt: list | None = None, status: int = 200) -> httpx.AsyncClient:
    def h(req: httpx.Request) -> httpx.Response:
        if zachyt is not None:
            zachyt.append(req)
        return httpx.Response(status, json=odpoved)
    return _klient(h)


def http_500() -> httpx.AsyncClient:
    return _klient(lambda req: httpx.Response(500, text="chyba"))


def http_timeout() -> httpx.AsyncClient:
    def h(req):
        raise httpx.ReadTimeout("timeout", request=req)
    return _klient(h)


OK = {"success": True, "hostname": "cosepece.cz", "action": "chat"}


async def test_uspech_vyzaduje_hostname_a_action():
    assert await over_token(http(OK), "t1", "1.2.3.4", "s")
    assert not await over_token(http({"success": True, "hostname": "evil.cz", "action": "chat"}), "t2", "1.2.3.4", "s")
    assert not await over_token(http({"success": True, "hostname": "cosepece.cz", "action": "jina"}), "t3",
                                "1.2.3.4", "s")
    assert not await over_token(http({"success": True, "hostname": "cosepece.cz"}), "t4", "1.2.3.4", "s")
    assert not await over_token(http({"success": True, "action": "chat"}), "t5", "1.2.3.4", "s")
    assert not await over_token(http({"success": False, "error-codes": ["timeout-or-duplicate"]}), "t6",
                                "1.2.3.4", "s")


async def test_success_musi_byt_true():
    assert not await over_token(http({"success": "true", "hostname": "cosepece.cz", "action": "chat"}), "t",
                                "1.2.3.4", "s")


async def test_vypadek_cloudflare_neni_uspech():
    with pytest.raises(OvereniNedostupne):
        await over_token(http_500(), "t1", "1.2.3.4", "s")
    with pytest.raises(OvereniNedostupne):
        await over_token(http_timeout(), "t2", "1.2.3.4", "s")


async def test_chyba_site_neni_uspech():
    def h(req):
        raise httpx.ConnectError("nejde", request=req)
    with pytest.raises(OvereniNedostupne):
        await over_token(_klient(h), "t", "1.2.3.4", "s")


async def test_necitelna_odpoved_neni_uspech():
    with pytest.raises(OvereniNedostupne):
        await over_token(_klient(lambda req: httpx.Response(200, text="<html>")), "t1", "1.2.3.4", "s")
    with pytest.raises(OvereniNedostupne):
        await over_token(_klient(lambda req: httpx.Response(200, json=[1])), "t2", "1.2.3.4", "s")


@pytest.mark.parametrize("kod", ["internal-error", "invalid-input-secret", "missing-input-secret"])
async def test_chyba_na_strane_serveru_je_nedostupnost(kod):
    """Chybu Cloudflare nebo špatné tajemství nevyřeší nový token od uživatele: 503, ne další výzva."""
    with pytest.raises(OvereniNedostupne):
        await over_token(http({"success": False, "error-codes": [kod]}), "t", "1.2.3.4", "s")


async def test_pozadavek_na_siteverify():
    zachyt: list[httpx.Request] = []
    assert await over_token(http(OK, zachyt), TEST_TOKEN, "1.2.3.4", TEST_SECRET_PROJDE)
    (req,) = zachyt
    assert req.method == "POST"
    assert str(req.url) == turnstile.SITEVERIFY == "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    telo = parse_qs(req.content.decode())
    assert telo == {"secret": [TEST_SECRET_PROJDE], "response": [TEST_TOKEN], "remoteip": ["1.2.3.4"]}
    assert req.extensions["timeout"]["read"] == turnstile.TIMEOUT_S == 4


async def test_token_je_jednorazovy_i_u_nas():
    """Druhé použití téhož tokenu neprojde ani bez dotazu na Cloudflare (pojistka k timeout-or-duplicate)."""
    zachyt: list[httpx.Request] = []
    klient = http(OK, zachyt)
    assert await over_token(klient, "stejny", "1.2.3.4", "s")
    assert not await over_token(klient, "stejny", "1.2.3.4", "s")
    assert len(zachyt) == 1


async def test_token_po_vypadku_uz_neplati():
    with pytest.raises(OvereniNedostupne):
        await over_token(http_500(), "t", "1.2.3.4", "s")
    assert not await over_token(http(OK), "t", "1.2.3.4", "s")


@pytest.mark.parametrize("token", ["", "   ", "x" * 2049])
async def test_prazdny_nebo_dlouhy_token_bez_volani(token):
    zachyt: list = []
    assert not await over_token(http(OK, zachyt), token, "1.2.3.4", "s")
    assert zachyt == []


async def test_bez_tajemstvi_je_nedostupnost():
    with pytest.raises(OvereniNedostupne):
        await over_token(http(OK), "t", "1.2.3.4", "")


async def test_hostname_z_prostredi(monkeypatch):
    monkeypatch.setenv("CHAT_TURNSTILE_HOSTNAME", "localhost")
    assert await over_token(http({"success": True, "hostname": "localhost", "action": "chat"}), "t1", "1.2.3.4", "s")
    assert not await over_token(http(OK), "t2", "1.2.3.4", "s")


async def test_tajemstvi_ani_token_nejdou_do_logu(caplog):
    caplog.set_level(logging.DEBUG)
    tajne = "tajemstvi-ktere-nesmi-do-logu"
    token = "token-ktery-nesmi-do-logu"
    await over_token(http({"success": False, "error-codes": ["invalid-input-response"]}), token, "1.2.3.4", tajne)
    with pytest.raises(OvereniNedostupne):
        await over_token(http({"success": False, "error-codes": ["invalid-input-secret"]}), token + "2",
                         "1.2.3.4", tajne)
    with pytest.raises(OvereniNedostupne):
        await over_token(http_500(), token + "3", "1.2.3.4", tajne)
    vse = caplog.text + json.dumps([str(r.args) for r in caplog.records])
    assert tajne not in vse
    assert token not in vse


async def test_vyjimka_nenese_tajemstvi():
    tajne = "tajemstvi-ktere-nesmi-do-vyjimky"
    with pytest.raises(OvereniNedostupne) as e:
        await over_token(http_timeout(), "t", "1.2.3.4", tajne)
    assert tajne not in str(e.value)
    assert e.value.__cause__ is None or tajne not in str(e.value.__cause__)
