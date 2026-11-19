"""Šablona videa a render karty do MP4."""
from __future__ import annotations

import json
import subprocess

import pytest

from conftest import load_fixture
from hlidac_reels.card import Card
from hlidac_reels.render import LANDSCAPE_SAFE, STAMPS, audio_cues, measure_blocks, render_reel, scene_ids, stamps_for


def test_card_from_json_roundtrip():
    raw = load_fixture("card-1.json")
    card = Card.from_json(json.dumps(raw, ensure_ascii=False))
    assert card.ct == 311
    assert isinstance(card.reasons, list)
    assert card.reasons[0]["key"] == raw["reasons"][0]["key"]
    assert Card.from_json(raw).ct == 311


def _with_reasons(keys: list[str]) -> Card:
    raw = load_fixture("card-1.json")
    raw["reasons"] = [{"key": k, "label": k, "points": 10} for k in keys]
    return Card.from_json(raw)


def test_stamps_only_for_mapped_keys():
    assert stamps_for(_with_reasons(["mp_bill", "deadline_cut"])) == ["ZKRÁTILI"]
    assert stamps_for(_with_reasons(["mp_bill"])) == []
    assert STAMPS == {"deadline_cut": "ZKRÁTILI", "pn_other_law": "PŘÍLEPEK", "p90_passed": "§ 90/2"}


def _probe(path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


@pytest.mark.render
def test_render_produces_valid_mp4(tmp_path):
    card = Card.from_json(load_fixture("card-1.json"))
    result = render_reel(card, tmp_path / "reel.mp4")
    assert result.path.exists()
    info = _probe(result.path)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert video["codec_name"] == "h264"
    assert video["pix_fmt"] == "yuv420p"
    assert audio["codec_name"] == "aac"
    assert 15 <= float(info["format"]["duration"]) <= 25.5


def _long_card(name: str = "card-3.json") -> Card:
    raw = load_fixture(name)
    raw["title"] = ("Promlčecí doba nových přestupků ve střetu zájmů je šest let, souběžně s přílepky "
                    "v trestním řádu, dotačních limitech zemědělců a vedení fakultních nemocnic a dalších")
    assert len(raw["title"]) >= 160
    raw["quote"] = "„" + "Souhrnná částka BISS vyplácená v jednom kalendářním roce žadatelům " * 4 + "“"
    return Card.from_json(raw)


@pytest.mark.render
def test_long_title_stays_in_safe_zone():
    boxes = measure_blocks(_long_card())
    for name in ("title", "perex", "quote"):
        b = boxes[name]
        assert b["top"] >= 150, name
        assert b["bottom"] <= 1920 - 340, name
        assert b["left"] >= 60, name
        assert b["right"] <= 1020, name
        assert not b["cut_mid_word"], name


# ---------- na šířku (1920 × 1080 pro X.com, šablona v2) ----------


def test_landscape_needs_known_orientation_and_template():
    card = Card.from_json(load_fixture("card-1.json"))
    with pytest.raises(ValueError):
        measure_blocks(card, "template_v2", "diagonal")
    with pytest.raises(ValueError):
        measure_blocks(card, "template", "landscape")      # v1 rozvržení na šířku nemá


@pytest.mark.render
@pytest.mark.parametrize("name", ["card-3.json", "card-1-v2.json", "card-2-v2.json"])
def test_landscape_long_title_stays_in_safe_zone(name):
    card = _long_card(name)
    card.perex = (card.perex + " ") * 4
    boxes = measure_blocks(card, "template_v2", "landscape")
    low, right, bottom = LANDSCAPE_SAFE, 1920 - LANDSCAPE_SAFE, 1080 - LANDSCAPE_SAFE
    scenes = [k for k in boxes if k.startswith("scene:")]
    assert len(scenes) >= 6
    for key in ["title", "perex", "quote", *scenes]:
        b = boxes[key]
        assert b["top"] >= low and b["bottom"] <= bottom, (key, b)
        assert b["left"] >= low and b["right"] <= right, (key, b)
        assert not b.get("cut_mid_word"), key
        assert not b.get("overflow"), key


@pytest.mark.render
def test_landscape_keeps_scenes_and_duration():
    card = Card.from_json(load_fixture("card-1-v2.json"))
    assert scene_ids(card, "template_v2", "landscape") == scene_ids(card, "template_v2")
    portrait, land = audio_cues(card, "template_v2"), audio_cues(card, "template_v2", "landscape")
    # stejné stěrky, razítka a závěr ve stejných časech; počet řádků perexu (zvuky „line“) se s šířkou liší
    fixed = ("whoosh", "outro", "stamp", "hit")
    assert [c for c in land if c["type"] in fixed] == [c for c in portrait if c["type"] in fixed]


@pytest.mark.render
def test_landscape_render_is_1920x1080_with_sound(tmp_path):
    card = Card.from_json(load_fixture("card-1.json"))
    result = render_reel(card, tmp_path / "reel-sirka.mp4", template="template_v2", orientation="landscape")
    info = _probe(result.path)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (video["width"], video["height"]) == (1920, 1080)
    assert video["r_frame_rate"] == "30/1" and video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p"
    assert audio["codec_name"] == "aac" and int(audio["sample_rate"]) == 48000
    assert abs(float(video["duration"]) - result.duration_s) <= 1 / 30
    assert abs(float(audio["duration"]) - float(video["duration"])) <= 2 / 30
