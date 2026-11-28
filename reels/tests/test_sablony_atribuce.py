"""Šablony a atribuce (docs/reels-citace-bezpecne.md, U6): „a N dalších“ u jmen, nadpisy rozpravy bez výroku
osoby, zdroj bodu „projev:“, blok zmena.proc jen z důvodové zprávy. Potřebuje Chromium (marker render)."""
from __future__ import annotations

import copy
import json

import pytest

from conftest import load_fixture
from hlidac_reels.card import Card
from hlidac_reels.render import _page, measure_blocks, scene_ids

AUTHOR = [{"cislo": 2376, "jmeno": "Jan Novák", "klub": "ANO", "spolu": 3}]

TEXTS_JS = """(sel) => {
  return [...document.querySelectorAll(sel)].map(e => e.textContent.replace(/\\s+/g, ' ').trim());
}"""

SCENE_JS = """(id) => {
  const sc = document.getElementById(id);
  sc.style.display = 'block';
  return {labs: [...sc.querySelectorAll('.lab')].map(e => e.textContent.trim()),
          who: [...sc.querySelectorAll('.who2')].length,
          src: [...sc.querySelectorAll('.bs .src')].map(e => e.textContent.trim())};
}"""


def _card(name: str, **change) -> Card:
    raw = copy.deepcopy(load_fixture(name))
    raw.update(change)
    return Card.from_json(raw)


def _eval(card: Card, template: str, js: str, arg):
    with _page(card, template) as page:
        return page.evaluate(js, arg)


@pytest.mark.render
@pytest.mark.parametrize("template,name", [("template_v3", "v3-pn-311.json"), ("template_pn_v1", "pribeh-pn-1813.json"),
                                           ("template_v2", "card-1-v2.json")])
def test_hook_autor_a_n_dalsich(template, name):
    sel = "#title-pn" if template == "template_v2" else "#hk-pn"
    cislo = load_fixture(name).get("pribeh", {}).get("cislo", 2376)
    author = [dict(AUTHOR[0], cislo=cislo)]
    text = " ".join(_eval(_card(name, pn_autor=author), template, TEXTS_JS, sel))
    assert f"PN {cislo} · Jan Novák a 3 další" in text, text
    five = [dict(author[0], spolu=5)]
    assert "Jan Novák a 5 dalších" in " ".join(_eval(_card(name, pn_autor=five), template, TEXTS_JS, sel))
    none = [dict(author[0], spolu=None)]
    text = " ".join(_eval(_card(name, pn_autor=none), template, TEXTS_JS, sel))
    assert f"PN {cislo} · Jan Novák" in text and "další" not in text, text


@pytest.mark.render
@pytest.mark.parametrize("orientation", ["portrait", "landscape"])
def test_pribeh_spolu_0_3_null(orientation):
    raw = load_fixture("pribeh-pn-1813.json")
    for spolu, want in ((3, "a 3 další poslanci"), (0, ""), (None, "předkladatel podle evidence psp.cz")):
        card = copy.deepcopy(raw)
        autor = card["pribeh"]["autor"]
        autor.pop("spolu", None)
        if spolu is None:
            autor["pozn"] = "předkladatel podle evidence psp.cz"
        else:
            autor["spolu"] = spolu
        with _page(Card.from_json(card), "template_pn_v1", orientation) as page:
            shown = page.evaluate(TEXTS_JS, "#kd-spolu")
        assert shown == [want], (spolu, shown)
        b = measure_blocks(Card.from_json(card), "template_pn_v1", orientation)
        scenes = {k: v for k, v in b.items() if k.startswith("scene:")}
        assert not any(v["overflow"] for v in scenes.values()), spolu


@pytest.mark.render
def test_v2_predkladatel_a_dalsi_poslanci():
    who = {"typ": "Poslanecký návrh", "jmeno": "Radek Vondráček", "klub": "ANO", "funkce": "první podepsaný",
           "skupina": {"count": 4}, "vice": True, "dalsi": 3, "dalsi_text": "a 3 další poslanci"}
    shown = _eval(_card("card-1-v2.json", predkladatel=who), "template_v2", TEXTS_JS, "#who-funkce")
    assert shown == ["první podepsaný · a 3 další poslanci"]
    unknown = dict(who, funkce="a další poslanci", dalsi=None, dalsi_text="a další poslanci")
    assert _eval(_card("card-1-v2.json", predkladatel=unknown), "template_v2", TEXTS_JS, "#who-funkce") == [
        "a další poslanci"]


@pytest.mark.render
def test_rozprava_nadpisy_bez_recnika_zdroj_projev():
    raw = load_fixture("v3-rozprava-311.json")
    raw = copy.deepcopy(raw)
    raw["rozprava"]["pro"]["lab"] = "Proč to předkladatel chce"
    raw["rozprava"]["pro"]["kdo"] = "Alena Schillerová, ministryně financí (ANO)"
    raw["rozprava"]["pro"]["body"][0]["zdroj"] = "Schillerová · 13. 10."
    card = Card.from_json(raw)
    pro = _eval(card, "template_v3", SCENE_JS, "s-r-pro")
    assert pro["labs"][0] == "V projevech pro návrh zaznělo" and pro["who"] == 0
    assert pro["src"][0] == "projev: Schillerová · 13. 10." and pro["src"][1].startswith("projev: ")
    assert _eval(card, "template_v3", SCENE_JS, "s-r-proti")["labs"][0] == "V projevech proti zaznělo"
    assert _eval(card, "template_v3", SCENE_JS, "s-r-odp")["labs"][0] == "V odpovědi zaznělo"


@pytest.mark.render
def test_zmena_proc_jen_s_duvodovou_zpravou():
    raw = load_fixture("v3-zmena-221.json")
    assert "s-z-proc" in scene_ids(Card.from_json(raw), "template_v3")
    labs = _eval(Card.from_json(raw), "template_v3", SCENE_JS, "s-z-proc")["labs"]
    assert labs[0] == "Co uvádí důvodová zpráva"
    other = copy.deepcopy(raw)
    other["zmena"]["proc"]["zdroj"] = "úvodní slovo 13. 10. 2026"
    other["zmena"].pop("pozor", None)
    assert "s-z-proc" not in scene_ids(Card.from_json(other), "template_v3")


@pytest.mark.render
def test_pn_karta_autor_a_n_dalsich():
    raw = copy.deepcopy(load_fixture("v3-pn-311.json"))
    raw["pn"]["top"][0]["spolu"] = 2
    texts = _eval(Card.from_json(raw), "template_v3", TEXTS_JS, "#pt-list .pch .src")
    assert texts[0].endswith(" a 2 další"), texts


@pytest.mark.render
@pytest.mark.parametrize("orientation", ["portrait", "landscape"])
def test_nejdelsi_jmeno_a_trimistny_pocet(orientation):
    from hlidac_reels.generator.validate import check_layout
    raw = copy.deepcopy(load_fixture("v3-pn-311.json"))
    raw["pn_autor"] = [{"cislo": 2376, "jmeno": "Pavla Pivoňka Vaňková-Dlouhá", "klub": "nezařazení", "spolu": 123},
                       {"cislo": 2377, "jmeno": "Benjamin Činčila Svárovský", "klub": "KDU-ČSL", "spolu": 999}]
    assert check_layout(raw, "template_v3", orientation) == []
    assert json.dumps(raw)  # karta zůstala serializovatelná
