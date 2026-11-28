"""Atribuce v generátoru: normalizace (spolu, kdo, stanoviska, citace úvodního slova, zdroj rozpravy, popisek)
a kontrola variant (citace_projevu, rozprava, zmena.proc, autopilot). Návrh docs/reels-citace-bezpecne.md U2–U7."""
from __future__ import annotations

import copy

from conftest import load_fixture
from hlidac_reels.generator import formats
from hlidac_reels.generator.normalize import normalize
from hlidac_reels.generator.validate import CLOSING, validate_variant

AI = "Titulky: shrnutí AI."


def _caption(text: str) -> str:
    return f"{text}\n\n{AI}\n\n{CLOSING}\n\n#snemovna"


def _keys(card: dict) -> list[str]:
    return [r["key"] for r in card["reasons"]]


def _info(**change) -> dict:
    info = {"cislo": 2376, "osoba": "Jan Novák", "klub": "ANO", "cas": "2026-11-01 10:00:00",
            "url_psp": "https://www.psp.cz/sqw/sd.sqw?cd=2376&o=10", "has_text": True, "spolu": 2}
    info.update(change)
    return info


def _pn_card() -> dict:
    card = load_fixture("v3-pn-311.json")
    card["pn"]["top"][0] = {"cislo": 2376, "autor": "Jan Novák", "klub": "ANO", "titulek": "Mění lhůtu",
                            "url": "https://www.psp.cz/sqw/sd.sqw?cd=2376&o=10", "spolu": 5}
    return card


def _check(card, caption, fmt="pn", info=None, autopilot=False, template="template_v3", focus=True):
    info = [_info()] if info is None else info
    new, cap, notes = normalize(card, caption, fmt=fmt, focus_info=info, autopilot=autopilot)
    errs = validate_variant(new, cap, template=template, ct=new["ct"], reason_keys=_keys(new), fmt=fmt,
                            focus_pn=[2376] if focus else None, focus_info=info, autopilot=autopilot)
    return new, cap, notes, errs


# ---------- spolupodepsaní PN (U7) ----------

def test_spolu_ze_serveru_prepise_model():
    new, _, notes, _ = _check(_pn_card(), _caption("PN 2376 podal Jan Novák (ANO)."))
    assert new["pn"]["top"][0]["spolu"] == 2
    assert new["pn_autor"] == [{"cislo": 2376, "jmeno": "Jan Novák", "klub": "ANO", "spolu": 2}]
    assert any("spolu „5“ → 2" in n for n in notes)


def test_spolu_null_pole_zmizi():
    new, _, notes, _ = _check(_pn_card(), _caption("PN 2376 podal Jan Novák (ANO)."), info=[_info(spolu=None)])
    assert "spolu" not in new["pn"]["top"][0]
    assert new["pn_autor"][0]["spolu"] is None


def test_spolu_neznamy_v_pribehu_pozn():
    card = load_fixture("pribeh-pn-1813.json")
    info = {"cislo": 1813, "osoba": "Michaela Šebelová", "klub": "STAN", "spolu": None, "vysledek": None}
    new, _, notes = normalize(card, _caption("PN 1813 podala Michaela Šebelová (STAN)."), fmt="pribeh",
                              focus_info=[info])
    autor = new["pribeh"]["autor"]
    assert "spolu" not in autor and autor["pozn"] == "předkladatel podle evidence psp.cz"
    info["spolu"] = 3
    new, _, _ = normalize(card, _caption("x"), fmt="pribeh", focus_info=[info])
    assert new["pribeh"]["autor"]["spolu"] == 3 and "pozn" not in new["pribeh"]["autor"]


def test_starsi_server_bez_spolu_nic_nedoplni():
    info = _info()
    del info["spolu"]
    caption = _caption("PN 2376 podal Jan Novák (ANO).")
    new, cap, _, errs = _check(_pn_card(), caption, info=[info])
    assert "spolu" not in new["pn_autor"][0] and cap == caption
    assert errs == [], errs


def test_popisek_doplni_a_n_dalsich():
    _, cap, notes, errs = _check(_pn_card(), _caption("PN 2376 podal Jan Novák (ANO)."))
    assert cap.startswith("PN 2376 podal Jan Novák (ANO) a 2 další poslanci.")
    assert errs == [], errs
    _, cap, _, _ = _check(_pn_card(), _caption("PN 2376 podal Jan Novák."))
    assert cap.startswith("PN 2376 podal Jan Novák (ANO) a 2 další poslanci.")


def test_popisek_neznamy_pocet_evidence():
    _, cap, _, errs = _check(_pn_card(), _caption("PN 2376 podal Jan Novák (ANO)."), info=[_info(spolu=None)])
    assert cap.startswith("PN 2376 podal Jan Novák (ANO, podle evidence psp.cz).")
    assert errs == [], errs


def test_popisek_sklonovane_jmeno_nemeni_kontrola_hlasi():
    caption = _caption("PN 2376 Jana Nováka (ANO) mění lhůtu.")
    _, cap, _, errs = _check(_pn_card(), caption)
    assert cap == caption
    assert any("Jan Novák" in e and "dalš" in e for e in errs), errs


def test_popisek_spatny_pocet_a_vedlejsi_veta():
    _, _, _, errs = _check(_pn_card(), _caption("PN 2376 podal Jan Novák (ANO) a 3 další poslanci."))
    assert any("špatný počet" in e for e in errs)
    # značka ve vedlejší větě se nepočítá: normalizace doplní tu správnou hned za jméno
    _, cap, _, _ = _check(_pn_card(), _caption("PN 2376 podal Jan Novák (ANO), který s 2 dalšími mění lhůtu."))
    assert cap.startswith("PN 2376 podal Jan Novák (ANO) a 2 další poslanci, který")


def test_popisek_jeden_dalsi():
    _, cap, _, errs = _check(_pn_card(), _caption("PN 2376 podal Jan Novák (ANO) a 1 další poslanec."),
                             info=[_info(spolu=1)])
    assert errs == [], errs


# ---------- předkladatel tisku (U7) ----------

WHO = {"typ": "Poslanecký návrh", "jmeno": "Radek Vondráček", "klub": "ANO", "funkce": "první podepsaný",
       "skupina": {"count": 4}, "vice": True, "dalsi": 3, "dalsi_text": "a 3 další poslanci"}


def _server(**extra) -> dict:
    card = load_fixture("card-1-v2.json")
    card.update(extra)
    return card


def test_kdo_ze_serveru():
    card = load_fixture("v3-zmena-221.json")
    card["kdo"] = "Radek Vondráček a 8 dalších poslanců"
    new, _, notes = normalize(card, _caption("x"), fmt="zmena", focus_info=[], server_card=_server(predkladatel=WHO))
    assert new["kdo"] == "Radek Vondráček a 3 další poslanci"
    unknown = dict(WHO, dalsi=None)
    new, _, _ = normalize(card, _caption("x"), fmt="zmena", focus_info=[], server_card=_server(predkladatel=unknown))
    assert new["kdo"] == "Radek Vondráček a další poslanci"
    gov = {"typ": "Vládní návrh", "jmeno": "Ministerstvo financí"}
    new, _, _ = normalize(card, _caption("x"), fmt="zmena", focus_info=[], server_card=_server(predkladatel=gov))
    assert new["kdo"] == "předkládá Ministerstvo financí"
    new, _, _ = normalize(card, _caption("x"), fmt="zmena", focus_info=[], server_card=_server(predkladatel=None))
    assert "kdo" not in new


def test_v2_predkladatel_v_popisku():
    server = _server(predkladatel=WHO)
    new, cap, _ = normalize(load_fixture("card-1-v2.json"), _caption("Návrh předložil Radek Vondráček (ANO)."),
                            fmt=None, focus_info=[], server_card=server)
    assert cap.startswith("Návrh předložil Radek Vondráček (ANO) a 3 další poslanci.")
    errs = validate_variant(new, cap, template="template_v2", ct=new["ct"], reason_keys=_keys(server), fmt=None)
    assert errs == [], errs


def test_citace_uvodniho_slova_zmizi():
    server = _server(predkladatel=dict(WHO, citace={"text": "Tohle je dobrý zákon."}))
    model = load_fixture("card-1-v2.json")
    model["predkladatel"] = dict(WHO, citace={"text": "x"})
    new, _, _ = normalize(model, _caption("x"), fmt=None, focus_info=[], server_card=server)
    assert "citace" not in new["predkladatel"]
    new, _, notes = normalize(model, _caption("x"), fmt=None, focus_info=[])
    assert "citace" not in new["predkladatel"] and any("predkladatel.citace" in n for n in notes)


# ---------- příběh PN: stanoviska (U3) ----------

def test_stanoviska_od_modelu_zmizi_ze_serveru_vybor():
    card = load_fixture("pribeh-pn-1813.json")
    card["pribeh"]["vysledek"]["stanoviska"] = "ministr: souhlas (stenozáznam)"
    info = {"cislo": 1813, "osoba": "Michaela Šebelová", "klub": "STAN", "spolu": 3,
            "vysledek": {"stav": "ceka", "stanovisko": None, "hlasovani": []}}
    new, _, _ = normalize(card, _caption("x"), fmt="pribeh", focus_info=[info])
    assert "stanoviska" not in new["pribeh"]["vysledek"]
    info["vysledek"]["stanovisko"] = "doporučující"
    new, _, _ = normalize(card, _caption("x"), fmt="pribeh", focus_info=[info])
    assert new["pribeh"]["vysledek"]["stanoviska"] == "garanční výbor: doporučující"


# ---------- autopilot: neověřené kluby (část C) ----------

def test_autopilot_smaze_neovereny_klub():
    card = _pn_card()
    card["pn"]["top"].append({"cislo": 9999, "autor": "Petr Jiný", "klub": "ODS", "titulek": "T", "spolu": 4})
    card["pn"]["autori"] = [{"jmeno": "Petr Jiný", "klub": "ODS", "count": 1},
                            {"jmeno": "Jan Novák", "klub": "SPD", "count": 1}]
    new, _, _ = normalize(card, _caption("x"), fmt="pn", focus_info=[_info()], autopilot=True)
    other = new["pn"]["top"][-1]
    assert "klub" not in other and "spolu" not in other
    assert new["pn"]["autori"][0] == {"jmeno": "Petr Jiný", "count": 1}
    assert new["pn"]["autori"][1]["klub"] == "ANO"
    # bez autopilota klub ostatních zůstane (jen spolu, které server neověřil, zmizí)
    new, _, _ = normalize(card, _caption("x"), fmt="pn", focus_info=[_info()])
    assert new["pn"]["top"][-1]["klub"] == "ODS" and "spolu" not in new["pn"]["top"][-1]


# ---------- citace projevu (U2) ----------

SPEECH = {"klic": "10/34/25/3", "recnik": "Alena Schillerová", "datum": "2026-10-13",
          "url": "https://www.psp.cz/eknih/2025ps/stenprot/034schuz/s034025.htm#r3",
          "autorizovano": True, "doslovne": True, "text": "Výnos daně pokryje ztráty z let 2022 a 2023."}
LEAD = "V projevu A. Schillerové 13. 10. zaznělo: „Výnos daně pokryje ztráty z let 2022 a 2023“."


def _v3(card=None, caption=None, autopilot=False):
    card = card or load_fixture("v3-cesta-311.json")
    caption = caption or _caption("Výbory dostaly 30 dní.")
    new, cap, _ = normalize(card, caption, fmt=card["format"], focus_info=[])
    return validate_variant(new, cap, template="template_v3", ct=new["ct"], reason_keys=_keys(new),
                            fmt=card["format"], autopilot=autopilot)


def test_citace_projevu_v_popisku():
    card = load_fixture("v3-cesta-311.json")
    caption = f"Výbory dostaly 30 dní.\n\n{LEAD}\n\n{AI}\n\n{CLOSING}"
    assert any(e.startswith("R5") for e in _v3(card, caption))
    card["citace_projevu"] = [SPEECH]
    assert _v3(card, caption) == []
    # v prvním odstavci ne (jde zkrácený na Threads a X)
    assert any(e.startswith("R6") for e in _v3(card, f"{LEAD}\n\n{AI}\n\n{CLOSING}"))


def test_citace_projevu_povinna_pole():
    card = load_fixture("v3-cesta-311.json")
    card["citace_projevu"] = [dict(SPEECH, klic="", url="https://example.com/x")]
    errs = _v3(card)
    assert any("chybí klic" in e for e in errs) and any("není odkaz na stenozáznam" in e for e in errs)
    card["citace_projevu"] = [dict(SPEECH, doslovne=True, text="")]
    assert any("doslovná citace bez textu" in e for e in _v3(card))


def test_autopilot_jen_autorizovany_projev():
    card = load_fixture("v3-cesta-311.json")
    card["citace_projevu"] = [dict(SPEECH, autorizovano=False)]
    assert not any("autopilot" in e for e in _v3(card))
    assert any("autopilot smí citovat jen autorizovaný projev" in e for e in _v3(card, autopilot=True))


def test_vyrok_osoby_v_karte_a_popisku():
    card = load_fixture("v3-cesta-311.json")
    card["perex"] = "Schillerová řekla, že výnos pokryje ztráty."
    assert any(e.startswith("R1 perex") for e in _v3(card))
    card = _pn_card()
    card["pn"]["pozn"] = "Opozice tvrdí, že jde o přílepek."
    _, _, _, errs = _check(card, _caption("PN 2376 podal Jan Novák (ANO)."))
    assert any(e.startswith("R1 pn.pozn") for e in errs), errs
    _, _, _, errs = _check(_pn_card(), _caption("PN 2376 podal Jan Novák (ANO). Novák řekl, že jde o úsporu."))
    assert any(e.startswith("R1 popisek") for e in errs)


# ---------- rozprava a zmena.proc (U2, U6) ----------

def test_rozprava_zdroj_ze_struktury():
    card = load_fixture("v3-rozprava-311.json")
    card["rozprava"]["pro"]["body"][0]["zdroj"] = "cokoli od modelu"
    card["rozprava"]["pro"]["kdo"] = "Alena Schillerová, ministryně financí (ANO)"
    new, _, _ = normalize(card, _caption("x"), fmt="rozprava", focus_info=[])
    r = new["rozprava"]
    assert r["pro"]["body"][0]["zdroj"] == "projev: A. Schillerová · 13. 10."
    assert r["namitky"]["body"][0]["zdroj"] == "projevy: Jakob, Munzar · 13. 10."
    assert "kdo" not in r["pro"]
    assert _v3(card) == []


def test_rozprava_bod_bez_klice():
    card = load_fixture("v3-rozprava-311.json")
    del card["rozprava"]["namitky"]["body"][0]["klic"]
    assert any("namitky bod 1 nemá klic" in e for e in _v3(card))


def test_zmena_proc_jen_z_duvodove_zpravy():
    card = load_fixture("v3-zmena-221.json")
    assert _v3(card) == []
    card["zmena"]["proc"]["zdroj"] = "úvodní slovo 13. 10. 2026"
    assert any("důvodové zprávy" in e for e in _v3(card))


def test_rozprava_mimo_autopilot():
    assert formats.pick_formats(["deadline_cut"], True, autopilot=True) == ["pn"]
    assert "rozprava" not in formats.pick_formats(["deadline_cut"], True)
    card = copy.deepcopy(load_fixture("v3-rozprava-311.json"))
    assert card["format"] == "rozprava"
