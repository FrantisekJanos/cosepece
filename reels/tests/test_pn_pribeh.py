"""Šablona template_pn_v1: příběh jednoho pozměňovacího návrhu (kdo, co, kdy, proč, jak dopadl, dopad).
Scény jen s doloženými daty, bezpečná zóna na výšku i na šířku, dlouhé texty bez přetečení."""
from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

import hlidac_reels
from conftest import FIXTURES, load_fixture
from hlidac_reels.card import Card
from hlidac_reels.render import LANDSCAPE_SAFE, _page, audio_cues, has_landscape, measure_blocks, scene_ids, stamps_for

PN = "template_pn_v1"
SAFE = {"top": 150, "bottom": 1920 - 340, "left": 60, "right": 1020}
SAFE_LAND = {"top": LANDSCAPE_SAFE, "bottom": 1080 - LANDSCAPE_SAFE, "left": LANDSCAPE_SAFE, "right": 1920 - LANDSCAPE_SAFE}
ALL = sorted(p.name for p in FIXTURES.glob("pribeh-pn-*.json"))
STORY = ["s-n-kdo", "s-n-co", "s-n-kdy", "s-n-naval", "s-n-znaky", "s-n-vys", "s-n-dopad"]
LONG = "Velmi dlouhý text, který má prověřit vměstnání do bezpečné zóny a zkrácení na celé slovo bez přetečení. " * 6
# slovo bez mezery delší než sloupec: musí se zalomit uvnitř, ne vytlačit scénu do šířky
WORD = "Nejneobhospodařovávatelnějšímunejneobhospodařovávatelnějšímu"
CUE_TYPES = {"whoosh", "tick", "hit", "stamp", "type", "highlight", "drop", "line", "slide", "strike", "pop", "outro"}
TEMPLATE_DIR = Path(hlidac_reels.__file__).parent / PN


def _card(name: str, **change) -> Card:
    raw = copy.deepcopy(load_fixture(name))
    for key, value in change.items():
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value
    return Card.from_json(raw)


def _story(name: str, **change) -> Card:
    """Karta se změněnými poli bloku pribeh (None = pole odebrat)."""
    raw = copy.deepcopy(load_fixture(name))
    for key, value in change.items():
        if value is None:
            raw["pribeh"].pop(key, None)
        else:
            raw["pribeh"][key] = value
    return Card.from_json(raw)


def _assert_safe(boxes: dict, safe: dict = SAFE) -> None:
    scenes = {k: v for k, v in boxes.items() if k.startswith("scene:")}
    assert scenes
    for name, b in scenes.items():
        assert not b["overflow"], (name, b)
        assert b["overflow_px"] == 0, (name, b)
        assert not b["cut_mid_word"], name
        assert b["top"] >= safe["top"] and b["bottom"] <= safe["bottom"], (name, b)
        assert b["left"] >= safe["left"] and b["right"] <= safe["right"], (name, b)


def test_fixtures_and_template_files():
    assert len(ALL) == 3
    states = {load_fixture(n)["pribeh"]["vysledek"]["stav"] for n in ALL}
    assert states == {"prijat", "castecne", "ceka"}
    for name in ALL:
        raw = load_fixture(name)
        assert raw["format"] == "pribeh" and str(raw["pribeh"]["cislo"]) in name
    assert has_landscape(PN)
    # pohyb jen přes seek(t): žádné CSS animace ani časovače, písma jen místní
    for css in TEMPLATE_DIR.glob("*.css"):
        assert not re.search(r"\b(transition|animation)\s*:", css.read_text(encoding="utf-8")), css.name
    js = (TEMPLATE_DIR / "reel.js").read_text(encoding="utf-8")
    assert not re.search(r"setTimeout|setInterval|requestAnimationFrame|Date\.now|Math\.random", js)
    html = (TEMPLATE_DIR / "reel.html").read_text(encoding="utf-8")
    assert "http" not in html and "../template/fonts.css" in html


def test_klub_labels_match_v3():
    # záloha popisků klubů musí být stejná jako ve v3 (tu hlídá test_clubs proti Clubs::label v PHP)
    def kluby(template: str) -> str:
        src = (Path(hlidac_reels.__file__).parent / template / "reel.js").read_text(encoding="utf-8")
        return re.search(r"var KLUBY\s*=\s*\{(.*?)\};", src, re.S).group(1)

    assert kluby(PN) == kluby("template_v3")


@pytest.mark.render
@pytest.mark.parametrize("name", ALL)
def test_story_scene_order(name):
    ids = scene_ids(_card(name), PN)
    assert ids[0] == "s-hook" and ids[-1] == "s-end"
    middle = ids[1:-1]
    assert middle == [s for s in STORY if s in middle]              # pořadí příběhu se nemění
    assert {"s-n-kdo", "s-n-co", "s-n-vys", "s-n-dopad"} <= set(middle)
    assert ("s-n-naval" in middle) == ("naval" in load_fixture(name)["pribeh"])


@pytest.mark.render
def test_without_story_data_only_common_scenes():
    assert scene_ids(_card(ALL[0], pribeh=None), PN) == ["s-hook", "s-end"]
    assert scene_ids(_card(ALL[0], format="pn"), PN) == ["s-hook", "s-end"]
    # bez čísla PN nebo bez autora se příběh nestaví
    assert scene_ids(_story(ALL[0], cislo=None), PN) == ["s-hook", "s-end"]
    assert scene_ids(_story(ALL[0], autor={"klub": "MS"}), PN) == ["s-hook", "s-end"]


@pytest.mark.render
def test_change_needs_literal_citation_and_source():
    raw = load_fixture("pribeh-pn-1796.json")["pribeh"]
    for missing in ("citace", "zdroj", "po"):
        co = {k: v for k, v in raw["co"].items() if k != missing}
        assert "s-n-co" not in scene_ids(_story("pribeh-pn-1796.json", co=co), PN), missing
    boxes = measure_blocks(_card("pribeh-pn-1796.json"), PN)
    assert "„18 362 Kč“" in boxes["co_cit"]["text"] and boxes["co_src"]["text"].startswith("zdroj: PN 1796")
    assert boxes["co_pred"]["text"] == "18 362 Kč" and boxes["co_po"]["text"] == "17 540 Kč"


@pytest.mark.render
def test_verdict_needs_documented_vote():
    raw = load_fixture("pribeh-pn-1796.json")["pribeh"]
    # přijat / zamítnut bez hlasování s odkazem se neukáže vůbec
    for stav in ("prijat", "zamitnut", "castecne"):
        v = dict(raw["vysledek"], stav=stav, hlasovani=[])
        assert "s-n-vys" not in scene_ids(_story("pribeh-pn-1796.json", vysledek=v), PN), stav
    vote = dict(raw["vysledek"]["hlasovani"][0])
    del vote["url"]
    v = dict(raw["vysledek"], hlasovani=[vote])
    assert "s-n-vys" not in scene_ids(_story("pribeh-pn-1796.json", vysledek=v), PN)
    # neznámý stav se neukáže, „čeká“ hlasování nepotřebuje
    assert "s-n-vys" not in scene_ids(_story("pribeh-pn-1796.json", vysledek=dict(raw["vysledek"], stav="vyhral")), PN)
    assert "s-n-vys" in scene_ids(_story("pribeh-pn-1796.json", vysledek={"stav": "ceka", "text": "Zatím se nehlasovalo."}), PN)


@pytest.mark.render
def test_verdict_words_and_vote_results():
    expect = {"pribeh-pn-1796.json": "PŘIJAT", "pribeh-pn-1813.json": "PŘIJAT ZČÁSTI", "pribeh-pn-2364.json": "ČEKÁ"}
    for name, word in expect.items():
        boxes = measure_blocks(_card(name), PN)
        assert boxes["verdikt"]["text"].strip() == word and boxes["verdikt"]["visible"], name
    boxes = measure_blocks(_card("pribeh-pn-1813.json"), PN)
    assert boxes["hl_vysledek_1"]["text"] == "přijato" and boxes["hl_vysledek_2"]["text"] == "zamítnuto"
    assert "hl_vysledek_1" not in measure_blocks(_card("pribeh-pn-2364.json"), PN)
    # zamítnutý návrh: verdikt i výsledek hlasování
    raw = load_fixture("pribeh-pn-1813.json")["pribeh"]
    v = dict(raw["vysledek"], stav="zamitnut", hlasovani=[raw["vysledek"]["hlasovani"][1]])
    boxes = measure_blocks(_story("pribeh-pn-1813.json", vysledek=v), PN)
    assert boxes["verdikt"]["text"].strip() == "ZAMÍTNUT" and boxes["hl_vysledek_1"]["text"] == "zamítnuto"
    _assert_safe(boxes)


@pytest.mark.render
def test_flags_need_source_and_stamp_needs_reason_key():
    def stamps(card: Card) -> int:
        with _page(card, PN) as page:
            return page.evaluate("document.querySelectorAll('#zn-stamps .stamp').length")

    c1813 = _card("pribeh-pn-1813.json")
    assert "PŘÍLEPEK" in stamps_for(c1813) and stamps(c1813) == 1
    # bez klíče důvodu pn_other_law razítko není, i když znak „přílepek“ v kartě je
    assert stamps(_card("pribeh-pn-1813.json", reasons=[])) == 0
    # klíč důvodu má tisk 235 kvůli jinému PN: návrh 1796 bez znaku „přílepek“ razítko nedostane
    c1796 = _card("pribeh-pn-1796.json")
    assert "PŘÍLEPEK" in stamps_for(c1796) and stamps(c1796) == 0
    # znak bez zdroje vypadne; bez znaků se scéna nezařadí
    raw = load_fixture("pribeh-pn-1813.json")["pribeh"]
    flags = [{k: v for k, v in f.items() if k != "zdroj"} for f in raw["znaky"]]
    assert "s-n-znaky" not in scene_ids(_story("pribeh-pn-1813.json", znaky=flags), PN)


@pytest.mark.render
def test_ai_labels_are_visible():
    boxes = measure_blocks(_card("pribeh-pn-2364.json"), PN)
    assert boxes["dopad_ai"]["visible"] and "AI" in boxes["dopad_ai"]["text"]
    assert boxes["znak_ai_3"]["visible"] and "AI" in boxes["znak_ai_3"]["text"]
    assert "znak_ai_1" not in boxes and "znak_ai_2" not in boxes          # znaky z textu PN štítek AI nemají
    assert "AI" in boxes["ai_note"]["text"] and boxes["ai_note"]["visible"]
    # chybějící údaj o původu shrnutí se bere jako AI
    raw = load_fixture("pribeh-pn-2364.json")["pribeh"]
    d = {k: v for k, v in raw["dopad"].items() if k != "ai"}
    assert measure_blocks(_story("pribeh-pn-2364.json", dopad=d), PN)["dopad_ai"]["visible"]


@pytest.mark.render
def test_author_club_from_server_wins():
    # klub ke dni podání posílá server v pn_autor; cizí PN v seznamu se do háčku nedostane
    extra = [{"cislo": 1796, "jmeno": "Boris Šťastný", "klub": "ODS"}, {"cislo": 9999, "jmeno": "Jiný Poslanec", "klub": "SPD"}]
    text = measure_blocks(_card("pribeh-pn-1796.json", pn_autor=extra), PN)["pn_autor"]["text"]
    assert "Boris Šťastný" in text and "ODS" in text and "Jiný" not in text
    text = measure_blocks(_card("pribeh-pn-1796.json"), PN)["pn_autor"]["text"]
    assert "PN 1796 · Boris Šťastný" in text and "Motoristé" in text


@pytest.mark.render
@pytest.mark.parametrize("name", ALL)
def test_fixture_blocks_in_safe_zone(name):
    _assert_safe(measure_blocks(_card(name), PN))


@pytest.mark.render
@pytest.mark.parametrize("name", ALL)
def test_landscape_fixture_blocks_in_safe_zone(name):
    _assert_safe(measure_blocks(_card(name), PN, "landscape"), SAFE_LAND)


def _long_card(filler: str, drop: tuple = ()) -> Card:
    """Všechna textová pole na doraz a všechny seznamy v plném počtu (nejhorší případ pro vměstnání).
    drop = bloky příběhu, které se vynechají (kratší video, do kterého se vejdou i volitelné scény)."""
    raw = copy.deepcopy(load_fixture("pribeh-pn-1813.json"))
    kluby = [{"klub": k, "pro": 20 - i, "proti": i, "zdrzel": 1}
             for i, k in enumerate(["ANO2011", "ODS", "STAN", "Piráti", "KDU-ČSL", "SPD", "MS", "TOP09", "Nezařaz"])]
    vote = {"o_cem": filler[:200], "cislo": 1234, "datum": "2026-11-04", "vysledek": "zamítnuto", "pro": 49, "proti": 95,
            "zdrzel": 20, "nehlasoval": 12, "kluby": kluby, "url": "https://www.psp.cz/sqw/hlasy.sqw?g=1"}
    raw.update(title=filler[:170], nazev=filler[:140], kdo=filler[:120], zdroje=filler[:200], ai_note=filler[:200],
               url="https://cosepece.cz/" + "x" * 80, faze=filler[:40])
    raw["pribeh"] = {
        "cislo": 12345,
        "autor": {"jmeno": "Jméno Velmidlouhépříjmení Dvojitépříjmení-Ještědelšípříjmení", "klub": "Nezařazení", "spolu": 123},
        "podano": "2026-11-22 23:59", "podano_pozn": filler[:160],
        "co": {"kde": filler[:90], "popis": filler[:200], "pred": filler[:200], "po": filler[:220], "pred_lab": filler[:60],
               "po_lab": filler[:60], "kontext": filler[:200], "citace": filler, "zdroj": filler[:120]},
        "osa": [{"date": f"2026-0{i + 1}-1{i}", "cas": "23:59", "label": filler[:160], "pn": i == 3, "hot": i == 1} for i in range(7)],
        "osa_souhrn": {"cislo": 1234, "jednotka": filler[:60], "popis": filler[:200]},
        "naval": {"dny": [{"date": f"2026-10-{10 + i}", "label": filler[:20], "count": 1 + i * 137, "own": i == 5} for i in range(11)],
                  "note": filler, "sub": filler[:200]},
        "znaky": [{"typ": "prilepek", "nadpis": filler[:90], "text": filler[:300], "zdroj": filler[:120], "ai": True} for _ in range(5)],
        "ferove": filler,
        "vysledek": {"stav": "castecne", "text": filler[:300], "hlasovani": [vote, dict(vote), dict(vote)],
                     "dal": [{"label": filler[:200]}] * 6, "stanoviska": filler[:250]},
        "dopad": {"text": filler, "fakta": [{"k": filler[:90], "v": filler[:90]}] * 4, "zdroj": filler[:200]},
    }
    for key in drop:
        del raw["pribeh"][key]
    return Card.from_json(raw)


# s plnou kartou plánovač vypustí volitelné scény (kdo, osa); druhá karta je bez tří bloků, aby se zařadily i ony
DROPS = [(), ("naval", "znaky", "dopad")]


@pytest.mark.render
@pytest.mark.parametrize("drop", DROPS, ids=["plna", "s-volitelnymi"])
@pytest.mark.parametrize("orientation,safe", [("portrait", SAFE), ("landscape", SAFE_LAND)])
@pytest.mark.parametrize("filler", [LONG, (WORD + " ") * 12], ids=["dlouhe-vety", "dlouha-slova"])
def test_long_texts_stay_in_safe_zone(filler, orientation, safe, drop):
    card = _long_card(filler, drop)
    ids = set(scene_ids(card, PN, orientation))
    want = {"s-n-kdo", "s-n-co", "s-n-kdy", "s-n-vys"} if drop else {"s-n-co", "s-n-naval", "s-n-znaky", "s-n-vys", "s-n-dopad"}
    assert want <= ids, ids
    boxes = measure_blocks(card, PN, orientation)
    if filler is LONG:
        _assert_safe(boxes, safe)
    else:
        # slovo bez mezery se láme uvnitř (zkrácení uprostřed slova tu nejde vyloučit), zóna a přetečení platí stejně
        for name, b in boxes.items():
            if name.startswith("scene:"):
                assert not b["overflow"] and b["overflow_px"] == 0, (name, b)
                assert b["top"] >= safe["top"] and b["bottom"] <= safe["bottom"], (name, b)
                assert b["left"] >= safe["left"] and b["right"] <= safe["right"], (name, b)
    assert not boxes["title"]["cut_mid_word"] or filler is not LONG


@pytest.mark.render
@pytest.mark.parametrize("orientation,safe", [("portrait", SAFE), ("landscape", SAFE_LAND)])
def test_waiting_story_with_long_texts(orientation, safe):
    # stav „čeká“ staví jiný obsah (další kroky místo hlasování): i ten se musí vejít
    raw = copy.deepcopy(_long_card(LONG).to_dict())
    raw["pribeh"]["vysledek"] = {"stav": "ceka", "text": LONG[:300], "dal": [{"label": LONG[:200], "ted": i == 0} for i in range(6)],
                                 "stanoviska": LONG[:250]}
    _assert_safe(measure_blocks(Card.from_json(raw), PN, orientation), safe)


@pytest.mark.render
@pytest.mark.parametrize("pozn", ["Krátká poznámka.", "Poznámka k pozměňovacímu návrhu o délce padesát jedna.", LONG[:120]])
def test_landscape_note_lengths_do_not_overflow(pozn):
    # regrese z v3 (scéna s-p-kolik přetékala na šířku o 62 px u poznámky kolem 51 znaků): poznámky všech scén
    # příběhu se na šířku vejdou při každé délce
    raw = copy.deepcopy(load_fixture("pribeh-pn-2364.json"))
    p = raw["pribeh"]
    p["ferove"] = p["podano_pozn"] = p["naval"]["sub"] = p["vysledek"]["stanoviska"] = p["co"]["kontext"] = pozn
    p["osa_souhrn"]["popis"] = p["dopad"]["zdroj"] = pozn
    _assert_safe(measure_blocks(Card.from_json(raw), PN, "landscape"), SAFE_LAND)


@pytest.mark.render
@pytest.mark.parametrize("name", ALL)
def test_check_layout_passes_in_both_orientations(name):
    # kontrola rozvržení, kterou generátor dělá před renderem, musí vzorové karty pustit
    from hlidac_reels.generator.validate import check_layout

    raw = load_fixture(name)
    assert check_layout(raw, PN) == []
    assert check_layout(raw, PN, orientation="landscape") == []


@pytest.mark.render
@pytest.mark.parametrize("name", ALL)
def test_landscape_keeps_scenes_duration_and_cues(name):
    card = _card(name)
    assert scene_ids(card, PN, "landscape") == scene_ids(card, PN)
    with _page(card, PN) as page:
        portrait = page.evaluate("window.duration()")
        assert 15 <= portrait <= 45.001
    with _page(card, PN, "landscape") as page:
        assert page.evaluate("window.duration()") == portrait
        assert page.evaluate("[window.innerWidth, window.innerHeight]") == [1920, 1080]
    assert audio_cues(card, PN, "landscape") == audio_cues(card, PN)


@pytest.mark.render
def test_cues_follow_scenes():
    card = _card("pribeh-pn-1813.json")
    cues = audio_cues(card, PN)
    times = [c["t"] for c in cues]
    assert times == sorted(times) and times[0] >= 0
    n = len(scene_ids(card, PN))
    assert sum(c["type"] == "whoosh" for c in cues) == n - 1
    assert sum(c["type"] == "outro" for c in cues) == 1
    assert {c["type"] for c in cues} <= CUE_TYPES
    # razítka: „po PN“ ve scéně změny, PŘÍLEPEK a verdikt
    assert sum(c["type"] == "stamp" for c in cues) == 3


@pytest.mark.render
@pytest.mark.parametrize("orientation", ["portrait", "landscape"])
def test_seek_is_deterministic(orientation):
    with _page(_card("pribeh-pn-1796.json"), PN, orientation) as page:
        page.evaluate("window.seek(26.4)")
        a = page.screenshot(type="png")
        page.evaluate("window.seek(40.0)")
        page.evaluate("window.seek(3.1)")
        page.evaluate("window.seek(26.4)")
        assert page.screenshot(type="png") == a
