"""Porovnání citace s textem projevu nebo zákona: zazněl výrok opravdu a kde přesně (bez DB).

Dvě úrovně shody:
- `presne`: po normalizaci (mezery a zalomení řádků, typografické uvozovky a pomlčky, „…“ = „...“,
  velikost písmen) je citace podřetězcem textu, který nezačíná ani nekončí uprostřed slova;
- `priblizne`: porovnávají se jen slova bez diakritiky a interpunkce, drobné rozdíly (vynechané slovo,
  jiná koncovka) se tolerují; skóre aspoň PRAH. Jiný začátek slova se netoleruje. Rozpor znamená
  `nenalezeno` i při skóre nad PRAH: slovo s opačným významem (budeme/nebudeme, je/není, pro/proti,
  i o pár slov posunuté), vynechané nebo přidané „není“, „ne“, „nebude“…, a jiné, chybějící nebo
  přebývající číslo (10 × 4 roky, § 44a × § 44b).

Citace se před porovnáním převede do NFC; měkký spojovník a znaky nulové šířky se v textu i citaci
přeskočí (offsety dál ukazují do původního textu).

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
MAX_MEZERA = 4         # nesouhlasný úsek do tolika slov se porovná po slovech; protějšek se hledá ±tolik slov
VAHA_DALSICH = 0.7     # po kandidátovi se skóre ≥ PRAH jen okna s aspoň takovou částí váhy nejsilnějšího

# všechny uvozovky a apostrofy, které normalizace sjednotí na " (nástroj over_citaci je ořezává z okrajů)
UVOZOVKY = "\"'`´„“”‟«»‹›‚‘’‛″′"

# měkký spojovník a znaky nulové šířky: v textu se přeskočí (slovo jimi rozdělené zůstane jedním slovem)
_NEVIDITELNE = "\xad\u200b\u200c\u200d\ufeff"
_SLOVO = re.compile(rf"\w+(?:[{_NEVIDITELNE}]+\w+)*")
_SLOVA_A_MEZERY = re.compile(rf"(\w+(?:[{_NEVIDITELNE}]+\w+)*)")      # split: [mezera, slovo, mezera, …]
_BEZ_NEVIDITELNYCH = str.maketrans("", "", _NEVIDITELNE)
_UVOZOVKY = re.compile(f"[{re.escape(UVOZOVKY)}]")
_POMLCKY = re.compile("[‐‑‒–—―−]")
# místa, kde normalizace mění délku: víc bílých nebo neviditelných znaků za sebou, jiný bílý znak než
# mezera, neviditelný znak, výpustka
_ZMENA_DELKY = re.compile(rf"[\s{_NEVIDITELNE}]{{2,}}|[^\S ]|[{_NEVIDITELNE}]|…")
# opačný význam, který nejde poznat podle předpony ne- (slova bez diakritiky), oběma směry
_OPACNE = {"je": "neni", "neni": "je", "pro": "proti", "proti": "pro", "ano": "ne", "ne": "ano"}
_CISLICE = re.compile(r"\d")
# samostatná záporná slova: chybí-li v citaci (nebo v textu) bez protějšku, význam se obrací
_ZAPORNA = {"ne", "neni", "nejsou", "nebyl", "nebyla", "nebylo", "nebyli", "nebyly", "nebudu", "nebudes",
            "nebude", "nebudeme", "nebudete", "nebudou"}


def najdi_citaci(text: str, citace: str) -> dict:
    """Najde citaci v textu projevu nebo zákona.

    Vrací {shoda: presne | priblizne | nenalezeno, skore: 0–1, od_znaku, do_znaku, nalezeny_text}.
    U více shod vrací první přesnou, jinak přibližnou s nejvyšším skóre (při shodě skóre první).
    `nenalezeno` nese nejbližšího kandidáta se skóre pod PRAH, nebo i nad PRAH, když je v kandidátovi
    rozpor (opačný význam nebo jiné číslo, viz _rozporne_slovo, _rozpor); bez jediného společného slova
    skóre 0 a offsety None. Citace kratší než MIN_SLOV slov = ValueError (krátké citace jsou nejednoznačné)."""
    citace = unicodedata.normalize("NFC", citace)
    zkontroluj_delku(citace)
    slova_citace, _ = _slova(citace)

    norm = _Normalizovany(text)
    hledana = _Normalizovany(citace).text.strip()
    i = norm.text.find(hledana)
    while i >= 0 and not _na_hranici_slov(norm.text, i, i + len(hledana)):
        i = norm.text.find(hledana, i + 1)
    if i >= 0:
        return _vysledek(text, "presne", 1.0, norm.puvodni_od(i), norm.puvodni_do(i + len(hledana)))

    slova, casti = _slova(text)
    # pořadí kandidátů: nejdřív úseky, které projdou (skóre ≥ PRAH bez rozporu), pak vyšší skóre,
    # pak dřívější úsek; klíč = (neprojde, -skóre, první slovo, za posledním slovem)
    nejlepsi = None
    oblasti = _oblasti(slova_citace, slova)
    for vaha, a, b in oblasti:
        if nejlepsi and -nejlepsi[1] >= PRAH and vaha < VAHA_DALSICH * oblasti[0][0]:
            break
        usek = _nejlepsi_usek(slova_citace, slova, a, b)
        if usek:
            skore, s, e, rozpor = usek
            klic = (rozpor or skore < PRAH, -skore, s, e)
            if nejlepsi is None or klic < nejlepsi:
                nejlepsi = klic
    if nejlepsi is None:
        return {"shoda": "nenalezeno", "skore": 0.0, "od_znaku": None, "do_znaku": None, "nalezeny_text": None}
    neprojde, skore, s, e = nejlepsi
    return _vysledek(text, "nenalezeno" if neprojde else "priblizne", -skore, *_hranice_slov(casti, s, e))


def zkontroluj_delku(citace: str) -> None:
    """Citace kratší než MIN_SLOV slov = ValueError (krátké citace jsou nejednoznačné)."""
    if len(_SLOVO.findall(unicodedata.normalize("NFC", citace))) < MIN_SLOV:
        raise ValueError(f"Citace musí mít aspoň {MIN_SLOV} slova, kratší je nejednoznačná.")


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


def _nahrada(usek: str) -> str:
    """Náhrada místa, kde normalizace mění délku: výpustka = „...“, bílé znaky = mezera, neviditelné znaky
    zmizí (i měkký spojovník na konci řádku, za ním slovo pokračuje)."""
    if usek == "…":
        return "..."
    return " " if any(c.isspace() for c in usek) and not usek.startswith("\xad") else ""


class _Normalizovany:
    """Text normalizovaný pro přesnou shodu a převod pozic v něm zpět do původního textu."""

    def __init__(self, text: str):
        s = _POMLCKY.sub("-", _UVOZOVKY.sub('"', _mala(text)))
        casti: list[str] = []
        # změny délky: (pozice náhrady v normalizovaném textu, začátek a konec v původním, délka náhrady)
        self._zmeny: list[tuple[int, int, int, int]] = []
        posledni = posun = 0
        for m in _ZMENA_DELKY.finditer(s):
            nahrada = _nahrada(m.group())
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


def _slova(s: str) -> tuple[list[str], list[str]]:
    """Slova textu malými písmeny bez diakritiky a neviditelných znaků (interpunkce a mezery odpadnou)
    a části textu z téhož průchodu [mezera, slovo, mezera, …, slovo, mezera] pro pozice (_hranice_slov)."""
    casti = _SLOVA_A_MEZERY.split(s)
    puvodni = casti[1::2]
    tvary = {w: _bez_diakritiky(w.translate(_BEZ_NEVIDITELNYCH)) for w in set(puvodni)}
    return [tvary[w] for w in puvodni], casti


def _hranice_slov(casti: list[str], s: int, e: int) -> tuple[int, int]:
    """Začátek slova s a konec slova e-1 v původním textu: součet délek částí před nimi (slovo k = casti[2k + 1])."""
    od = sum(map(len, casti[:2 * s + 1]))
    return od, od + sum(map(len, casti[2 * s + 1:2 * e]))


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
    """Nejpodobnější úsek slov textu v oblasti [a, b): (skóre, první slovo, za posledním slovem, rozpor).

    Skóre 0–1 = podíl shodných znaků při zarovnání po slovech: shodná slova se počítají celá, nesouhlasná
    slova (mezery mezi bloky do MAX_MEZERA slov, nesouhlasný začátek a konec citace) jen společným
    začátkem při jiné koncovce (_shoda_slov). `rozpor` = zápor nebo jiné číslo v zarovnaném úseku."""
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
        """Nesouhlasný začátek citace před blokem k proti slovům textu před ním (od bloku doleva):
        (první slovo úseku, shodné znaky, znaky textu před blokem, rozpor)."""
        s = a + bloky[k].b
        okno = slova[max(0, s - bloky[k].a - MAX_MEZERA):s][::-1]
        shoda, delka, rozpor = _okraj(cit[:bloky[k].a][::-1], okno)
        return s - delka, shoda, sum(map(len, slova[s - delka:s])), rozpor

    def konec(k: int) -> tuple[int, int, int, bool]:
        """Totéž pro nesouhlasný konec citace za blokem k (od bloku doprava)."""
        e = a + bloky[k].b + bloky[k].size
        zbytek = cit[bloky[k].a + bloky[k].size:]
        shoda, delka, rozpor = _okraj(zbytek, slova[e:e + len(zbytek) + MAX_MEZERA])
        return e + delka, shoda, sum(map(len, slova[e:e + delka])), rozpor

    # krajní bloky můžou být náhodné shody běžných slov v okolí: řada bloků začne jedním z prvních tří
    # a skončí jedním z posledních tří, vyhraje kombinace s nejvyšším skóre
    n = len(bloky)
    zacatky = {k: zacatek(k) for k in range(min(3, n))}
    konce = {k: konec(k) for k in range(max(0, n - 3), n)}
    nejlepsi = None
    for k1, (od, shoda_pred, znaky_pred, rozpor_pred) in zacatky.items():
        for k2, (do, shoda_za, znaky_za, rozpor_za) in konce.items():
            if k2 < k1:
                continue
            shoda_uvnitr = znaky_bloku[k2 + 1] - znaky_bloku[k1] + znaky_mezer[k2] - znaky_mezer[k1]
            znaky_uvnitr = znaky_oblasti[bloky[k2].b + bloky[k2].size] - znaky_oblasti[bloky[k1].b]
            shoda = shoda_uvnitr + shoda_pred + shoda_za
            znaky_useku = znaky_uvnitr + znaky_pred + znaky_za
            skore = round(2 * shoda / (znaky_citace + znaky_useku), 3)
            if nejlepsi is None or skore > nejlepsi[0]:
                rozpor = rozpor_pred or rozpor_za or any(m[1] for m in mezery[k1:k2])
                nejlepsi = (skore, od, do, rozpor)
    return nejlepsi


def _mezera(x: list[str], y: list[str]) -> tuple[int, bool]:
    """Nesouhlasná slova mezi dvěma bloky: citace x, text y (shodná slova v mezeře nejsou, ta jsou v blocích).

    Vrací (shodné znaky slov spárovaných po pořadí, rozpor). Delší úsek než MAX_MEZERA slov se nepočítá.
    Rozpor: rozporné slovo na kterékoli straně (číslo nebo samostatný zápor bez protějšku), nebo slovo
    s opačným významem do MAX_MEZERA slov od místa, kam by podle pořadí patřilo."""
    shoda = sum(map(_shoda_slov, x, y)) if max(len(x), len(y)) <= MAX_MEZERA else 0
    if any(map(_rozporne_slovo, x)) or any(map(_rozporne_slovo, y)):
        return shoda, True
    # nejdřív levně: má některé slovo citace v mezeře vůbec protějšek s opačným významem?
    v_textu = set(y)
    podezrela = [i for i, w in enumerate(x) if v_textu.intersection(_opacna_slova(w))]
    posun = len(y) - len(x)
    for i in podezrela:
        od, do = min(i, i + posun) - MAX_MEZERA, max(i, i + posun) + MAX_MEZERA + 1
        if any(_zapor(x[i], v) for v in y[max(0, od):do]):
            return shoda, True
    return shoda, False


def _okraj(x: list[str], y: list[str]) -> tuple[int, int, bool]:
    """Nesouhlasný začátek nebo konec citace x proti slovům textu y, obojí od bloku ven.

    Každé slovo citace hledá protějšek (_protejsek) mezi MAX_MEZERA + 1 nejbližšími volnými slovy textu;
    přeskočená slova textu patří k úseku. Bez protějšku nahradí slovo textu na svém místě, ledaže to slovo
    je protějškem některého z dalších slov citace (pak je slovo citace navíc). Úsek tak končí na slově,
    které citaci odpovídá, ne po pevném počtu slov. Po MAX_MEZERA slovech citace bez protějšku za sebou
    je zarovnání ztracené (kandidát s citací nesouvisí) a zbytek se páruje jen po pořadí.
    Vrací (shodné znaky, kolik slov y patří k úseku, rozpor)."""
    j = shoda = bez_protejsku = 0
    rozpor = False
    for i, w in enumerate(x):
        if bez_protejsku >= MAX_MEZERA:
            if j < len(y):
                shoda += _shoda_slov(w, y[j])
                rozpor = rozpor or _rozpor(w, y[j]) or _rozporne_slovo(y[j])
                j += 1
            rozpor = rozpor or _rozporne_slovo(w)
            continue
        k = next((k for k in range(j, min(len(y), j + MAX_MEZERA + 1)) if _protejsek(w, y[k])), None)
        bez_protejsku = 0 if k is not None else bez_protejsku + 1
        if k is not None:
            rozpor = rozpor or any(map(_rozporne_slovo, y[j:k])) or _rozpor(w, y[k])
            shoda += _shoda_slov(w, y[k])
            j = k + 1
        elif j < len(y) and not any(_protejsek(v, y[j]) for v in x[i + 1:i + 1 + MAX_MEZERA]):
            rozpor = rozpor or _rozporne_slovo(w) or _rozporne_slovo(y[j])
            j += 1
        else:
            rozpor = rozpor or _rozporne_slovo(w)
    return shoda, j, rozpor


def _shoda_slov(x: str, y: str) -> int:
    """Shodné znaky dvou slov na stejném místě: stejné slovo celé, jiná koncovka (obě slova aspoň 4 znaky,
    společný začátek aspoň 3 znaky, liší se nejvýš 3 poslední znaky) společný začátek, jinak 0
    (jiný začátek slova není koncovka: „proto“ není „to“, „nebudeme“ není „budeme“, „proti“ není „pro“)."""
    if x == y:
        return len(x)
    if len(x) < 4 or len(y) < 4 or x[:3] != y[:3]:
        return 0
    p = len(os.path.commonprefix((x, y)))
    return p if max(len(x), len(y)) - p <= 3 else 0


def _protejsek(x: str, y: str) -> bool:
    """Slovo textu y odpovídá slovu citace x: stejné nebo jiná koncovka, opačný význam, nebo obě čísla."""
    return (x == y or (x[:3] == y[:3] and _shoda_slov(x, y) > 0) or _zapor(x, y)
            or (_ma_cislici(x) and _ma_cislici(y)))


def _rozpor(x: str, y: str) -> bool:
    """Spárovaná slova citace a textu obracejí význam: zápor, nebo jiné číslo."""
    return _zapor(x, y) or (x != y and (_ma_cislici(x) or _ma_cislici(y)))


def _rozporne_slovo(w: str) -> bool:
    """Slovo bez protějšku, které mění význam: číslo (chybí nebo přebývá) nebo samostatný zápor."""
    return w in _ZAPORNA or _ma_cislici(w)


def _ma_cislici(w: str) -> bool:
    return _CISLICE.search(w) is not None


def _zapor(x: str, y: str) -> bool:
    """Slova s opačným významem: zápor předponou ne- (budeme/nebudeme, přijatelné/nepřijatelné)
    a nepravidelné dvojice z _OPACNE (je/není, pro/proti, ano/ne)."""
    if len(x) == len(y) + 2 and len(y) >= 2 and x.startswith("ne") and x.endswith(y):
        return True
    if len(y) == len(x) + 2 and len(x) >= 2 and y.startswith("ne") and y.endswith(x):
        return True
    return _OPACNE.get(x) == y


def _opacna_slova(w: str) -> tuple[str, ...]:
    """Slova, která mají s w opačný význam (_zapor), pro rychlé hledání v množině."""
    return ("ne" + w, w[2:] if w.startswith("ne") else "", _OPACNE.get(w, ""))
