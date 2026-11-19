"""Přepočet rozpravy u projevů jedné schůze (volá ho ingest.assemble po uložení projevů).

projev_tisk (tisky z bodu a ct projevu), druh vystoupení, předchozí řečník ve stejném bodě,
odhad adresáta faktické poznámky a PN zmíněné v textu; pravidla jsou v rozprava_pravidla.
"""
from __future__ import annotations

from datetime import date
from typing import NamedTuple

from .rozprava_pravidla import druh_vystoupeni, osloveny_recnik, pn_z_textu, shoda_prijmeni, tisky_z_konce


class _ProjevRozpravy(NamedTuple):
    id: int
    datum: date | None
    id_bod: int | None
    ct: int | None
    ct_zdroj: str | None
    predsedajici: bool
    role: str | None
    recnik: str | None
    id_osoba: int | None
    text: str


# stejné ct z více zdrojů: zůstane zdroj s nižším číslem
_PRIORITA_ZDROJE = {"rec": 0, "bod": 1, "nadpis": 2}
MAX_KANDIDATU = 15  # adresát faktické se hledá mezi tolika posledními řečníky bodu


def prepocitej_rozpravu(cur, obdobi: int, schuze: int) -> dict:
    """Přepočítá rozpravu u projevů schůze: projev_tisk, druh, predchozi_recnik_id, reaguje_na_odhad, projev_pn.

    Idempotentní: řádky projev_tisk a projev_pn projevů schůze se smažou a vloží znovu, sloupce se přepíšou.
    Zrušené projevy (zruseno IS NOT NULL) nedostanou řádky ani druh a nikomu nejsou předchozím řečníkem.
    Úseky bez řečníka (recnik i id_osoba NULL) se přepočítají, ale předchozím řečníkem ani adresátem nejsou.
    """
    cur.execute(
        """SELECT id, datum, id_bod, ct, ct_zdroj, predsedajici, role, recnik, id_osoba, text, zruseno IS NOT NULL
           FROM steno.projev WHERE obdobi = %s AND schuze = %s ORDER BY poradi, id""",
        (obdobi, schuze),
    )
    projevy = [_ProjevRozpravy(*r[:10]) for r in cur.fetchall() if not r[10]]
    cur.execute("SELECT id_bod, konec, nazev FROM steno.bod WHERE id_bod = ANY(%s)",
                (list({p.id_bod for p in projevy if p.id_bod is not None}),))
    tisky_bodu = {i: tisky_z_konce(konec) or tisky_z_konce(nazev) for i, konec, nazev in cur.fetchall()}
    cur.execute("SELECT id_osoba, jmeno, prijmeni FROM steno.osoba WHERE id_osoba = ANY(%s)",
                (list({p.id_osoba for p in projevy if p.id_osoba is not None}),))
    osoby = {i: (j, pr) for i, j, pr in cur.fetchall()}
    # příjmení osob období (člen klubu nebo řečník kteréhokoli projevu): jen ta smí být adresátem z textu
    cur.execute("""SELECT DISTINCT o.prijmeni FROM steno.osoba o
                   WHERE o.prijmeni IS NOT NULL
                     AND (o.id_osoba IN (SELECT id_osoba FROM steno.klub_clenstvi)
                          OR o.id_osoba IN (SELECT id_osoba FROM steno.projev WHERE obdobi = %s))""", (obdobi,))
    prijmeni_obdobi = [r[0] for r in cur.fetchall()]

    tisk_rows, pn_rows, sloupce = [], [], []
    # nepředsedající projevy se řečníkem podle dne a bodu / ct (kandidáti na předchozího řečníka)
    podle_bodu: dict[tuple, list[_ProjevRozpravy]] = {}
    podle_ct: dict[tuple, list[_ProjevRozpravy]] = {}
    predchozi: _ProjevRozpravy | None = None
    for p in projevy:
        tisk_rows += [(p.id, ct, zdroj) for ct, zdroj in _tisky_projevu(p, tisky_bodu).items()]
        pn_rows += [(p.id, cislo) for cislo in pn_z_textu(p.text)]

        text_predsedajiciho = (predchozi.text if predchozi and predchozi.predsedajici
                               and predchozi.datum == p.datum else None)
        druh = druh_vystoupeni(text_predsedajiciho, p.role, p.predsedajici, _prijmeni(p, osoby))
        # stejný bod: stejné id_bod, u projevu bez bodu stejné ct
        if p.id_bod is not None:
            kandidati = podle_bodu.get((p.datum, p.id_bod), [])
        elif p.ct is not None:
            kandidati = podle_ct.get((p.datum, p.ct), [])
        else:
            kandidati = []
        reaguje = _odhad_adresata(p.text, kandidati, osoby, prijmeni_obdobi) if druh == "fakticka" else None
        sloupce.append((p.id, druh, kandidati[-1].id if kandidati else None, reaguje))
        # úsek bez řečníka (aname 0: začátek části bez kotvy) nikomu předchozím řečníkem ani adresátem není
        if not p.predsedajici and (p.recnik or p.id_osoba is not None):
            if p.id_bod is not None:
                podle_bodu.setdefault((p.datum, p.id_bod), []).append(p)
            if p.ct is not None:
                podle_ct.setdefault((p.datum, p.ct), []).append(p)
        predchozi = p

    cur.execute("""DELETE FROM steno.projev_tisk WHERE projev_id IN
                   (SELECT id FROM steno.projev WHERE obdobi = %s AND schuze = %s)""", (obdobi, schuze))
    cur.execute("""DELETE FROM steno.projev_pn WHERE projev_id IN
                   (SELECT id FROM steno.projev WHERE obdobi = %s AND schuze = %s)""", (obdobi, schuze))
    # přepisují se jen změněné řádky: zápis řádku projevu je drahý (~1 ms) a při běžném složení se nic nemění
    cur.execute("""UPDATE steno.projev SET druh = NULL, predchozi_recnik_id = NULL, reaguje_na_odhad = NULL
                   WHERE obdobi = %s AND schuze = %s AND zruseno IS NOT NULL
                     AND (druh IS NOT NULL OR predchozi_recnik_id IS NOT NULL OR reaguje_na_odhad IS NOT NULL)""",
                (obdobi, schuze))
    cur.executemany(
        """UPDATE steno.projev SET druh = %(druh)s, predchozi_recnik_id = %(pred)s, reaguje_na_odhad = %(reag)s
           WHERE id = %(id)s
             AND (druh, predchozi_recnik_id, reaguje_na_odhad) IS DISTINCT FROM (%(druh)s, %(pred)s, %(reag)s)""",
        [dict(id=i, druh=d, pred=pr, reag=r) for i, d, pr, r in sloupce],
    )
    cur.executemany("INSERT INTO steno.projev_tisk (projev_id, ct, zdroj) VALUES (%s, %s, %s)", tisk_rows)
    cur.executemany("INSERT INTO steno.projev_pn (projev_id, cislo_pn) VALUES (%s, %s)", pn_rows)
    return {"projevu": len(projevy), "projev_tisk": len(tisk_rows), "projev_pn": len(pn_rows)}


def _tisky_projevu(p: _ProjevRozpravy, tisky_bodu: dict[int, list[int]]) -> dict[int, str]:
    """ct -> zdroj: ct projevu s jeho zdrojem (rec | bod | nadpis, viz ingest._ct_bodu) a tisky z konce či názvu
    bodu (bod)."""
    zdroje: dict[int, str] = {}
    vlastni = [(p.ct, p.ct_zdroj)] if p.ct is not None else []
    for ct, zdroj in vlastni + [(t, "bod") for t in tisky_bodu.get(p.id_bod, [])]:
        if ct not in zdroje or _PRIORITA_ZDROJE[zdroj] < _PRIORITA_ZDROJE[zdroje[ct]]:
            zdroje[ct] = zdroj
    return zdroje


def _prijmeni(p: _ProjevRozpravy, osoby: dict[int, tuple]) -> str | None:
    """Příjmení řečníka: ze steno.osoba, jinak poslední slovo textu odkazu."""
    if p.id_osoba in osoby and osoby[p.id_osoba][1]:
        return osoby[p.id_osoba][1]
    slova = (p.recnik or "").split()
    return slova[-1] if slova else None


def _odhad_adresata(text: str, kandidati: list[_ProjevRozpravy], osoby: dict[int, tuple],
                    prijmeni_obdobi: list[str]) -> str | None:
    """Na koho faktická poznámka reaguje: jméno nejbližšího z posledních řečníků bodu, jehož příjmení
    odpovídá oslovenému tvaru; bez shody tvar z textu, ale jen když je to příjmení osoby období
    (jinak „Pirátů“, „Takže“ …), bez osloveného jména None."""
    tvar = osloveny_recnik(text)
    if tvar is None:
        return None
    for k in reversed(kandidati[-MAX_KANDIDATU:]):
        prijmeni = _prijmeni(k, osoby)
        if prijmeni and shoda_prijmeni(tvar, prijmeni):
            return " ".join(x for x in osoby[k.id_osoba] if x) if k.id_osoba in osoby else k.recnik
    return tvar if any(shoda_prijmeni(tvar, p) for p in prijmeni_obdobi) else None
