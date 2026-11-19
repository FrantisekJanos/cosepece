"""Testy parseru struktury textů návrhů zákonů a PN nad uloženými texty z DB (bez sítě a bez DB)."""
import re
from pathlib import Path

import pytest

from hlidac_rag.texty.parser import normativni_a_duvodova, rozdel

FIX = Path(__file__).parent / "fixtures" / "texty"
FIXTURES = sorted(p.name for p in FIX.glob("*.txt"))


def read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def bez_opakovani(cely, typ):
    # pokračování celku po citovaném novém znění má stejné označení
    return list(dict.fromkeys(c.oznaceni for c in cely if c.typ == typ))


def test_fixtures_existuji():
    assert len(FIXTURES) >= 14


@pytest.mark.parametrize("soubor", FIXTURES)
def test_invariant_spojeni(soubor):
    t = (FIX / soubor).read_text(encoding="utf-8")
    cely = rozdel(t)
    assert re.sub(r"\s", "", "".join(c.text for c in cely)) == re.sub(r"\s", "", t)
    assert all(len(c.text) <= 4000 for c in cely)
    assert [c.poradi for c in cely] == list(range(len(cely)))
    assert all(t[c.od_znaku:].lstrip().startswith(c.text.lstrip()[:30]) for c in cely)
    assert all(t[c.od_znaku:c.od_znaku + len(c.text)] == c.text for c in cely)


def test_tisk_311_ma_clanky_a_duvodovou():
    cely = rozdel(read("tisk-311.txt"))
    assert {"Čl. I", "Čl. II"} <= {c.oznaceni for c in cely}
    assert any(c.typ == "duvodova_zprava" for c in cely)
    assert any("Ustanovení § 17c, § 20ba" in c.text and c.typ != "duvodova_zprava" for c in cely)
    assert any(c.meni_zakon == "586/1992" for c in cely)


def test_paragraf_ve_vete_neni_nadpis():
    cely = rozdel("Čl. I\n1. V § 17c odst. 2 se slova „a“ nahrazují slovy „b“.\n2. Za § 20b se vkládá § 20ba, který zní:\n§ 20ba\nText.")
    assert [c.oznaceni for c in cely if c.typ == "bod"] == ["bod 1", "bod 2"]


def test_bez_struktury():
    cely = rozdel("Odstavec bez struktury.\n" * 300)
    assert {c.typ for c in cely} == {"odstavec_volny"} and all(len(c.text) <= 2100 for c in cely)


def test_normativni_a_duvodova():
    norm, duv = normativni_a_duvodova("Čl. I\nText.\n\nOdůvodnění:\nProto.")
    assert norm.strip() == "Čl. I\nText." and duv.strip().startswith("Odůvodnění")
    assert normativni_a_duvodova("Jen text.") == ("Jen text.", "")


def test_pn_vice_zakonu_ma_casti_a_menene_zakony():
    cely = rozdel(read("pn-1813.txt"))
    clanky = {c.oznaceni: c for c in cely if c.typ == "clanek"}
    assert clanky["Čl. IV"].meni_zakon == "551/1991" and clanky["Čl. V"].meni_zakon == "280/1992"
    assert clanky["Čl. V"].cesta == "bod 3 / ČÁST TŘETÍ / Čl. V"
    assert bez_opakovani(cely, "bod") == ["bod 1", "bod 2", "bod 3", "bod 4"]
    cely = rozdel(read("pn-2199.txt"))
    assert {c.meni_zakon for c in cely if c.cesta.startswith("ČÁST ČTVRTÁ / Čl. VII")} == {"324/2025"}


def test_pn_bod_na_samostatnem_radku_a_odůvodneni_mezi_body():
    cely = rozdel(read("pn-627.txt"))
    assert bez_opakovani(cely, "bod") == ["bod 1", "bod 2", "bod 3"]
    assert sum(c.typ == "duvodova_zprava" for c in cely) == 3


def test_pn_bez_struktury():
    cely = rozdel(read("pn-2342.txt"))
    assert {c.typ for c in cely} == {"odstavec_volny", "duvodova_zprava"}


def test_novy_zakon_po_paragrafech():
    cely = rozdel(read("tisk-324.txt"))
    assert [c.oznaceni for c in cely if c.typ == "paragraf"] == ["§ 1", "§ 2", "§ 3", "§ 4"]


def test_dlouhy_clanek_se_deli_po_odstavcich():
    cely = rozdel(read("tisk-311.txt"))
    casti = [c for c in cely if c.cesta.startswith("Čl. I (")]
    assert len(casti) == 2 and [c.cesta for c in casti] == ["Čl. I (1/2)", "Čl. I (2/2)"]
    assert all(len(c.text) <= 4000 for c in casti)
    # nadpis paragrafu v uvozovkách (nové znění) je samostatný celek pod článkem
    assert any(c.cesta == "Čl. I / § 21ab" for c in cely)


def test_citovany_paragraf_konci_s_uvozovkou():
    cely = rozdel(read("tisk-311.txt"))
    par = next(c for c in cely if c.oznaceni == "§ 20ba")
    assert "V § 21 se odstavec 5 zrušuje" not in par.text and par.text.rstrip().endswith("nulový.“.")
    dalsi = next(c for c in cely if "V § 21 se odstavec 5 zrušuje" in c.text)
    assert (dalsi.typ, dalsi.oznaceni, dalsi.cesta, dalsi.meni_zakon) == ("clanek", "Čl. I", "Čl. I", "586/1992")
    assert not any("V § 38a se na konci odstavce 1" in c.text for c in cely if c.typ == "paragraf")


def test_pn_text_po_citaci_patri_bodu():
    t = ("1. V čl. I se za bod 3 vkládá nový bod 4, který zní:\n"
         "„4. V § 5 se slova „a“ nahrazují slovy „b“.“.\n"
         "Dosavadní body 4 až 10 se označují jako body 5 až 11.\n"
         "2. V čl. II se slovo „x“ zrušuje.")
    cely = rozdel(t)
    pokr = next(c for c in cely if c.text.startswith("Dosavadní body"))
    assert (pokr.oznaceni, pokr.cesta) == ("bod 1", "bod 1")
    assert [c.cesta for c in cely if c.typ == "bod"] == ["bod 1", "bod 1 / bod 4", "bod 1", "bod 2"]


def test_citovany_bod_v_tisku_je_potomek():
    t = "\n".join([
        "Čl. I",
        "Zákon č. 1/2000 Sb., o něčem, se mění takto:",
        "1. V čl. II bod 3 zní:",
        "„3. V § 5 se slova „a“ nahrazují slovy „b“.“.",
        "2. V § 6 se slovo „c“ zrušuje.",
    ])
    cely = rozdel(t)
    assert [c.cesta for c in cely if c.typ == "bod"] == ["Čl. I / bod 1", "Čl. I / bod 1 / bod 3", "Čl. I / bod 2"]
    assert all(c.meni_zakon == "1/2000" for c in cely if c.typ == "bod")


def test_citovany_clanek_v_bodu_nevytlaci_strukturu():
    t = "\n".join([
        "ČÁST PRVNÍ",
        "Čl. I",
        "Zákon č. 1/2000 Sb., o něčem, se mění takto:",
        "1. Za čl. I se vkládá nový čl. II, který zní:",
        "„Čl. II",
        "Přechodné ustanovení",
        "Text.“.",
        "2. V § 3 se slovo „c“ zrušuje.",
        "Čl. II",
        "Účinnost",
        "Tento zákon nabývá účinnosti.",
    ])
    cely = rozdel(t)
    cesty = [c.cesta for c in cely if c.typ in ("clanek", "bod")]
    assert cesty == ["ČÁST PRVNÍ / Čl. I", "ČÁST PRVNÍ / Čl. I / bod 1", "ČÁST PRVNÍ / Čl. I / bod 1 / Čl. II",
                     "ČÁST PRVNÍ / Čl. I / bod 2", "ČÁST PRVNÍ / Čl. II"]
    assert next(c for c in cely if c.cesta == "ČÁST PRVNÍ / Čl. I / bod 2").meni_zakon == "1/2000"


def test_neuzavrena_uvozovka_nezanori_dalsi_clanek():
    t = "\n".join([
        "Čl. I",
        "Zákon č. 1/2000 Sb., o něčem, se mění takto:",
        "1. V § 2 se slovo „a zrušuje.",
        "Čl. II",
        "Účinnost",
        "Tento zákon nabývá účinnosti.",
    ])
    assert [c.cesta for c in rozdel(t) if c.typ == "clanek"] == ["Čl. I", "Čl. II"]


def test_neuzavrena_citace_bodu_nezanori_dalsi_citaci():
    t = "\n".join([
        "V čl. I se za bod 13 vkládá nový bod 14, který zní:",
        "„14. V § 5 se slovo „a“ nahrazuje slovem „b“.",  # chybí závěrečná “
        "V čl. I bod 20 zní:",
        "„20. V § 9 se odstavec 2 zrušuje.“.",
    ])
    assert [c.cesta for c in rozdel(t) if c.typ == "bod"] == ["bod 14", "bod 20"]
    # PN 1989 má nevyvážené uvozovky; citované body tisku nesmí tvořit hlubokou cestu
    assert max(c.cesta.count(" / ") for c in rozdel(read("pn-1989.txt"))) <= 2
