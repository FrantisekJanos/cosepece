"""Ověření citací a přisouzení v odpovědi modelu po odstavcích (spec 9.2), bez LLM a sítě.

Model píše doslovnou citaci v uvozovkách (kterékoli z citace_shoda.UVOZOVKY kromě ` ´ ″ ′, i smíšené
a přes jedno zalomení řádku) nebo jako blokovou citaci (řádky začínající `>`) a hned za ní `[N]`. Server
odpověď drží po odstavcích (Odstavce) a odstavec s citací (ma_citat) pustí až po kontrole (over_odstavec)
proti textu zdroje N z registru:

- `presne`, `priblizne`: text zůstane, citát nese offsety do textu zdroje a u projevu řečníka z registru;
- `nenalezeno` (výrok ve zdroji není) a `neovereno` (chybí [N], zdroj bez textu, výrok přisouzený jiné
  osobě, nespárovaná uvozovka před [N]): uvozovky zmizí, přibude ZNACKA_NEOVERENO a citát se nikomu
  nepřisoudí. Krátký pojem v uvozovkách bez [N] zůstane, jen když u něj ve větě není jméno osoby ani
  uvozovací sloveso („Babiš řekl: „zloději“.“ je citát).

Přisouzení u projevu: jména osob z DB v úseku před citátem (od konce předchozího citátu) a za ním do konce
věty („„…“ [1], řekl Petr Malý.“); když tam žádné není, platí jména z nejbližšího dřívějšího úseku.
Jiná osoba než řečník zdroje = `neovereno`, s jedinou výjimkou „B cituje A“: v odstavci je jmenovaný řečník
zdroje B a jediná další osoba A, nalezené místo stojí v projevu B uvnitř párových typografických uvozovek
(„…“, »…«, «…», ‚…‘, ‹…›; rovné " a ' ne), nejbližší jméno osoby před otvírací uvozovkou je A a mezi ním
a uvozovkou není zápor ani jiná osoba. Chybné přisouzení je horší než neověřená citace.
"""
from __future__ import annotations

import re
import string
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field

from ..citace_shoda import _ZAPORNA, MIN_SLOV, UVOZOVKY, najdi_citaci, zkontroluj_delku
from .registr import Registr, Zdroj

ZNACKA_NEOVERENO = "(neověřeno ve zdroji)"
TYPY_S_TEXTEM = ("cel", "projev")
STROP = 2_000           # delší část citace se ověří po kusech
KUS = 1_000             # délka kusu dlouhé části
MEZERA_KUSU = 100       # nejvýš tolik znaků zdroje mezi sousedními kusy dlouhé části
MEZERA_VYPUSTKY = 300   # nejvýš tolik znaků zdroje přeskočí výpustka mezi částmi citace
OKNO_UVOZOVEK = 300     # jak daleko od nalezeného místa se ve zdroji hledají uvozovky, které ho obklopují
OKNO_JMENA = 300        # jak daleko před otvírací uvozovkou ve zdroji se hledá jméno citované osoby

# ` ´ jsou značky markdownu a akcent, ″ ′ palce a minuty
_ZNAKY_UVOZOVEK = "".join(c for c in UVOZOVKY if c not in "`´″′")
_JEN_OTVIRACI = "„‟"
_APOSTROFY = "'’‛"
# typografické páry pro výjimku B cituje A: otvírací → přípustné zavírací
_TYPOGRAFICKE = {"„": "“”", "‟": "“”", "»": "«", "«": "»", "‚": "‘’", "‹": "›", "›": "‹"}
_TYPOGRAFICKE_ZNAKY = "„‟“”»«‚‘’‹›"

_KONEC_ODSTAVCE = re.compile(r"\n(?:[ \t]*\n)+")
_BLOK_RADEK = re.compile(r"^[ \t]*>[ \t]?(.*)$", re.M)
_ODKAZ = re.compile(r"\[(\d+)\]")
_ODKAZ_ZA = re.compile(r"[ \t]*[,.;:]?[ \t]*\[(\d+)\]")
_ODKAZ_NA_KONCI = re.compile(r"[ \t]*\[(\d+)\][ \t]*[.,;:]?[ \t]*$")
_ODKAZ_NA_ZACATKU = re.compile(r"[ \t]*\[(\d+)\]")
_KONEC_VETY = re.compile(r"[.!?](?=\s|$)")
_OKRAJ_ZACATEK = string.whitespace + "…."
_OKRAJ_KONEC = string.whitespace + "….,;:!?"
_VYPUSTKA = re.compile(r"\[…\]|\(…\)|\[\.\.\.\]|\(\.\.\.\)|…|\.\.\.")
_SLOVO = re.compile(r"\w+")
_DATUM_TITULKU = re.compile(r",\s*\d{1,2}\.\s*\d{1,2}\.\s*\d{4}$")
_ZAVORKA = re.compile(r"\s*\([^()]*\)$")
_UVOZOVACI = re.compile(r"(?<!\w)(rekl|rekla|rekli|rika|rikal|rikala|uvedl|uvedla|uvedli|citoval|citovala|cituji"
                        r"|cituje|napsal|napsala|prohlasil|prohlasila|tvrdil|tvrdila|dodal|dodala|nazval|nazvala"
                        r"|oznacil|oznacila)(?!\w)")
# zápor: samostatná záporná slova a sloveso s předponou ne- (kromě běžných slov, která záporem nejsou)
_ZAPOR_SLOVA = _ZAPORNA | {"nikdy", "nic", "nikdo", "nikoho", "nicim", "zadny", "zadna", "zadne", "zadneho"}
_NE_NEZAPOR = ("nej", "nekd", "neko", "neco", "nebo", "nez", "nedel", "nemoc", "nemec", "nerv")


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
    """Obsahuje odstavec uvozovku (ne apostrof uvnitř slova) nebo blokovou citaci?"""
    return (any(ch in _ZNAKY_UVOZOVEK and not _apostrof(odstavec, i) for i, ch in enumerate(odstavec))
            or bool(_BLOK_RADEK.search(odstavec)))


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
    konec: int         # konec citace včetně [N]
    blok: bool


def over_odstavec(odstavec: str, registr: Registr, text_zdroje: Callable[[int], str | None],
                  jmena_osob: Callable[[str], list[str]]) -> Overeni:
    """Ověří citace odstavce proti zdrojům z registru; vrátí upravený text a citáty
    {n, shoda, od, do, recnik, neautorizovano} v pořadí v odstavci (od/do = offsety v textu zdroje)."""
    bloky = _bloky(odstavec)
    pary, nesparovane = _uvozovky(odstavec, [(b.od, b.do) for b in bloky])
    citaty = sorted(bloky + _inline(odstavec, pary, jmena_osob), key=lambda c: c.od)
    useky = _useky_jmen(odstavec, citaty)
    jmena_useku: dict[int, list[str]] = {}

    def jmena_k(i: int) -> list[str]:
        """Jména u citátu i (před ním a za ním do konce věty); bez nich jména nejbližšího dřívějšího úseku."""
        for k in range(i, -1, -1):
            if k not in jmena_useku:
                jmena_useku[k] = [j for u in useky[k] if u for j in jmena_osob(u)]
            if jmena_useku[k]:
                return jmena_useku[k]
        return []

    vysledky = [(c.od, _over_citat(c, registr, text_zdroje, lambda i=i: jmena_k(i), jmena_osob))
                for i, c in enumerate(citaty)]

    # úpravy textu: (začátek, konec, náhrada)
    upravy: list[tuple[int, int, str]] = []
    znackovane_odkazy: set[int] = set()
    for c, (_, v) in zip(citaty, vysledky):
        if v["shoda"] not in ("presne", "priblizne"):
            upravy.append((c.od, c.do, f"{_bez_uvozovek(c)} {ZNACKA_NEOVERENO}"))
            znackovane_odkazy.add(c.konec)
    # nespárovaná uvozovka před [N]: zmizí, před [N] přibude značka a neověřený citát
    for p in nesparovane:
        odkaz = _ODKAZ.search(odstavec, p + 1)
        if odkaz is None:
            continue
        upravy.append((p, p + 1, ""))
        if odkaz.end() not in znackovane_odkazy:
            znackovane_odkazy.add(odkaz.end())
            upravy.append((odkaz.start(), odkaz.start(), f"{ZNACKA_NEOVERENO} "))
            vysledky.append((p, {"n": int(odkaz.group(1)), "shoda": "neovereno", "od": None, "do": None,
                                 "recnik": None, "neautorizovano": False}))
    casti, posledni = [], 0
    for od, do, nahrada in sorted(upravy):
        casti += [odstavec[posledni:od], nahrada]
        posledni = do
    casti.append(odstavec[posledni:])
    return Overeni("".join(casti), [v for _, v in sorted(vysledky, key=lambda x: x[0])])


# --- nalezení citátů v odstavci ----------------------------------------------------------------

def _apostrof(s: str, i: int) -> bool:
    """Apostrof uvnitř slova (O'Neill, don’t) není uvozovka."""
    return s[i] in _APOSTROFY and 0 < i < len(s) - 1 and s[i - 1].isalnum() and s[i + 1].isalnum()


def _uvozovky(s: str, vynechat: list[tuple[int, int]]) -> tuple[list[tuple[int, int]], list[int]]:
    """Páry uvozovek (otvírací, zavírací) a pozice nespárovaných, mimo blokové citace.

    Zavírací je nejbližší další uvozovka, která není jen otvírací („ ‟); vnořené ‚…‘ se přeskočí.
    Jen otvírací uvozovka uvnitř = předchozí uvozovka zůstala nespárovaná. Lineárně: když otvírací
    nenajde zavírací do konce odstavce, nespárované jsou i všechny další."""
    pozice = [i for i, ch in enumerate(s) if ch in _ZNAKY_UVOZOVEK and not _apostrof(s, i)
              and not any(od <= i < do for od, do in vynechat)]
    pary: list[tuple[int, int]] = []
    nesparovane: list[int] = []
    k = 0
    while k < len(pozice):
        vnoreno, j, zaviraci = 0, k + 1, None
        while j < len(pozice):
            ch = s[pozice[j]]
            if ch in _JEN_OTVIRACI:
                break
            if ch == "‚":
                vnoreno += 1
            elif vnoreno and ch in "‘’":
                vnoreno -= 1
            else:
                zaviraci = j
                break
            j += 1
        if zaviraci is not None:
            pary.append((pozice[k], pozice[zaviraci]))
            k = zaviraci + 1
        elif j >= len(pozice):
            nesparovane += pozice[k:]
            break
        else:
            nesparovane.append(pozice[k])
            k += 1
    return pary, nesparovane


def _zacatek_vety(s: str, p: int, od: int) -> int:
    """Začátek věty, ve které je pozice p (ne dřív než od)."""
    konce = [m.end() for m in _KONEC_VETY.finditer(s, od, p)]
    return konce[-1] if konce else od


def _konec_vety(s: str, p: int) -> int:
    m = _KONEC_VETY.search(s, p)
    return m.end() if m else len(s)


def _inline(s: str, pary: list[tuple[int, int]], jmena_osob: Callable[[str], list[str]]) -> list[_Citat]:
    out: list[_Citat] = []
    predchozi = 0
    for o, z in pary:
        vnitrek = s[o + 1:z]
        if not vnitrek.strip():
            continue
        odkaz = _ODKAZ_ZA.match(s, z + 1)
        n = int(odkaz.group(1)) if odkaz else None
        konec = odkaz.end() if odkaz else z + 1
        if n is None and len(_SLOVO.findall(vnitrek)) < MIN_SLOV:
            # krátký pojem v uvozovkách bez odkazu je citát, jen když ho věta přisuzuje osobě
            okoli = [s[_zacatek_vety(s, o, predchozi):o], s[z + 1:_konec_vety(s, z + 1)]]
            if not any(_UVOZOVACI.search(_bez_diakritiky(u)) or jmena_osob(u) for u in okoli if u.strip()):
                continue
        out.append(_Citat(o, z + 1, vnitrek, n, konec, False))
        predchozi = konec
    return out


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
            konec = radky[j].start(1) + m.end()
        elif odstavec[do:do + 1] == "\n" and (m := _ODKAZ_NA_ZACATKU.match(odstavec, do + 1)):
            n, konec = int(m.group(1)), m.end()
        out.append(_Citat(od, do, "\n".join(texty), n, konec, True))
        i = j + 1
    return out


def _useky_jmen(s: str, citaty: list[_Citat]) -> list[tuple[str, str]]:
    """Úseky odstavce, ve kterých se hledají jména k citátu: před ním (od konce předchozího úseku)
    a za ním do konce věty, pokud věta skončí dřív, než začne další citát."""
    out: list[tuple[str, str]] = []
    zacatek = 0
    for i, c in enumerate(citaty):
        pred = s[zacatek:c.od]
        konec = _konec_vety(s, c.konec)
        dalsi = citaty[i + 1].od if i + 1 < len(citaty) else len(s)
        if konec <= dalsi:
            out.append((pred, s[c.konec:konec]))
            zacatek = konec
        else:
            out.append((pred, ""))
            zacatek = c.konec
    return out


def _bez_uvozovek(c: _Citat) -> str:
    return c.vnitrek.rstrip() if c.blok else c.vnitrek


# --- ověření jednoho citátu --------------------------------------------------------------------

def _over_citat(c: _Citat, registr: Registr, text_zdroje: Callable[[int], str | None],
                jmena: Callable[[], list[str]], jmena_osob: Callable[[str], list[str]]) -> dict:
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
        recnik = _prisouzeni(zdroj, jmena(), text, od, do, jmena_osob)
        if recnik is None:
            return vysledek
    return {**vysledek, "shoda": shoda, "od": od, "do": do, "recnik": recnik}


_NENALEZENO = ("nenalezeno", None, None)


def _najdi(citace: str, text: str) -> tuple[str, int | None, int | None]:
    """Najde citaci v textu zdroje. Části mezi výpustkami po sobě, nejvýš MEZERA_VYPUSTKY znaků od sebe,
    bez záporu v přeskočeném textu a (je-li jich víc) aspoň dvouslovné; část nad STROP po kusech KUS znaků
    těsně za sebou. `presne` jen když jsou přesné všechny části a kusy."""
    citace = unicodedata.normalize("NFC", citace).lstrip(_OKRAJ_ZACATEK).rstrip(_OKRAJ_KONEC)
    casti = [p.strip() for p in _VYPUSTKA.split(citace)]
    casti = [p for p in casti if p]
    if not casti or (len(casti) > 1 and any(len(_SLOVO.findall(p)) < 2 for p in casti)):
        return _NENALEZENO
    kusy = [(k, i == 0 or j > 0) for i, p in enumerate(casti) for j, k in enumerate(_kusy(p))]
    zacatek = konec = None
    vsechny_presne = True
    for kus, za_vypustkou_ne in kusy:
        if konec is None:
            shoda, od, do = _hledej(kus, text, 0, len(text))
        else:
            mezera = MEZERA_KUSU if za_vypustkou_ne else MEZERA_VYPUSTKY
            shoda, od, do = _hledej(kus, text, konec, min(len(text), konec + mezera + 2 * len(kus) + 50))
            if shoda != "nenalezeno" and (od - konec > mezera or _zapor(text[konec:od])):
                return _NENALEZENO
        if shoda == "nenalezeno":
            return _NENALEZENO
        zacatek = od if zacatek is None else zacatek
        konec = do
        vsechny_presne = vsechny_presne and shoda == "presne"
    return ("presne" if vsechny_presne else "priblizne"), zacatek, konec


def _kusy(cast: str) -> list[str]:
    """Část do STROP celá, delší po kusech nejvýš KUS znaků na hranicích slov."""
    if len(cast) <= STROP:
        return [cast]
    out: list[str] = []
    while len(cast) > KUS:
        k = cast.rfind(" ", 0, KUS)
        k = k if k > 0 else KUS
        out.append(cast[:k].strip())
        cast = cast[k:].strip()
    return out + ([cast] if cast else [])


def _hledej(cast: str, text: str, od: int, do: int) -> tuple[str, int | None, int | None]:
    """Hledá část citace v text[od:do]; krátkou (méně než MIN_SLOV slov) jen doslova bez ohledu na velikost
    písmen a mezery, delší přes najdi_citaci. Offsety jsou do celého textu."""
    zbytek = text[od:do]
    try:
        zkontroluj_delku(cast)
    except ValueError:
        slova = cast.split()
        m = re.search(r"(?<!\w)" + r"\s+".join(map(re.escape, slova)) + r"(?!\w)", zbytek, re.I) if slova else None
        return ("presne", od + m.start(), od + m.end()) if m else _NENALEZENO
    r = najdi_citaci(zbytek, cast)
    if r["shoda"] == "nenalezeno":
        return _NENALEZENO
    return r["shoda"], od + r["od_znaku"], od + r["do_znaku"]


def _zapor(s: str) -> bool:
    """Je v úseku zápor (nikdy, není, nebudu, nesouhlasím…)?"""
    for w in _SLOVO.findall(_bez_diakritiky(s)):
        if w in _ZAPOR_SLOVA or (w.startswith("ne") and len(w) >= 5 and not w.startswith(_NE_NEZAPOR)):
            return True
    return False


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


def _je_osoba(jmeno: str, osoba: str) -> bool:
    """Jméno z textu patří osobě: všechna jeho slova jsou ve jméně osoby (bez klubu v závorce)."""
    slova = _slova_jmena(jmeno)
    return bool(slova) and set(slova) <= set(_slova_jmena(_ZAVORKA.sub("", osoba)))


def _kmen_prijmeni(jmeno: str) -> str:
    """Začátek příjmení pro hledání ve skloňovaném tvaru (Hrnčíř, Hrnčíře)."""
    slova = _slova_jmena(jmeno)
    w = slova[-1] if slova else ""
    return w if len(w) <= 4 else w[:max(4, len(w) - 2)]


def _stejna_osoba(a: str, b: str) -> bool:
    return _je_osoba(a, b) or _je_osoba(b, a) or (bool(_kmen_prijmeni(a)) and _kmen_prijmeni(a) == _kmen_prijmeni(b))


def _otviraci_uvozovka(text: str, od: int, do: int) -> int | None:
    """Pozice typografické otvírací uvozovky, uvnitř jejíhož páru nalezené místo ve zdroji leží: nejbližší
    typografická uvozovka před místem otvírá, nejbližší za ním ji párově zavírá (obojí do OKNO_UVOZOVEK)."""
    pred = text[max(0, od - OKNO_UVOZOVEK):od]
    p = max(pred.rfind(ch) for ch in _TYPOGRAFICKE_ZNAKY)
    if p < 0 or pred[p] not in _TYPOGRAFICKE:
        return None
    za = text[do:do + OKNO_UVOZOVEK]
    pozice = [za.find(ch) for ch in _TYPOGRAFICKE_ZNAKY if za.find(ch) >= 0]
    if not pozice or za[min(pozice)] not in _TYPOGRAFICKE[pred[p]]:
        return None
    return max(0, od - OKNO_UVOZOVEK) + p


def _cituje_a(text: str, od: int, do: int, a: str, jmena_osob: Callable[[str], list[str]]) -> bool:
    """Místo v projevu je výrok osoby a: v typografických uvozovkách, nejbližší jméno před otvírací
    uvozovkou je a a mezi ním a uvozovkou není zápor ani jiná osoba."""
    q = _otviraci_uvozovka(text, od, do)
    if q is None:
        return False
    okno = text[max(0, q - OKNO_JMENA):q]
    norm = _bez_diakritiky(okno)
    nejblizsi = None       # (konec slova se jménem, jméno)
    for j in jmena_osob(okno):
        kmen = _kmen_prijmeni(j)
        vyskyty = list(re.finditer(r"(?<!\w)" + re.escape(kmen) + r"\w*", norm)) if kmen else []
        if vyskyty and (nejblizsi is None or vyskyty[-1].end() > nejblizsi[0]):
            nejblizsi = (vyskyty[-1].end(), j)
    if nejblizsi is None or not _stejna_osoba(nejblizsi[1], a):
        return False
    mezera = okno[nejblizsi[0]:]
    return not _zapor(mezera) and all(_stejna_osoba(j, a) for j in jmena_osob(mezera))


def _prisouzeni(zdroj: Zdroj, jmena: list[str], text: str, od: int, do: int,
                jmena_osob: Callable[[str], list[str]]) -> str | None:
    """Komu výrok přisoudit; None = jiná osoba než řečník zdroje (citát je neověřený)."""
    recnik = _recnik(zdroj)
    cizi = [j for j in jmena if not _je_osoba(j, recnik)]
    if not cizi:
        return recnik
    a = cizi[0]
    if len(cizi) == len(jmena) or not all(_stejna_osoba(j, a) for j in cizi):
        return None         # řečník zdroje v odstavci jmenovaný není, nebo je cizích osob víc
    if not _cituje_a(text, od, do, a, jmena_osob):
        return None
    return f"{a} (cituje ho {recnik})"
