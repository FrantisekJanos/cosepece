"""Logování služby chatu: token Telegram bota je v URL API a nesmí skončit v logu kontejneru (final review I3)."""
import logging

import httpx2 as httpx
import pytest

from hlidac_rag.chat import hlaseni
from hlidac_rag.chat.__main__ import TICHE_LOGGERY, nastav_logovani

pytestmark = pytest.mark.anyio

TOKEN = "123456:tajny-token-bota"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def obnov_urovne():
    puvodni = {j: logging.getLogger(j).level for j in TICHE_LOGGERY}
    yield
    for j, uroven in puvodni.items():
        logging.getLogger(j).setLevel(uroven)


class _T:
    telegram_token = TOKEN
    telegram_chat = "42"


async def test_posli_nezaloguje_token_na_info(caplog, obnov_urovne):
    nastav_logovani()
    caplog.set_level(logging.INFO)
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))
    async with httpx.AsyncClient(transport=transport) as http:
        assert await hlaseni.posli(http, _T(), "test") is True
    assert TOKEN not in caplog.text
    assert "bot" + TOKEN not in caplog.text


def test_klientske_knihovny_loguji_az_od_warning(obnov_urovne):
    nastav_logovani()
    for jmeno in ("httpx2", "httpx", "httpcore"):
        assert jmeno in TICHE_LOGGERY
        assert logging.getLogger(jmeno).getEffectiveLevel() >= logging.WARNING
