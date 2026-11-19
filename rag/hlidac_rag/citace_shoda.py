"""Porovnání citace s textem projevu nebo zákona: zazněl výrok opravdu a kde přesně (bez DB).

Dvě úrovně shody:
- `presne`: po normalizaci (mezery a zalomení řádků, typografické uvozovky a pomlčky, „…“ = „...“,
  velikost písmen) je citace podřetězcem textu, který nezačíná ani nekončí uprostřed slova;
- `priblizne`: porovnávají se jen slova bez diakritiky a interpunkce, drobné rozdíly (vynechané slovo,
  jiná koncovka) se tolerují; skóre aspoň PRAH.

Offsety `od_znaku`/`do_znaku` jsou indexy do původního textu (konec exkluzivně), takže
`text[od_znaku:do_znaku] == nalezeny_text`.
"""
from __future__ import annotations

import math
import re
import unicodedata
from bisect import bisect_right
from collections import defaultdict
from difflib import SequenceMatcher
from itertools import accumulate

PRAH = 0.85
MIN_SLOV = 3
MIN_OKEN = 8           # kolik oken textu se nejméně porovná podrobně
SLOV_OKEN = 8_000      # kolik slov textu se v oknech nejvýš porovná (krátká citace = víc levných oken)
MAX_HLASU = 50_000     # kolik výskytů kotevních slov se při předvýběru nejvýš projde
MAX_MEZERA = 4         # nesouhlasný úsek do tolika slov se porovná po znacích (jiná koncovka)

_SLOVO = re.compile(r"\w+")
_UVOZOVKY = re.compile("[\"'`´„“”‟«»‹›‚‘’‛″′]")
_POMLCKY = re.compile("[‐‑‒–—―−]")
# místa, kde normalizace mění délku: víc bílých znaků za sebou, jiný bílý znak než mezera, výpustka
_ZMENA_DELKY = re.compile(r"\s{2,}|[^\S ]|…")


def najdi_citaci(text: str, citace: str) -> dict:
    """Najde citaci v textu projevu nebo zákona.

    Vrací {shoda: presne | priblizne | nenalezeno, skore: 0–1, od_znaku, do_znaku, nalezeny_text}.
    U více shod vrací první přesnou, jinak přibližnou s nejvyšším skóre (při shodě skóre první).
    `nenalezeno` nese nejbližšího kandidáta se skóre pod PRAH; bez jediného společného slova skóre 0
    a offsety None. Citace kratší než MIN_SLOV slov = ValueError (krátké citace jsou nejednoznačné)."""
    slova_citace = _slova(citace)
    if len(slova_citace) < MIN_SLOV:
        raise ValueError(f"Citace musí mít aspoň {MIN_SLOV} slova, kratší je nejednoznačná.")

    norm = _Normalizovany(text)
    hledana = _Normalizovany(citace).text.strip()
    i = norm.text.find(hledana)
    while i >= 0 and not _na_hranici_slov(norm.text, i, i + len(hledana)):
        i = norm.text.find(hledana, i + 1)
    if i >= 0:
        return _vysledek(text, "presne", 1.0, norm.puvodni_od(i), norm.puvodni_do(i + len(hledana)))

    slova = _slova(text)
    nejlepsi = None  # (skóre, první slovo, za posledním slovem)
    for a, b in dict.fromkeys(_oblasti(slova_citace, slova)):
        usek = _nejlepsi_usek(slova_citace, slova, a, b)
        if usek and (nejlepsi is None or (-usek[0], usek[1]) < (-nejlepsi[0], nejlepsi[1])):
            nejlepsi = usek
    if nejlepsi is None:
        return {"shoda": "nenalezeno", "skore": 0.0, "od_znaku": None, "do_znaku": None, "nalezeny_text": None}
    skore, s, e = nejlepsi
    hranice = _hranice_slov(text, s, e)
    return _vysledek(text, "priblizne" if skore >= PRAH else "nenalezeno", skore, *hranice)


def _vysledek(text: str, shoda: str, skore: float, od: int, do: int) -> dict:
    return {"shoda": shoda, "skore": skore, "od_znaku": od, "do_znaku": do, "nalezeny_text": text[od:do]}


# --- přesná shoda -------------------------------------------------------------------------------

def _mala(s: str) -> str:
    """Malá písmena při zachování délky (znak, který by se rozpadl na víc znaků, zůstane, jak je)."""
    m = s.lower()
    if len(m) == len(s):
        return m
    return "".join(c.lower() if len(c.lower()) == 1 else c for c in s)


def _na_hranici_slov(s: str, od: int, do: int) -> bool:
    """Úsek s[od:do] nezačíná ani nekončí uprostřed slova („to je chyba“ není v „proto je chyba“)."""
    def pismeno(c: str) -> bool:
        return c.isalnum() or c == "_"
    return (not (od > 0 and pismeno(s[od - 1]) and pismeno(s[od]))
            and not (do < len(s) and pismeno(s[do - 1]) and pismeno(s[do])))


class _Normalizovany:
    """Text normalizovaný pro přesnou shodu a převod pozic v něm zpět do původního textu."""

    def __init__(self, text: str):
        s = _POMLCKY.sub("-", _UVOZOVKY.sub('"', _mala(text)))
        casti: list[str] = []
        # změny délky: (pozice náhrady v normalizovaném textu, začátek a konec v původním, délka náhrady)
        self._zmeny: list[tuple[int, int, int, int]] = []
        posledni = posun = 0
        for m in _ZMENA_DELKY.finditer(s):
            nahrada = "..." if m.group() == "…" else " "
            casti.append(s[posledni:m.start()])
            casti.append(nahrada)
            self._zmeny.append((m.start() + posun, m.start(), m.end(), len(nahrada)))
            posun += len(nahrada) - (m.end() - m.start())
            posledni = m.end()
        casti.append(s[posledni:])
        self.text = "".join(casti)
        self._pozice = [z[0] for z in self._zmeny]

    def puvodni_od(self, i: int) -> int:
        """Začátek v původním textu pro pozici i v normalizovaném textu."""
        k = bisect_right(self._pozice, i) - 1
        if k < 0:
            return i
        p, od, do, n = self._zmeny[k]
        return od if i < p + n else do + (i - p - n)

    def puvodni_do(self, j: int) -> int:
        """Konec (exkluzivní) v původním textu pro konec j v normalizovaném textu."""
        k = bisect_right(self._pozice, j - 1) - 1
        if k < 0:
            return j
        p, od, do, n = self._zmeny[k]
        return do if j - 1 < p + n else do + (j - p - n)


# --- přibližná shoda ----------------------------------------------------------------------------

def _bez_diakritiky(slovo: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", slovo.lower()) if not unicodedata.combining(c))


def _slova(s: str) -> list[str]:
    """Slova textu malými písmeny bez diakritiky; interpunkce a mezery odpadnou."""
    puvodni = _SLOVO.findall(s)
    tvary = {w: _bez_diakritiky(w) for w in set(puvodni)}
    return [tvary[w] for w in puvodni]


def _hranice_slov(text: str, s: int, e: int) -> tuple[int, int]:
    """Začátek slova s a konec slova e-1 (pořadí slov podle _SLOVO) v původním textu."""
    od = do = None
    for k, m in enumerate(_SLOVO.finditer(text)):
        if k == s:
            od = m.start()
        if k == e - 1:
            do = m.end()
            break
    return od, do


def _kmen(slovo: str) -> str:
    """Začátek slova pro předvýběr, aby jiná koncovka neschovala vzácné slovo; krátká slova a čísla celá."""
    return slovo[:4] if len(slovo) >= 5 and slovo.isalpha() else slovo


def _oblasti(cit: list[str], slova: list[str]) -> list[tuple[int, int]]:
    """Předvýběr: oblasti slov textu [a, b), kde citace nejspíš leží, od nejpravděpodobnější.

    Každý výskyt kmene slova citace (od nejvzácnějšího, dokud nepřekročí MAX_HLASU) hlasuje pro
    diagonálu = odhadnutý začátek citace v textu. Diagonály se sčítají do košů šířky `sirka` (tolerance
    vynechaných a přidaných slov). Okno = dva sousední koše, jeho váha = součet vah pozic citace, které
    v něm mají výskyt (každá jednou; vzácné slovo váží víc než „a“ nebo „000“). Vítězí okna s největší
    váhou. Oblast okna začíná s okrajem před oknem, na začátku okna a na jeho dvou nejsilnějších
    diagonálách: zarovnání bere nejdřívější z rovnocenných shod, takže opakovaná fráze před pravým
    místem by ho jinak přetáhla na sebe."""
    m = len(cit)
    kmeny = [_kmen(w) for w in cit]
    vyskyty: dict[str, list[int]] = {k: [] for k in kmeny}
    kmen_tvaru = {w: k for w in set(slova) if (k := _kmen(w)) in vyskyty}
    for j, w in enumerate(slova):
        k = kmen_tvaru.get(w)
        if k is not None:
            vyskyty[k].append(j)
    vaha: dict[int, float] = {}
    diagonaly: defaultdict[int, set[int]] = defaultdict(set)
    celkem = 0
    for n, i in sorted((len(vyskyty[k]), i) for i, k in enumerate(kmeny) if vyskyty[k]):
        if celkem and celkem + n > MAX_HLASU:
            break
        celkem += n
        vaha[i] = math.log(1 + len(slova) / n)
        for j in vyskyty[kmeny[i]]:
            diagonaly[j - i].add(i)
    # při skóre ≥ PRAH je úsek textu nejvýš o ~35 % kratší nebo delší než citace
    sirka = 2 * m // 5 + 3
    kose: defaultdict[int, set[int]] = defaultdict(set)
    for d, pozice in diagonaly.items():
        kose[d // sirka] |= pozice
    prazdny: set[int] = set()
    okna = {k: sum(vaha[i] for i in kose.get(k, prazdny) | kose.get(k + 1, prazdny))
            for k in set(kose) | {k - 1 for k in kose}}
    pocet = max(MIN_OKEN, SLOV_OKEN // (4 * sirka + m))
    oblasti: list[tuple[int, int]] = []
    obsazena: set[int] = set()
    for k in sorted(okna, key=lambda k: (-okna[k], k)):
        if obsazena.intersection((k - 1, k, k + 1)):
            continue
        obsazena.add(k)
        konec = min(len(slova), (k + 3) * sirka + m)
        v_okne = [d for d in range(k * sirka, (k + 2) * sirka) if d in diagonaly]
        nejsilnejsi = sorted(v_okne, key=lambda d: -sum(vaha[i] for i in diagonaly[d]))[:2]
        oblasti += [(max(0, a), konec) for a in ((k - 1) * sirka, k * sirka, *nejsilnejsi)]
        if len(obsazena) == pocet:
            break
    return oblasti


def _nejlepsi_usek(cit: list[str], slova: list[str], a: int, b: int) -> tuple[float, int, int] | None:
    """Nejpodobnější úsek slov textu v oblasti [a, b): (skóre, první slovo, za posledním slovem).

    Skóre 0–1 = podíl shodných znaků při zarovnání po slovech: shodná slova se počítají celá, nesouhlasné
    úseky do MAX_MEZERA slov po znacích (jiná koncovka), delší nesouhlasné úseky jako neshoda."""
    oblast = slova[a:b]
    bloky = [x for x in SequenceMatcher(None, cit, oblast, autojunk=False).get_matching_blocks() if x.size]
    if not bloky:
        return None
    # krajní bloky můžou být náhodné shody běžných slov v okolí: z prvních a posledních tří bloků
    # vyber řadu s nejlepším poměrem shodných znaků
    znaky_citace = sum(map(len, cit))
    znaky_oblasti = list(accumulate(map(len, oblast), initial=0))
    znaky_bloku = list(accumulate((sum(map(len, cit[x.a:x.a + x.size])) for x in bloky), initial=0))

    def pomer(k1: int, k2: int) -> float:
        shoda = znaky_bloku[k2 + 1] - znaky_bloku[k1]
        usek = znaky_oblasti[bloky[k2].b + bloky[k2].size] - znaky_oblasti[bloky[k1].b]
        return 2 * shoda / (znaky_citace + usek)

    n = len(bloky)
    k1, k2 = max(((k1, k2) for k1 in range(min(3, n)) for k2 in range(max(k1, n - 3), n)),
                 key=lambda k: pomer(*k))
    rada = bloky[k1:k2 + 1]
    shoda = znaky_bloku[k2 + 1] - znaky_bloku[k1]
    shoda += sum(_shoda_mezery(cit[x.a + x.size:y.a], oblast[x.b + x.size:y.b]) for x, y in zip(rada, rada[1:]))
    s, e = a + rada[0].b, a + rada[-1].b + rada[-1].size
    znaky_useku = znaky_oblasti[e - a] - znaky_oblasti[s - a]
    # nesouhlasný začátek a konec citace (např. jiná koncovka prvního slova): úsek zkusit rozšířit
    pred, za = cit[:rada[0].a], cit[rada[-1].a + rada[-1].size:]
    s2, e2 = max(0, s - len(pred)), min(len(slova), e + len(za))
    rozsireni_od = [(s, 0, 0), (s2, _shoda_mezery(pred, slova[s2:s]), sum(map(len, slova[s2:s])))]
    rozsireni_do = [(e, 0, 0), (e2, _shoda_mezery(za, slova[e:e2]), sum(map(len, slova[e:e2])))]
    return max(((round(2 * (shoda + sh1 + sh2) / (znaky_citace + znaky_useku + zn1 + zn2), 3), od, do)
                for od, sh1, zn1 in rozsireni_od for do, sh2, zn2 in rozsireni_do), key=lambda u: u[0])


def _shoda_mezery(x: list[str], y: list[str]) -> int:
    """Počet shodných znaků nesouhlasného úseku citace x a textu y; úsek delší než MAX_MEZERA slov = 0."""
    if not x or not y or max(len(x), len(y)) > MAX_MEZERA:
        return 0
    bloky = SequenceMatcher(None, "".join(x), "".join(y), autojunk=False).get_matching_blocks()
    return sum(blok.size for blok in bloky)
