"""Normalizace karty a popisku před kontrolou (plán F2, Task 3)."""
from __future__ import annotations

import copy

from conftest import load_fixture
from hlidac_reels.generator.normalize import normalize
from hlidac_reels.generator.validate import CLOSING, validate_variant

CAPTION = "Popisek k tisku.\n\n" + CLOSING + "\n\n#snemovna"


def _norm(card, caption=CAPTION, fmt="zmena"):
    return normalize(card, caption, fmt=fmt, focus_info=[])


def test_rozdeli_cislo_a_jednotku():
    card = load_fixture("v3-zmena-221.json")
    card["hook"] = {"big": "800 mil. Kč", "unit": "přesun mezi kapitolami", "sub": "x"}
    new, cap, notes = _norm(card)
    assert new["hook"]["big"] == "800"
    assert new["hook"]["unit"] == "mil. Kč přesun mezi kapitolami"
    assert new["hook"]["sub"] == "x" and cap == CAPTION
    assert len(notes) == 1 and "800 mil. Kč" in notes[0]
    # vstupní karta zůstane beze změny
    assert card["hook"]["big"] == "800 mil. Kč"


def test_rozdeli_desetinne_cislo_a_tisice():
    for big, num, unit in (("1,5 mld. Kč", "1,5", "mld. Kč"), ("600 mil.", "600", "mil."),
                           ("2 000 mil. Kč", "2 000", "mil. Kč")):
        card = {"hook": {"big": big, "unit": "", "sub": ""}}
        new, _, notes = _norm(card, fmt="cesta")
        assert (new["hook"]["big"], new["hook"]["unit"]) == (num, unit) and notes


def test_jednotka_uz_v_unit_se_nezdvoji():
    card = {"hook": {"big": "800 mil. Kč", "unit": "mil. Kč", "sub": ""}}
    new, _, _ = _norm(card, fmt="cesta")
    assert new["hook"] == {"big": "800", "unit": "mil. Kč", "sub": ""}


def test_cislo_bez_jednotky_nemeni():
    for big in (30, "30", "2 000", "10 → 4", "1,5"):
        card = {"hook": {"big": big, "unit": "dní", "sub": ""}}
        new, cap, notes = _norm(card, fmt="cesta")
        assert new["hook"] == {"big": big, "unit": "dní", "sub": ""} and notes == [] and cap == CAPTION


def test_zmena_bod_bez_citace_vynecha():
    card = load_fixture("v3-zmena-221.json")
    count = len(card["zmena"]["body"])
    card["zmena"]["body"][0]["citace"] = ""
    new, _, notes = _norm(card)
    assert len(new["zmena"]["body"]) == count - 1
    assert any("citace" in n for n in notes)
    keys = [r["key"] for r in new["reasons"]]
    assert validate_variant(new, CAPTION, template="template_v3", ct=221, reason_keys=keys, fmt="zmena") == []


def test_zmena_bez_jedineho_bodu_je_chyba():
    # Review Focus 5: bez jediné citace nevznikne prázdné video, ale chyba
    card = load_fixture("v3-zmena-221.json")
    for b in card["zmena"]["body"]:
        b["citace"] = ""
    new, _, _ = _norm(card)
    keys = [r["key"] for r in new["reasons"]]
    errs = validate_variant(new, CAPTION, template="template_v3", ct=221, reason_keys=keys, fmt="zmena")
    assert any("žádný bod nemá citaci" in e for e in errs), errs


def test_doplni_zaverecnou_vetu():
    card = load_fixture("v3-zmena-221.json")
    new, cap, notes = _norm(copy.deepcopy(card), caption="Krátký popisek.")
    assert cap.startswith("Krátký popisek.") and cap.endswith(CLOSING)
    assert any("Hodnotíme postup" in n for n in notes)
    assert new == card


def test_platna_karta_bez_zasahu():
    card = load_fixture("v3-zmena-221.json")
    new, cap, notes = _norm(card)
    assert new == card and cap == CAPTION and notes == []
