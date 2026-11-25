"""Šablona videa a render karty do MP4."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import hlidac_reels
from conftest import load_fixture
from hlidac_reels.card import Card
from hlidac_reels.render import (LANDSCAPE_SAFE, STAMPS, _page, audio_cues, has_landscape, measure_blocks,
                                 render_reel, scene_ids, stamps_for)


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


LONG_TITLE = ("Promlčecí doba nových přestupků ve střetu zájmů je šest let, souběžně s přílepky "
              "v trestním řádu, dotačních limitech zemědělců a vedení fakultních nemocnic a dalších")


def _long_card(name: str = "card-3.json") -> Card:
    raw = load_fixture(name)
    raw["title"] = LONG_TITLE
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


def test_has_landscape():
    assert has_landscape("template_v2") and has_landscape("template_v3")
    assert not has_landscape("template") and not has_landscape("neexistuje")


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


# ---------- F4: autor zadaného PN se štítkem klubu pod titulkem (šablona v2, karta.pn_autor ze serveru) ----------

V2 = "template_v2"
V2_311 = Path(hlidac_reels.__file__).parent / "generator" / "examples" / "card-v2-311.json"
# ustálený stav scény titulku (poslední okamžik před stěrkou): řádek autorů PN a styl štítků klubů
TITLE_PN_JS = """() => {
  const scene = document.getElementById('s-title');
  let last = null;
  for (let t = 0; t < window.duration(); t += 0.05) {
    window.seek(t);
    if (getComputedStyle(scene).display !== 'none') last = t;
  }
  window.seek(last - 0.25);
  const style = k => {
    const s = getComputedStyle(k);
    return {bg: s.backgroundColor, color: s.color, border: s.borderTopColor, size: s.fontSize,
            transform: s.textTransform, height: k.getBoundingClientRect().height};
  };
  const row = document.getElementById('title-pn');
  if (!row) return {text: '', shown: false, chips: [], who: null};
  const who = style(document.getElementById('who-klub'));
  delete who.height;   // scéna předkladatele na kartě není, porovná se jen vzhled
  return {text: row.textContent, shown: row.offsetParent !== null && +getComputedStyle(row).opacity > 0.99,
          chips: [...row.querySelectorAll('.klub')].map(k => ({text: k.textContent, ...style(k)})), who: who};
}"""


def _v2_311(**change) -> dict:
    raw = json.loads(V2_311.read_text(encoding="utf-8"))
    for key, value in change.items():
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value
    return raw


def _title_pn(raw: dict) -> dict:
    with _page(Card.from_json(raw), V2) as page:
        return page.evaluate(TITLE_PN_JS)


@pytest.mark.render
def test_v2_titulek_autor_pn_se_stitkem():
    row = _title_pn(_v2_311(pn_autor=[{"cislo": 2315, "jmeno": "Marian Jurečka", "klub": "KDU-ČSL"}]))
    assert row["shown"], row
    assert "PN 2315 · Marian Jurečka" in row["text"], row["text"]
    # štítek jako #who-klub, popisek ze serveru bez předpony „klub“
    assert [c["text"] for c in row["chips"]] == ["KDU-ČSL"]
    chip = {k: v for k, v in row["chips"][0].items() if k not in ("text", "height")}
    assert chip == row["who"], (chip, row["who"])


@pytest.mark.render
def test_v2_bez_klubu_bez_stitku():
    # klub server nemá (člen vlády, chybí členství): jméno ano, štítek žádný
    row = _title_pn(_v2_311(pn_autor=[{"cislo": 2315, "jmeno": "Marian Jurečka", "klub": None}]))
    assert row["shown"] and "PN 2315 · Marian Jurečka" in row["text"], row
    assert row["chips"] == []
    # karta bez pn_autor: řádek skrytý
    row = _title_pn(_v2_311(pn_autor=None))
    assert not row["shown"] and row["chips"] == [] and row["text"] == "", row


@pytest.mark.render
def test_v2_dva_kluby_stejny_stitek():
    # neutralita: jeden šedý štítek pro všechny kluby, žádné stranické barvy ani velikosti
    row = _title_pn(_v2_311(pn_autor=[{"cislo": 2315, "jmeno": "Jan Novák", "klub": "ANO"},
                                      {"cislo": 2316, "jmeno": "Marian Jurečka", "klub": "KDU-ČSL"}]))
    chips = row["chips"]
    assert [c["text"] for c in chips] == ["ANO", "KDU-ČSL"]
    style = [{k: v for k, v in c.items() if k != "text"} for c in chips]
    assert style[0] == style[1], style


@pytest.mark.render
@pytest.mark.parametrize("orientation", ["portrait", "landscape"])
def test_v2_dlouha_jmena_nepretekaji(orientation):
    from hlidac_reels.generator.validate import SAFE, check_layout
    from test_v3 import PN_AUTOR_LONG

    # nejhorší případ scény titulku: dlouhý titulek a tři autoři s dlouhými jmény a nejdelším popiskem klubu
    raw = _v2_311(title=LONG_TITLE, pn_autor=PN_AUTOR_LONG)
    if orientation == "portrait":
        assert check_layout(raw, V2) == []
        boxes, safe = measure_blocks(Card.from_json(raw), V2), SAFE
    else:
        boxes = measure_blocks(Card.from_json(raw), V2, "landscape")
        safe = {"top": LANDSCAPE_SAFE, "bottom": 1080 - LANDSCAPE_SAFE,
                "left": LANDSCAPE_SAFE, "right": 1920 - LANDSCAPE_SAFE}
        for key in [k for k in boxes if k.startswith("scene:")]:
            b = boxes[key]
            assert not b["overflow"] and b["overflow_px"] == 0, (key, b)
            assert b["top"] >= safe["top"] and b["bottom"] <= safe["bottom"], (key, b)
            assert b["left"] >= safe["left"] and b["right"] <= safe["right"], (key, b)
    b = boxes["pn_autor"]
    assert b["visible"], b
    assert b["top"] >= safe["top"] and b["bottom"] <= safe["bottom"], b
    assert b["left"] >= safe["left"] and b["right"] <= safe["right"], b
    # červená linka pod řádkem zůstane vidět (16 px): při plné scéně se dřív smrštila na 0 px a fit() přetečení neviděl
    assert boxes["scene:s-title"]["bottom"] >= b["bottom"] + 16, (boxes["scene:s-title"], b)
    # první dva autoři (u tří PN téhož autora dvě čísla a „a další“), třetí autor jen jako „a další“
    text = " ".join(b["text"].split())
    assert "PN 2376, 2377 a další · Jan Novák-Dlouhý" in text and "PN 2379 · Gabriela Svárovská" in text, text
    assert "Činčila" not in text and text.endswith("a další"), text
