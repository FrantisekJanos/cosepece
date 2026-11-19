"""Testy porovnání citace s textem projevu nebo zákona (najdi_citaci), bez databáze."""
from __future__ import annotations

import random
import re
import time
import unicodedata

import pytest

from hlidac_rag.citace_shoda import PRAH, najdi_citaci

PROJEV = (
    "Vážený pane předsedající, vážené kolegyně, vážení kolegové. Dovolte mi, abych se vyjádřil "
    "k novele zákona o střetu zájmů. Tato novela zkracuje lhůtu pro vymáhání dotací z deseti let "
    "na čtyři roky, a to i zpětně. Považuji to za naprosto nepřijatelné.\n\n"
    "Druhý bod se týká připomínkového řízení, které se tady úplně obešlo. Ministerstvo nám to "
    "předložilo jako poslanecký návrh a nikdo z odborné veřejnosti se k tomu nemohl vyjádřit."
)


def overeni_offsetu(text: str, r: dict) -> None:
    assert text[r["od_znaku"]:r["do_znaku"]] == r["nalezeny_text"]


def bez_diakritiky(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def test_presna_shoda():
    citace = "Tato novela zkracuje lhůtu pro vymáhání dotací z deseti let na čtyři roky"
    r = najdi_citaci(PROJEV, citace)
    assert r["shoda"] == "presne"
    assert r["skore"] == 1.0
    assert r["od_znaku"] == PROJEV.index(citace)
    assert r["nalezeny_text"] == citace
    overeni_offsetu(PROJEV, r)


def test_presna_shoda_vrati_klice_v_poradi():
    r = najdi_citaci(PROJEV, "abych se vyjádřil")
    assert list(r) == ["shoda", "skore", "od_znaku", "do_znaku", "nalezeny_text"]


def test_velka_pismena_a_okolni_mezery_jsou_presna_shoda():
    r = najdi_citaci(PROJEV, "  považuji TO za naprosto nepřijatelné ")
    assert r["shoda"] == "presne"
    assert r["nalezeny_text"] == "Považuji to za naprosto nepřijatelné"


def test_jine_uvozovky_a_pomlcky():
    text = "Ministr řekl: „Tohle je – podle mě – jasná věc.“ A odešel."
    r = najdi_citaci(text, '"Tohle je - podle mě - jasná věc."')
    assert r["shoda"] == "presne"
    assert r["nalezeny_text"] == "„Tohle je – podle mě – jasná věc.“"
    overeni_offsetu(text, r)


def test_jednoduche_uvozovky():
    text = "Říkal tomu ‚pozměňovák na poslední chvíli‘ a smál se."
    r = najdi_citaci(text, "'pozměňovák na poslední chvíli'")
    assert r["shoda"] == "presne"
    overeni_offsetu(text, r)


def test_zalomeni_radku_a_vice_mezer():
    text = "Tato novela   zkracuje\nlhůtu pro\r\n vymáhání dotací."
    r = najdi_citaci(text, "novela zkracuje lhůtu pro vymáhání dotací")
    assert r["shoda"] == "presne"
    assert r["od_znaku"] == text.index("novela")
    assert r["nalezeny_text"] == "novela   zkracuje\nlhůtu pro\r\n vymáhání dotací"
    overeni_offsetu(text, r)


def test_zalomeni_v_citaci():
    r = najdi_citaci(PROJEV, "Tato novela\nzkracuje lhůtu\n\npro vymáhání")
    assert r["shoda"] == "presne"
    assert r["nalezeny_text"] == "Tato novela zkracuje lhůtu pro vymáhání"


def test_vypustka_trojtecka_a_znak():
    text = "Já nevím… asi ano. Uvidíme..."
    r = najdi_citaci(text, "Já nevím... asi ano")
    assert r["shoda"] == "presne"
    assert r["nalezeny_text"] == "Já nevím… asi ano"
    r = najdi_citaci(text, "asi ano. Uvidíme…")
    assert r["shoda"] == "presne"
    assert r["nalezeny_text"] == "asi ano. Uvidíme..."
    overeni_offsetu(text, r)


def test_offsety_za_zmenami_delky():
    # vypuštka a zúžené mezery před shodou posunou normalizované pozice proti původním
    text = "Úvod…  a\n\n\n…   potom přijde ta podstatná věta o dotacích."
    citace = "ta podstatná věta o dotacích"
    r = najdi_citaci(text, citace)
    assert r["shoda"] == "presne"
    assert r["od_znaku"] == text.index(citace)
    assert r["do_znaku"] == text.index(citace) + len(citace)


def test_presna_shoda_nezacina_ani_nekonci_uprostred_slova():
    r = najdi_citaci("Řekl, že proto je chyba odejít.", "to je chyba")
    assert r["shoda"] == "priblizne"
    r = najdi_citaci("Tohle je chybami posetý text.", "tohle je chyb")
    assert r["shoda"] == "priblizne"
    assert r["nalezeny_text"] == "Tohle je chybami"
    r = najdi_citaci("Nejdřív proto je chyba, potom: to je chyba.", "to je chyba")
    assert r["shoda"] == "presne"
    assert r["od_znaku"] == 31


def test_vice_presnych_shod_vrati_prvni():
    text = "Opakuji, že to je chyba. A znovu: to je chyba. A potřetí: to je chyba."
    r = najdi_citaci(text, "to je chyba")
    assert r["shoda"] == "presne"
    assert r["od_znaku"] == text.index("to je chyba")


def test_pres_hranici_odstavce():
    citace = "Považuji to za naprosto nepřijatelné. Druhý bod se týká připomínkového řízení"
    r = najdi_citaci(PROJEV, citace)
    assert r["shoda"] == "presne"
    assert "\n\n" in r["nalezeny_text"]
    overeni_offsetu(PROJEV, r)


def test_pres_hranici_odstavce_bez_tecky_je_priblizne():
    r = najdi_citaci(PROJEV, "za naprosto nepřijatelné druhý bod se týká připomínkového řízení")
    assert r["shoda"] == "priblizne"
    assert r["skore"] == 1.0
    assert r["nalezeny_text"] == "za naprosto nepřijatelné.\n\nDruhý bod se týká připomínkového řízení"
    overeni_offsetu(PROJEV, r)


def test_bez_diakritiky_je_priblizne():
    r = najdi_citaci(PROJEV, "zkracuje lhutu pro vymahani dotaci z deseti let na ctyri roky")
    assert r["shoda"] == "priblizne"
    assert r["skore"] == 1.0
    assert r["nalezeny_text"] == "zkracuje lhůtu pro vymáhání dotací z deseti let na čtyři roky"
    overeni_offsetu(PROJEV, r)


def test_bez_interpunkce_je_priblizne():
    r = najdi_citaci(PROJEV, "Dovolte mi abych se vyjádřil k novele zákona o střetu zájmů")
    assert r["shoda"] == "priblizne"
    assert r["nalezeny_text"] == "Dovolte mi, abych se vyjádřil k novele zákona o střetu zájmů"


def test_vynechane_slovo():
    r = najdi_citaci(PROJEV, "Ministerstvo nám to předložilo jako návrh a nikdo z odborné veřejnosti se k tomu nemohl")
    assert r["shoda"] == "priblizne"
    assert PRAH <= r["skore"] < 1.0
    assert r["nalezeny_text"] == (
        "Ministerstvo nám to předložilo jako poslanecký návrh a nikdo z odborné veřejnosti se k tomu nemohl")
    overeni_offsetu(PROJEV, r)


def test_prehazena_koncovka():
    r = najdi_citaci(PROJEV, "abych se vyjádřil k novelám zákonu o střetu zájmů")
    assert r["shoda"] == "priblizne"
    assert PRAH <= r["skore"] < 1.0
    assert r["nalezeny_text"] == "abych se vyjádřil k novele zákona o střetu zájmů"


def test_koncovka_v_prvnim_a_poslednim_slove():
    r = najdi_citaci(PROJEV, "novelu zákona o střetu zájmy")
    assert r["shoda"] == "priblizne"
    assert r["nalezeny_text"] == "novele zákona o střetu zájmů"


def test_jiny_recnik_nenalezeno_s_kandidatem():
    # jiný řečník řekl něco jiného, jen se pár slov překrývá
    citace = "Tento zákon podporujeme, protože konečně zpřísňuje pravidla pro politiky a dotace"
    r = najdi_citaci(PROJEV, citace)
    assert r["shoda"] == "nenalezeno"
    assert 0 < r["skore"] < PRAH
    assert r["nalezeny_text"]
    overeni_offsetu(PROJEV, r)


def test_nenalezeno_bez_spolecneho_slova():
    r = najdi_citaci(PROJEV, "cizí věta bez jediného shodného výrazu xyzq")
    assert r == {"shoda": "nenalezeno", "skore": 0.0, "od_znaku": None, "do_znaku": None, "nalezeny_text": None}


def test_prazdny_text_je_nenalezeno():
    r = najdi_citaci("", "tři slova tady")
    assert r["shoda"] == "nenalezeno"
    assert r["skore"] == 0.0
    assert r["od_znaku"] is None


@pytest.mark.parametrize("citace", ["", "   ", "dobrý den", "§ 44a", "„Ano.“ – Ne."])
def test_kratka_citace_je_valueerror(citace):
    with pytest.raises(ValueError):
        najdi_citaci(PROJEV, citace)


def test_priblizne_vybere_nejvyssi_skore():
    text = ("Nejdřív řekl, že novele zákonu o dani nikdo nerozumí. Pak dodal: "
            "Novele zákona o dani z příjmů nikdo nerozumí, a odešel.")
    r = najdi_citaci(text, "novele zakona o dani z prijmu nikdo nerozumi")
    assert r["shoda"] == "priblizne"
    assert r["skore"] == 1.0
    assert r["od_znaku"] == text.index("Novele zákona")


def test_priblizne_pri_shode_skore_vrati_prvni():
    text = "Řekl: Daň z příjmů se zvýší. Pak znovu: Daň z příjmů se zvýší. A ještě jednou."
    r = najdi_citaci(text, "dan z prijmu se zvysi")
    assert r["shoda"] == "priblizne"
    assert r["od_znaku"] == text.index("Daň z příjmů")


# --- výkon (hrubé pojistky) -------------------------------------------------------------------

_SLABIKY = ["ka", "ze", "po", "sně", "mov", "ní", "zá", "kon", "da", "ně", "pří", "spě", "vek", "ře",
            "še", "úř", "ad", "vlá", "dy", "ost", "ra", "va", "to", "je", "se", "čí", "lo", "ma"]


def generovany_text(delka: int, seed: int = 1) -> str:
    """Pseudočeský text s Zipfovým rozdělením slov, větami a odstavci."""
    rnd = random.Random(seed)
    slovnik = ["".join(rnd.choice(_SLABIKY) for _ in range(rnd.randint(1, 4))) for _ in range(5000)]
    vahy = [1 / (i + 1) for i in range(len(slovnik))]
    casti, n = [], 0
    while n < delka:
        veta = " ".join(rnd.choices(slovnik, vahy, k=rnd.randint(6, 20))).capitalize() + ". "
        if rnd.random() < 0.15:
            veta += "\n\n"
        casti.append(veta)
        n += len(veta)
    return "".join(casti)[:delka]


def vyber_citaci(text: str, od: int, delka: int) -> tuple[int, str]:
    """Úsek textu od začátku slova po konec celého slova, nejvýš dané délky."""
    start = re.compile(r"\b\w").search(text, od).start()
    # poslední, možná useknuté slovo vypustit
    return start, text[start:start + delka].rsplit(None, 1)[0]


def pozmenena(citace: str) -> str:
    """Citace bez diakritiky a s vynechaným slovem uprostřed (vynutí přibližné hledání)."""
    slova = bez_diakritiky(citace).split()
    del slova[len(slova) // 2]
    return " ".join(slova)


def test_rychlost_60k_znaku_citace_2000_znaku():
    text = generovany_text(60_000)
    start, citace = vyber_citaci(text, 30_000, 2000)
    t0 = time.perf_counter()
    r = najdi_citaci(text, pozmenena(citace))
    trvani = time.perf_counter() - t0
    assert r["shoda"] == "priblizne"
    assert r["od_znaku"] == start
    assert trvani < 0.2, trvani


def test_rychlost_60k_znaku_nenalezeno():
    text = generovany_text(60_000)
    citace = generovany_text(2000, seed=7).rsplit(" ", 1)[0]
    t0 = time.perf_counter()
    r = najdi_citaci(text, citace)
    trvani = time.perf_counter() - t0
    assert r["shoda"] == "nenalezeno"
    assert trvani < 0.2, trvani


def test_rychlost_3m_znaku_citace_300_znaku():
    text = generovany_text(3_000_000)
    start, citace = vyber_citaci(text, 2_400_000, 300)
    t0 = time.perf_counter()
    r = najdi_citaci(text, pozmenena(citace))
    trvani = time.perf_counter() - t0
    assert r["shoda"] == "priblizne"
    assert r["od_znaku"] == start
    assert trvani < 3.0, trvani
