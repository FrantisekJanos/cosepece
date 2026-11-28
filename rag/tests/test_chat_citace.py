"""Testy ověření citací a přisouzení po odstavcích (spec 9.2), bez DB a sítě."""
from __future__ import annotations

import re
import time

import pytest

from hlidac_rag.chat.citace import ZNACKA_NEOVERENO, Odstavce, _konce_vet, ma_citat, over_odstavec
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

@pytest.mark.parametrize("t", ['Novák řekl: „text“ [3]', 'Novák řekl: "text" [3]', 'Novák řekl: “text” [3]', '»text« [3]', '> text\n[3]'])
def test_vsechny_uvozovky(t):
    assert ma_citat(t)


def test_bez_uvozovek_neni_citat():
    assert not ma_citat("Návrh byl zamítnut [1]. Viz 3 > 2.")


# --- shoda --------------------------------------------------------------------------------------

def test_presna_citace_projde_s_offsety():
    zdroj = "… rozpočet je vyrovnaný …"
    v = over_odstavec('Novák uvedl: „rozpočet je vyrovnaný“ [1].', REG_PROJEV, lambda n: zdroj, bez_jmen)
    c = v.citaty[0]
    assert c["shoda"] == "presne" and c["recnik"] == "Jan Novák (ANO)" and v.text.count("„") == 1
    assert zdroj[c["od"]:c["do"]] == "rozpočet je vyrovnaný" and c["n"] == 1
    assert v.text == 'Novák uvedl: „rozpočet je vyrovnaný“ [1].'


def test_priblizna_se_neprepisuje():
    t = 'Novák uvedl: „státní rozpočet na příští rok je zcela vyrovnaný“ [1].'
    v = over_odstavec(t, REG_PROJEV, lambda n: "Státní rozpočet na příští rok je vyrovnaný, řekl jsem.", bez_jmen)
    assert v.citaty[0]["shoda"] == "priblizne" and v.text == t and v.citaty[0]["recnik"] == "Jan Novák (ANO)"
    assert v.citaty[0]["od"] == 0


def test_nenalezena_ztrati_uvozovky():
    v = over_odstavec('Novák uvedl: „to nikdy neřekl“ [1].', REG_PROJEV, lambda n: "jiný text", bez_jmen)
    c = v.citaty[0]
    assert c["shoda"] == "nenalezeno" and "„" not in v.text and "“" not in v.text
    assert ZNACKA_NEOVERENO in v.text and "[1]" in v.text
    assert c["recnik"] is None and c["od"] is None and c["do"] is None


def test_opacny_vyznam_je_nenalezeno():
    v = over_odstavec('Novák uvedl: „rozpočet nebude vyrovnaný letos“ [1].', REG_PROJEV,
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
    return [j for j, k in (("Alena Schillerová", "Schillerov"), ("Jan Hrnčíř", "Hrnčíř"), ("Petr Malý", "Mal"))
            if k in s]


def test_skutecny_projev_schillerove_bez_uvozovek_je_neovereno():
    # skutečné znění 10/31/145/2 výrok Hrnčíře do uvozovek nedává: přisouzení A se neověří (rozhodnutí r1 #1)
    t = 'Ministryně Schillerová citovala poslance Hrnčíře: „kdyby místo toho investoval do lihovin a děvčat“ [4].'
    v = over_odstavec(t, SCHILLEROVA, lambda n: TEXT_SCHILLEROVE, jmena)
    c = v.citaty[0]
    assert c["shoda"] == "neovereno" and c["recnik"] is None and "„" not in v.text and ZNACKA_NEOVERENO in v.text


B_VYROVNANY = 'Ministryně Schillerová citovala poslance Hrnčíře: „rozpočet je zcela vyrovnaný a zdravý“ [4].'


@pytest.mark.parametrize("zdroj", [
    # r1 #1: popřený výrok bez uvozovek
    "Pan poslanec Hrnčíř řekl, že nesouhlasí s tvrzením, že rozpočet je zcela vyrovnaný a zdravý.",
    # r1 #1: popřený výrok v uvozovkách, zápor mezi jménem a uvozovkou
    "Pan poslanec Hrnčíř řekl, že nesouhlasí s tvrzením „rozpočet je zcela vyrovnaný a zdravý“.",
    "Pan poslanec Hrnčíř nikdy neřekl „rozpočet je zcela vyrovnaný a zdravý“.",
    # r1 #2: rovné uvozovky se pro výjimku neuznávají
    'Pan poslanec Hrnčíř řekl: "rozpočet je zcela vyrovnaný a zdravý".',
    "Pan poslanec Hrnčíř řekl: 'rozpočet je zcela vyrovnaný a zdravý'.",
    # r1 #3: nejbližší jméno před otvírací uvozovkou je jiná osoba
    "Pan poslanec Hrnčíř souhlasí s tím, co řekl Petr Malý: „rozpočet je zcela vyrovnaný a zdravý“.",
    # r1 #3: místo leží mimo pár, který otvírá jméno A
    "Hrnčíř řekl „to je skandál“ a já říkám: rozpočet je zcela vyrovnaný a zdravý, „věřte mi“.",
])
def test_b_cituje_a_jen_v_typografickych_uvozovkach_u_jmena_a(zdroj):
    v = over_odstavec(B_VYROVNANY, SCHILLEROVA, lambda n: zdroj, jmena)
    assert v.citaty[0]["shoda"] == "neovereno" and v.citaty[0]["recnik"] is None


@pytest.mark.parametrize("zdroj", [
    # r2 #1: výjimka B cituje A je zrušená, cizí jméno v odstavci = neověřeno i u výroku A v uvozovkách
    "Pan poslanec Hrnčíř řekl: „Rozpočet je zcela vyrovnaný a zdravý.“ To je jeho názor.",
    "Pan poslanec Hrnčíř řekl: »rozpočet je zcela vyrovnaný a zdravý«.",
    "Pan poslanec Hrnčíř napsal ‚rozpočet je zcela vyrovnaný a zdravý‘.",
    # r2: obejití z re-recenze (B mluví k A, A cituje jiného, vnořené uvozovky, zápor mimo seznam)
    "Pane Hrnčíři, já vám říkám: „rozpočet je zcela vyrovnaný a zdravý“.",
    "Pan Hrnčíř ocitoval zprávu NKÚ: „rozpočet je zcela vyrovnaný a zdravý“.",
    "Pan Hrnčíř řekl: „Ministryně tvrdí ‚rozpočet je zcela vyrovnaný a zdravý‘, a to je lež.“",
    "Pan Hrnčíř popřel, že by kdy řekl: „rozpočet je zcela vyrovnaný a zdravý“.",
    "Pan Hrnčíř mi vkládá do úst: „rozpočet je zcela vyrovnaný a zdravý“.",
])
def test_b_cituje_a_je_vzdy_neovereno(zdroj):
    v = over_odstavec(B_VYROVNANY, SCHILLEROVA, lambda n: zdroj, jmena)
    assert v.citaty[0]["shoda"] == "neovereno" and v.citaty[0]["recnik"] is None
    assert "„" not in v.text and ZNACKA_NEOVERENO in v.text


def test_vyrok_cizi_osoby_bez_rekneho_citujiciho_je_neovereno():
    # Hrnčíř to ve Sněmovně neřekl; zdroj je projev Schillerové (scénář C-b-cituje-a)
    t = 'Poslanec Hrnčíř ve Sněmovně řekl: „kdyby místo toho investoval do lihovin a děvčat“ [4].'
    v = over_odstavec(t, SCHILLEROVA, lambda n: TEXT_SCHILLEROVE, jmena)
    assert v.citaty[0]["shoda"] == "neovereno" and v.citaty[0]["recnik"] is None



B_CITUJE = 'Ministryně Schillerová citovala poslance Hrnčíře: „kdyby místo toho investoval do lihovin a děvčat“ [4].'


def test_b_cituje_a_v_uvozovkach_je_neovereno():
    # projev Schillerové 10/31/145/2 s výrokem Hrnčíře v uvozovkách: odstavec jmenuje Hrnčíře → neověřeno (r2 #1)
    zdroj = ("Tohle vystihl na odborném semináři 16. září pan poslanec Jan Hrnčíř. Cituji ho: „Kdyby místo toho "
             "investoval do lihovin a děvčat, pak by asi vydělal více.“ Není to můj názor.")
    v = over_odstavec(B_CITUJE, SCHILLEROVA, lambda n: zdroj, jmena)
    assert v.citaty[0]["shoda"] == "neovereno" and v.citaty[0]["recnik"] is None
    # ani bez jména Hrnčíře v odstavci: místo ve zdroji je v uvozovkách za „Cituji ho“ (r3 c, K1)
    t = 'Ministryně Schillerová řekla: „kdyby místo toho investoval do lihovin a děvčat“ [4].'
    v = over_odstavec(t, SCHILLEROVA, lambda n: zdroj, jmena)
    assert v.citaty[0]["shoda"] == "neovereno"
    # vlastní výrok Schillerové se jí přisoudí
    vlastni = "Já říkám: kdyby místo toho investoval do lihovin a děvčat, vydělal by víc."
    v = over_odstavec(t, SCHILLEROVA, lambda n: vlastni, jmena)
    assert v.citaty[0]["shoda"] == "presne" and v.citaty[0]["recnik"] == "Alena Schillerová (ANO)"


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


def test_dva_recnici_v_jedne_vete_jsou_neovereni():
    # r3 b: věta s citátem jmenuje i jinou osobu → neověřeno (v samostatných větách projdou, test_r2_3_…)
    reg = Registr([Zdroj(1, "projev:a", "projev", "Jan Novák (ANO), 1. 1. 2026", {}, False),
                   Zdroj(2, "projev:b", "projev", "Petr Malý (ODS), 1. 1. 2026", {}, False)])
    texty = {1: "rozpočet je vyrovnaný", 2: "to není pravda vůbec"}
    t = 'Jan Novák řekl „rozpočet je vyrovnaný“ [1] a Petr Malý odpověděl „to není pravda vůbec“ [2].'
    v = over_odstavec(t, reg, texty.get, lambda s: [j for j in ("Jan Novák", "Petr Malý") if j in s])
    assert [c["shoda"] for c in v.citaty] == ["neovereno", "neovereno"]


def test_podmet_z_predchoziho_citatu_plati_kdyz_dalsi_jmeno_chybi():
    reg = Registr([Zdroj(1, "projev:a", "projev", "Petr Malý (ODS), 1. 1. 2026", {}, False),
                   Zdroj(2, "projev:b", "projev", "Jan Novák (ANO), 1. 1. 2026", {}, False)])
    texty = {1: "rozpočet je vyrovnaný", 2: "to není pravda vůbec"}
    t = 'Petr Malý řekl „rozpočet je vyrovnaný“ [1] a dodal „to není pravda vůbec“ [2].'
    v = over_odstavec(t, reg, texty.get, lambda s: ["Petr Malý"] if "Malý" in s else [])
    assert [c["shoda"] for c in v.citaty] == ["presne", "neovereno"]


def test_neautorizovany_projev_se_oznaci():
    reg = Registr([Zdroj(1, "projev:a", "projev", "Jan Jakob (TOP09), 13. 11. 2026", {"autorizovano": False}, False)])
    v = over_odstavec('Jakob řekl, že daň „dopadne na jednu jedinou firmu“ [1].', reg,
                      lambda n: "Zavádíte sektorovou daň, která dopadne na jednu jedinou firmu.", bez_jmen)
    assert v.citaty[0]["shoda"] == "presne" and v.citaty[0]["neautorizovano"] is True
    assert over_odstavec('Novák: „rozpočet je vyrovnaný“ [1]', REG_PROJEV, lambda n: "rozpočet je vyrovnaný",
                         bez_jmen).citaty[0]["neautorizovano"] is False


def test_celek_textu_nema_recnika():
    reg = Registr([Zdroj(2, "cel:tisk:221:schvalene:§ 44a", "cel", "Tisk 221, § 44a", {}, False)])
    v = over_odstavec('Zákon říká: „lze zahájit nejpozději do 4 let“ [2].', reg,
                      lambda n: "Řízení lze zahájit nejpozději do 4 let ode dne.", bez_jmen)
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
    v = over_odstavec('Novák řekl: „rozpočet je vyrovnaný“ [9].', REG_PROJEV, lambda n: "rozpočet je vyrovnaný", bez_jmen)
    assert v.citaty[0]["shoda"] == "neovereno" and v.citaty[0]["n"] == 9


def test_citat_bez_cisla_je_neovereno():
    v = over_odstavec('Novák řekl: „rozpočet je vyrovnaný“, a pak odešel [1].', REG_PROJEV,
                      lambda n: "rozpočet je vyrovnaný", bez_jmen)
    c = v.citaty[0]
    assert c["shoda"] == "neovereno" and c["n"] is None
    assert v.text == f"Novák řekl: rozpočet je vyrovnaný {ZNACKA_NEOVERENO}, a pak odešel [1]."


def test_kratky_pojem_bez_cisla_zustane():
    t = 'Takzvaný „lex Babiš“ upravuje střet zájmů [1].'
    v = over_odstavec(t, REG_PROJEV, lambda n: "x", bez_jmen)
    assert v.citaty == [] and v.text == t


def test_kratka_citace_s_cislem_se_hleda_doslova():
    v = over_odstavec('Novák řekl „naprosto nepřijatelné“ [1].', REG_PROJEV, lambda n: "Považuji to za naprosto  nepřijatelné.",
                      bez_jmen)
    assert v.citaty[0]["shoda"] == "presne" and v.citaty[0]["od"] == 15
    v = over_odstavec('Novák řekl „zcela přijatelné“ [1].', REG_PROJEV, lambda n: "Považuji to za nepřijatelné.", bez_jmen)
    assert v.citaty[0]["shoda"] == "nenalezeno"


def test_dalsi_uvozovky_a_vypustky():
    zdroj = "Vážení, rozpočet na příští rok je vyrovnaný. A k tomu dodávám, že deficit klesá každý rok."
    t = 'Novák řekl: "…rozpočet na příští rok je vyrovnaný… deficit klesá každý rok…" [1] a »něco co neřekl nikdy« [1].'
    v = over_odstavec(t, REG_PROJEV, lambda n: zdroj, bez_jmen)
    # r2 #2: znění s výpustkou je nejvýš přibližné
    assert [c["shoda"] for c in v.citaty] == ["priblizne", "nenalezeno"]
    assert zdroj[v.citaty[0]["od"]:v.citaty[0]["do"]].startswith("rozpočet") and v.citaty[0]["do"] == len(zdroj) - 1
    assert '"…rozpočet' in v.text and "»" not in v.text


def test_casti_s_vypustkou_v_obracenem_poradi_neprojdou():
    zdroj = "rozpočet na příští rok je vyrovnaný. A k tomu dodávám, že deficit klesá každý rok."
    t = '„deficit klesá každý rok … rozpočet na příští rok je vyrovnaný“ [1]'
    assert over_odstavec(t, REG_PROJEV, lambda n: zdroj, bez_jmen).citaty[0]["shoda"] == "nenalezeno"


def test_blokova_citace():
    zdroj = "Úvod. Rozpočet na příští rok je vyrovnaný a deficit klesá. Konec."
    v = over_odstavec("Novák uvedl:\n> Rozpočet na příští rok je vyrovnaný\n> a deficit klesá.\n[1]", REG_PROJEV,
                      lambda n: zdroj, bez_jmen)
    assert v.citaty[0]["shoda"] == "presne" and v.text.startswith("Novák uvedl:\n> ")
    v = over_odstavec("Novák uvedl:\n> Rozpočet bude letos přebytkový a deficit zmizí. [1]", REG_PROJEV,
                      lambda n: zdroj, bez_jmen)
    assert v.citaty[0]["shoda"] == "nenalezeno" and ">" not in v.text and ZNACKA_NEOVERENO in v.text
    assert v.text.startswith("Novák uvedl:\nRozpočet bude letos")


def test_dlouha_citace_overi_zacatek_a_konec():
    slova = [f"slovo{i % 97}x{i}" for i in range(300)]
    zdroj = "Úvod projevu. " + " ".join(slova) + " Závěr."
    citat = " ".join(slova)
    assert len(citat) > 3000
    v = over_odstavec(f"Novák řekl: „{citat}“ [1].", REG_PROJEV, lambda n: zdroj, bez_jmen)
    c = v.citaty[0]
    assert c["shoda"] == "presne" and zdroj[c["od"]:c["do"]] == citat
    # změněný konec dlouhé citace se pozná
    jiny = " ".join(slova[:-12] + ["vymyšlený"] * 12)
    assert over_odstavec(f"„{jiny}“ [1]", REG_PROJEV, lambda n: zdroj, bez_jmen).citaty[0]["shoda"] == "nenalezeno"
    # začátek a konec sedí, ale úsek ve zdroji je mnohem delší než citace
    mezi = "a " * 4000
    delsi = "Úvod. " + " ".join(slova[:150]) + " " + mezi + " ".join(slova[150:])
    assert over_odstavec(f"„{citat}“ [1]", REG_PROJEV, lambda n: delsi, bez_jmen).citaty[0]["shoda"] == "nenalezeno"


# --- nálezy recenze r1 ------------------------------------------------------------------------

def test_r1_4_vlozeny_zapor_doprostred_dlouhe_citace_neni_presne():
    slova = [f"slovo{i % 97}x{i}" for i in range(300)]
    zdroj = " ".join(slova)
    citat = " ".join(slova[:150] + ["nesouhlasím", "vůbec"] + slova[150:])
    assert len(citat) > 3000
    assert over_odstavec(f"„{citat}“ [1]", REG_PROJEV, lambda n: zdroj, bez_jmen).citaty[0]["shoda"] != "presne"
    # dlouhá citace, ve které chybí kus ze středu zdroje
    kratsi = " ".join(slova[:140] + slova[160:])
    assert over_odstavec(f"„{kratsi}“ [1]", REG_PROJEV, lambda n: zdroj, bez_jmen).citaty[0]["shoda"] != "presne"


def test_r1_5_vypustka_nesmi_preskocit_zapor():
    zdroj = "Já nikdy nebudu podporovat zrušení daně z nemovitostí."
    v = over_odstavec("Novák řekl: „Já … podporovat zrušení daně“ [1].", REG_PROJEV, lambda n: zdroj, bez_jmen)
    assert v.citaty[0]["shoda"] == "nenalezeno"
    v = over_odstavec("Novák řekl: „Já osobně … podporovat zrušení daně“ [1].", REG_PROJEV,
                      lambda n: "Já osobně nikdy nebudu podporovat zrušení daně.", bez_jmen)
    assert v.citaty[0]["shoda"] == "nenalezeno"


def test_r1_5_casti_daleko_od_sebe_neprojdou():
    zdroj = "rozpočet na příští rok je vyrovnaný. " + "Dlouhý text. " * 2000 + "deficit klesá každý rok."
    assert len(zdroj) > 25_000
    t = "„rozpočet na příští rok je vyrovnaný … deficit klesá každý rok“ [1]"
    assert over_odstavec(t, REG_PROJEV, lambda n: zdroj, bez_jmen).citaty[0]["shoda"] == "nenalezeno"


def test_r1_6_jmeno_za_citatem_do_konce_vety():
    t = "„rozpočet je vyrovnaný“ [1], řekl Petr Malý."
    v = over_odstavec(t, REG_PROJEV, lambda n: "rozpočet je vyrovnaný", lambda s: ["Petr Malý"] if "Malý" in s else [])
    assert v.citaty[0]["shoda"] == "neovereno" and "„" not in v.text
    t = "„rozpočet je vyrovnaný“ [1], řekl Jan Novák."
    v = over_odstavec(t, REG_PROJEV, lambda n: "rozpočet je vyrovnaný", lambda s: ["Jan Novák"] if "Novák" in s else [])
    assert v.citaty[0]["shoda"] == "presne"


@pytest.mark.parametrize("t", [
    'Novák řekl: „rozpočet je vyrovnaný" [1].', 'Novák řekl: “rozpočet je vyrovnaný“ [1].', 'Novák řekl: ”rozpočet je vyrovnaný” [1].',
    'Novák řekl: ‚rozpočet je vyrovnaný‘ [1].', "Novák řekl: 'rozpočet je vyrovnaný' [1].", 'Novák řekl: ‹rozpočet je vyrovnaný› [1].',
    'Novák řekl: „rozpočet je\nvyrovnaný“ [1].',
])
def test_r1_7_varianty_uvozovek(t):
    assert ma_citat(t)
    v = over_odstavec(t, REG_PROJEV, lambda n: "rozpočet je vyrovnaný", bez_jmen)
    assert [c["shoda"] for c in v.citaty] == ["presne"] and v.text == t
    v = over_odstavec(t, REG_PROJEV, lambda n: "jiný text úplně", bez_jmen)
    assert [c["shoda"] for c in v.citaty] == ["nenalezeno"]
    assert not any(ch in v.text for ch in "„“”‚‘'‹›\"") and ZNACKA_NEOVERENO in v.text


def test_r1_7_vnorene_uvozovky_a_apostrof():
    t = "Novák řekl: „rozpočet je podle O'Neilla ‚vyrovnaný‘ a zdravý“ [1]."
    v = over_odstavec(t, REG_PROJEV, lambda n: "rozpočet je podle O'Neilla ‚vyrovnaný‘ a zdravý", bez_jmen)
    assert [c["shoda"] for c in v.citaty] == ["presne"] and v.text == t


def test_r1_7_nesparovana_uvozovka_pred_odkazem():
    v = over_odstavec("Novák řekl: „rozpočet je vyrovnaný [1].", REG_PROJEV, lambda n: "rozpočet je vyrovnaný", bez_jmen)
    assert v.text == f"Novák řekl: rozpočet je vyrovnaný {ZNACKA_NEOVERENO} [1]."
    assert [(c["n"], c["shoda"]) for c in v.citaty] == [(1, "neovereno")]
    # nespárovaná uvozovka bez [N] text nemění
    t = 'Monitor 27" stojí 5 000 Kč.'
    assert over_odstavec(t, REG_PROJEV, lambda n: None, bez_jmen).text == t


def test_r1_8_kratky_citat_u_osoby_nebo_slovesa():
    babis = lambda s: ["Andrej Babiš"] if "Babiš" in s else []  # noqa: E731
    v = over_odstavec("Babiš řekl: „zloději“.", REG_PROJEV, lambda n: None, babis)
    assert v.text == f"Babiš řekl: zloději {ZNACKA_NEOVERENO}." and v.citaty[0]["shoda"] == "neovereno"
    v = over_odstavec("Opozici nazval „zloději“.", REG_PROJEV, lambda n: None, bez_jmen)
    assert "„" not in v.text and v.citaty[0]["n"] is None
    v = over_odstavec("„Zloději“, řekl Babiš.", REG_PROJEV, lambda n: None, babis)
    assert "„" not in v.text
    t = "Novela je známá jako „lex Babiš“ už roky."
    assert over_odstavec(t, REG_PROJEV, lambda n: None, bez_jmen).text == t


@pytest.mark.parametrize("t", [
    "„" + " " * 20_000 + "x“ [1]",
    "„x" + " " * 20_000 + "“ [1]",
    "„" + "." * 20_000 + "“ [1]",
    "„" * 20_000 + " [1]",
    "„a … " * 4_000 + "“ [1]",
    "„slovo " + "…" * 20_000 + " slovo“ [1]",
])
def test_r1_9_vykon(t):
    zacatek = time.perf_counter()
    over_odstavec(t, REG_PROJEV, lambda n: "rozpočet je vyrovnaný", bez_jmen)
    assert time.perf_counter() - zacatek < 0.2


# --- nálezy re-recenze r2 ---------------------------------------------------------------------

@pytest.mark.parametrize("zdroj", [
    "Já bych byl proti tomu, abychom podporovali zrušení daně z nemovitosti.",
    "Já to odmítám, abychom podporovali zrušení daně z nemovitosti.",
    "Já jsem popřel, že bychom podporovali zrušení daně z nemovitosti.",
    "Já říkám, že je nepravda, že bychom podporovali zrušení daně z nemovitosti.",
    "Já se ohradil, že bychom podporovali zrušení daně z nemovitosti.",
    "Já tvrdím, že je lež, že bychom podporovali zrušení daně z nemovitosti.",
])
def test_r2_2_vypustka_preskocila_slovo_obracejici_smysl(zdroj):
    t = "„Já … podporovali zrušení daně z nemovitosti“ [1]"
    assert over_odstavec(t, REG_PROJEV, lambda n: zdroj, bez_jmen).citaty[0]["shoda"] == "nenalezeno"
    assert over_odstavec("„Já bych … podporovali zrušení daně z nemovitosti“ [1]", REG_PROJEV, lambda n: zdroj,
                         bez_jmen).citaty[0]["shoda"] == "nenalezeno"


def test_r2_2_vypustka_bez_zaporu_je_nejvys_priblizne():
    zdroj = "Já bych byl pro to, abychom podporovali zrušení daně z nemovitosti."
    v = over_odstavec("Novák: „Já bych … podporovali zrušení daně z nemovitosti“ [1]", REG_PROJEV, lambda n: zdroj,
                      bez_jmen)
    assert v.citaty[0]["shoda"] == "priblizne"


@pytest.mark.parametrize("t", [
    "„rozpočet je zcela vyrovnaný“ [1], řekl posl. Petr Malý.",
    "„rozpočet je zcela vyrovnaný“ [1], prohlásil Ing. Petr Malý.",
    "„rozpočet je zcela vyrovnaný“ [1], řekl 16. září Petr Malý.",
    "„rozpočet je zcela vyrovnaný“ [1], řekl doc. MUDr. Petr Malý.",
    "„rozpočet je zcela vyrovnaný“ [1]. Tak to řekl Petr Malý.",
])
def test_r2_3_zkratky_neukoncuji_vetu(t):
    v = over_odstavec(t, REG_PROJEV, lambda n: "rozpočet je zcela vyrovnaný", lambda s: ["Petr Malý"] if "Mal" in s else [])
    assert v.citaty[0]["shoda"] == "neovereno"


def test_r2_3_dalsi_veta_s_vlastnim_citatem_se_neprida():
    reg = Registr([Zdroj(1, "projev:a", "projev", "Jan Novák (ANO), 1. 1. 2026", {}, False),
                   Zdroj(2, "projev:b", "projev", "Petr Malý (ODS), 1. 1. 2026", {}, False)])
    texty = {1: "rozpočet je vyrovnaný", 2: "to není pravda vůbec"}
    t = "Jan Novák řekl „rozpočet je vyrovnaný“ [1]. Petr Malý odpověděl „to není pravda vůbec“ [2]."
    v = over_odstavec(t, reg, texty.get, lambda s: [j for j in ("Jan Novák", "Petr Malý") if j in s])
    assert [c["shoda"] for c in v.citaty] == ["presne", "presne"]


@pytest.mark.parametrize("zdroj", [
    "Netvrdím, že rozpočet je vyrovnaný.",
    "Nemyslím si, že rozpočet je vyrovnaný.",
    "Není pravda, že rozpočet je vyrovnaný.",
    "Popírám, že rozpočet je vyrovnaný.",
    "Odmítám, že rozpočet je vyrovnaný.",
])
def test_r2_4_popreni_pred_mistem_ve_zdroji_neni_presne(zdroj):
    v = over_odstavec("Novák řekl: „rozpočet je vyrovnaný“ [1].", REG_PROJEV, lambda n: zdroj, bez_jmen)
    assert v.citaty[0]["shoda"] == "priblizne"


def test_r2_4_popreni_v_predchozi_vete_neplati():
    zdroj = "Netvrdím to. Ale rozpočet je vyrovnaný."
    v = over_odstavec("Novák řekl: „rozpočet je vyrovnaný“ [1].", REG_PROJEV, lambda n: zdroj, bez_jmen)
    assert v.citaty[0]["shoda"] == "presne"


def test_r2_kratky_citat_podle_osoby_v_predchozi_vete():
    v = over_odstavec("Babiš vystoupil. Opozici považuje za „zloděje“.", REG_PROJEV, lambda n: None,
                      lambda s: ["Andrej Babiš"] if "Babiš" in s else [])
    assert [c["shoda"] for c in v.citaty] == ["neovereno"] and "„" not in v.text
    v = over_odstavec("Podle premiéra jde o „zloděje“.", REG_PROJEV, lambda n: None, bez_jmen)
    assert [c["shoda"] for c in v.citaty] == ["neovereno"] and "„" not in v.text


# --- re-recenze r3: pozitivní pravidlo přisouzení (sondy probe13d/e/f) -----------------------

X = "rozpočet je zcela vyrovnaný a zdravý"
CEL = Registr([Zdroj(3, "cel:311:§1", "cel", "Tisk 311, § 1", {}, False)])
MIX = Registr([Zdroj(1, "projev:a", "projev", "Jan Novák (ANO), 1. 1. 2026", {}, False),
               Zdroj(2, "projev:m", "projev", "Petr Malý (ODS), 1. 1. 2026", {}, False)])
VLASTNI = f"Já říkám, že {X}."


def jm(s: str) -> list[str]:
    vzory = [("Alena Schillerová", "schillerov"), ("Jan Hrnčíř", "hrnčíř"), ("Petr Malý", "mal[ýé]"),
             ("Andrej Babiš", "babiš"), ("Jan Novák", "novák")]
    return [j for j, k in vzory if re.search(k, s.lower())]


@pytest.mark.parametrize("t", [
    f"Premiér ve Sněmovně řekl: „{X}“ [1].",                              # R1 role
    f"Ministryně financí prohlásila: „{X}“ [1].",                         # R2
    f"Na to odpověděl: „{X}“ [1].",                                      # R3 zájmeno / bez jména
    f"Předseda poslaneckého klubu SPD uvedl: „{X}“ [1].",                 # R4
    f"V rozpravě zaznělo: „{X}“ [1].",                                   # bez jména
])
def test_r3_k4_role_nebo_zajmeno_bez_jmena_je_neovereno(t):
    assert over_odstavec(t, REG_PROJEV, lambda n: VLASTNI, jm).citaty[0]["shoda"] == "neovereno"


@pytest.mark.parametrize("t", [
    f"Babiš ve Sněmovně řekl: „{X}“ [3].",                               # L1
    f"„{X}“ [3], řekl Petr Malý.",                                       # L2
    f"Ministr uvedl: „{X}“ [3].",
    f"On sám to shrnul: „{X}“ [3].",
])
def test_r3_k2_text_zakona_prisouzeny_osobe_je_neovereno(t):
    assert over_odstavec(t, CEL, lambda n: f"§ 1 {X}.", jm).citaty[0]["shoda"] == "neovereno"


def test_r3_k2_text_zakona_bez_osoby_projde():
    v = over_odstavec(f"Paragraf 1 stanoví: „{X}“ [3].", CEL, lambda n: f"§ 1 {X}.", jm)
    assert v.citaty[0]["shoda"] == "presne" and v.citaty[0]["recnik"] is None


@pytest.mark.parametrize("t", [
    f"„{X}“ [1]. To je jasné. Řekl to Petr Malý.",                         # T1
    f"„{X}“ [1]. Tak to řekl Petr Malý v pořadu „Partie“.",                # T2
    f"Novák řekl: „{X}“ [1]. To je jasné. Řekl to i Petr Malý.",           # K3 jméno v další větě
    f"Výroky z rozpravy:\n- „{X}“ [1] (Petr Malý)",                        # T4
    f"„{X}“ [1] (Petr Malý)",                                            # T5
    f"„{X}“ [1], řekl JUDr. Ph.D. Petr Malý.",                             # Z1
    f"„{X}“ [1], řekl gen. Petr Malý.",                                   # Z2
    f"„{X}“ [1], uvedl plk. v. v. Petr Malý.",                             # Z3
    f"„{X}“ [2] řekl Malý. „{X}“ [1].",                                    # M2
])
def test_r3_k3_jine_jmeno_nebo_zadne_je_neovereno(t):
    v = over_odstavec(t, MIX, lambda n: VLASTNI if n == 1 else "nic", jm)
    assert all(c["shoda"] in ("neovereno", "nenalezeno") for c in v.citaty if c["n"] == 1)


def test_r3_k3_t3_dalsi_citat_neblokuje_kontrolu():
    t = f"„{X}“ [1]. Tak to řekl Petr Malý, který dodal „to je fakt dobře“ [2]."
    v = over_odstavec(t, MIX, lambda n: VLASTNI, jm)
    assert [c["shoda"] for c in v.citaty] == ["neovereno", "nenalezeno"]


def test_r3_b_jmenovany_recnik_projde_i_sklonovany():
    for t in (f"Novák řekl: „{X}“ [1].", f"Podle slov Nováka platí: „{X}“ [1].", f"„{X}“ [1], řekl Jan Novák."):
        v = over_odstavec(t, REG_PROJEV, lambda n: VLASTNI, jm)
        assert v.citaty[0]["shoda"] == "presne" and v.citaty[0]["recnik"] == "Jan Novák (ANO)", t
    v = over_odstavec(f"Schillerové slova zněla: „{X}“ [4].", SCHILLEROVA, lambda n: VLASTNI, jm)
    assert v.citaty[0]["shoda"] == "presne"
    # přídavné jméno „nová“ není Novák
    v = over_odstavec(f"Nová vláda řekla: „{X}“ [1].", REG_PROJEV, lambda n: VLASTNI, jm)
    assert v.citaty[0]["shoda"] == "neovereno"


@pytest.mark.parametrize("zdroj", [
    f"Pan Hrnčíř tu řekl: „{X}“. To není můj názor, jen cituji.",           # Q1 v uvozovkách
    f"Cituji pana poslance Hrnčíře: {X}. Konec citace.",                   # Q2 cituji
    f"Jak řekl kolega, {X}.",                                             # jak řekl
    f"Podle ministerstva {X}.",                                           # podle
    f"Pan Hrnčíř, když mluvil o rozpočtu, {X}.",                          # jiné jméno v okně zdroje
])
def test_r3_k1_recnik_ve_zdroji_cituje_jineho(zdroj):
    v = over_odstavec(f"Novák řekl: „{X}“ [1].", REG_PROJEV, lambda n: zdroj, jm)
    assert v.citaty[0]["shoda"] == "neovereno"


@pytest.mark.parametrize("t", [
    "Schillerová ve Sněmovně řekla: „kdyby místo toho investoval do lihovin a děvčat, pak by asi vydělal více“ [4].",
    "Ministryně financí řekla: „kdyby místo toho investoval do lihovin a děvčat, pak by asi vydělal více“ [4].",
])
def test_r3_skutecny_text_schillerove_je_neovereno(t):
    # SCH1, SCH2: „…když řekl [Hrnčíř]… jenom cituji“
    v = over_odstavec(t, SCHILLEROVA, lambda n: TEXT_SCHILLEROVE, jm)
    assert v.citaty[0]["shoda"] == "neovereno"


def test_r3_m1_dva_zdroje_v_jedne_vete():
    v = over_odstavec(f"Petr Malý řekl „{X}“ [2] a Jan Novák „{X}“ [1].", MIX, lambda n: VLASTNI, jm)
    assert [c["shoda"] for c in v.citaty] == ["neovereno", "neovereno"]


def test_r3_d_vykon_odstavce_s_daty():
    t = " ".join(f"Dne {i % 28 + 1}. 11. 2026 řekl Novák „rozpočet je vyrovnaný číslo {i}“ [1]." for i in range(280))
    assert len(t) > 18_000
    zacatek = time.perf_counter()
    over_odstavec(t, REG_PROJEV, lambda n: "x " * 500, bez_jmen)
    assert time.perf_counter() - zacatek < 0.3


def test_r3_d_vykon_konce_vet():
    s = "Dne 16. září řekl posl. Novák, že č. 5 platí. " * 1_400
    assert len(s) > 60_000
    zacatek = time.perf_counter()
    konce = list(_konce_vet(s, 0, len(s)))
    assert time.perf_counter() - zacatek < 0.3 and len(konce) == 1_400


@pytest.mark.parametrize("n", [5_000, 20_000, 60_000])
def test_r3_d_vykon_sondy_f(n):
    for t, zdroj in (("„aaa bbb ccc“ [1] " + "Slovo. " * (n // 7), "aaa bbb ccc"),
                     (" ".join(f"„aaa bbb ccc{i}“ [1]. Věta." for i in range(n // 30)), "x " * 1000),
                     ("„aaa bbb ccc“ [1]", "Slovo. " * (n // 7) + "aaa bbb ccc")):
        zacatek = time.perf_counter()
        over_odstavec(t, REG_PROJEV, lambda k: zdroj, bez_jmen)
        assert time.perf_counter() - zacatek < 1.0
