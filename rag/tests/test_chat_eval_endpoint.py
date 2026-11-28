"""Kouřový eval přes endpoint (úloha 19): skript proti službě z `vytvor()` s falešným LLM a úložištěm v paměti.
Skutečné API ani server se nevolají; klíč je jen testovací."""
from __future__ import annotations

import json

import httpx2 as httpx
import pytest
from test_chat_app import BETA_KLIC, EVAL_KLIC, ODP_TEXT, PUVOD, U, N, Svet

from eval.chat import chat_eval
from eval.chat.chat_eval import SCENARE, main, navratovy_kod, spust, vyber_scenare
from eval.chat.kontroly import nacti_scenare

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _svet(odpovedi, **kw) -> Svet:
    return Svet(odpovedi, **kw)


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


async def test_selhani_na_tahu_pres_strop_zustava_selhanim():
    s = _svet([ODP_TEXT("Určitě volte X.")])
    v = await _spust(s, [_scenar(tahy=[{"text": "Koho volit?", "must_not": ["volte"], "kour": True}])], max_usd=0.0001)
    assert v[0]["stav"] == "selhani" and navratovy_kod(v) == 1
    assert len(v) == 1


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


@pytest.mark.parametrize("hodnota", ["-1", "0", "nan", "inf", "abc"])
def test_max_usd_jen_konecne_kladne(hodnota):
    with pytest.raises(SystemExit):
        chat_eval._parser().parse_args(["--max-usd", hodnota])
    assert chat_eval._parser().parse_args(["--max-usd", "0.5"]).max_usd == 0.5


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


async def test_beta_z_prostredi_projde_skrytym_rezimem(capsys, tmp_path):
    """Brána v redeploy.sh běží v kontejneru chat: cookie chat_beta si podepíše klíčem CHAT_BETA_KLIC z prostředí."""
    s = _svet([ODP_TEXT("Ano, prošel.")], n=N(CHAT_REZIM="skryte"))
    env = {"CHAT_EVAL_KLIC": EVAL_KLIC, "CHAT_BETA_KLIC": BETA_KLIC.decode()}
    scenare = json.dumps([_scenar()])
    soubor = tmp_path / "s.json"
    soubor.write_text(scenare, encoding="utf-8")
    async with _klient(s) as http:
        a = chat_eval._parser().parse_args(["--url", PUVOD, "--scenare", str(soubor), "--beta-z-prostredi"])
        a.cookie_beta = chat_eval._cookie_beta(a, env)
        assert await chat_eval._hlavni(a, EVAL_KLIC, http) == 0
    ven = capsys.readouterr()
    assert BETA_KLIC.decode() not in ven.out + ven.err


def test_beta_z_prostredi_bez_klice_a_spolu_s_cookie(capsys):
    assert main(["--beta-z-prostredi"], env={"CHAT_EVAL_KLIC": "x"}) == 2
    assert "CHAT_BETA_KLIC" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        chat_eval._parser().parse_args(["--beta-z-prostredi", "--cookie-beta", "x"])


class _UlozisteSeZpravami:
    """Úložiště, které počítá zprávy relace jako `chat.zprava_log` (všechny v jedné minutě, eval je rychlý)."""

    def __init__(self, ulozit):
        self.puvodni_utrata = ulozit.utrata
        self.puvodni_zaloz = ulozit.zaloz_odpoved
        self.ulozit = ulozit
        self.zprav: dict[str, int] = {}

    def zaloz_odpoved(self, kid, text, kontext):
        rid = self.ulozit.konverzace_[kid]["relace_id"]
        self.zprav[rid] = self.zprav.get(rid, 0) + 1
        return self.puvodni_zaloz(kid, text, kontext)

    def utrata(self, rid, otisk, ted):
        u = self.puvodni_utrata(rid, otisk, ted)
        n = self.zprav.get(rid, 0)
        return u.__class__(**{**u.__dict__, "zprav_min": n, "zprav_den": n})


async def test_kour_sada_neprekroci_limit_zprav_relace_profilu_5_usd():
    """Celá kouřová sada pod profilem $5 (Haiku): limit zpráv relace za minutu nesmí eval zastavit (final review I1).
    Skutečné `rozpocet.zkontroluj` ve službě, zprávy se počítají za relaci."""
    kour = vyber_scenare(nacti_scenare(SCENARE), "kour")
    tahu = sum(len(s["tahy"]) for s in kour)
    assert tahu > 6
    s = _svet([ODP_TEXT("Odpověď bez citace.") for _ in range(tahu)],
              n=N(CHAT_MODEL="claude-haiku-5-5", CHAT_ROZPOCET_DEN_USD="5", CHAT_ZPRAV_RELACE_MIN="6",
                  CHAT_ZPRAV_RELACE_DEN="30"))
    pocitadlo = _UlozisteSeZpravami(s.ulozit)
    s.ulozit.zaloz_odpoved = pocitadlo.zaloz_odpoved
    s.ulozit.utrata = pocitadlo.utrata
    v = await _spust(s, kour)
    assert not [x for x in v if x["stav"] == "sluzba"], [x["chyba"] for x in v if x["chyba"]]
    assert len(v) == tahu and s.llm.volani == tahu
    assert max(pocitadlo.zprav.values()) <= max(len(x["tahy"]) for x in kour)   # nová relace na scénář
