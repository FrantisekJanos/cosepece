"""Kontrola atribuce výroků a autorství v textech Reels (docs/reels-citace-bezpecne.md, U1, R1–R7)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hlidac_reels import attribution as at
from hlidac_reels import caption as cap

EXAMPLES = Path(__file__).parents[1] / "hlidac_reels" / "generator" / "examples"
FIXTURES = Path(__file__).parent / "fixtures"
REGRESE = Path(__file__).parent / "regrese"
PEOPLE = ["Alena Schillerová", "Jan Novák"]
CLOSING = "Hodnotíme postup, ne politický obsah."


def _errs(text: str, **kw) -> list[str]:
    args = dict(where="popisek", people=PEOPLE, docs=[], speeches=[])
    args.update(kw)
    return at.attribution_errors(text, **args)


def _codes(errs: list[str]) -> set[str]:
    return {e.split(" ", 1)[0] for e in errs}


# ---------- R1: sloveso mluvení u osoby, role nebo klubu ----------

@pytest.mark.parametrize("text", [
    "Hrnčíř řekl, že návrh je protiústavní.",
    "Opozice tvrdí, že daň zaplatí jedna firma.",
    "Ministryně upozornila na výpadek příjmů.",
    "Předkladatelé změnu zdůvodňují nařízením EU.",
    "Schillerová v rozpravě dodala, že výnos pokryje ztráty.",
    "Poslanci ANO varovali před dopadem.",
])
def test_r1_chyba(text):
    assert "R1" in _codes(_errs(text))


@pytest.mark.parametrize("text", [
    "Přechodné ustanovení (čl. VI) říká, že nová lhůta platí i zpětně.",
    "Důvodová zpráva uvádí odhad 5,5 mld. Kč.",
    "PN 2376 podal Jan Novák (ANO).",
    "Sněmovna návrh zamítla 95 hlasy.",
    "Jednací řád říká, že lhůtu lze zkrátit nejvýš na 7 dní.",
    "Tvrdé údaje: 30 návrhů za den.",
])
def test_r1_bez_chyby(text):
    assert "R1" not in _codes(_errs(text))


def test_r1_vyjimka_dokumentu_neplati_s_osobou():
    assert "R1" in _codes(_errs("PN poslance Nováka říká, že lhůta se zkrátí."))


# ---------- R2: „podle X“ u osoby ----------

@pytest.mark.parametrize("text", [
    "Podle Schillerové jde o nutné opatření.",
    "Podle předkladatelů jde o sladění s praxí.",
    "Výnos pokryje ztráty, podle jejích slov do roka.",
    "Podle Jana Hrnčíře je to protiústavní.",
])
def test_r2_chyba(text):
    assert "R2" in _codes(_errs(text))


@pytest.mark.parametrize("text", [
    "Lhůtu lze zkrátit podle § 90 odst. 2.",
    "Postupuje se podle jednacího řádu.",
    "Podle rozboru AI jde o přílepek.",
    "Autor podle evidence psp.cz: Jan Novák (ANO).",
    "Podle Ústavy musí zákon podepsat prezident.",
    "Podle důvodové zprávy jde o 5 mld. Kč.",
])
def test_r2_bez_chyby(text):
    assert "R2" not in _codes(_errs(text))


# ---------- R3: „Jméno: „…““ ----------

def test_r3_chyba():
    assert "R3" in _codes(_errs("Jan Novák: „Tohle je podvod na voliče.“"))
    assert "R3" in _codes(_errs("Ministryně – „výnos pokryje ztráty“"))


def test_r3_bez_chyby():
    assert "R3" not in _codes(_errs("V textu PN stojí: „lhůta činí 4 roky“", docs=["lhůta činí 4 roky"]))


# ---------- R4: uvozovky bez zdroje ----------

def test_r4_citace_dokumentu_projde():
    doc = "se zvyšují výdaje specifického ukazatele o částku 600 000 000 Kč"
    assert "R4" not in _codes(_errs("PN mění rozpočet: „výdaje specifického ukazatele o částku“.", docs=[doc]))


def test_r4_uvozovky_bez_opory():
    assert "R4" in _codes(_errs("PN mění rozpočet: „výdaje rostou o celou miliardu korun“."))


def test_r4_kratke_uvozovky_projdou():
    assert "R4" not in _codes(_errs("V § 11a se číslo „10“ nahrazuje číslem „4“."))


def test_r4_sjednoceni_mezer_a_uvozovek():
    doc = 'ukazatele "Aktivní politika zaměstnanosti celkem" o částku'
    assert "R4" not in _codes(_errs("Mění „Aktivní politika  zaměstnanosti celkem“ ve výdajích.", docs=[doc]))


# ---------- R5: úvod projevu jen se záznamem v kartě ----------

SPEECH = {"klic": "10/34/25/3", "recnik": "Alena Schillerová", "datum": "2026-10-13",
          "url": "https://www.psp.cz/eknih/2025ps/stenprot/034schuz/s034025.htm#r3",
          "autorizovano": True, "doslovne": True, "text": "Výnos daně pokryje ztráty z let 2022 a 2023 v plné výši."}


def test_speech_leads_tvar():
    leads = at.speech_leads("V projevu A. Schillerové 13. 10. zaznělo: „Výnos daně pokryje ztráty z let 2022.“")
    assert len(leads) == 1
    lead = leads[0]
    assert (lead["den"], lead["mesic"], lead["doslovne"]) == (13, 10, True)
    assert lead["citat"].startswith("Výnos daně")
    shrnuti = at.speech_leads("V projevu A. Schillerové 13. 10. zaznělo, že výnos pokryje ztráty.")
    assert shrnuti[0]["doslovne"] is False


def test_r5_uvod_s_polozkou_projde():
    text = "V projevu A. Schillerové 13. 10. zaznělo: „Výnos daně pokryje ztráty z let 2022 a 2023“."
    assert _errs(text, speeches=[SPEECH]) == []


@pytest.mark.parametrize("text", [
    "V projevu A. Schillerové 14. 10. zaznělo: „Výnos daně pokryje ztráty z let 2022 a 2023“.",
    "V projevu J. Nováka 13. 10. zaznělo: „Výnos daně pokryje ztráty z let 2022 a 2023“.",
    "V projevu A. Schillerové 13. 10. zaznělo: „Výnos daně nepokryje vůbec nic z let 2022“.",
])
def test_r5_jine_datum_jmeno_text(text):
    assert "R5" in _codes(_errs(text, speeches=[SPEECH]))


def test_r5_bez_polozky():
    assert "R5" in _codes(_errs("V projevu A. Schillerové 13. 10. zaznělo, že výnos pokryje ztráty."))


def test_r5_neautorizovany_bez_znacky():
    sp = dict(SPEECH, autorizovano=False)
    text = "V projevu A. Schillerové 13. 10. zaznělo, že výnos pokryje ztráty."
    assert "R5" in _codes(_errs(text, speeches=[sp]))
    ok = "V projevu A. Schillerové 13. 10. (neautorizovaný záznam) zaznělo, že výnos pokryje ztráty."
    assert _errs(ok, speeches=[sp]) == []


# ---------- R7: motiv ----------

@pytest.mark.parametrize("text", [
    "Poslanci lhůtu obešli.",
    "Novela prošla potichu.",
    "PN podal Jan Novák, aby zachránil rozpočet.",
    "Vláda chce zdanit marže rafinerií.",
    "Poslanci se snaží změnu protlačit.",
])
def test_r7_chyba(text):
    assert "R7" in _codes(_errs(text))


@pytest.mark.parametrize("text", [
    "Jednací řád dovoluje lhůtu zkrátit, aby výbory stihly rozpočet.",
    "Návrh chce měnit i další zákony.",
])
def test_r7_bez_chyby(text):
    assert "R7" not in _codes(_errs(text))


# ---------- navrhovatelé: a N dalších ----------

AUTHOR = [{"jmeno": "Jan Novák", "spolu": 2}]


@pytest.mark.parametrize("text", [
    "PN 2376 podal Jan Novák (ANO) a 2 další poslanci.",
    "PN 2376 podal Jan Novák (ANO) se 2 dalšími poslanci.",
    "Návrh Jana Nováka a 2 dalších poslanců mění lhůtu.",
])
def test_spolupodepsani_bez_chyby(text):
    assert at.cosigner_errors(text, where="popisek", authors=AUTHOR) == []


@pytest.mark.parametrize("text", [
    "PN 2376 podal Jan Novák (ANO).",
    "PN 2376 podal Jan Novák (ANO) a 3 další poslanci.",
    "PN 2376 podal Jan Novák (ANO), který s 2 dalšími poslanci mění lhůtu.",
    "PN 2376 podal Jan Novák (ANO). Podepsali ho i 2 další poslanci.",
])
def test_spolupodepsani_chyba(text):
    assert at.cosigner_errors(text, where="popisek", authors=AUTHOR)


def test_spolupodepsani_jeden_a_nula():
    assert at.cosigner_errors("PN podal Jan Novák (ANO) a 1 další poslanec.", where="p",
                              authors=[{"jmeno": "Jan Novák", "spolu": 1}]) == []
    assert at.cosigner_errors("PN podal Jan Novák (ANO).", where="p", authors=[{"jmeno": "Jan Novák", "spolu": 0}]) == []


def test_spolupodepsani_neznamy_pocet():
    authors = [{"jmeno": "Jan Novák", "spolu": None}]
    assert at.cosigner_errors("PN podal Jan Novák (ANO).", where="p", authors=authors)
    assert at.cosigner_errors("PN podal Jan Novák (ANO, podle evidence psp.cz).", where="p", authors=authors) == []


def test_spolupodepsani_starsi_server_bez_klice():
    assert at.cosigner_errors("PN podal Jan Novák (ANO).", where="p", authors=[{"jmeno": "Jan Novák"}]) == []


def test_predkladatel_skupina():
    who = {"typ": "Poslanecký návrh", "jmeno": "Radek Vondráček", "vice": True, "dalsi": 3}
    assert at.proposer_errors("Návrh předložil Radek Vondráček (ANO).", where="p", proposer=who)
    assert at.proposer_errors("Návrh předložil Radek Vondráček (ANO) a 3 další poslanci.", where="p",
                              proposer=who) == []
    unknown = dict(who, dalsi=None)
    assert at.proposer_errors("Návrh předložil Radek Vondráček (ANO) a další poslanci.", where="p",
                              proposer=unknown) == []
    assert at.proposer_errors("Návrh předložil Radek Vondráček (ANO).", where="p", proposer=unknown)
    single = {"typ": "Poslanecký návrh", "jmeno": "Radek Vondráček", "vice": False}
    assert at.proposer_errors("Návrh předložil Radek Vondráček (ANO).", where="p", proposer=single) == []


# ---------- celá karta, popisek, Threads a X ----------

def _caption(first: str) -> str:
    return f"{first}\n\nDruhý odstavec je o postupu.\n\n{CLOSING}\n\n#snemovna"


def test_public_r6_citace_v_prvnim_odstavci():
    card = {"citace_projevu": [SPEECH]}
    first = "V projevu A. Schillerové 13. 10. zaznělo, že výnos pokryje ztráty."
    errs = at.public_text_errors(card, _caption(first), 311)
    assert "R6" in _codes(errs)
    later = f"PN 2376 mění lhůtu.\n\n{first}\n\n{CLOSING}"
    assert "R6" not in _codes(at.public_text_errors(card, later, 311))


def test_public_useknuta_citace_v_odvozenem_textu():
    doc = "slovo " * 120
    first = "Text PN mění lhůtu: „" + doc.strip() + "“ a tím končí."
    card = {"quote": doc.strip()}
    errs = at.public_text_errors(card, _caption(first), 311)
    assert any("useknut" in e for e in errs)


def test_public_znacka_useknuta_na_x():
    # první odstavec bez konce věty: x_text ho zkrátí na slovo hned za klubem a značka „a 2 další“ odpadne
    filler = "slovo " * 36 + "abcdef "
    first = filler + "Jan Novák (ANO) a 2 další poslanci podali PN 2376 a mění jím lhůtu u dotací"
    card = {"pn_autor": [{"cislo": 2376, "jmeno": "Jan Novák", "klub": "ANO", "spolu": 2}]}
    caption = _caption(first)
    assert "a 2 další" not in cap.x_text(caption, 311)
    errs = at.public_text_errors(card, caption, 311)
    assert any(e.startswith("text pro X") and "Jan Novák" in e for e in errs), errs
    assert not any(e.startswith("popisek") for e in errs)


def test_public_ai_v_popisku():
    card = {"pribeh": {"znaky": [{"typ": "ai", "ai": True, "zdroj": "AI"}]}}
    caption = _caption("PN 2376 mění lhůtu.")
    assert any("AI" in e for e in at.public_text_errors(card, caption, 311))
    assert not any("AI" in e for e in at.public_text_errors(card, caption.replace("lhůtu.", "lhůtu (shrnutí AI)."), 311))


def test_public_prilepek_v_prvnim_odstavci_bez_ai():
    errs = at.public_text_errors({}, _caption("PN 2376 je přílepek k zákonu o daních."), 311)
    assert any("AI" in e for e in errs)
    ok = at.public_text_errors({}, _caption("PN 2376 je podle AI přílepek k zákonu o daních."), 311)
    assert not any("AI" in e for e in ok)


def test_texts_of_card_vynecha_data_a_citace():
    card = {"ct": 1, "url": "https://x", "nazev": "Zákon", "quote": "citace", "title": "Titulek",
            "reasons": [{"key": "a", "label": "Řekl"}], "pribeh": {"co": {"citace": "c", "popis": "Popis"}},
            "zmena": {"body": [{"citace": "z", "co": "Co"}]}, "pn": {"top": [{"autor": "Jan Novák", "titulek": "T"}]}}
    paths = dict(at.texts_of_card(card))
    assert paths == {"title": "Titulek", "pribeh.co.popis": "Popis", "zmena.body[0].co": "Co", "pn.top[0].titulek": "T"}


# ---------- regresní běh nad vzory ----------

def _cards(folder: Path, pattern: str) -> list[Path]:
    return sorted(folder.glob(pattern))


@pytest.mark.parametrize("path", _cards(EXAMPLES, "*.json") + _cards(FIXTURES, "v3-*.json")
                         + _cards(FIXTURES, "pribeh-pn-*.json") + _cards(FIXTURES, "card-*.json"),
                         ids=lambda p: p.name)
def test_vzory_bez_nalezu(path):
    card = json.loads(path.read_text(encoding="utf-8"))
    assert at.public_text_errors(card, "", card.get("ct")) == []


@pytest.mark.parametrize("path", _cards(EXAMPLES, "caption-*.txt"), ids=lambda p: p.name)
def test_vzory_popisku_bez_nalezu(path):
    assert at.public_text_errors({}, path.read_text(encoding="utf-8").strip(), 311) == []


# skutečné karty generátoru z doby před kontrolou; nálezy jsou správné a čekané (proto pravidla vznikla)
REGRESE_EXPECTED = {
    "job7-v3-zmena.json": {"R2"},
}


@pytest.mark.parametrize("path", _cards(REGRESE, "*.json"), ids=lambda p: p.name)
def test_regrese(path):
    card = json.loads(path.read_text(encoding="utf-8"))
    errs = at.public_text_errors(card, "", card.get("ct"))
    assert _codes(errs) == REGRESE_EXPECTED.get(path.name, set()), errs
    # „podala … spolu s Eliškou Olšákovou“ je úkon, ne výrok
    assert not any(e.startswith("R1") for e in errs)


def test_r3_dvojtecka_bez_uvozovek():
    assert "R3" in _codes(_errs("Ministryně financí: marže rafinerií vyrostly kvůli krizi."))
    assert "R3" in _codes(_errs("Schillerová: marže vyrostly."))
    assert "R3" not in _codes(_errs("Autor podle evidence psp.cz: Jan Novák (ANO)."))
