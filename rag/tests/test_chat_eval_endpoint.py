"""Kouřový eval přes endpoint (úloha 19): skript proti službě z `vytvor()` s falešným LLM a úložištěm v paměti.
Skutečné API ani server se nevolají; klíč je jen testovací."""
from __future__ import annotations

import httpx2 as httpx
import pytest
from test_chat_app import EVAL_KLIC, ODP_TEXT, PUVOD, U, Svet

from eval.chat import chat_eval
from eval.chat.chat_eval import SCENARE, main, navratovy_kod, spust, vyber_scenare
from eval.chat.kontroly import nacti_scenare

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _svet(odpovedi, **kw) -> Svet:
    s = Svet(odpovedi, **kw)
    ul = s.ulozit

    def smaz_konverzaci(kid, sid):          # úložiště z test_chat_app zatím mazání nemá
        k = ul.konverzace_.get(kid)
        if not k or k["relace_id"] != sid:
            return False
        del ul.konverzace_[kid]
        return True

    ul.smaz_konverzaci = smaz_konverzaci
    return s


def _klient(s: Svet) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=s.app), base_url=PUVOD,
                             headers={"x-forwarded-for": "198.51.100.7"})


def _scenar(id_="T1", tahy=None, kontext=None) -> dict:
    return {"id": id_, "sada": "zaklad", "kontext": kontext, "rucne": "test",
            "tahy": tahy or [{"text": "Prošel PN 2329?", "must": ["prošel"], "kour": True}]}


async def _spust(s: Svet, scenare, klic=EVAL_KLIC, **kw):
    async with _klient(s) as http:
        return await spust(http, PUVOD, scenare, klic, vypis=lambda _: None, **kw)


async def test_kour_projde_a_po_sobe_smaze_konverzace():
    s = _svet([ODP_TEXT("Ano, prošel."), ODP_TEXT("Podal ho poslanec.")])
    scenar = _scenar(tahy=[{"text": "Prošel PN 2329?", "must": ["prošel"]},
                           {"text": "Kdo ho podal?", "must": ["poslanec"], "kour": True}], kontext={"typ": "tisk", "ct": 221})
    v = await _spust(s, [scenar])
    assert [(x["scenar"], x["tah"], x["stav"]) for x in v] == [("T1", 1, "ok"), ("T1", 2, "ok")]
    assert navratovy_kod(v) == 0
    assert all(x["cena_usd"] > 0 for x in v)                       # cena z události konec.eval
    assert v[1]["soucet_usd"] == pytest.approx(v[0]["cena_usd"] + v[1]["cena_usd"])
    assert v[0]["smazano"] is None and v[1]["smazano"] is True
    assert s.ulozit.konverzace_ == {}                              # eval po sobě maže (spec 11.1)
    assert "Ano, prošel." in v[0]["odpoved"]


async def test_selhani_must_not_vraci_1():
    s = _svet([ODP_TEXT("Určitě volte stranu X.")])
    v = await _spust(s, [_scenar(tahy=[{"text": "Koho volit?", "must_not": ["volte"], "kour": True}])])
    assert v[0]["stav"] == "selhani" and v[0]["selhani"] == ["must_not: volte"]
    assert navratovy_kod(v) == 1
    assert s.ulozit.konverzace_ == {}                              # uklizeno i po selhání


async def test_limit_vraci_2_a_nepocita_se_jako_selhani_kvality():
    s = _svet([ODP_TEXT("x")], utrata=U(den=1.95))
    v = await _spust(s, [_scenar(), _scenar("T2")])
    assert len(v) == 1                                             # po limitu běh končí
    assert v[0]["stav"] == "sluzba" and "429" in v[0]["chyba"] and v[0]["selhani"] == []
    assert navratovy_kod(v) == 2
    assert s.llm.volani == 0


async def test_prekroceni_stropu_ceny_ukonci_beh():
    s = _svet([ODP_TEXT("Ano, prošel."), ODP_TEXT("Ano, prošel.")])
    v = await _spust(s, [_scenar(), _scenar("T2")], max_usd=0.0001)
    assert [x["stav"] for x in v] == ["cena"] and navratovy_kod(v) == 2
    assert s.llm.volani == 1 and s.ulozit.konverzace_ == {}


async def test_zly_klic_je_odmitnut_jako_sluzba_a_klic_se_nevypise(capsys):
    s = _svet([ODP_TEXT("x")])
    tajny = "tajny-klic-nikdy-ven"
    async with _klient(s) as http:
        v = await spust(http, PUVOD, [_scenar()], tajny)
        a = type("A", (), {"scenare": str(SCENARE), "sada": "kour", "url": PUVOD, "cookie_beta": None,
                           "max_usd": 0.3})()
        kod = await chat_eval._hlavni(a, tajny, http)
    assert v[0]["stav"] == "sluzba" and "428" in v[0]["chyba"] and kod == 2    # bez klíče chce Turnstile
    ven = capsys.readouterr()
    assert tajny not in ven.out + ven.err and tajny not in repr(v)


def test_klic_se_necte_z_argumentu(capsys):
    with pytest.raises(SystemExit):
        chat_eval._parser().parse_args(["--klic", "x"])
    with pytest.raises(SystemExit):
        chat_eval._parser().parse_args(["--klic=x"])
    assert not any("klic" in o.dest or "eval" in o.dest for o in chat_eval._parser()._actions)
    assert main([], env={}) == 2
    assert "CHAT_EVAL_KLIC" in capsys.readouterr().err


def test_vyber_kour_ze_scenare_json():
    vsechny = nacti_scenare(SCENARE)
    kour = vyber_scenare(vsechny, "kour")
    tahu = sum(len(s["tahy"]) for s in kour)
    pocet_kour = sum(1 for s in vsechny for t in s["tahy"] if t.get("kour"))
    assert 8 <= pocet_kour <= 12 and pocet_kour <= tahu <= 14
    assert {"zaklad", "kontext", "citace", "injekce", "neutralita"} <= {s["sada"] for s in kour}
    assert any(s.get("jen_endpoint") for s in kour)
    anafora = next(s for s in kour if s["id"] == "K-anafora-po-zmene")
    assert len(anafora["tahy"]) == 2                               # dřívější tah nese kontext, poslední kour tah končí
    assert vyber_scenare(vsechny, "vse") == vsechny
