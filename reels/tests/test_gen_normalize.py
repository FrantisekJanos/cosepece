"""Normalizace karty a popisku před kontrolou (plán F2, Task 3)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from conftest import load_fixture
from hlidac_reels.generator.normalize import normalize
from hlidac_reels.generator.validate import CAPTION_LIMIT, CLOSING, validate_variant

CAPTION = "Popisek k tisku (shrnutí AI).\n\n" + CLOSING + "\n\n#snemovna"


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
    # číslo za číslem ani slovo za číslem se nerozdělí; mezery za číslem a za „z“ se jen nezalomí
    for big, nbsp in (("10 z 12", "10\u00a0z\u00a012"), ("4 roky místo 10", "4\u00a0roky místo 10")):
        card = {"hook": {"big": big, "unit": "dní", "sub": ""}}
        new, cap, notes = _norm(card, fmt="cesta")
        assert new["hook"] == {"big": nbsp, "unit": "dní", "sub": ""} and cap == CAPTION
        assert notes == ["hook.big: nezalomitelné mezery"]


def test_hook_10_z_dvanacti_zustane_cele():
    # „10 z dvanácti“ je jeden údaj: v3 háček (#hk-row) by číslo a „Z DVANÁCTI“ v jednotce zalomil od sebe
    card = {"hook": {"big": "10 z dvanácti"}}
    new, cap, notes = _norm(card, fmt="cesta")
    assert new["hook"] == {"big": "10\u00a0z\u00a0dvanácti"}
    assert notes == ["hook.big: nezalomitelné mezery"] and cap == CAPTION
    # druhá normalizace (cílený běh opravy) nic nezmění
    again, _, notes2 = _norm(new, fmt="cesta")
    assert again == new and notes2 == []
    # jednotka od modelu zůstane; nedělí se ani další přípony začínající předložkou nebo spojkou
    for big, nbsp in (("10 z dvanácti", "10\u00a0z\u00a0dvanácti"), ("8 ze sedmnácti", "8\u00a0ze sedmnácti"),
                      ("3 na den", "3\u00a0na den"), ("2 a více", "2\u00a0a\u00a0více")):
        card = {"hook": {"big": big, "unit": "návrhů", "sub": ""}}
        new, _, notes = _norm(card, fmt="cesta")
        assert new["hook"] == {"big": nbsp, "unit": "návrhů", "sub": ""}, big
        assert notes == ["hook.big: nezalomitelné mezery"], notes


def test_hook_unit_s_predlozkou_nezalomi():
    card = {"hook": {"big": "10", "unit": "z dvanácti", "sub": ""}}
    new, _, notes = _norm(card, fmt="cesta")
    assert new["hook"] == {"big": "10", "unit": "z\u00a0dvanácti", "sub": ""}
    assert notes == ["hook.unit: nezalomitelné mezery"]
    # velké jednopísmenné slovo i předložka uprostřed jednotky
    card = {"hook": {"big": "600", "unit": "mil. Kč V rozpočtu a k dávkám", "sub": ""}}
    new, _, _ = _norm(card, fmt="cesta")
    assert new["hook"]["unit"] == "mil. Kč V\u00a0rozpočtu a\u00a0k\u00a0dávkám"
    # po rozdělení „600 mil. Kč“ se jednotka od modelu s předložkou také nezalomí
    card = {"hook": {"big": "600 mil. Kč", "unit": "z dávek na rekvalifikace", "sub": ""}}
    new, _, notes = _norm(card, fmt="cesta")
    assert new["hook"] == {"big": "600", "unit": "mil. Kč z\u00a0dávek na rekvalifikace", "sub": ""}
    assert len(notes) == 2 and notes[1] == "hook.unit: nezalomitelné mezery"


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
    return f"{text}\n\nTitulky: shrnutí AI.\n\n{CLOSING}\n\n#snemovna"


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
    # „z 30“ v jednotce háčku vzorové karty dostane nezalomitelnou mezeru (F5)
    assert len(notes) == 2 and notes[0] == "hook.unit: nezalomitelné mezery" and "pn_autor" in notes[1]
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
    # rozhoduje první výskyt celého jména: závorka za ním se nemění (správný klub, nebo chyba pro kontrolu)
    text = "Test Poslanec  (předkladatel) podal PN 9801. Test Poslanec podal i další PN."
    new, cap, notes = _pn(card, _caption(text), info)
    assert cap == _caption(text) and not any(n.startswith("popisek") for n in notes)
    keys = [r["key"] for r in new["reasons"]]
    assert validate_variant(new, cap, template="template_v3", ct=311, reason_keys=keys, fmt="pn", focus_pn=[9801]) == [
        "popisek uvádí u autora PN 9801 jiný klub: „Test Poslanec (předkladatel)“, ze serveru (ODS)"]
    _, cap, _ = _pn(card, _caption("PN 9801 podal Test Poslanec, Test Poslanec (předkladatel) tvrdí opak."), info)
    assert cap == _caption("PN 9801 podal Test Poslanec (ODS), Test Poslanec (předkladatel) tvrdí opak.")
    # skloňované jméno („Test Poslance“) ani delší slovo („Test Poslanecký“) se za celé jméno nepočítá
    _, cap, notes = _pn(card, _caption("Návrh Test Poslance a Test Poslanecký klub, PN 9801."), info)
    assert cap == _caption("Návrh Test Poslance a Test Poslanecký klub, PN 9801.")
    assert not any(n.startswith("popisek") for n in notes)
    # dvojité příjmení jiného člověka se nerozdělí; jméno s nezlomitelnou mezerou se najde
    _, cap, _ = _pn(card, _caption("PN 9801 podal Test Poslanec-Dlouhý, ne Test Poslanec."), info)
    assert cap == _caption("PN 9801 podal Test Poslanec-Dlouhý, ne Test Poslanec (ODS).")


def test_popisek_doplni_klub_obema_autorum_tehoz_klubu():
    # klub prvního autora v popisku neznamená, že ho má i druhý autor z téhož klubu
    card = _pn_card()
    info = [_info()[0], {"cislo": 9803, "osoba": "Jana Malá", "klub": "ANO"}]
    new, cap, notes = _pn(card, _caption("PN 9801 podal Test Poslanec, PN 9803 Jana Malá."), info)
    assert cap == _caption("PN 9801 podal Test Poslanec (ANO), PN 9803 Jana Malá (ANO).")
    assert [n for n in notes if n.startswith("popisek")] == ["popisek: za „Test Poslanec“ doplněn klub (ANO)",
                                                            "popisek: za „Jana Malá“ doplněn klub (ANO)"]
    keys = [r["key"] for r in new["reasons"]]
    assert validate_variant(new, cap, template="template_v3", ct=311, reason_keys=keys, fmt="pn",
                            focus_pn=[9801, 9803]) == []
    # klub už u jména je: druhá normalizace nic nevloží
    _, cap2, notes2 = _pn(new, cap, info)
    assert cap2 == cap and not any(n.startswith("popisek") for n in notes2)


def test_popisek_klub_nad_limit_nevlozi():
    # vložení by popisek dostalo přes limit: zůstane, jak je, a chybu klubu vrátí kontrola
    card = _pn_card()
    head = "PN 9801 podal Test Poslanec. "
    pad = CAPTION_LIMIT - len(_caption(head)) - 3
    caption = _caption(head + "x" * pad)
    assert len(caption) == CAPTION_LIMIT - 3
    new, cap, notes = _pn(card, caption, info=_info()[:1])
    assert cap == caption and not any(n.startswith("popisek") for n in notes)
    keys = [r["key"] for r in new["reasons"]]
    assert validate_variant(new, cap, template="template_v3", ct=311, reason_keys=keys, fmt="pn", focus_pn=[9801]) == [
        "popisek neuvádí klub autora PN 9801: „Test Poslanec (ANO)“"]


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


# --- Kdo to přináší: předkladatel a autor PN z titulku platí z karty serveru (card_v2), jen ve variantě v2 ---

WHO = {"typ": "Vládní návrh", "jmeno": "Ministerstvo financí", "predlozeno": "2026-08-25"}
HEAD_AUTHOR = [{"cislo": 2181, "jmeno": "Hana Ančincová", "klub": "Piráti"}]


def _server_card(**extra) -> dict:
    card = load_fixture("card-1-v2.json")
    card.update(extra)
    return card


def test_v2_predkladatel_a_autor_ze_serveru():
    server = _server_card(predkladatel=WHO, pn_autor=HEAD_AUTHOR)
    model = load_fixture("card-1-v2.json")
    # model předkladatele přepsal a autora PN si vymyslel jinak
    model["predkladatel"] = {"typ": "Vládní návrh", "jmeno": "Vláda ČR"}
    model["pn_autor"] = [{"cislo": 2181, "jmeno": "Někdo Jiný", "klub": "ODS"}]
    caption = _caption("PN 2181 podala Hana Ančincová (Piráti).")
    new, cap, notes = normalize(model, caption, fmt=None, focus_info=[], server_card=server)
    assert new["predkladatel"] == WHO
    assert new["pn_autor"] == HEAD_AUTHOR
    assert any(n.startswith("predkladatel:") for n in notes) and any(n.startswith("pn_autor:") for n in notes)
    # druhá normalizace nic nemění
    again, _, notes2 = normalize(new, cap, fmt=None, focus_info=[], server_card=server)
    assert again == new and notes2 == []


def test_v2_bez_udaju_serveru_pole_od_modelu_zmizi():
    model = load_fixture("card-1-v2.json")
    model["predkladatel"] = {"typ": "Vládní návrh", "jmeno": "Vláda ČR"}
    model["pn_autor"] = HEAD_AUTHOR
    new, _, notes = normalize(model, CAPTION, fmt=None, focus_info=[], server_card=_server_card())
    assert "predkladatel" not in new and "pn_autor" not in new
    assert any(n.startswith("predkladatel:") for n in notes)


def test_v2_doplni_predkladatele_kdyz_ho_model_vynechal():
    server = _server_card(predkladatel=WHO)
    new, _, notes = normalize(load_fixture("card-1-v2.json"), CAPTION, fmt=None, focus_info=[], server_card=server)
    assert new["predkladatel"] == WHO and "pn_autor" not in new
    assert notes == ["predkladatel: ze serveru (Ministerstvo financí)"]


def test_v2_vadny_tvar_ze_serveru_se_nebere():
    # starší nebo poškozená data: předkladatel bez jména a autor bez čísla do karty nejdou
    server = _server_card(predkladatel={"typ": "Vládní návrh"}, pn_autor=[{"jmeno": "Bez Čísla"}, "x"])
    new, _, _ = normalize(load_fixture("card-1-v2.json"), CAPTION, fmt=None, focus_info=[], server_card=server)
    assert "predkladatel" not in new and "pn_autor" not in new


def test_zadane_pn_maji_prednost_pred_autorem_z_titulku():
    server = _server_card(pn_autor=HEAD_AUTHOR)
    new, _, _ = normalize(load_fixture("card-1-v2.json"), CAPTION, fmt=None, focus_info=_info(), server_card=server)
    assert new["pn_autor"] == AUTHORS


def test_v3_autora_z_titulku_nebere():
    # varianty v3 si volí vlastní úhel: autor PN z titulku karty serveru by mohl patřit k jinému faktu
    server = _server_card(predkladatel=WHO, pn_autor=HEAD_AUTHOR)
    new, _, _ = normalize(load_fixture("v3-pn-311.json"), CAPTION, fmt="pn", focus_info=[], server_card=server)
    assert "pn_autor" not in new and "predkladatel" not in new


def test_bez_karty_serveru_predkladatel_zustane():
    # starší volání (bez server_card): předkladatel od modelu zůstane jako dřív
    model = load_fixture("card-1-v2.json")
    model["predkladatel"] = WHO
    new, _, _ = normalize(model, CAPTION, fmt=None, focus_info=[])
    assert new["predkladatel"] == WHO


def test_kontrola_v2_s_autorem_z_titulku():
    # s autorem PN z titulku chce kontrola v popisku jméno a klub jako u /pn
    server = _server_card(predkladatel=WHO, pn_autor=HEAD_AUTHOR)
    keys = [r["key"] for r in server["reasons"]]
    for caption, ok in ((_caption("PN 2181 podala Hana Ančincová (Piráti)."), True),
                        (_caption("PN 2181 podala Hana Ančincová."), True),   # klub doplní normalizace
                        (_caption("Pozměňovací návrh mění penzijní spoření."), False)):
        new, cap, _ = normalize(load_fixture("card-1-v2.json"), caption, fmt=None, focus_info=[], server_card=server)
        errs = validate_variant(new, cap, template="template_v2", ct=new["ct"], reason_keys=keys, fmt=None)
        assert (errs == []) is ok, errs
