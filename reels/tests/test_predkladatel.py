"""Volitelné scény předkladatele v šabloně v2: „Kdo to přináší“ a „Úvodní slovo na plénu“."""
from __future__ import annotations

import copy

import pytest

from conftest import load_fixture
from hlidac_reels.card import Card
from hlidac_reels.render import audio_cues, measure_blocks, scene_ids

V2 = "template_v2"
SAFE = {"top": 150, "bottom": 1920 - 340, "left": 60, "right": 1020}


def _card(name: str, **change) -> Card:
    raw = copy.deepcopy(load_fixture(name))
    for key, value in change.items():
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value
    return Card.from_json(raw)


def test_card_keeps_predkladatel_in_extras():
    card = _card("card-174-predkladatel.json")
    assert card.extras["predkladatel"]["skupina"]["count"] == 9
    assert card.to_dict()["predkladatel"]["citace"]["autorizovano"] is False


@pytest.mark.render
def test_scenes_only_with_predkladatel_field():
    # stávající karta bez pole: žádná nová scéna
    assert not {"s-who", "s-said"} & set(scene_ids(_card("card-1-v2.json"), V2))
    ids = scene_ids(_card("card-174-predkladatel.json"), V2)
    assert ids[:3] == ["s-hook", "s-who", "s-said"]
    assert ids[-1] == "s-end"
    # stejná karta bez pole: scény zmizí
    assert not {"s-who", "s-said"} & set(scene_ids(_card("card-174-predkladatel.json", predkladatel=None), V2))
    # předkladatel bez citace (tisk ještě nebyl na plénu): jen „Kdo to přináší“
    ids = scene_ids(_card("card-221-predkladatel.json"), V2)
    assert "s-who" in ids and "s-said" not in ids


@pytest.mark.render
def test_citation_without_source_url_is_not_shown():
    raw = load_fixture("card-174-predkladatel.json")
    pk = copy.deepcopy(raw["predkladatel"])
    del pk["citace"]["url"]
    ids = scene_ids(_card("card-174-predkladatel.json", predkladatel=pk), V2)
    assert "s-who" in ids and "s-said" not in ids


@pytest.mark.render
def test_unauthorized_label_visible_only_when_not_authorized():
    boxes = measure_blocks(_card("card-174-predkladatel.json"), V2)   # autorizovano: false
    tag = boxes["said_unauth"]
    assert tag["visible"]
    assert tag["text"] == "neautorizováno"
    for side in ("top", "left"):
        assert tag[side] >= SAFE[side]
    for side in ("bottom", "right"):
        assert tag[side] <= SAFE[side]
    boxes = measure_blocks(_card("card-311-predkladatel.json"), V2)   # autorizovano: true
    assert not boxes["said_unauth"]["visible"]
    # chybějící údaj o autorizaci se bere jako neautorizováno
    raw = load_fixture("card-311-predkladatel.json")
    pk = copy.deepcopy(raw["predkladatel"])
    del pk["citace"]["autorizovano"]
    boxes = measure_blocks(_card("card-311-predkladatel.json", predkladatel=pk), V2)
    assert boxes["said_unauth"]["visible"]


@pytest.mark.render
@pytest.mark.parametrize("ct", [311, 174, 115, 213, 221])
def test_predkladatel_blocks_in_safe_zone(ct):
    card = _card(f"card-{ct}-predkladatel.json")
    boxes = measure_blocks(card, V2)
    for name in ("who", "said"):
        if name not in boxes:
            continue
        b = boxes[name]
        assert b["visible"], name
        assert b["top"] >= SAFE["top"] and b["bottom"] <= SAFE["bottom"], name
        assert b["left"] >= SAFE["left"] and b["right"] <= SAFE["right"], name
        assert not b["cut_mid_word"], name


@pytest.mark.render
def test_long_citation_is_fitted_on_whole_word():
    raw = load_fixture("card-213-predkladatel.json")
    pk = copy.deepcopy(raw["predkladatel"])
    pk["citace"]["text"] = "Navrhovaná opatření reagují na zkušenosti z posledních let při zvládání migrace. " * 12
    b = measure_blocks(_card("card-213-predkladatel.json", predkladatel=pk), V2)["said"]
    assert b["top"] >= SAFE["top"] and b["bottom"] <= SAFE["bottom"]
    assert b["left"] >= SAFE["left"] and b["right"] <= SAFE["right"]
    assert not b["cut_mid_word"]
    assert b["text"].rstrip().endswith("…")


@pytest.mark.render
def test_predkladatel_reel_fits_30_s_and_has_cues():
    from hlidac_reels.render import _page

    for ct in (311, 174, 115, 213, 221):
        card = _card(f"card-{ct}-predkladatel.json")
        with _page(card, V2) as page:
            assert 15 <= page.evaluate("window.duration()") <= 30.001, ct
    cues = audio_cues(_card("card-174-predkladatel.json"), V2)
    times = [c["t"] for c in cues]
    assert times == sorted(times)
    # 9 navrhovatelů = 9 tiků ve scéně „Kdo to přináší“ (mezi 1. a 2. stěrkou), citace se píše na stroji
    w = [c["t"] for c in cues if c["type"] == "whoosh"]
    assert sum(c["type"] == "tick" and w[0] < c["t"] < w[1] for c in cues) == 9
    assert sum(c["type"] == "type" and w[1] < c["t"] < w[2] for c in cues) > 10
