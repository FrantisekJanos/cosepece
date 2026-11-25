"""Normalizace karty a popisku před kontrolou (plán F2, Task 3)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

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
    for big in (30, "30", "2 000", "10 → 4", "1,5", "10 z 12", "4 roky místo 10"):
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


# --- F4: autor a klub zadaných PN ze serveru (pn_autor, pn.top, popisek) ---

CONTRACT = Path(__file__).resolve().parents[2] / "tests" / "contract" / "focus-pn-info.json"
AUTHORS = [{"cislo": 9801, "jmeno": "Test Poslanec", "klub": "ANO"},
           {"cislo": 9802, "jmeno": "Test Ministr", "klub": None}]


def _info() -> list[dict]:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def _pn(card, caption, info=None):
    return normalize(card, caption, fmt="pn", focus_info=_info() if info is None else info)


def _caption(text: str) -> str:
    return f"{text}\n\n{CLOSING}\n\n#snemovna"


def _top(cislo, autor, klub):
    return {"cislo": cislo, "autor": autor, "klub": klub, "titulek": "Mění lhůtu",
            "url": f"https://www.psp.cz/sqw/sd.sqw?cd={cislo}&o=10"}


def _pn_card() -> dict:
    """Karta formátu pn, jejíž top začíná zadaným PN 9801 (autor a klub už odpovídají serveru)."""
    card = load_fixture("v3-pn-311.json")
    card["pn"]["top"][0] = _top(9801, "Test Poslanec", "ANO")
    return card


def test_pn_autor_ze_serveru():
    card = _pn_card()
    caption = _caption("PN 9801 podal Test Poslanec (ANO), PN 9802 Test Ministr.")
    new, cap, notes = _pn(card, caption)
    assert new["pn_autor"] == AUTHORS
    assert cap == caption and "pn_autor" not in card
    assert len(notes) == 1 and "pn_autor" in notes[0]
    # druhá normalizace (cílený běh opravy) kartu už nemění a nic nezapíše
    again, cap2, notes2 = _pn(new, cap)
    assert again == new and cap2 == cap and notes2 == []
    # i varianta v2 (bez pn.top) dostane pn_autor
    v2, _, _ = normalize(load_fixture("card-1-v2.json"), caption, fmt=None, focus_info=_info())
    assert v2["pn_autor"] == AUTHORS


def test_pn_autor_bez_zadani_odstrani():
    card = _pn_card()
    card["pn_autor"] = [{"cislo": 2315, "jmeno": "Marian Jurečka", "klub": "KDU-ČSL"}]
    new, _, notes = _pn(card, CAPTION, info=[])
    assert "pn_autor" not in new
    assert any("pn_autor" in n for n in notes)
    # položky bez čísla nebo bez jména se nepočítají
    new, _, _ = _pn(card, CAPTION, info=[{"cislo": None, "osoba": "X"}, {"cislo": 9801, "osoba": " "}])
    assert "pn_autor" not in new


def test_top_prepise_klub():
    # Review Focus 2: server klub nemá (člen vlády), model ho do top napsal z MCP → klíč klub zmizí
    card = _pn_card()
    other = _top(2315, "Marian Jurečka", "KDU-ČSL")
    card["pn"]["top"] = [_top(9801, "Test Poslanec", "ODS"), _top(9802, "Test Ministr", "SPD"), copy.deepcopy(other)]
    new, _, notes = _pn(card, CAPTION)
    top = new["pn"]["top"]
    assert top[0]["klub"] == "ANO" and top[0]["autor"] == "Test Poslanec"
    assert "klub" not in top[1] and top[1]["autor"] == "Test Ministr"
    assert top[2] == other
    top_notes = [n for n in notes if n.startswith("pn.top")]
    assert len(top_notes) == 2, notes
    assert "pn.top PN 9801: klub „ODS“ → „ANO“ (server)" in top_notes
    assert any(n.startswith("pn.top PN 9802: klub „SPD“") for n in top_notes)
    # vstupní karta zůstane beze změny
    assert card["pn"]["top"][0]["klub"] == "ODS"


def test_autori_klub_ze_serveru():
    # pruhy autorů nesou klub od modelu z najdi_osobu (poslední klub); u autorů zadaných PN platí klub ke dni podání
    card = _pn_card()
    other = {"jmeno": "Marian Jurečka", "klub": "KDU-ČSL", "count": 9}
    card["pn"]["autori"] = [{"jmeno": " Test Poslanec ", "klub": "Piráti", "count": 3},
                            {"jmeno": "Test Ministr", "klub": "SPD", "count": 2}, copy.deepcopy(other)]
    new, _, notes = _pn(card, CAPTION)
    autori = new["pn"]["autori"]
    assert autori[0] == {"jmeno": " Test Poslanec ", "klub": "ANO", "count": 3}
    # člen vlády bez klubu ze serveru: klub zmizí, nic se neodhaduje
    assert autori[1] == {"jmeno": "Test Ministr", "count": 2}
    assert autori[2] == other
    assert [n for n in notes if n.startswith("pn.autori")] == [
        "pn.autori Test Poslanec: klub „Piráti“ → „ANO“ (server)",
        "pn.autori Test Ministr: klub „SPD“ odstraněn (server klub neuvádí)"]
    # vstupní karta zůstane beze změny; druhá normalizace nic nezmění
    assert card["pn"]["autori"][0]["klub"] == "Piráti"
    again, _, notes2 = _pn(new, CAPTION)
    assert again == new and not any(n.startswith("pn.autori") for n in notes2)
    # chybějící klub u autora zadaného PN se doplní
    card["pn"]["autori"] = [{"jmeno": "Test Poslanec", "count": 3}]
    new, _, notes = _pn(card, CAPTION)
    assert new["pn"]["autori"] == [{"jmeno": "Test Poslanec", "klub": "ANO", "count": 3}]
    assert "pn.autori Test Poslanec: klub (žádný) → „ANO“ (server)" in notes


def test_autori_ruzne_kluby_tehoz_jmena():
    # dva zadané PN téhož poslance s různým klubem ke dni podání: jeden pruh nemůže nést oba, klub zmizí
    info = _info() + [dict(_info()[0], cislo=9805, klub="Piráti")]
    card = _pn_card()
    card["pn"]["autori"] = [{"jmeno": "Test Poslanec", "klub": "ANO", "count": 4}]
    new, _, notes = _pn(card, CAPTION, info)
    assert new["pn"]["autori"] == [{"jmeno": "Test Poslanec", "count": 4}]
    assert "pn.autori Test Poslanec: klub „ANO“ odstraněn (kluby zadaných PN se liší)" in notes
    # klub ze serveru jen u jednoho z PN téhož jména: také se neodhaduje
    info = _info() + [dict(_info()[0], cislo=9805, klub=None)]
    new, _, _ = _pn(card, CAPTION, info)
    assert new["pn"]["autori"] == [{"jmeno": "Test Poslanec", "count": 4}]
    # bez zadaných PN se pruhy nemění
    new, _, notes = _pn(card, CAPTION, info=[])
    assert new["pn"]["autori"] == card["pn"]["autori"]
    assert not any(n.startswith("pn.autori") for n in notes)


def test_top_prepise_autora():
    card = _pn_card()
    card["pn"]["top"] = [_top(9801, "T. Poslanec", "ANO")]
    new, _, notes = _pn(card, CAPTION)
    assert new["pn"]["top"][0]["autor"] == "Test Poslanec"
    assert [n for n in notes if n.startswith("pn.top")] == ["pn.top PN 9801: autor „T. Poslanec“ → „Test Poslanec“ (server)"]


def test_top_bez_osoby_ze_serveru_odstrani_klub():
    # zadaný PN bez autora v zadání: bez údaje serveru žádný klub; autor zůstane, jak ho napsal model
    info = _info() + [{"cislo": 9803, "osoba": None, "cas": None, "titulek": None,
                       "url_psp": "https://www.psp.cz/sqw/sd.sqw?cd=9803&o=10", "id_osoba": None,
                       "klub": None, "klub_ke_dni": None, "has_text": False},
                      {"cislo": 9804, "osoba": "", "klub": "ANO"}]
    card = _pn_card()
    card["pn"]["top"] = [_top(9803, "Jan Model", "SPD"), _top(9804, "Petr Model", "ODS"), _top(9805, "X Y", "STAN")]
    new, _, notes = _pn(card, CAPTION, info)
    top = new["pn"]["top"]
    assert "klub" not in top[0] and top[0]["autor"] == "Jan Model"
    assert "klub" not in top[1] and top[1]["autor"] == "Petr Model"
    assert top[2] == _top(9805, "X Y", "STAN")
    assert [n for n in notes if n.startswith("pn.top")] == [
        "pn.top PN 9803: klub „SPD“ odstraněn (server klub neuvádí)",
        "pn.top PN 9804: klub „ODS“ odstraněn (server klub neuvádí)"]
    # pn_autor nese jen PN s autorem ze serveru
    assert new["pn_autor"] == AUTHORS


def test_popisek_doplni_klub():
    card = _pn_card()
    new, cap, notes = _pn(card, _caption("PN 9801 podal Test Poslanec. Další věta."), info=_info()[:1])
    assert cap == _caption("PN 9801 podal Test Poslanec (ANO). Další věta.")
    assert "popisek: za „Test Poslanec“ doplněn klub (ANO)" in notes
    keys = [r["key"] for r in new["reasons"]]
    assert validate_variant(new, cap, template="template_v3", ct=311, reason_keys=keys, fmt="pn",
                            focus_pn=[9801]) == []
    # s oběma zadanými PN chce kontrola i druhého autora; člen vlády bez klubu stačí jménem
    new, cap, _ = _pn(card, _caption("PN 9801 podal Test Poslanec. Další věta."))
    errs = validate_variant(new, cap, template="template_v3", ct=311, reason_keys=keys, fmt="pn",
                            focus_pn=[9801, 9802])
    assert errs == ["popisek neuvádí autora PN 9802 (Test Ministr)"]


def test_popisek_doplni_klub_za_prvni_vyskyt_bez_zavorky():
    card = _pn_card()
    info = [dict(_info()[0], klub="ODS")]
    _, cap, _ = _pn(card, _caption("Test Poslanec  (předkladatel) podal PN 9801. Test Poslanec tvrdí opak."), info)
    assert cap == _caption("Test Poslanec  (předkladatel) podal PN 9801. Test Poslanec (ODS) tvrdí opak.")
    # skloňované jméno („Test Poslance“) ani delší slovo („Test Poslanecký“) se za celé jméno nepočítá
    _, cap, notes = _pn(card, _caption("Návrh Test Poslance a Test Poslanecký klub, PN 9801."), info)
    assert cap == _caption("Návrh Test Poslance a Test Poslanecký klub, PN 9801.")
    assert not any(n.startswith("popisek") for n in notes)
    # dvojité příjmení jiného člověka se nerozdělí; jméno s nezlomitelnou mezerou se najde
    _, cap, _ = _pn(card, _caption("PN 9801 podal Test Poslanec-Dlouhý, ne Test Poslanec."), info)
    assert cap == _caption("PN 9801 podal Test Poslanec-Dlouhý, ne Test Poslanec (ODS).")


def test_popisek_s_klubem_nemeni():
    card = _pn_card()
    for text in ("PN 9801 podal Test Poslanec (ANO).", "PN 9801 podal poslanec za ANO Test Poslanec."):
        caption = _caption(text)
        _, cap, notes = _pn(card, caption)
        assert cap == caption
        assert not any(n.startswith("popisek") for n in notes), notes


def test_stary_server_bez_klubu():
    # Review Focus 4: worker nasazený dřív než web, server klub ani has_text neposílá
    info = [{k: v for k, v in i.items() if k not in ("klub", "klub_ke_dni", "has_text", "id_osoba")} for i in _info()]
    card = _pn_card()
    caption = _caption("PN 9801 podal Test Poslanec, PN 9802 Test Ministr.")
    new, cap, notes = _pn(card, caption, info)
    assert new["pn_autor"] == [dict(a, klub=None) for a in AUTHORS]
    assert cap == caption
    assert not any(n.startswith("popisek") for n in notes)
    keys = [r["key"] for r in new["reasons"]]
    errs = validate_variant(new, cap, template="template_v3", ct=311, reason_keys=keys, fmt="pn",
                            focus_pn=[9801, 9802])
    assert errs == []
