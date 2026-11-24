"""Validace karet a popisků generátoru (plán Task 2)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from conftest import load_fixture
from hlidac_reels.generator.normalize import normalize
from hlidac_reels.generator.validate import caption_authors, check_layout, has_word, validate_variant

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
    # bod bez citace normalizace vynechá; kontrola ho proto snese, chyba je až bez jediné citace
    card = load_fixture("v3-zmena-221.json")
    card["zmena"]["body"][0]["citace"] = ""
    assert _check(card) == []
    for b in card["zmena"]["body"]:
        b["citace"] = ""
    assert any("žádný bod nemá citaci" in e for e in _check(card))
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


@pytest.mark.render
def test_check_layout_rika_o_kolik_a_co():
    # šablona titulek zkrátí na celé slovo a řádek háčku zmenší nejvýš na 60 %; scénu proto rozbije jen jednotka
    # háčku z jediného dlouhého slova (nezalomí se ani zmenšená); chyba musí říct, o kolik px scéna přetéká
    # a která pole (s délkou) se na ní podílejí
    card = copy.deepcopy(load_fixture("v3-pn-115.json"))
    card["title"] = ("Pozměňovací návrhy k novele trestního zákoníku sahají do dalších zákonů " * 3)[:120]
    card["hook"] = dict(card["hook"], unit="pozměňovacíchnávrhůadalšíchpět")
    errs = [e for e in check_layout(card, "template_v3") if e.startswith("scene:s-hook:")]
    over = [e for e in errs if "přetéká o" in e]
    assert over, errs
    assert "px" in over[0] and "title 120 znaků" in over[0] and "hook.unit 30 znaků" in over[0], over[0]


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


# --- F4: popisek videa k vybraným PN jmenuje autora a jeho klub (card.pn_autor) ---

def _pn_card(authors: list[dict]) -> dict:
    card = load_fixture("v3-pn-311.json")
    card["pn"]["top"][0] = dict(card["pn"]["top"][0], cislo=authors[0]["cislo"])
    card["pn_autor"] = authors
    return card


def _caption(text: str) -> str:
    return f"{text}\n\nHodnotíme postup, ne politický obsah.\n\n#snemovna"


def _author_errs(card: dict, text: str) -> list[str]:
    nums = [a["cislo"] for a in card["pn_autor"]]
    return [e for e in _check(card, _caption(text), focus_pn=nums) if "autor" in e]


POSLANEC = {"cislo": 9801, "jmeno": "Test Poslanec", "klub": "ANO"}


def test_popisek_bez_klubu_chyba():
    card = _pn_card([POSLANEC])
    assert _author_errs(card, "PN 9801 podal Test Poslanec (ANO).") == []
    errs = _author_errs(card, "PN 9801 podal Test Poslanec (ANO2011).")
    assert errs == ["popisek neuvádí klub autora PN 9801: „Test Poslanec (ANO)“"]


def test_popisek_sklonovane_jmeno_bez_klubu():
    # Review Focus 3: skloňované jméno normalizace nenajde, klub nevloží; kontrola chce klub, autora uzná
    contract = Path(__file__).resolve().parents[2] / "tests" / "contract" / "focus-pn-info.json"
    info = json.loads(contract.read_text(encoding="utf-8"))
    card = load_fixture("v3-pn-311.json")
    card["pn"]["top"][0] = dict(card["pn"]["top"][0], cislo=9801)
    caption = _caption("Návrh poslance Testa Poslance, PN 9801. PN 9802 podal ministr Test Ministr.")
    new, cap, _ = normalize(card, caption, fmt="pn", focus_info=info)
    assert cap == caption
    errs = [e for e in _check(new, cap, focus_pn=[9801, 9802]) if "autor" in e]
    assert errs == ["popisek neuvádí klub autora PN 9801: „Test Poslanec (ANO)“"]


def test_popisek_bez_autora_chyba():
    card = _pn_card([POSLANEC])
    errs = _author_errs(card, "PN 9801 mění lhůtu, podporuje ho ANO.")
    assert errs == ["popisek neuvádí autora PN 9801 (Test Poslanec)"]
    # víc PN téhož autora v jedné chybě; autor bez klubu klub nepotřebuje
    card = _pn_card([POSLANEC, dict(POSLANEC, cislo=9803), {"cislo": 9802, "jmeno": "Test Ministr", "klub": None}])
    errs = _author_errs(card, "PN 9801 a 9803 mění lhůtu, PN 9802 podal Test Ministr.")
    assert errs == ["popisek neuvádí autora PN 9801, 9803 (Test Poslanec)",
                    "popisek neuvádí klub autora PN 9801, 9803: „Test Poslanec (ANO)“"]
    # u spojovníku stačí první část příjmení, skloňovaná
    card = _pn_card([{"cislo": 9801, "jmeno": "Jana Nováková-Svobodová", "klub": "TOP 09"}])
    assert _author_errs(card, "PN 9801 podala poslankyně Novákové (TOP 09).") == []
    assert _author_errs(card, "PN 9801 podala poslankyně Svobodová (TOP 09).") == [
        "popisek neuvádí autora PN 9801 (Jana Nováková-Svobodová)"]


def test_kontrola_jen_dvou_autoru():
    authors = [POSLANEC, {"cislo": 9802, "jmeno": "Jana Malá", "klub": "ODS"},
               {"cislo": 9803, "jmeno": "Petr Velký", "klub": "STAN"}]
    card = _pn_card(authors)
    assert caption_authors(card) == [{"jmeno": "Test Poslanec", "klub": "ANO", "cisla": [9801]},
                                     {"jmeno": "Jana Malá", "klub": "ODS", "cisla": [9802]}]
    assert _author_errs(card, "PN 9801 podal Test Poslanec (ANO), PN 9802 Jana Malá (ODS) a další.") == []
    # bez pn_autor se autor nekontroluje
    del card["pn_autor"]
    assert caption_authors(card) == []
    assert not any("autor" in e for e in _check(card, _caption("PN 2315 mění lhůtu.")))


def test_kontrola_pn_v_karte_nepocita_pn_autor():
    # pn_autor doplňuje normalizace ze zadání; video má o PN mluvit samo, číslo jen v pn_autor nestačí
    card = load_fixture("v3-pn-311.json")
    card["pn_autor"] = [POSLANEC]
    errs = _check(card, _caption("PN 9801 podal Test Poslanec (ANO)."), focus_pn=[9801])
    assert any("karta nezmiňuje" in e for e in errs)


def test_has_word():
    assert has_word("(ANO)", "ANO")
    assert not has_word("ANO2011", "ANO")
    assert has_word("za TOP 09.", "TOP 09")
    assert not has_word("TOP 091", "TOP 09") and not has_word("ano, souhlasí", "ANO")
    assert has_word("klub KDU-ČSL podpořil", "KDU-ČSL") and not has_word("KDU-ČSLx", "KDU-ČSL")
    assert has_word("Motoristé.", "Motoristé") and not has_word("Motoristéch", "Motoristé")
    assert not has_word("cokoli", "")
