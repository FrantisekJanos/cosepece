"""Šablona v3: formáty z dat MCP (cesta, zmena, rozprava, pn). Scény formátu jen s jeho daty, bezpečná zóna."""
from __future__ import annotations

import copy
import json
import subprocess

import pytest

from conftest import FIXTURES, load_fixture
from hlidac_reels.card import Card
from hlidac_reels.render import LANDSCAPE_SAFE, _page, audio_cues, measure_blocks, render_reel, scene_ids, stamps_for

V3 = "template_v3"
SAFE = {"top": 150, "bottom": 1920 - 340, "left": 60, "right": 1020}
# na šířku (1920 × 1080, X.com): stejný okraj dokola
SAFE_LAND = {"top": LANDSCAPE_SAFE, "bottom": 1080 - LANDSCAPE_SAFE, "left": LANDSCAPE_SAFE, "right": 1920 - LANDSCAPE_SAFE}
ALL = sorted(p.name for p in FIXTURES.glob("v3-*.json"))
COMMON = {"s-hook", "s-ctx", "s-end"}
PREFIX = {"cesta": ("s-c-",), "zmena": ("s-z-",), "rozprava": ("s-r-",), "pn": ("s-p-",)}
LONG = "Velmi dlouhý text, který má prověřit vměstnání do bezpečné zóny a zkrácení na celé slovo bez přetečení. " * 6
CUE_TYPES = {"whoosh", "tick", "hit", "stamp", "type", "highlight", "drop", "line", "slide", "strike", "pop", "outro"}


def _card(name: str, **change) -> Card:
    raw = copy.deepcopy(load_fixture(name))
    for key, value in change.items():
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value
    return Card.from_json(raw)


def _format_scenes(ids: list[str]) -> set[str]:
    # s-dny (graf PN po dnech) sdílí formáty cesta a pn
    return set(ids) - COMMON - {"s-dny"}


def _assert_safe(boxes: dict, safe: dict = SAFE) -> None:
    scenes = {k: v for k, v in boxes.items() if k.startswith("scene:")}
    assert scenes
    for name, b in scenes.items():
        assert not b["overflow"], name
        assert not b["cut_mid_word"], name
        assert b["top"] >= safe["top"] and b["bottom"] <= safe["bottom"], (name, b)
        assert b["left"] >= safe["left"] and b["right"] <= safe["right"], (name, b)


def test_fixtures_cover_all_formats():
    formats = {load_fixture(n)["format"] for n in ALL}
    assert formats == {"cesta", "zmena", "rozprava", "pn"}
    for name in ALL:
        card = _card(name)
        assert card.extras["format"] in name
        assert card.to_dict()[card.extras["format"]]


@pytest.mark.render
@pytest.mark.parametrize("name", ALL)
def test_format_shows_only_its_own_scenes(name):
    fmt = load_fixture(name)["format"]
    ids = scene_ids(_card(name), V3)
    assert ids[0] == "s-hook" and ids[-1] == "s-end"
    own = _format_scenes(ids)
    assert own and all(i.startswith(PREFIX[fmt]) for i in own), ids
    # bez dat formátu zůstanou jen společné scény
    assert not _format_scenes(scene_ids(_card(name, **{fmt: None}), V3))


@pytest.mark.render
def test_foreign_format_data_are_ignored():
    # karta formátu „cesta“ s přibalenými daty rozpravy, změn a PN: scény cizích formátů se nezařadí
    extra = {"rozprava": load_fixture("v3-rozprava-311.json")["rozprava"], "pn": load_fixture("v3-pn-311.json")["pn"],
             "zmena": load_fixture("v3-zmena-311.json")["zmena"]}
    ids = scene_ids(_card("v3-cesta-311.json", **extra), V3)
    assert all(i.startswith("s-c-") for i in _format_scenes(ids)), ids
    # neznámý formát: jen společné scény
    assert not _format_scenes(scene_ids(_card("v3-cesta-311.json", format="neznamy"), V3))


@pytest.mark.render
def test_rozprava_needs_both_sides_and_links():
    raw = load_fixture("v3-rozprava-311.json")
    full = scene_ids(_card("v3-rozprava-311.json"), V3)
    assert {"s-r-kdo", "s-r-pro", "s-r-proti", "s-r-odp"} <= set(full)
    # vyváženost: bez námitek (nebo bez důvodů předkladatele) se shrnutí argumentů neukáže vůbec
    for side in ("pro", "namitky"):
        r = copy.deepcopy(raw["rozprava"])
        r[side]["body"] = []
        ids = scene_ids(_card("v3-rozprava-311.json", rozprava=r), V3)
        assert "s-r-kdo" in ids and not {"s-r-pro", "s-r-proti", "s-r-odp"} & set(ids), side
    # bod bez odkazu na projev se nezobrazí
    r = copy.deepcopy(raw["rozprava"])
    for b in r["namitky"]["body"]:
        del b["url"]
    assert "s-r-proti" not in scene_ids(_card("v3-rozprava-311.json", rozprava=r), V3)


@pytest.mark.render
def test_rozprava_ai_label_and_unauthorized_are_visible():
    boxes = measure_blocks(_card("v3-rozprava-311.json"), V3)
    for key in ("r_pro_ai", "r_proti_ai", "r_odp_ai"):
        assert boxes[key]["visible"] and "AI" in boxes[key]["text"], key
    assert "Shrnutí AI" in boxes["ai_note"]["text"] and boxes["ai_note"]["visible"]
    assert boxes["rk_unauth"]["visible"] and boxes["rk_unauth"]["text"].startswith("19 z 40")
    assert not any(k.startswith("r_pro_unauth") for k in boxes)      # autorizované projevy štítek nemají
    # neautorizovaný projev (i chybějící údaj) dostane štítek u svého bodu
    r = copy.deepcopy(load_fixture("v3-rozprava-311.json")["rozprava"])
    r["pro"]["body"][0]["autorizovano"] = False
    del r["pro"]["body"][1]["autorizovano"]
    boxes = measure_blocks(_card("v3-rozprava-311.json", rozprava=r), V3)
    for i in (1, 2):
        assert boxes[f"r_pro_unauth_{i}"]["visible"] and boxes[f"r_pro_unauth_{i}"]["text"] == "neautorizováno"
    assert "r_pro_unauth_3" not in boxes


@pytest.mark.render
def test_zmena_needs_literal_citation_and_source():
    raw = load_fixture("v3-zmena-221.json")
    assert {"s-z-1", "s-z-2", "s-z-3", "s-z-proc"} <= set(scene_ids(_card("v3-zmena-221.json"), V3))
    z = copy.deepcopy(raw["zmena"])
    del z["body"][0]["citace"]
    del z["body"][1]["zdroj"]
    ids = scene_ids(_card("v3-zmena-221.json", zmena=z), V3)
    assert "s-z-1" in ids and "s-z-2" not in ids      # zbyla jediná doložená změna
    boxes = measure_blocks(_card("v3-zmena-221.json"), V3)
    assert "„10“" in boxes["zcit1"]["text"] and boxes["zsrc1"]["text"].startswith("zdroj: tisk 221")


@pytest.mark.render
def test_stamps_only_with_reason_key():
    def stamps(card: Card) -> int:
        with _page(card, V3) as page:
            return page.evaluate("document.querySelectorAll('#cn-stamps .stamp, #pj-stamps .stamp').length")

    c311 = _card("v3-cesta-311.json")
    assert stamps_for(c311) == ["ZKRÁTILI"] and stamps(c311) == 1
    assert stamps(_card("v3-cesta-311.json", reasons=[])) == 0
    # tisk 174: krátká pauza před 3. čtením není klíč deadline_cut, razítko ZKRÁTILI se neukáže
    assert stamps(_card("v3-cesta-174.json")) == 0
    assert stamps(_card("v3-pn-115.json")) == 1
    assert stamps(_card("v3-pn-115.json", reasons=[])) == 0


@pytest.mark.render
@pytest.mark.parametrize("name", ALL)
def test_fixture_blocks_in_safe_zone(name):
    _assert_safe(measure_blocks(_card(name), V3))


def _long_card(name: str) -> tuple[Card, str]:
    """Karta formátu s extrémně dlouhými texty ve všech polích (zkouška vměstnání)."""
    raw = copy.deepcopy(load_fixture(name))
    fmt = raw["format"]
    raw["title"] = LONG[:170]
    raw["perex"] = LONG
    raw["nazev"] = LONG[:140]
    d = raw[fmt]
    if fmt == "cesta":
        for k in d["kroky"]:
            k["label"] = LONG[:160]
        d["ferove"] = LONG
        raw["pn_dny"] = {"dny": [{"date": f"2026-10-{10 + i}", "count": 1 + i * 7} for i in range(9)],
                         "note": LONG, "sub": LONG[:200]}
    elif fmt == "zmena":
        for b in d["body"]:
            b["co"], b["pred"], b["po"], b["citace"] = LONG[:150], LONG[:200], LONG[:220], LONG
        d["proc"]["text"] = d["pozor"]["text"] = LONG
    elif fmt == "rozprava":
        for side in ("pro", "namitky", "odpovedi"):
            for b in d[side]["body"]:
                b["text"] = LONG[:320]
                b["autorizovano"] = False
                b["kluby"] = ["ANO2011", "KDU-ČSL", "TOP09", "Piráti"]
        d["namitky"]["pozn"] = LONG
        d["kluby"] += [{"klub": "SPD", "projevu": 3}, {"klub": "Motoristé", "projevu": 2}]
    else:
        d["pozn"] = d["jine_pozn"] = LONG
        for x in d["top"]:
            x["titulek"] = LONG[:300]
        for x in d["autori"]:
            x["jmeno"] = "Jméno Velmidlouhépříjmení Dvojitépříjmení"
    return Card.from_json(raw), fmt


LONG_CARDS = ["v3-cesta-174.json", "v3-zmena-221.json", "v3-rozprava-311.json", "v3-pn-115.json"]


@pytest.mark.render
@pytest.mark.parametrize("name", LONG_CARDS)
def test_long_texts_stay_in_safe_zone(name):
    card, fmt = _long_card(name)
    boxes = measure_blocks(card, V3)
    _assert_safe(boxes)
    # dlouhý text se buď zmenší, nebo zkrátí; zkrácení jen na celé slovo
    if fmt in ("cesta", "pn"):
        assert boxes["title"]["truncated"] or boxes["perex"]["truncated"]
    assert not boxes["title"]["cut_mid_word"] and not boxes["perex"]["cut_mid_word"]


# ---------- na šířku (1920 × 1080 pro X.com) ----------


@pytest.mark.render
@pytest.mark.parametrize("name", ALL)
def test_landscape_fixture_blocks_in_safe_zone(name):
    _assert_safe(measure_blocks(_card(name), V3, "landscape"), SAFE_LAND)


@pytest.mark.render
@pytest.mark.parametrize("name", LONG_CARDS)
def test_landscape_long_texts_stay_in_safe_zone(name):
    card, _ = _long_card(name)
    boxes = measure_blocks(card, V3, "landscape")
    _assert_safe(boxes, SAFE_LAND)
    t = boxes["title"]
    assert t["top"] >= SAFE_LAND["top"] and t["bottom"] <= SAFE_LAND["bottom"], t
    assert t["left"] >= SAFE_LAND["left"] and t["right"] <= SAFE_LAND["right"], t
    assert not t["cut_mid_word"] and not boxes["perex"]["cut_mid_word"]


@pytest.mark.render
@pytest.mark.parametrize("name", ALL)
def test_landscape_keeps_scenes_duration_and_cues(name):
    # na šířku se mění jen rozvržení: scény, délka i zvukové události zůstávají
    card = _card(name)
    assert scene_ids(card, V3, "landscape") == scene_ids(card, V3)
    with _page(card, V3) as page:
        portrait = page.evaluate("window.duration()")
        assert not page.evaluate("document.documentElement.classList.contains('landscape')")
    with _page(card, V3, "landscape") as page:
        assert page.evaluate("window.duration()") == portrait
        assert page.evaluate("document.documentElement.classList.contains('landscape')")
        assert page.evaluate("[window.innerWidth, window.innerHeight]") == [1920, 1080]
    assert audio_cues(card, V3, "landscape") == audio_cues(card, V3)


@pytest.mark.render
def test_landscape_render_is_1920x1080_with_sound(tmp_path):
    card = _card("v3-cesta-174.json")
    result = render_reel(card, tmp_path / "reel-sirka.mp4", template=V3, orientation="landscape")
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(result.path)],
                         capture_output=True, text=True, check=True)
    streams = json.loads(out.stdout)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    audio = next(s for s in streams if s["codec_type"] == "audio")
    assert (video["width"], video["height"]) == (1920, 1080)
    assert video["r_frame_rate"] == "30/1" and video["pix_fmt"] == "yuv420p"
    assert audio["codec_name"] == "aac" and int(audio["sample_rate"]) == 48000
    assert abs(float(video["duration"]) - result.duration_s) <= 1 / 30
    assert abs(float(audio["duration"]) - float(video["duration"])) <= 2 / 30


@pytest.mark.render
def test_v3_duration_and_cues():
    for name in ALL:
        with _page(_card(name), V3) as page:
            assert 15 <= page.evaluate("window.duration()") <= 40.001, name
    card = _card("v3-cesta-311.json")
    cues = audio_cues(card, V3)
    times = [c["t"] for c in cues]
    assert times == sorted(times) and times[0] >= 0
    n = len(scene_ids(card, V3))
    assert sum(c["type"] == "whoosh" for c in cues) == n - 1
    assert sum(c["type"] == "outro" for c in cues) == 1
    assert sum(c["type"] == "stamp" for c in cues) == 1          # jediné razítko ZKRÁTILI
    assert {c["type"] for c in cues} <= CUE_TYPES


@pytest.mark.render
def test_v3_seek_is_deterministic():
    # stejný čas dává stejný obraz bez ohledu na to, odkud se na něj skočí
    with _page(_card("v3-zmena-311.json"), V3) as page:
        page.evaluate("window.seek(12.4)")
        a = page.screenshot(type="png")
        page.evaluate("window.seek(31.0)")
        page.evaluate("window.seek(3.1)")
        page.evaluate("window.seek(12.4)")
        assert page.screenshot(type="png") == a
