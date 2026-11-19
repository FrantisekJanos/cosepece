"""Testy parseru struktury textů návrhů zákonů a PN nad uloženými texty z DB (bez sítě a bez DB)."""
import re
from pathlib import Path

import pytest

from hlidac_rag.texty.parser import normativni_a_duvodova, rozdel

FIX = Path(__file__).parent / "fixtures" / "texty"
FIXTURES = sorted(p.name for p in FIX.glob("*.txt"))


def read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


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
    assert [c.oznaceni for c in cely if c.typ == "bod"] == ["bod 1", "bod 2", "bod 3", "bod 4"]
    cely = rozdel(read("pn-2199.txt"))
    assert {c.meni_zakon for c in cely if c.cesta.startswith("ČÁST ČTVRTÁ / Čl. VII")} == {"324/2025"}


def test_pn_bod_na_samostatnem_radku_a_odůvodneni_mezi_body():
    cely = rozdel(read("pn-627.txt"))
    assert [c.oznaceni for c in cely if c.typ == "bod"] == ["bod 1", "bod 2", "bod 3"]
    assert sum(c.typ == "duvodova_zprava" for c in cely) == 3


def test_pn_bez_struktury():
    cely = rozdel(read("pn-2342.txt"))
    assert {c.typ for c in cely} == {"odstavec_volny", "duvodova_zprava"}


def test_novy_zakon_po_paragrafech():
    cely = rozdel(read("tisk-324.txt"))
    assert [c.oznaceni for c in cely if c.typ == "paragraf"] == ["§ 1", "§ 2", "§ 3", "§ 4"]


def test_dlouhy_clanek_se_deli_po_odstavcich():
    cely = rozdel(read("tisk-311.txt"))
    casti = [c for c in cely if c.oznaceni == "Čl. I" and c.typ == "clanek"]
    assert len(casti) >= 2 and casti[0].cesta.endswith(f"(1/{len(casti)})")
    # nadpis paragrafu v uvozovkách (nové znění) je samostatný celek pod článkem
    assert any(c.cesta == "Čl. I / § 21ab" for c in cely)
