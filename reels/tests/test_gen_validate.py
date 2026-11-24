"""Validace karet a popisků generátoru (plán Task 2)."""
from __future__ import annotations

import copy

import pytest

from conftest import load_fixture
from hlidac_reels.generator.validate import check_layout, validate_variant

CAPTION_OK = (
    "Na mimořádnou daň pro rafinerie dostaly sněmovní výbory 30 dní. Běžná lhůta je 60.\n\n"
    "Zkrátit lhůtu pro výbory až o 30 dní jednací řád Sněmovně dovoluje (§ 91 odst. 2).\n\n"
    "Hodnotíme postup, ne politický obsah.\n\n#snemovna #zakony #cosepece"
)
V3 = ["v3-cesta-311.json", "v3-pn-115.json", "v3-zmena-221.json", "v3-cesta-174.json"]


def _keys(card: dict) -> list[str]:
    return [r["key"] for r in card["reasons"]]


def _check(card: dict, caption: str = CAPTION_OK, **kw) -> list[str]:
    template = "template_v3" if card.get("format") else "template_v2"
    args = dict(template=template, ct=card["ct"], reason_keys=_keys(card), fmt=card.get("format"))
    args.update(kw)
    return validate_variant(card, caption, **args)


@pytest.mark.parametrize("name", V3 + ["card-1-v2.json"])
def test_fixtury_projdou(name):
    assert _check(load_fixture(name)) == []


def test_cizi_ct():
    card = load_fixture("v3-cesta-311.json")
    assert any("ct" in e for e in _check(card, ct=115))


def test_vymysleny_klic_duvodu():
    card = load_fixture("v3-cesta-174.json")
    keys = _keys(card)
    card["reasons"].append({"key": "p90_passed", "label": "Schváleno v 1. čtení"})
    assert any("p90_passed" in e for e in _check(card, reason_keys=keys))


def test_jiny_format_nez_zadany():
    assert _check(load_fixture("v3-cesta-311.json"), fmt="pn")


def test_v3_bez_zdroju():
    card = load_fixture("v3-cesta-311.json")
    del card["zdroje"]
    assert any("zdroje" in e for e in _check(card))


def test_zmena_bod_bez_citace_nebo_zdroje():
    card = load_fixture("v3-zmena-221.json")
    card["zmena"]["body"][0]["citace"] = ""
    assert _check(card)
    card = load_fixture("v3-zmena-221.json")
    card["zmena"]["body"][0]["zdroj"] = ""
    assert _check(card)


def test_rozprava_bod_bez_url():
    card = load_fixture("v3-rozprava-311.json")
    assert _check(card) == []
    card["rozprava"]["pro"]["body"][0]["url"] = ""
    assert _check(card)


def test_popisek_dlouhy():
    errs = _check(load_fixture("v3-cesta-311.json"), caption="x" * 2121 + "\nHodnotíme postup, ne politický obsah.")
    assert any("2120" in e.replace(" ", "") for e in errs)


def test_popisek_bez_zaverecne_vety():
    errs = _check(load_fixture("v3-cesta-311.json"), caption="Krátký popisek.")
    assert any("Hodnotíme postup" in e for e in errs)


def test_v2_bez_titulku_nebo_citace():
    card = load_fixture("card-1-v2.json")
    card["title"] = ""
    assert any("title" in e for e in _check(card))
    card = load_fixture("card-1-v2.json")
    card["quote"] = " "
    assert any("quote" in e for e in _check(card))


@pytest.mark.render
def test_layout_fixtura_projde():
    assert check_layout(load_fixture("v3-cesta-311.json"), "template_v3") == []


def test_layout_preteceni_neprojde(monkeypatch):
    # šablony samy zkracují na celé slovo (ani 3000 znaků nepřeteče), proto se pojistka zkouší podvrženým měřením
    import hlidac_reels.render as render
    boxes = {"scene:s-hook": {"top": 100, "bottom": 900, "left": 60, "right": 1020, "overflow": True, "cut_mid_word": False}}
    monkeypatch.setattr(render, "measure_blocks", lambda card, template: boxes)
    errs = check_layout(load_fixture("card-1-v2.json"), "template_v2")
    assert any("přetéká" in e for e in errs) and any("zón" in e for e in errs)


@pytest.mark.parametrize("key", ["verified", "quote", "quote_source"])
def test_v3_bez_poli_karty(key):
    # Card.from_json je vyžaduje; bez nich by render spadl
    card = load_fixture("v3-cesta-311.json")
    del card[key]
    assert any(key in e for e in _check(card))


def test_video_k_pn_karta_zminuje_pn():
    card = load_fixture("v3-pn-115.json")
    caption = CAPTION_OK.replace("Na mimořádnou", "PN 1418 na mimořádnou")
    # číslo v poli karty (pn.top[].cislo) i v textu popisku stačí
    assert _check(card, caption, focus_pn=[1418, 9999]) == []
    errs = _check(card, caption, focus_pn=[9999])
    assert any("karta nezmiňuje" in e and "9999" in e for e in errs)
    assert any("popisek nezmiňuje" in e for e in errs)
    # číslo jako součást delšího čísla se nepočítá
    v2 = load_fixture("card-1-v2.json")
    v2["title"] = "PN 14180 mění zákon"
    assert any("karta nezmiňuje" in e for e in _check(v2, focus_pn=[1418]))
    v2["title"] = "PN č. 1418 mění zákon"
    assert not any("karta nezmiňuje" in e for e in _check(v2, focus_pn=[1418]))
    # bez zadání PN se nic nekontroluje
    assert _check(load_fixture("card-1-v2.json")) == []
