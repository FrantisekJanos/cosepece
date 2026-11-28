"""Testy ověření citací a přisouzení po odstavcích (spec 9.2), bez DB a sítě."""
from __future__ import annotations

import pytest

from hlidac_rag.chat.citace import ZNACKA_NEOVERENO, Odstavce, ma_citat, over_odstavec
from hlidac_rag.chat.registr import Registr, Zdroj

REG_PROJEV = Registr([Zdroj(1, "projev:10/1/1/1", "projev", "Jan Novák (ANO), 12. 11. 2026",
                            {"autorizovano": True}, False)])


def bez_jmen(_: str) -> list[str]:
    return []


# --- odstavce ---------------------------------------------------------------------------------

def test_odstavce_drzi_rozepsany():
    o = Odstavce()
    assert o.pridej("První věta.") == []
    assert o.pridej(" Druhá.\n\nTřetí") == ["První věta. Druhá."]
    assert o.zbytek() == "Třetí"


def test_odstavce_oddelovac_rozdeleny_mezi_delty_a_vic_odstavcu():
    o = Odstavce()
    assert o.pridej("A.\n") == []
    assert o.pridej("\nB.\n\n\nC.\n\nD") == ["A.", "B.", "C."]
    assert o.zbytek() == "D"


# --- poznání citátu ---------------------------------------------------------------------------

@pytest.mark.parametrize("t", ['Řekl: „text“ [3]', 'Řekl: "text" [3]', 'Řekl: “text” [3]', '»text« [3]', '> text\n[3]'])
def test_vsechny_uvozovky(t):
    assert ma_citat(t)


def test_bez_uvozovek_neni_citat():
    assert not ma_citat("Návrh byl zamítnut [1]. Viz 3 > 2.")


# --- shoda --------------------------------------------------------------------------------------

def test_presna_citace_projde_s_offsety():
    zdroj = "… rozpočet je vyrovnaný …"
    v = over_odstavec('Uvedl: „rozpočet je vyrovnaný“ [1].', REG_PROJEV, lambda n: zdroj, bez_jmen)
    c = v.citaty[0]
    assert c["shoda"] == "presne" and c["recnik"] == "Jan Novák (ANO)" and v.text.count("„") == 1
    assert zdroj[c["od"]:c["do"]] == "rozpočet je vyrovnaný" and c["n"] == 1
    assert v.text == 'Uvedl: „rozpočet je vyrovnaný“ [1].'


def test_priblizna_se_neprepisuje():
    t = 'Uvedl: „státní rozpočet na příští rok je zcela vyrovnaný“ [1].'
    v = over_odstavec(t, REG_PROJEV, lambda n: "Státní rozpočet na příští rok je vyrovnaný, řekl jsem.", bez_jmen)
    assert v.citaty[0]["shoda"] == "priblizne" and v.text == t and v.citaty[0]["recnik"] == "Jan Novák (ANO)"
    assert v.citaty[0]["od"] == 0


def test_nenalezena_ztrati_uvozovky():
    v = over_odstavec('Uvedl: „to nikdy neřekl“ [1].', REG_PROJEV, lambda n: "jiný text", bez_jmen)
    c = v.citaty[0]
    assert c["shoda"] == "nenalezeno" and "„" not in v.text and "“" not in v.text
    assert ZNACKA_NEOVERENO in v.text and "[1]" in v.text
    assert c["recnik"] is None and c["od"] is None and c["do"] is None


def test_opacny_vyznam_je_nenalezeno():
    v = over_odstavec('Uvedl: „rozpočet nebude vyrovnaný letos“ [1].', REG_PROJEV,
                      lambda n: "rozpočet bude vyrovnaný letos", bez_jmen)
    assert v.citaty[0]["shoda"] == "nenalezeno"


# --- přisouzení -------------------------------------------------------------------------------

def test_b_cituje_a_je_neovereno():
    v = over_odstavec('Petr Malý řekl: „rozpočet je vyrovnaný“ [1].', REG_PROJEV, lambda n: "rozpočet je vyrovnaný",
                      lambda s: ["Petr Malý"])
    c = v.citaty[0]
    assert c["shoda"] == "neovereno" and c["recnik"] is None and "„" not in v.text and ZNACKA_NEOVERENO in v.text


def test_jmeno_recnika_zdroje_projde():
    v = over_odstavec('Jan Novák řekl: „rozpočet je vyrovnaný“ [1].', REG_PROJEV, lambda n: "rozpočet je vyrovnaný",
                      lambda s: ["Jan Novák"] if "Novák" in s else [])
    assert v.citaty[0]["shoda"] == "presne" and v.citaty[0]["recnik"] == "Jan Novák (ANO)"


SCHILLEROVA = Registr([Zdroj(4, "projev:10/31/145/2", "projev", "Alena Schillerová (ANO), 24. 9. 2026",
                             {"autorizovano": True}, False)])
TEXT_SCHILLEROVE = ("Tohle velmi dobře vystihl na odborném semináři 16. září pan poslanec Jan Hrnčíř, když řekl, "
                    "že kdyby místo toho investoval do lihovin a děvčat, pak by asi vydělal více. Není to můj názor, "
                    "jenom cituji pana poslance Hrnčíře.")


def jmena(s: str) -> list[str]:
    return [j for j, k in (("Alena Schillerová", "Schillerov"), ("Jan Hrnčíř", "Hrnčíř")) if k in s]


def test_b_cituje_a_se_prisoudi_a_s_uvedenim_kdo_cituje():
    t = 'Ministryně Schillerová citovala poslance Hrnčíře: „kdyby místo toho investoval do lihovin a děvčat“ [4].'
    v = over_odstavec(t, SCHILLEROVA, lambda n: TEXT_SCHILLEROVE, jmena)
    c = v.citaty[0]
    assert c["shoda"] == "presne" and c["recnik"] == "Jan Hrnčíř (cituje ho Alena Schillerová (ANO))"
    assert v.text == t


def test_vyrok_cizi_osoby_bez_rekneho_citujiciho_je_neovereno():
    # Hrnčíř to ve Sněmovně neřekl; zdroj je projev Schillerové (scénář C-b-cituje-a)
    t = 'Poslanec Hrnčíř ve Sněmovně řekl: „kdyby místo toho investoval do lihovin a děvčat“ [4].'
    v = over_odstavec(t, SCHILLEROVA, lambda n: TEXT_SCHILLEROVE, jmena)
    assert v.citaty[0]["shoda"] == "neovereno" and v.citaty[0]["recnik"] is None



B_CITUJE = 'Ministryně Schillerová citovala poslance Hrnčíře: „kdyby místo toho investoval do lihovin a děvčat“ [4].'


def test_b_cituje_a_v_uvozovkach_projde():
    # projev Schillerové 10/31/145/2 s výrokem Hrnčíře v uvozovkách
    zdroj = ("Tohle vystihl na odborném semináři 16. září pan poslanec Jan Hrnčíř. Cituji ho: „Kdyby místo toho "
             "investoval do lihovin a děvčat, pak by asi vydělal více.“ Není to můj názor.")
    v = over_odstavec(B_CITUJE, SCHILLEROVA, lambda n: zdroj, jmena)
    assert v.citaty[0]["shoda"] == "presne"
    assert v.citaty[0]["recnik"] == "Jan Hrnčíř (cituje ho Alena Schillerová (ANO))"


@pytest.mark.parametrize("zdroj", [
    # pouhá zmínka jména A, výrok je B
    "Pan poslanec Jan Hrnčíř tu dnes není. Kdyby místo toho investoval do lihovin a děvčat, vydělal by víc.",
    # uvozovací sloveso daleko od výroku
    "Pan poslanec Hrnčíř včera řekl, že nesouhlasí s celým návrhem rozpočtu a se vším, co k tomu vláda předložila. "
    "Já k tomu říkám: kdyby místo toho investoval do lihovin a děvčat, vydělal by víc.",
    # v uvozovkách je jiný úsek, výrok B za nimi
    "Hrnčíř napsal „to je skandál“. Kdyby místo toho investoval do lihovin a děvčat, vydělal by víc.",
])
def test_b_se_zminkou_jmena_a_je_neovereno(zdroj):
    v = over_odstavec(B_CITUJE, SCHILLEROVA, lambda n: zdroj, jmena)
    assert v.citaty[0]["shoda"] == "neovereno" and v.citaty[0]["recnik"] is None and "„" not in v.text


def test_b_cituje_a_v_uvozovkach_bez_jmena_a_v_okoli_je_neovereno():
    zdroj = "Jak se říká: „kdyby místo toho investoval do lihovin a děvčat, vydělal by víc“."
    assert over_odstavec(B_CITUJE, SCHILLEROVA, lambda n: zdroj, jmena).citaty[0]["shoda"] == "neovereno"


def test_jmeno_ktere_ve_zdroji_neni_neprojde_ani_s_recnikem():
    t = 'Schillerová a Petr Malý shodně řekli: „kdyby místo toho investoval do lihovin a děvčat“ [4].'
    v = over_odstavec(t, SCHILLEROVA, lambda n: TEXT_SCHILLEROVE,
                      lambda s: ["Alena Schillerová", "Petr Malý"])
    assert v.citaty[0]["shoda"] == "neovereno"


def test_jmeno_z_predchoziho_citatu_neplati_pro_dalsi():
    reg = Registr([Zdroj(1, "projev:a", "projev", "Jan Novák (ANO), 1. 1. 2026", {}, False),
                   Zdroj(2, "projev:b", "projev", "Petr Malý (ODS), 1. 1. 2026", {}, False)])
    texty = {1: "rozpočet je vyrovnaný", 2: "to není pravda vůbec"}
    t = 'Jan Novák řekl „rozpočet je vyrovnaný“ [1] a Petr Malý odpověděl „to není pravda vůbec“ [2].'
    v = over_odstavec(t, reg, texty.get, lambda s: [j for j in ("Jan Novák", "Petr Malý") if j in s])
    assert [c["shoda"] for c in v.citaty] == ["presne", "presne"]
    assert [c["recnik"] for c in v.citaty] == ["Jan Novák (ANO)", "Petr Malý (ODS)"]


def test_podmet_z_predchoziho_citatu_plati_kdyz_dalsi_jmeno_chybi():
    reg = Registr([Zdroj(1, "projev:a", "projev", "Petr Malý (ODS), 1. 1. 2026", {}, False),
                   Zdroj(2, "projev:b", "projev", "Jan Novák (ANO), 1. 1. 2026", {}, False)])
    texty = {1: "rozpočet je vyrovnaný", 2: "to není pravda vůbec"}
    t = 'Petr Malý řekl „rozpočet je vyrovnaný“ [1] a dodal „to není pravda vůbec“ [2].'
    v = over_odstavec(t, reg, texty.get, lambda s: ["Petr Malý"] if "Malý" in s else [])
    assert [c["shoda"] for c in v.citaty] == ["presne", "neovereno"]


def test_neautorizovany_projev_se_oznaci():
    reg = Registr([Zdroj(1, "projev:a", "projev", "Jan Jakob (TOP09), 13. 11. 2026", {"autorizovano": False}, False)])
    v = over_odstavec('Řekl, že daň „dopadne na jednu jedinou firmu“ [1].', reg,
                      lambda n: "Zavádíte sektorovou daň, která dopadne na jednu jedinou firmu.", bez_jmen)
    assert v.citaty[0]["shoda"] == "presne" and v.citaty[0]["neautorizovano"] is True
    assert over_odstavec('„rozpočet je vyrovnaný“ [1]', REG_PROJEV, lambda n: "rozpočet je vyrovnaný",
                         bez_jmen).citaty[0]["neautorizovano"] is False


def test_celek_textu_nema_recnika():
    reg = Registr([Zdroj(2, "cel:tisk:221:schvalene:§ 44a", "cel", "Tisk 221, § 44a", {}, False)])
    v = over_odstavec('Zákon říká: „lze zahájit nejpozději do 4 let“ [2].', reg,
                      lambda n: "Řízení lze zahájit nejpozději do 4 let ode dne.", lambda s: ["Petr Malý"])
    assert v.citaty[0]["shoda"] == "presne" and v.citaty[0]["recnik"] is None


# --- zdroj a číslo ----------------------------------------------------------------------------

def test_zdroj_bez_textu_je_neovereno():
    reg = Registr([Zdroj(1, "tisk:221", "tisk", "Tisk 221", {}, False)])
    v = over_odstavec('Tisk uvádí: „lhůta se zkracuje na čtyři roky“ [1].', reg, lambda n: None, bez_jmen)
    assert v.citaty[0]["shoda"] == "neovereno" and "„" not in v.text
    # ani text vrácený pro jiný typ než cel/projev se nepoužije
    v = over_odstavec('Tisk uvádí: „lhůta se zkracuje na čtyři roky“ [1].', reg,
                      lambda n: "lhůta se zkracuje na čtyři roky", bez_jmen)
    assert v.citaty[0]["shoda"] == "neovereno"


def test_cislo_mimo_registr_je_neovereno():
    v = over_odstavec('Řekl: „rozpočet je vyrovnaný“ [9].', REG_PROJEV, lambda n: "rozpočet je vyrovnaný", bez_jmen)
    assert v.citaty[0]["shoda"] == "neovereno" and v.citaty[0]["n"] == 9


def test_citat_bez_cisla_je_neovereno():
    v = over_odstavec('Řekl: „rozpočet je vyrovnaný“, a pak odešel [1].', REG_PROJEV,
                      lambda n: "rozpočet je vyrovnaný", bez_jmen)
    c = v.citaty[0]
    assert c["shoda"] == "neovereno" and c["n"] is None
    assert v.text == f"Řekl: rozpočet je vyrovnaný {ZNACKA_NEOVERENO}, a pak odešel [1]."


def test_kratky_pojem_bez_cisla_zustane():
    t = 'Takzvaný „lex Babiš“ upravuje střet zájmů [1].'
    v = over_odstavec(t, REG_PROJEV, lambda n: "x", bez_jmen)
    assert v.citaty == [] and v.text == t


def test_kratka_citace_s_cislem_se_hleda_doslova():
    v = over_odstavec('Řekl „naprosto nepřijatelné“ [1].', REG_PROJEV, lambda n: "Považuji to za naprosto  nepřijatelné.",
                      bez_jmen)
    assert v.citaty[0]["shoda"] == "presne" and v.citaty[0]["od"] == 15
    v = over_odstavec('Řekl „zcela přijatelné“ [1].', REG_PROJEV, lambda n: "Považuji to za nepřijatelné.", bez_jmen)
    assert v.citaty[0]["shoda"] == "nenalezeno"


def test_dalsi_uvozovky_a_vypustky():
    zdroj = "Vážení, rozpočet na příští rok je vyrovnaný. A k tomu dodávám, že deficit klesá každý rok."
    t = 'Řekl: "…rozpočet na příští rok je vyrovnaný… deficit klesá každý rok…" [1] a »něco co neřekl nikdy« [1].'
    v = over_odstavec(t, REG_PROJEV, lambda n: zdroj, bez_jmen)
    assert [c["shoda"] for c in v.citaty] == ["presne", "nenalezeno"]
    assert zdroj[v.citaty[0]["od"]:v.citaty[0]["do"]].startswith("rozpočet") and v.citaty[0]["do"] == len(zdroj) - 1
    assert '"…rozpočet' in v.text and "»" not in v.text


def test_casti_s_vypustkou_v_obracenem_poradi_neprojdou():
    zdroj = "rozpočet na příští rok je vyrovnaný. A k tomu dodávám, že deficit klesá každý rok."
    t = '„deficit klesá každý rok … rozpočet na příští rok je vyrovnaný“ [1]'
    assert over_odstavec(t, REG_PROJEV, lambda n: zdroj, bez_jmen).citaty[0]["shoda"] == "nenalezeno"


def test_blokova_citace():
    zdroj = "Úvod. Rozpočet na příští rok je vyrovnaný a deficit klesá. Konec."
    v = over_odstavec("> Rozpočet na příští rok je vyrovnaný\n> a deficit klesá.\n[1]", REG_PROJEV, lambda n: zdroj,
                      bez_jmen)
    assert v.citaty[0]["shoda"] == "presne" and v.text.startswith("> ")
    v = over_odstavec("Uvedl:\n> Rozpočet bude letos přebytkový a deficit zmizí. [1]", REG_PROJEV, lambda n: zdroj,
                      bez_jmen)
    assert v.citaty[0]["shoda"] == "nenalezeno" and ">" not in v.text and ZNACKA_NEOVERENO in v.text
    assert v.text.startswith("Uvedl:\nRozpočet bude letos")


def test_dlouha_citace_overi_zacatek_a_konec():
    slova = [f"slovo{i % 97}x{i}" for i in range(300)]
    zdroj = "Úvod projevu. " + " ".join(slova) + " Závěr."
    citat = " ".join(slova)
    assert len(citat) > 3000
    v = over_odstavec(f"Řekl: „{citat}“ [1].", REG_PROJEV, lambda n: zdroj, bez_jmen)
    c = v.citaty[0]
    assert c["shoda"] == "presne" and zdroj[c["od"]:c["do"]] == citat
    # změněný konec dlouhé citace se pozná
    jiny = " ".join(slova[:-12] + ["vymyšlený"] * 12)
    assert over_odstavec(f"„{jiny}“ [1]", REG_PROJEV, lambda n: zdroj, bez_jmen).citaty[0]["shoda"] == "nenalezeno"
    # začátek a konec sedí, ale úsek ve zdroji je mnohem delší než citace
    mezi = "a " * 4000
    delsi = "Úvod. " + " ".join(slova[:150]) + " " + mezi + " ".join(slova[150:])
    assert over_odstavec(f"„{citat}“ [1]", REG_PROJEV, lambda n: delsi, bez_jmen).citaty[0]["shoda"] == "nenalezeno"
