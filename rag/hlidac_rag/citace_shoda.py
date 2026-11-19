"""Porovnání citace s textem projevu nebo zákona: zazněl výrok opravdu a kde přesně (bez DB).

Dvě úrovně shody:
- `presne`: po normalizaci (mezery a zalomení řádků, typografické uvozovky a pomlčky, „…“ = „...“,
  velikost písmen) je citace podřetězcem textu, který nezačíná ani nekončí uprostřed slova;
- `priblizne`: porovnávají se jen slova bez diakritiky a interpunkce, drobné rozdíly (vynechané slovo,
  jiná koncovka) se tolerují; skóre aspoň PRAH. Jiný začátek slova se netoleruje a slovo s opačným
  významem (budeme/nebudeme, je/není, pro/proti) znamená `nenalezeno` i při skóre nad PRAH.

Offsety `od_znaku`/`do_znaku` jsou indexy do původního textu (konec exkluzivně), takže
`text[od_znaku:do_znaku] == nalezeny_text`.
"""
from __future__ import annotations

import math
import os
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
VAHA_DALSICH = 0.7     # po nalezené shodě se porovnají jen okna s aspoň takovou částí váhy nejsilnějšího

_SLOVO = re.compile(r"\w+")
_UVOZOVKY = re.compile("[\"'`´„“”‟«»‹›‚‘’‛″′]")
_POMLCKY = re.compile("[‐‑‒–—―−]")
# opačný význam, který nejde poznat podle předpony ne- (slova bez diakritiky)
_OPACNA = {frozenset(p) for p in (("je", "neni"), ("pro", "proti"), ("ano", "ne"))}
# místa, kde normalizace mění délku: víc bílých znaků za sebou, jiný bílý znak než mezera, výpustka
_ZMENA_DELKY = re.compile(r"\s{2,}|[^\S ]|…")


def najdi_citaci(text: str, citace: str) -> dict:
    """Najde citaci v textu projevu nebo zákona.

    Vrací {shoda: presne | priblizne | nenalezeno, skore: 0–1, od_znaku, do_znaku, nalezeny_text}.
    U více shod vrací první přesnou, jinak přibližnou s nejvyšším skóre (při shodě skóre první).
    `nenalezeno` nese nejbližšího kandidáta se skóre pod PRAH, nebo i nad PRAH, když se kandidát liší
    slovem s opačným významem (obrácený význam není citace); bez jediného společného slova skóre 0
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
    # pořadí kandidátů: nejdřív úseky, které projdou (skóre ≥ PRAH bez opačného významu), pak vyšší
    # skóre, pak dřívější úsek; klíč = (neprojde, -skóre, první slovo, za posledním slovem)
    nejlepsi = None
    oblasti = _oblasti(slova_citace, slova)
    for vaha, a, b in oblasti:
        if nejlepsi and not nejlepsi[0] and vaha < VAHA_DALSICH * oblasti[0][0]:
            break
        usek = _nejlepsi_usek(slova_citace, slova, a, b)
        if usek:
            skore, s, e, zapor = usek
            klic = (zapor or skore < PRAH, -skore, s, e)
            if nejlepsi is None or klic < nejlepsi:
                nejlepsi = klic
    if nejlepsi is None:
        return {"shoda": "nenalezeno", "skore": 0.0, "od_znaku": None, "do_znaku": None, "nalezeny_text": None}
    neprojde, skore, s, e = nejlepsi
    return _vysledek(text, "nenalezeno" if neprojde else "priblizne", -skore, *_hranice_slov(text, s, e))


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


def _oblasti(cit: list[str], slova: list[str]) -> list[tuple[float, int, int]]:
    """Předvýběr: oblasti slov textu (váha okna, a, b) pro slova [a, b), kde citace nejspíš leží, od nejsilnější.

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
    oblasti: list[tuple[float, int, int]] = []
    obsazena: set[int] = set()
    for k in sorted(okna, key=lambda k: (-okna[k], k)):
        if obsazena.intersection((k - 1, k, k + 1)):
            continue
        obsazena.add(k)
        konec = min(len(slova), (k + 3) * sirka + m)
        v_okne = [d for d in range(k * sirka, (k + 2) * sirka) if d in diagonaly]
        nejsilnejsi = sorted(v_okne, key=lambda d: -sum(vaha[i] for i in diagonaly[d]))[:2]
        zacatky = dict.fromkeys(max(0, a) for a in ((k - 1) * sirka, k * sirka, *nejsilnejsi))
        oblasti += [(okna[k], a, konec) for a in zacatky]
        if len(obsazena) == pocet:
            break
    return oblasti


def _nejlepsi_usek(cit: list[str], slova: list[str], a: int, b: int) -> tuple[float, int, int, bool] | None:
    """Nejpodobnější úsek slov textu v oblasti [a, b): (skóre, první slovo, za posledním slovem, zápor).

    Skóre 0–1 = podíl shodných znaků při zarovnání po slovech: shodná slova se počítají celá, nesouhlasná
    slova (mezery mezi bloky do MAX_MEZERA slov a nesouhlasný začátek a konec citace proti slovům textu
    na stejném místě) jen společným začátkem při jiné koncovce (_shoda_slov). `zápor` = některé
    spárované slovo má opačný význam (_zapor)."""
    oblast = slova[a:b]
    bloky = [x for x in SequenceMatcher(None, cit, oblast, autojunk=False).get_matching_blocks() if x.size]
    if not bloky:
        return None
    znaky_citace = sum(map(len, cit))
    znaky_oblasti = list(accumulate(map(len, oblast), initial=0))
    znaky_bloku = list(accumulate((sum(map(len, cit[x.a:x.a + x.size])) for x in bloky), initial=0))
    mezery = [_mezera(cit[x.a + x.size:y.a], oblast[x.b + x.size:y.b]) for x, y in zip(bloky, bloky[1:])]
    znaky_mezer = list(accumulate((m[0] for m in mezery), initial=0))

    def zacatek(k: int) -> tuple[int, int, int, bool]:
        """Úsek od bloku k včetně slov textu před ním proti nesouhlasnému začátku citace:
        (první slovo, shodné znaky, znaky textu před blokem, zápor)."""
        s = a + bloky[k].b
        od = max(0, s - bloky[k].a)
        shoda, zapor = _okraj(cit[:bloky[k].a][::-1], slova[od:s][::-1])
        return od, shoda, sum(map(len, slova[od:s])), zapor

    def konec(k: int) -> tuple[int, int, int, bool]:
        """Totéž pro konec úseku za blokem k a nesouhlasný konec citace."""
        e = a + bloky[k].b + bloky[k].size
        do = min(len(slova), e + len(cit) - bloky[k].a - bloky[k].size)
        shoda, zapor = _okraj(cit[bloky[k].a + bloky[k].size:], slova[e:do])
        return do, shoda, sum(map(len, slova[e:do])), zapor

    # krajní bloky můžou být náhodné shody běžných slov v okolí: z prvních a posledních tří bloků
    # vyber řadu s nejvyšším skóre
    n = len(bloky)
    zacatky = {k: zacatek(k) for k in range(min(3, n))}
    konce = {k: konec(k) for k in range(max(0, n - 3), n)}
    nejlepsi = None
    for k1, (od, sh1, zn1, zap1) in zacatky.items():
        for k2, (do, sh2, zn2, zap2) in konce.items():
            if k2 < k1:
                continue
            shoda = znaky_bloku[k2 + 1] - znaky_bloku[k1] + znaky_mezer[k2] - znaky_mezer[k1] + sh1 + sh2
            usek = znaky_oblasti[bloky[k2].b + bloky[k2].size] - znaky_oblasti[bloky[k1].b] + zn1 + zn2
            skore = round(2 * shoda / (znaky_citace + usek), 3)
            if nejlepsi is None or skore > nejlepsi[0]:
                zapor = zap1 or zap2 or any(m[1] for m in mezery[k1:k2])
                nejlepsi = (skore, od, do, zapor)
    return nejlepsi


def _mezera(x: list[str], y: list[str]) -> tuple[int, bool]:
    """Nesouhlasná slova mezi dvěma bloky: (shodné znaky slov spárovaných po pořadí, zápor).

    Úsek delší než MAX_MEZERA slov se nepočítá; zápor se v krátkém úseku hledá mezi všemi dvojicemi."""
    if max(len(x), len(y)) > MAX_MEZERA:
        return 0, any(map(_zapor, x, y))
    return sum(map(_shoda_slov, x, y)), any(_zapor(p, q) for p in x for q in y)


def _okraj(x: list[str], y: list[str]) -> tuple[int, bool]:
    """Nesouhlasný začátek nebo konec citace x proti slovům textu y na stejném místě (od bloku ven)."""
    return sum(map(_shoda_slov, x, y)), any(map(_zapor, x, y))


def _shoda_slov(x: str, y: str) -> int:
    """Shodné znaky dvou slov na stejném místě: stejné slovo celé, jiná koncovka (obě slova aspoň 4 znaky,
    společný začátek aspoň 3 znaky, liší se nejvýš 3 poslední znaky) společný začátek, jinak 0
    (jiný začátek slova není koncovka: „proto“ není „to“, „nebudeme“ není „budeme“, „proti“ není „pro“)."""
    if x == y:
        return len(x)
    p = len(os.path.commonprefix((x, y)))
    return p if min(len(x), len(y)) >= 4 and p >= 3 and max(len(x), len(y)) - p <= 3 else 0


def _zapor(x: str, y: str) -> bool:
    """Slova s opačným významem: zápor předponou ne- (budeme/nebudeme, přijatelné/nepřijatelné)
    a nepravidelné dvojice z _OPACNA (je/není, pro/proti, ano/ne)."""
    return ((x == "ne" + y and len(y) >= 2) or (y == "ne" + x and len(x) >= 2)
            or frozenset((x, y)) in _OPACNA)
