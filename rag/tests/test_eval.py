import json

from eval.run import check, odpoved_z_vysledku, porovnej, render_tabulka, vyber_otazky
from mcp_types import CallToolResult, TextContent


def test_check_cesta_obsahuje():
    ok, _ = check({"pn": [{"cislo": 894, "osoba": "Radek Vondráček"}]}, {"cesta": "pn.*.osoba", "obsahuje": "Vondráček"})
    assert ok


def test_check_rovna_se_selze_s_popisem():
    ok, msg = check({"pocet": 2}, {"cesta": "pocet", "rovna_se": 1})
    assert not ok and "pocet" in msg


def test_check_je_chyba():
    assert check("Tisk 99999 v 10. období nesleduji.", {"je_chyba": True})[0] is False  # text bez isError
    assert check({"__error__": "Tisk nenalezen"}, {"je_chyba": True})[0] is True


def test_check_index_a_chybejici_cesta():
    odp = {"projevy": [{"klic": "a"}, {"klic": "b"}]}
    assert check(odp, {"cesta": "projevy.1.klic", "rovna_se": "b"})[0]
    ok, msg = check(odp, {"cesta": "projevy.5.klic", "rovna_se": "b"})
    assert not ok and "projevy.5.klic" in msg


def test_check_chyba_bez_je_chyba_selze():
    assert check({"__error__": "x"}, {"cesta": "a", "rovna_se": 1})[0] is False


def test_check_text_obsahuje():
    assert check("Ustanovení § 17c, § 20ba", {"obsahuje": "§ 17c"})[0]


def test_vyber_otazky_podle_faze():
    otazky = [{"id": "Q1", "faze": "dnes"}, {"id": "Q2", "faze": "pr2"}, {"id": "Q3", "faze": "3"}]
    assert [o["id"] for o in vyber_otazky(otazky, "dnes,pr2")] == ["Q1", "Q2"]
    assert len(vyber_otazky(otazky, None)) == 3


def test_render_a_porovnani():
    radky = [
        {"otazka": "Q1", "volani": "najdi_osobu", "ok": True, "ms": 120, "znaku": 500, "detail": ""},
        {"otazka": "Q1", "volani": "get_tisk", "ok": False, "ms": 80, "znaku": 3000, "detail": "pocet"},
    ]
    md = render_tabulka(radky, "2026-11-19", predchozi=None)
    assert "| Q1 | najdi_osobu | ok |" in md and "| Q1 | get_tisk | FAIL |" in md
    assert "ok: 1/2" in md and "medián znaků: 1750" in md and "max znaků: 3000" in md
    prev = {"ok": 2, "celkem": 2, "median_znaku": 1000, "max_znaku": 2000}
    assert "ok 2/2" in porovnej(prev, {"ok": 1, "celkem": 2, "median_znaku": 1750, "max_znaku": 3000})


def test_odpoved_z_vysledku_structured_text_a_chyba():
    text = TextContent(type="text", text='{"pocet": 2}')
    assert odpoved_z_vysledku(CallToolResult(content=[text], structured_content={"pocet": 2})) == {"pocet": 2}
    assert odpoved_z_vysledku(CallToolResult(content=[text])) == {"pocet": 2}
    assert odpoved_z_vysledku(CallToolResult(content=[TextContent(type="text", text="volný text")])) == "volný text"
    chyba = odpoved_z_vysledku(CallToolResult(content=[TextContent(type="text", text="Tisk nenalezen")], is_error=True))
    assert chyba == {"__error__": "Tisk nenalezen"}
    assert check(chyba, {"je_chyba": True})[0]
