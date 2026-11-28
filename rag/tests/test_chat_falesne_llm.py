"""Falešný model pro ruční zkoušku widgetu (CHAT_FALESNE_LLM=1): dvě kola nástrojů, potom pevná odpověď."""
import asyncio

import pytest

from hlidac_rag.chat.__main__ import FALESNA_ODPOVED, FALESNE_NASTROJE, FalesneLLM
from hlidac_rag.chat.config import nacti
from hlidac_rag.chat.nastroje import Vysledek
from hlidac_rag.chat.registr import Registr
from hlidac_rag.chat.smycka import tah

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_dve_kola_nastroju_a_pevna_odpoved():
    volane = []

    async def nastroj(nazev, argumenty):
        volane.append(nazev)
        return Vysledek(text="{}", polozky=[])

    udalosti = [u async for u in tah(FalesneLLM(prodleva_s=0), nastroj, [{"role": "user", "content": "Ahoj"}],
                                     Registr(), nacti({}), "claude-sonnet-5-5", asyncio.Event())]
    assert volane == [n for n, _ in FALESNE_NASTROJE]
    konec = udalosti[-1].data
    assert konec["stav"] == "ok" and konec["kol"] == 2
    assert "".join(u.data["text"] for u in udalosti if u.typ == "text").strip() == FALESNA_ODPOVED.strip()
