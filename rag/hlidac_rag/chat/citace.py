"""Ověření citací a přisouzení v odpovědi modelu po odstavcích (spec 9.2), bez LLM a sítě.

Model píše doslovnou citaci v uvozovkách („…“, "…", “…”, »…«, «…») nebo jako blokovou citaci (řádky
začínající `>`) a hned za ní `[N]`. Server odpověď drží po odstavcích (Odstavce) a odstavec s citací
(ma_citat) pustí až po kontrole (over_odstavec) proti textu zdroje N z registru:

- `presne`, `priblizne`: text zůstane, citát nese offsety do textu zdroje a u projevu řečníka z registru;
- `nenalezeno` (výrok ve zdroji není) a `neovereno` (chybí [N], zdroj bez textu, výrok přisouzený jiné
  osobě): uvozovky zmizí, za text přibude ZNACKA_NEOVERENO a citát se nikomu nepřisoudí.

Přisouzení u projevu: jména osob z DB v textu před citátem (od konce předchozího citátu; když tam žádné
není, platí jména z nejbližšího dřívějšího úseku). Jiná osoba než řečník zdroje = `neovereno`, s výjimkou
„B cituje A“: v odstavci je jmenovaný i řečník zdroje a jméno té osoby stojí v textu projevu u nalezeného
místa; výrok se pak přisoudí A s uvedením, kdo ho cituje.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field

from ..citace_shoda import MIN_SLOV, najdi_citaci, zkontroluj_delku
from .registr import Registr, Zdroj

ZNACKA_NEOVERENO = "(neověřeno ve zdroji)"
TYPY_S_TEXTEM = ("cel", "projev")
STROP = 2_000          # delší citace se ověří jen začátkem a koncem (každý KUS znaků)
KUS = 1_000
OKNO_JMENA = 1_500     # jak daleko před nalezeným místem ve zdroji se hledá jméno citované osoby (B cituje A)

_KONEC_ODSTAVCE = re.compile(r"\n(?:[ \t]*\n)+")
_INLINE = re.compile(r"„([^„“”\n]+)[“”]|“([^“”\n]+)”|\"([^\"\n]+)\"|»([^»«\n]+)«|«([^«»\n]+)»")
_BLOK_RADEK = re.compile(r"^[ \t]*>[ \t]?(.*)$", re.M)
_ODKAZ_ZA = re.compile(r"[ \t]*[,.;:]?[ \t]*\[(\d+)\]")
_ODKAZ_NA_KONCI = re.compile(r"[ \t]*\[(\d+)\][ \t]*[.,;:]?[ \t]*$")
_ODKAZ_NA_ZACATKU = re.compile(r"[ \t]*\[(\d+)\]")
_OKRAJ_ZACATEK = re.compile(r"^[\s…\.]+")
_OKRAJ_KONEC = re.compile(r"[\s…\.,;:!?]+$")
_VYPUSTKA = re.compile(r"\s*(?:\[…\]|\(…\)|\[\.\.\.\]|\(\.\.\.\)|…|\.\.\.)\s*")
_SLOVO = re.compile(r"\w+")
_DATUM_TITULKU = re.compile(r",\s*\d{1,2}\.\s*\d{1,2}\.\s*\d{4}$")
_ZAVORKA = re.compile(r"\s*\([^()]*\)$")


class Odstavce:
    """Skládá delty streamu do odstavců (oddělených prázdným řádkem); rozepsaný drží."""

    def __init__(self):
        self._buf = ""

    def pridej(self, delta: str) -> list[str]:
        """Přidá deltu a vrátí dokončené odstavce (bez oddělovače)."""
        self._buf += delta
        casti = _KONEC_ODSTAVCE.split(self._buf)
        self._buf = casti.pop()
        return [c for c in casti if c]

    def zbytek(self) -> str:
        """Rozepsaný odstavec (na konci streamu se ověří jako poslední)."""
        return self._buf


def ma_citat(odstavec: str) -> bool:
    """Obsahuje odstavec text v uvozovkách nebo blokovou citaci?"""
    return bool(_INLINE.search(odstavec) or _BLOK_RADEK.search(odstavec))


@dataclass
class Overeni:
    text: str
    citaty: list[dict] = field(default_factory=list)


@dataclass
class _Citat:
    od: int            # úsek odstavce, který se při neověření nahradí
    do: int
    vnitrek: str       # text citace, jak ho model napsal
    n: int | None
    konec: int         # konec citace včetně [N] (odsud začíná úsek pro jména dalšího citátu)
    blok: bool


def over_odstavec(odstavec: str, registr: Registr, text_zdroje: Callable[[int], str | None],
                  jmena_osob: Callable[[str], list[str]]) -> Overeni:
    """Ověří citace odstavce proti zdrojům z registru; vrátí upravený text a citáty
    {n, shoda, od, do, recnik, neautorizovano} v pořadí v odstavci (od/do = offsety v textu zdroje)."""
    citaty = _najdi_citaty(odstavec)
    usek_jmen: dict[int, list[str]] = {}

    def jmena_pred(i: int) -> list[str]:
        """Jména před citátem i; když v jeho úseku žádné nejsou, jména z nejbližšího dřívějšího úseku."""
        for k in range(i, -1, -1):
            if k not in usek_jmen:
                od = citaty[k - 1].konec if k else 0
                usek_jmen[k] = list(jmena_osob(odstavec[od:citaty[k].od]))
            if usek_jmen[k]:
                return usek_jmen[k]
        return []

    vysledky: list[dict] = []
    for i, c in enumerate(citaty):
        vysledky.append(_over_citat(c, registr, text_zdroje, lambda i=i: jmena_pred(i)))

    casti, posledni = [], 0
    for c, v in zip(citaty, vysledky):
        if v["shoda"] in ("presne", "priblizne"):
            continue
        casti += [odstavec[posledni:c.od], _bez_uvozovek(c), " ", ZNACKA_NEOVERENO]
        posledni = c.do
    casti.append(odstavec[posledni:])
    return Overeni("".join(casti), vysledky)


# --- nalezení citátů v odstavci ----------------------------------------------------------------

def _najdi_citaty(odstavec: str) -> list[_Citat]:
    out = _bloky(odstavec)
    for m in _INLINE.finditer(odstavec):
        if any(b.od <= m.start() < b.do for b in out if b.blok):
            continue
        vnitrek = next(g for g in m.groups() if g is not None)
        odkaz = _ODKAZ_ZA.match(odstavec, m.end())
        n = int(odkaz.group(1)) if odkaz else None
        if n is None and len(_SLOVO.findall(vnitrek)) < MIN_SLOV:
            continue        # krátký pojem v uvozovkách bez odkazu není citace
        out.append(_Citat(m.start(), m.end(), vnitrek, n, odkaz.end() if odkaz else m.end(), False))
    return sorted(out, key=lambda c: c.od)


def _bloky(odstavec: str) -> list[_Citat]:
    """Bloková citace = souvislé řádky s `>`; [N] na konci posledního řádku nebo na začátku dalšího."""
    out: list[_Citat] = []
    radky = list(_BLOK_RADEK.finditer(odstavec))
    i = 0
    while i < len(radky):
        j = i
        while j + 1 < len(radky) and radky[j + 1].start() == radky[j].end() + 1:
            j += 1
        od, do = radky[i].start(), radky[j].end()
        texty = [r.group(1) for r in radky[i:j + 1]]
        n, konec = None, do
        m = _ODKAZ_NA_KONCI.search(texty[-1])
        if m:
            n = int(m.group(1))
            texty[-1] = texty[-1][:m.start()]
            do = radky[j].start(1) + m.start()
        elif odstavec[do:do + 1] == "\n" and (m := _ODKAZ_NA_ZACATKU.match(odstavec, do + 1)):
            n, konec = int(m.group(1)), m.end()
        out.append(_Citat(od, do, "\n".join(texty), n, konec, True))
        i = j + 1
    return out


def _bez_uvozovek(c: _Citat) -> str:
    return c.vnitrek.rstrip() if c.blok else c.vnitrek


# --- ověření jednoho citátu --------------------------------------------------------------------

def _over_citat(c: _Citat, registr: Registr, text_zdroje: Callable[[int], str | None],
                jmena: Callable[[], list[str]]) -> dict:
    vysledek = {"n": c.n, "shoda": "neovereno", "od": None, "do": None, "recnik": None, "neautorizovano": False}
    zdroj = registr.podle_n(c.n) if c.n is not None else None
    if zdroj is None:
        return vysledek
    if zdroj.typ == "projev":
        vysledek["neautorizovano"] = zdroj.meta.get("autorizovano") is False
    text = text_zdroje(c.n) if zdroj.typ in TYPY_S_TEXTEM else None
    if not text:
        return vysledek
    shoda, od, do = _najdi(c.vnitrek, text)
    if shoda == "nenalezeno":
        vysledek["shoda"] = "nenalezeno"
        return vysledek
    recnik = None
    if zdroj.typ == "projev":
        recnik = _prisouzeni(zdroj, jmena(), text, od, do)
        if recnik is None:
            return vysledek
    return {**vysledek, "shoda": shoda, "od": od, "do": do, "recnik": recnik}


def _najdi(citace: str, text: str) -> tuple[str, int | None, int | None]:
    """Najde citaci v textu zdroje: části mezi výpustkami po sobě, dlouhá část začátkem a koncem."""
    citace = _OKRAJ_KONEC.sub("", _OKRAJ_ZACATEK.sub("", unicodedata.normalize("NFC", citace)))
    casti = [p for p in _VYPUSTKA.split(citace) if p.strip()]
    if not casti:
        return "nenalezeno", None, None
    pozice, zacatek, vsechny_presne = 0, None, True
    for cast in casti:
        if len(cast) > STROP:
            hlava, pata = _hlava(cast), _pata(cast)
            r1 = _hledej(hlava, text, pozice)
            r2 = _hledej(pata, text, r1[2]) if r1[0] != "nenalezeno" else r1
            if r2[0] == "nenalezeno" or r2[2] - r1[1] > 2 * len(cast) + 200:
                return "nenalezeno", None, None
            shoda, od, do = ("presne" if r1[0] == r2[0] == "presne" else "priblizne"), r1[1], r2[2]
        else:
            shoda, od, do = _hledej(cast, text, pozice)
            if shoda == "nenalezeno":
                return "nenalezeno", None, None
        zacatek = od if zacatek is None else zacatek
        vsechny_presne = vsechny_presne and shoda == "presne"
        pozice = do
    return ("presne" if vsechny_presne else "priblizne"), zacatek, pozice


def _hlava(s: str) -> str:
    k = s.rfind(" ", 0, KUS)
    return s[:k if k > 0 else KUS]


def _pata(s: str) -> str:
    k = s.find(" ", len(s) - KUS)
    return s[k + 1 if k >= 0 else len(s) - KUS:]


def _hledej(cast: str, text: str, od: int) -> tuple[str, int | None, int | None]:
    """Hledá část citace v text[od:]; krátkou (méně než MIN_SLOV slov) jen doslova bez ohledu na velikost
    písmen a mezery, delší přes najdi_citaci. Offsety jsou do celého textu."""
    zbytek = text[od:]
    try:
        zkontroluj_delku(cast)
    except ValueError:
        slova = cast.split()
        m = re.search(r"(?<!\w)" + r"\s+".join(map(re.escape, slova)) + r"(?!\w)", zbytek, re.I) if slova else None
        return ("presne", od + m.start(), od + m.end()) if m else ("nenalezeno", None, None)
    r = najdi_citaci(zbytek, cast)
    if r["shoda"] == "nenalezeno":
        return "nenalezeno", None, None
    return r["shoda"], od + r["od_znaku"], od + r["do_znaku"]


# --- přisouzení ---------------------------------------------------------------------------------

def _recnik(zdroj: Zdroj) -> str:
    """Řečník projevu pro čip: „Jméno (klub)“ z meta, jinak z titulku bez data."""
    if zdroj.meta.get("recnik"):
        klub = zdroj.meta.get("klub")
        return f"{zdroj.meta['recnik']} ({klub})" if klub else str(zdroj.meta["recnik"])
    return _DATUM_TITULKU.sub("", zdroj.titulek).strip()


def _slova_jmena(jmeno: str) -> list[str]:
    return [_bez_diakritiky(w) for w in _SLOVO.findall(jmeno) if len(w) > 1]


def _bez_diakritiky(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if not unicodedata.combining(c))


def _je_osoba(jmeno: str, recnik: str) -> bool:
    """Jméno z textu patří řečníkovi: všechna jeho slova jsou ve jméně řečníka (bez klubu)."""
    slova = _slova_jmena(jmeno)
    return bool(slova) and set(slova) <= set(_slova_jmena(_ZAVORKA.sub("", recnik)))


def _kmen_prijmeni(jmeno: str) -> str:
    """Začátek příjmení pro hledání ve skloňovaném tvaru (Hrnčíř, Hrnčíře)."""
    slova = _slova_jmena(jmeno)
    w = slova[-1] if slova else ""
    return w if len(w) <= 4 else w[:max(4, len(w) - 2)]


def _prisouzeni(zdroj: Zdroj, jmena: list[str], text: str, od: int, do: int) -> str | None:
    """Komu výrok přisoudit; None = jiná osoba než řečník zdroje (citát je neověřený)."""
    recnik = _recnik(zdroj)
    cizi = [j for j in jmena if not _je_osoba(j, recnik)]
    if not cizi:
        return recnik
    if len(cizi) == len(jmena):
        return None         # řečník zdroje v odstavci jmenovaný není
    okoli = _bez_diakritiky(text[max(0, od - OKNO_JMENA):do + KUS // 2])
    for j in cizi:
        kmen = _kmen_prijmeni(j)
        if not kmen or not re.search(r"(?<!\w)" + re.escape(kmen), okoli):
            return None
    return f"{', '.join(cizi)} (cituje {'ho' if len(cizi) == 1 else 'je'} {recnik})"
