"""Smlouva serveru a generátoru (F4): údaje o zadaných PN ze serveru až do zadání a karty.

Vstup je společný soubor `tests/contract/focus-pn-info.json`, který čte i PHP test výstupu `ReelJobs::pnInfo`.
Model, render i ffprobe jsou podvržené (fixture `fake` z test_gen_generate.py).
"""
from __future__ import annotations

import json
from pathlib import Path

from conftest import load_fixture
from hlidac_reels.generator.normalize import normalize
from hlidac_reels.generator.validate import CAPTION_LIMIT, CLOSING, find_name, name_in_paren, validate_variant
from test_gen_generate import _run, fake  # noqa: F401

CONTRACT = Path(__file__).resolve().parents[2] / "tests" / "contract" / "focus-pn-info.json"


def test_pn_info_ze_serveru_az_do_karty(tmp_path, fake):  # noqa: F811
    info = json.loads(CONTRACT.read_text(encoding="utf-8"))
    r = _run(tmp_path, focus_pn=[9801, 9802], focus_info=info, render=False)
    # PN 9802 nemá text (has_text: false) → jen formát pn
    assert r.formats == ["pn"]
    out = tmp_path / "out"
    zadani = (out / "podklady" / "zadani.md").read_text(encoding="utf-8")
    assert "Test Poslanec (klub ANO ke dni podání 15. 3. 2026)" in zadani
    # normalizace zapíše autory ze serveru do karty: klub ke dni podání, člen vlády bez klubu
    card = json.loads((out / "variant-2" / "card.json").read_text(encoding="utf-8"))
    assert card["pn_autor"] == [{"cislo": 9801, "jmeno": "Test Poslanec", "klub": "ANO"},
                                {"cislo": 9802, "jmeno": "Test Ministr", "klub": None}]


# --- F5: jméno autora uvnitř závorky („(Jan Novák)“) dostane klub za čárkou, ne vnořenou závorku ---

NOVAK = [{"cislo": 2376, "osoba": "Jan Novák", "klub": "ANO"}]


def _caption(text: str) -> str:
    return f"{text}\n\n{CLOSING}\n\n#snemovna"


def _novak_card() -> dict:
    card = load_fixture("v3-pn-311.json")
    card["pn"]["top"][0] = dict(card["pn"]["top"][0], cislo=2376, autor="Jan Novák", klub="ANO")
    return card


def _errs(card: dict, caption: str, focus_pn: tuple[int, ...] = (2376,)) -> list[str]:
    return validate_variant(card, caption, template="template_v3", ct=311,
                            reason_keys=[r["key"] for r in card["reasons"]], fmt="pn", focus_pn=list(focus_pn))


def _name_in_paren(text: str, jmeno: str = "Jan Novák") -> str | None:
    start, end, _ = find_name(text, jmeno)
    return name_in_paren(text, start, end)


def test_name_in_paren():
    assert _name_in_paren("PN 2376 (Jan Novák) mění lhůtu.") == ""
    assert _name_in_paren("PN 2376 (podal Jan Novák, ANO) mění lhůtu.") == ", ANO"
    # závorka zavřená před jménem, jméno bez závorky
    assert _name_in_paren("Tisk 311 (daně) a PN 2376 podal Jan Novák (ANO).") is None
    assert _name_in_paren("PN 2376 podal Jan Novák.") is None
    # otevřená závorka bez své „)“ za jménem
    assert _name_in_paren("PN 2376 (podal Jan Novák.") is None
    # text za jménem končí i na konci řádku a před další „(“ (neuzavřená závorka, vnořená závorka jiného autora)
    assert _name_in_paren("PN 2376 (podal Jan Novák, ODS.\nPN 2377 Jana Malá (ANO)") == ", ODS."
    assert _name_in_paren("PN 2376 (Jan Novák a Jana Malá (ANO))") == " a Jana Malá "


def test_klub_do_jmena_v_zavorce():
    card = _novak_card()
    caption = _caption("Pozměňovací návrh 2376 (Jan Novák) mění lhůtu pro vrácení dotace.")
    new, cap, notes = normalize(card, caption, fmt="pn", focus_info=NOVAK)
    assert cap == _caption("Pozměňovací návrh 2376 (Jan Novák, ANO) mění lhůtu pro vrácení dotace.")
    assert "((" not in cap and ") )" not in cap and "(ANO)" not in cap
    assert "popisek: za „Jan Novák“ v závorce doplněn klub (ANO)" in notes
    assert _errs(new, cap) == []
    # druhá normalizace (cílený běh opravy) už nic nevloží
    _, cap2, notes2 = normalize(new, cap, fmt="pn", focus_info=NOVAK)
    assert cap2 == cap and not any(n.startswith("popisek") for n in notes2)
    # klub před jménem v téže závorce stačí, nic se nevkládá
    text = _caption("Pozměňovací návrh 2376 (za ANO Jan Novák) mění lhůtu.")
    new, cap, _ = normalize(card, text, fmt="pn", focus_info=NOVAK)
    assert cap == text and _errs(new, cap) == []
    # limit délky popisku platí stejně: vložení by ho přesáhlo, chybu vrátí kontrola
    head = "Pozměňovací návrh 2376 (Jan Novák) mění lhůtu. "
    caption = _caption(head + "x" * (CAPTION_LIMIT - len(_caption(head)) - 3))
    new, cap, notes = normalize(card, caption, fmt="pn", focus_info=NOVAK)
    assert cap == caption and not any(n.startswith("popisek") for n in notes)
    # jméno stojí v závorce: chyba radí tvar bez vnořené závorky
    assert _errs(new, cap) == ["popisek neuvádí klub autora PN 2376: „Jan Novák, ANO“"]


def _other_club(which: str, shown: str, klub: str) -> str:
    return f"popisek uvádí u autora {which} jiný klub nebo text za jménem: „{shown}“, ze serveru ({klub})"


def test_jmeno_v_zavorce_s_jinym_klubem_je_chyba():
    card = _novak_card()
    caption = _caption("Pozměňovací návrh 2376 (Jan Novák, ODS) mění lhůtu pro vrácení dotace.")
    # normalizace jiný text v závorce nepřepisuje ani nedoplňuje (vzniklo by „Jan Novák, ANO, ODS“)
    new, cap, notes = normalize(card, caption, fmt="pn", focus_info=NOVAK)
    assert cap == caption and not any(n.startswith("popisek") for n in notes)
    assert _errs(new, cap) == [_other_club("PN 2376", "Jan Novák, ODS", "ANO")]
    # klub platí jen jako první údaj za jménem: připsaný na konec závorky nestačí
    new, cap, _ = normalize(card, _caption("Pozměňovací návrh 2376 (Jan Novák, ODS, ANO) mění lhůtu."),
                            fmt="pn", focus_info=NOVAK)
    assert _errs(new, cap) == [_other_club("PN 2376", "Jan Novák, ODS, ANO", "ANO")]
    # neuzavřená závorka: klub jiného autora na dalším řádku se nepočítá
    new, cap, _ = normalize(card, _caption("Pozměňovací návrh 2376 (podal Jan Novák, ODS.\nPN 2377 Jana Malá (ANO)."),
                            fmt="pn", focus_info=NOVAK)
    assert _errs(new, cap) == [_other_club("PN 2376", "Jan Novák, ODS.", "ANO")]
    # správný klub hned za jménem projde, i s dalším údajem za ním
    for text in ("Pozměňovací návrh 2376 (Jan Novák, ANO) mění lhůtu.",
                 "Pozměňovací návrh 2376 (Jan Novák, ANO, předkladatel) mění lhůtu.",
                 "Pozměňovací návrh 2376 (Jan Novák , ANO) mění lhůtu."):
        new, cap, _ = normalize(card, _caption(text), fmt="pn", focus_info=NOVAK)
        assert cap == _caption(text) and _errs(new, cap) == [], text


def test_dva_autori_v_zavorce_s_prohozenymi_kluby():
    # kluby dvou autorů v jedné závorce prohozené: chyba u obou, klub druhého autora neomluví prvního
    info = NOVAK + [{"cislo": 2377, "osoba": "Jana Malá", "klub": "ODS"}]
    caption = _caption("PN 2376 a 2377 (Jan Novák, ODS a Jana Malá, ANO) mění lhůtu.")
    new, cap, notes = normalize(_novak_card(), caption, fmt="pn", focus_info=info)
    assert cap == caption and not any(n.startswith("popisek") for n in notes)
    assert _errs(new, cap, (2376, 2377)) == [
        _other_club("PN 2376", "Jan Novák, ODS a Jana Malá, ANO", "ANO"),
        _other_club("PN 2377", "Jana Malá, ANO", "ODS")]
    # se správnými kluby projdou oba
    caption = _caption("PN 2376 a 2377 (Jan Novák, ANO a Jana Malá, ODS) mění lhůtu.")
    new, cap, _ = normalize(_novak_card(), caption, fmt="pn", focus_info=info)
    assert cap == caption and _errs(new, cap, (2376, 2377)) == []
