"""Testy čistých pravidel pro stenoprotokoly (věty z reálných stenoprotokolů psp.cz)."""
from __future__ import annotations

import inspect
import re

import pytest

from hlidac_rag.psp.rozprava_pravidla import (
    DRUHY,
    druh_vystoupeni,
    osloveny_recnik,
    pn_z_textu,
    shoda_prijmeni,
    tisky_z_konce,
)


# --- tisky_z_konce ---------------------------------------------------------

@pytest.mark.parametrize("konec, ocekavano", [
    ("/sněmovní tisk 76/", [76]),
    ("/sněmovní tisk 76/6/", [76]),
    ("/sněmovní tisk 77/8/", [77]),
    ("/sněmovní tisky 76 a 77/", [76, 77]),
    ("/sněmovní tisky 55 a 56/", [55, 56]),
    ("tisky 76, 77 a 78", [76, 77, 78]),
    ("285/1", [285]),
    ("Není sn.tiskem", []),
    ("/sněmovní dokument 12/", []),
    ("/sněmovní tisk 76-E/", []),
    ("", []),
    (None, []),
])
def test_tisky_z_konce(konec, ocekavano):
    assert tisky_z_konce(konec) == ocekavano


# --- shoda_prijmeni --------------------------------------------------------

@pytest.mark.parametrize("tvar, prijmeni", [
    ("Haase", "Haas"), ("Haasovi", "Haas"), ("Vlčka", "Vlček"),
    ("Mrázové", "Mrázová"), ("Krškové", "Kršková"), ("Kovářová", "Kovářová"),
])
def test_shoda_prijmeni_ano(tvar, prijmeni):
    assert shoda_prijmeni(tvar, prijmeni)


def test_shoda_prijmeni_ne():
    assert not shoda_prijmeni("Novotného", "Haas")


# --- osloveny_recnik -------------------------------------------------------

@pytest.mark.parametrize("text, ocekavano", [
    ("Vaším prostřednictvím, pane místopředsedo, bych chtěl reagovat na Karla Haase. Já vždycky si vážím", "Haase"),
    ("Děkuju. Zareaguju na pana poslance Vlčka, protože to nebylo přesné.", "Vlčka"),
    ("Já bych chtěl reagovat na svého ctěného kolegu Karla Haase.", "Haase"),
    ("Paní poslankyně Kovářová, prostřednictvím pana předsedajícího, to není pravda.", "Kovářová"),
    ("Děkuji. Já bych se chtěla vyjádřit k odpovědi paní ministryně Mrázové, že to je otázka stavebního práva.", "Mrázové"),
    ("já si dovolím reagovat na svého předřečníka, pana poslance Davida Pražáka, prostřednictvím pana předsedajícího, a jeho návrh", "Pražáka"),
    ("Děkuji za slovo, pane předsedající. Chtěl bych říct něco k věci.", None),
    ("Děkuju, prostřednictvím pana předsedajícího bych řekl, že ano.", None),
    ("", None),
    (None, None),
])
def test_osloveny_recnik(text, ocekavano):
    assert osloveny_recnik(text) == ocekavano


# --- druh_vystoupeni -------------------------------------------------------

def test_druh_predsedajici():
    assert druh_vystoupeni("cokoli faktická", "Poslanec", True) == "predsedajici"


def test_druh_fakticka():
    t = "Děkuju za dodržení času. Bude reagovat pan kolega Haas s faktickou poznámkou."
    assert druh_vystoupeni(t, "Poslanec Karel Haas", False) == "fakticka"
    assert druh_vystoupeni(t, "Poslanec Karel Haas", False, "Haas") == "fakticka"


def test_druh_vice_lidi_podle_prijmeni():
    t = "Mám tady jednu faktickou poznámku, potom s přednostním právem předseda Sněmovny Tomio Okamura a pak pojedeme zase podle plánu."
    assert druh_vystoupeni(t, "Předseda PSP", False, "Okamura") == "prednostni"
    t2 = "Nyní vystoupí poslední s přednostním právem předseda ODS Martin Kupka a potom se tedy připraví paní poslankyně Julie Smejkalová."
    assert druh_vystoupeni(t2, "Poslanec Martin Kupka", False, "Kupka") == "prednostni"
    assert druh_vystoupeni(t2, "Poslankyně Julie Smejkalová", False, "Smejkalová") == "recnik"


def test_druh_fakticka_za_jmenem():
    t = "Pan poslanec Hřib vystoupí s faktickou poznámkou."
    assert druh_vystoupeni(t, "Poslanec Zdeněk Hřib", False, "Hřib") == "fakticka"


def test_druh_zpravodaj():
    t = "Prosím, aby se slova ujal zpravodaj pro prvé čtení, pan poslanec Blažej."
    assert druh_vystoupeni(t, "Poslanec Blažej", False) == "zpravodaj"
    assert druh_vystoupeni("Pane zpravodaji, máte slovo.", "Poslanec X", False) == "zpravodaj"


def test_druh_predkladatel():
    t = "Předložený návrh uvede za navrhovatele místopředsedkyně vlády a ministryně financí Alena Schillerová."
    assert druh_vystoupeni(t, "Místopředsedkyně vlády a ministryně financí Alena Schillerová", False) == "predkladatel"
    assert druh_vystoupeni("Nyní se táži navrhovatelky, zda má zájem vystoupit?", "Poslankyně X", False) == "predkladatel"


def test_druh_podekovani_neni_rozhodujici():
    t = "Já vám děkuji, paní zpravodajko. Nyní vystoupí pan poslanec Jan Berki."
    assert druh_vystoupeni(t, "Poslanec Jan Berki", False) == "recnik"
    t2 = "Já vám děkuji, paní zpravodajko."
    assert druh_vystoupeni(t2, "Poslanec Jan Berki", False) == "recnik"


def test_druh_ministr_z_role():
    assert druh_vystoupeni("Prosím, pane ministře.", "Ministr financí Zbyněk Stanjura", False) == "ministr"
    assert druh_vystoupeni(None, "Ministryně pro místní rozvoj ČR Zuzana Mrázová", False) == "ministr"
    assert druh_vystoupeni(None, "Předseda vlády Petr Fiala", False) == "ministr"


def test_druh_proceduralni_a_recnik():
    assert druh_vystoupeni("Máte slovo, procedurální návrh.", "Poslanec X", False) == "proceduralni"
    assert druh_vystoupeni(None, "Poslanec X", False) == "recnik"
    assert druh_vystoupeni("Prosím, pan poslanec Bureš.", None, False) == "recnik"


def test_druhy_pokryva_vsechny_vystupy_druh_vystoupeni():
    """DRUHY (filtr druh v nástrojích MCP) = právě hodnoty, které druh_vystoupeni vrací, v daném pořadí."""
    assert DRUHY == ("recnik", "fakticka", "prednostni", "zpravodaj", "predkladatel", "ministr", "proceduralni",
                     "predsedajici")
    vraci = set(re.findall(r'return "(\w+)"', inspect.getsource(druh_vystoupeni)))
    assert vraci == set(DRUHY)
    vzorky = [("x", "Poslanec X", True), ("S faktickou poznámkou pan poslanec X.", "Poslanec X", False),
              ("S přednostním právem pan předseda X.", "Poslanec X", False), ("Prosím pana zpravodaje.", None, False),
              ("Za navrhovatele vystoupí pan X.", None, False), (None, "Ministr financí X", False),
              ("Máte slovo, procedurální návrh.", None, False), (None, "Poslanec X", False)]
    assert [druh_vystoupeni(*v) for v in vzorky] == ["predsedajici", *DRUHY[1:7], "recnik"]


# --- pn_z_textu ------------------------------------------------------------

@pytest.mark.parametrize("text, ocekavano", [
    ("Já se hlásím k pozměňovacímu návrhu vloženému do systému jako sněmovní dokument číslo 887 k tomuto sněmovnímu tisku", [887]),
    ("Každopádně se chci přihlásit k pozměňovacímu návrhu pod sněmovním číslem 857.", [857]),
    ("A poslední SD 789, schvalování státního rozpočtu", [789]),
    ("Následně pozměňovací návrhy pod sněmovním dokumentem 2244 a 2245, dám společné odůvodnění.", [2244, 2245]),
    ("Ty pozměňovací návrhy mají čísla 571, 572, 573 a 574", [571, 572, 573, 574]),
    ("sněmovnímu dokumentu číslo 492 až 498 v tabulce pod číslem 16 až 22", [492, 493, 494, 495, 496, 497, 498]),
    ("Pak tady máme pozměňovací návrh 497.", [497]),
    ("pozměňovací návrh, sněmovní dokument pod číslem 707.", [707]),
    ("pozměňovacímu návrhu 2327, který započítává změnu za období 2021 až 2025", [2327]),
    ("pozměňovacích návrhů 2300 až 2999", [2300]),
    ("sněmovní dokument 512, pořadové číslo 4.", [512]),
    ("v pozměňovacím návrhu je 1 073 stránek", []),
    ("§ 2312 zákona", []),
    ("v roce 2025 a 2026", []),
    ("sněmovní tisk 285", []),
    ("vládní návrh zákona, sněmovní tisk 94, k pozměňovacímu návrhu", []),
    ("", []),
])
def test_pn_z_textu(text, ocekavano):
    assert pn_z_textu(text) == ocekavano


def test_pn_z_textu_vlastni_cislo():
    # reálná věta: „Můj druhý pozměňovací návrh má číslo 2092 a navrhuje …“
    assert pn_z_textu("Můj druhý pozměňovací návrh má číslo 2092 a navrhuje zavedení povinného krácení") == [2092]


# --- regrese: shoda příjmení a běžná malá slova ----------------------------

@pytest.mark.parametrize("tvar, prijmeni", [
    ("Haase", "Haas"), ("Haasovi", "Haas"), ("Vlčka", "Vlček"), ("Válka", "Válek"),
    ("Němce", "Němec"), ("Mrázové", "Mrázová"), ("Krškové", "Kršková"),
    ("Kovářová", "Kovářová"), ("Kupky", "Kupka"), ("Novotného", "Novotný"),
    ("Pražáka", "Pražák"), ("Okamury", "Okamura"),
])
def test_shoda_prijmeni_tabulka_ano(tvar, prijmeni):
    assert shoda_prijmeni(tvar, prijmeni)


@pytest.mark.parametrize("tvar, prijmeni", [
    ("Novotného", "Novák"), ("Bendla", "Berki"), ("Bereme", "Berki"),
])
def test_shoda_prijmeni_tabulka_ne(tvar, prijmeni):
    assert not shoda_prijmeni(tvar, prijmeni)


# pohyblivé e (-ek, -ec, -el): kmen je příjmení bez „e“ (Marek -> mark), 1. pád se porovná celý
@pytest.mark.parametrize("tvar, prijmeni, ocekavano", [
    ("Marka", "Marek", True), ("Markovi", "Marek", True), ("Marek", "Marek", True),
    ("Maříkovi", "Marek", False), ("Marně", "Marek", False),
    ("Vlček", "Vlček", True), ("Vlčkovi", "Vlček", True), ("Válek", "Válek", True), ("Němec", "Němec", True),
    ("Havla", "Havel", True), ("STANu", "Staněk", False),
    ("Niemiece", "Niemiec", True),   # e, které nevypadává: tvar začíná celým příjmením
])
def test_shoda_prijmeni_pohyblive_e(tvar, prijmeni, ocekavano):
    assert shoda_prijmeni(tvar, prijmeni) is ocekavano


# víceslovná příjmení: tvar se porovná s každým slovem příjmení (krátká slova jako „ml.“ se nepočítají)
@pytest.mark.parametrize("tvar, prijmeni, ocekavano", [
    ("Adamové", "Pekarová Adamová", True), ("Pekarové", "Pekarová Adamová", True),
    ("Vaňková", "Pivoňka Vaňková", True), ("Pivoňka", "Pivoňka Vaňková", True),
    ("Crkvenjaš", "Němečková Crkvenjaš", True), ("Petrové", "Marcová - Petrová", True),
    ("Batkové", "Šťáhlavská-Batková", True), ("Kučery", "Kučera ml.", True),
    ("Mluvím", "Kučera ml.", False), ("Novákové", "Pekarová Adamová", False),
])
def test_shoda_prijmeni_viceslovne(tvar, prijmeni, ocekavano):
    assert shoda_prijmeni(tvar, prijmeni) is ocekavano


def test_druh_podekovani_nerozhoduje_ani_s_prijmenim():
    # poděkování předchozímu řečníkovi (i se shodným příjmením a „faktickou“) o druhu dalšího nerozhoduje
    t = "Děkuji panu poslanci Maříkovi za faktickou poznámku. Nyní vystoupí pan poslanec Marek."
    assert druh_vystoupeni(t, "Poslanec Josef Marek", False, "Marek") == "recnik"
    t2 = "Děkuji panu poslanci Markovi za faktickou poznámku. Nyní vystoupí v rozpravě pan poslanec Marek."
    assert druh_vystoupeni(t2, "Poslanec Josef Marek", False, "Marek") == "recnik"
    # oznámení s poděkováním v jedné větě rozhoduje dál (věty ze stenoprotokolů)
    for t3, prijmeni, druh in [
        ("Děkuji. Další faktická poznámka – Vlastimil Válek. Vaše 2 minuty.", "Válek", "fakticka"),
        ("Děkujeme a s faktickou poznámkou přichází pan místopředseda Skopeček.", "Skopeček", "fakticka"),
        ("Děkuji paní poslankyni Janě Pastuchové a s faktickou poznámkou předsedkyně klubu Pirátů, paní Olga "
         "Richterová.", "Richterová", "fakticka"),
        ("Děkuji, pane poslanče, a s faktickou poznámkou pan poslanec Michal Zuna.", "Zuna", "fakticka"),
        ("Děkuji za dodržení času a zatím poslední faktická poznámka Vlastimil Válek.", "Válek", "fakticka"),
        ("Děkuji, pane místopředsedo, a s přednostním právem předseda Pirátské strany Zdeněk Hřib.", "Hřib",
         "prednostni"),
        ("Děkuji s přednostním právem první místopředseda Poslanecké sněmovny Patrik Nacher. Prosím.", "Nacher",
         "prednostni"),
        ("Čas, paní poslankyně. Děkuji a ještě jednou pan poslanec Válek. Připraví se paní poslankyně Urbanová "
         "s faktickou poznámkou.", "Válek", "recnik"),
    ]:
        assert druh_vystoupeni(t3, "Poslanec X", False, prijmeni) == druh, t3
    # poděkovaná osoba je ta, která právě mluvila: její věta o dalším vystoupení nerozhoduje
    t4 = "Děkuji, paní poslankyně Kovářová, a s faktickou poznámkou pan poslanec Haas."
    assert druh_vystoupeni(t4, "Poslankyně Věra Kovářová", False, "Kovářová") == "recnik"
    assert druh_vystoupeni(t4, "Poslanec Karel Haas", False, "Haas") == "fakticka"


def test_druh_meni_prijmeni():
    """Bez příjmení rozhoduje poslední věta s klíčovým slovem, s příjmením úsek o řečníkovi."""
    t = ("Nyní vystoupí poslední s přednostním právem předseda ODS Martin Kupka a potom se tedy připraví "
         "paní poslankyně Julie Smejkalová.")
    assert druh_vystoupeni(t, "Poslankyně Julie Smejkalová", False) == "prednostni"
    assert druh_vystoupeni(t, "Poslankyně Julie Smejkalová", False, "Smejkalová") == "recnik"
    # víceslovné příjmení: úsek o paní Pivoňka Vaňkové, ne o paní Ančincové za „potom“
    t2 = ("Tím pádem tedy otvírám obecnou rozpravu, do které se přihlásily s přednostním právem, stanoviskem klubu "
          "paní poslankyně Pavla Pivoňka Vaňková a potom vlastně už bez přednostního práva paní poslankyně "
          "Hana Ančincová.")
    assert druh_vystoupeni(t2, "Poslankyně Pavla Pivoňka Vaňková", False, "Pivoňka Vaňková") == "prednostni"
    assert druh_vystoupeni(t2, "Poslankyně Hana Ančincová", False, "Ančincová") == "recnik"


# „bez přednostního práva“: rozhoduje zmínka s předložkou nejbližší před příjmením (věty ze stenoprotokolů)
@pytest.mark.parametrize("text, prijmeni, ocekavano", [
    ("Ano, nyní vystoupí s přednostním právem – respektive bez přednostního práva, s řádnou přihláškou – "
     "pan předseda Zdeněk Hřib. Prosím, máte slovo.", "Hřib", "recnik"),                       # 10/1/49/9
    ("Bez přednostního práva. Dobře. Prosím.", "Hřib", "recnik"),                                   # 10/7/2/6
    ("V této chvíli jsme vyčerpali poslance s přednostním právem a přihlášeni jsou poslanci bez přednostního "
     "práva, takže sem k řečništi pozvu Michala Zunu, připraví se Jiří Pospíšil.", "Zuna", "recnik"),  # 10/10/16/1
    ("Takže s přednostním právem Zdeněk Hřib – Zdeněk Hřib ne, Olga Richterová ne, Marian Jurečka s přednostním "
     "právem ne, takže tady mám dvě přihlášky bez přednostního práva – Petr Bendl a Petr Hladík.\n\n"
     "Takže já poprosím Petra Bendla.", "Bendl", "recnik"),                                         # 10/20/298/2
    ("Dalším přihlášeným bez přednostního práva, protože ti, co mají přednostní právo a žádali tady nejsou, "
     "je pan poslanec Jan Bureš.", "Bureš", "recnik"),                                               # 10/24/30/2
    ("Nyní tedy paní předsedkyně Olga Richterová – chápu, že se hlásíte bez přednostního práva, když to vidím "
     "v pořadí?", "Richterová", "recnik"),                                                           # 10/34/461/4
    ("Je to předseda Hřib, předseda Richterová, předseda Matěj Ondřej Havel, místopředseda Skopeček, předseda "
     "Jakob, takže s přednostním právem mám pět, a dále je tady bez přednostního práva Marian Jurečka, "
     "Lukáš Vlček, Gabriela Svárovská a Adriana Chochelová – čtyři.", "Hřib", "prednostni"),         # 10/10/164/6
    ("Já přečtu to pořadí přednostních práv, abyste se zorientovali, takže: předseda Hřib, předsedkyně "
     "Richterová...", "Hřib", "prednostni"),                                                         # 10/34/161/1
    ("Poprosím poslední s přednostním právem, Marek Výborný.", "Výborný", "prednostni"),
    ("S přednostním právem se přihlásila – jestli to ještě platí – Olga Richterová.", None, "prednostni"),
])
def test_druh_bez_prednostniho_prava(text, prijmeni, ocekavano):
    assert druh_vystoupeni(text, "Poslanec X", False, prijmeni) == ocekavano


def test_druh_male_slovo_nepoplete_prijmeni():
    t = "Pan poslanec Berki vystoupí s faktickou poznámkou. Bereme si pauzu."
    assert druh_vystoupeni(t, "Poslanec Jan Berki", False, "Berki") == "fakticka"
    t2 = "Pan poslanec Marek vystoupí s faktickou poznámkou. Marně čekám na odpověď."
    assert druh_vystoupeni(t2, "Poslanec Marek", False, "Marek") == "fakticka"
