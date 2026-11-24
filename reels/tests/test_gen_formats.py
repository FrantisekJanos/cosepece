"""Výběr dvou formátů v3 pro sadu generátoru (spec: Výběr formátů v3)."""
import pytest

from hlidac_reels.generator.formats import pick_formats


def test_zkracena_lhuta_a_pozdni_pn():
    assert pick_formats(["deadline_cut", "late_pn"], has_amendments=False) == ["cesta", "pn"]


def test_poradi_podle_poradi_klicu():
    assert pick_formats(["pn_other_law", "deadline_cut"], has_amendments=False) == ["pn", "cesta"]


def test_jediny_format_doplni_zmena():
    assert pick_formats(["p90_passed"], has_amendments=True) == ["cesta", "zmena"]


def test_jediny_format_bez_novelizace_doplni_pn():
    assert pick_formats(["p90_passed"], has_amendments=False) == ["cesta", "pn"]


def test_zadny_klic():
    assert pick_formats([], has_amendments=True) == ["zmena", "pn"]
    assert pick_formats([], has_amendments=False) == ["pn", "cesta"]


def test_video_k_pn_ma_pevne_formaty():
    # zadání /pn: oba formáty v3 o obsahu PN, hint ani klíče důvodů nerozhodují
    assert pick_formats(["deadline_cut"], False, hint="cesta", focus=True) == ["pn", "zmena"]
    assert pick_formats([], True, focus=True) == ["pn", "zmena"]


def test_focus_bez_textu_jen_pn():
    # PN bez textu: varianta zmena nemá z čeho citovat, sada má jen v2 a pn
    assert pick_formats(["deadline_cut"], True, focus=True, has_text=False) == ["pn"]
    assert pick_formats([], True, focus=True, has_text=True) == ["pn", "zmena"]
    # bez /pn se has_text nebere
    assert pick_formats([], True, has_text=False) == ["zmena", "pn"]


def test_hint_je_prvni():
    assert pick_formats(["deadline_cut", "late_pn"], False, hint="zmena") == ["zmena", "cesta"]


def test_rozprava_jen_z_hintu():
    assert "rozprava" not in pick_formats(["deadline_cut", "late_pn", "pn_other_law"], True)
    assert pick_formats(["deadline_cut"], False, hint="rozprava")[0] == "rozprava"


def test_neznamy_hint_vyhodi_valueerror():
    with pytest.raises(ValueError):
        pick_formats([], False, hint="video")
