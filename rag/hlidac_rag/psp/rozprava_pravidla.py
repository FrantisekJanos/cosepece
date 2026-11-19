"""Čistá pravidla pro stenoprotokoly (bez DB): tisky z bodu, druh vystoupení,
oslovený řečník, shoda příjmení a čísla PN zmíněná v textu."""
from __future__ import annotations

import re
import unicodedata

# --- tisky z konce bodu ----------------------------------------------------

RE_TISKY = re.compile(
    r"tisk\w*\s+(\d+(?:/\d+)?(?:(?:\s*,\s*|\s+a\s+)\d+(?:/\d+)?)*)", re.I
)
RE_JEN_CISLO = re.compile(r"\s*/?\s*(\d+)(?:/\d+)?\s*/?\s*")


def tisky_z_konce(konec: str | None) -> list[int]:
    """Čísla tisků z `bod.konec` nebo názvu bodu, seřazená a bez duplicit.

    „/sněmovní tisk 76/6/“ -> [76]; „/sněmovní tisky 76 a 77/“ -> [76, 77].
    Jiné řady (sněmovní dokument, unijní „76-E“) a „Není sn.tiskem“ -> [].
    """
    if not konec:
        return []
    if re.search(r"dokument|\d-E\b", konec, re.I):
        return []
    m = RE_TISKY.search(konec)
    if m:
        cisla = {int(x.split("/")[0]) for x in re.findall(r"\d+(?:/\d+)?", m.group(1))}
        return sorted(cisla)
    m = RE_JEN_CISLO.fullmatch(konec)
    return [int(m.group(1))] if m else []


# --- shoda příjmení --------------------------------------------------------

RE_SLOVA = re.compile(r"[^\W\d_]+", re.U)


def _bez_diakritiky(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)
    ).lower()


def _kmen(slovo: str) -> str:
    """Kmen slova příjmení (bez diakritiky): bez koncové samohlásky, u „-ek/-ec/-el“ bez pohyblivého e
    (Marek -> mark, Vlček -> vlck); kratší než 3 znaky = celé slovo."""
    if slovo.endswith(("ek", "ec", "el")):  # pohyblivé e: Vlček -> Vlčka, Němec -> Němce
        kmen = slovo[:-2] + slovo[-1]
    elif slovo[-1] in "aeiouy":  # koncová samohláska: Kupka -> Kupky
        kmen = slovo[:-1]
    else:
        kmen = slovo
    return kmen if len(kmen) >= 3 else slovo


def _slova_prijmeni(prijmeni: str) -> list[str]:
    """Slova příjmení bez diakritiky; u víceslovného (Pekarová Adamová, Šťáhlavská-Batková, Kučera ml.)
    jen slova od 3 písmen."""
    slova = RE_SLOVA.findall(_bez_diakritiky(prijmeni))
    dlouha = [s for s in slova if len(s) >= 3]
    return dlouha if len(slova) > 1 and dlouha else slova


def shoda_prijmeni(tvar: str, prijmeni: str) -> bool:
    """Odpovídá ohnutý tvar příjmení (Haase, Vlčka, Adamové) základnímu tvaru (Haas, Vlček, Pekarová Adamová)?

    Bez diakritiky a velikosti písmen; `tvar` začíná některým slovem příjmení (Niemiece ~ Niemiec), nebo
    jeho kmenem (viz _kmen: Marka ~ Marek, ale Maříkovi ne).
    """
    t = _bez_diakritiky(tvar)
    if not t:
        return False
    return any(t.startswith(s) or t.startswith(_kmen(s)) for s in _slova_prijmeni(prijmeni))


# --- oslovený řečník -------------------------------------------------------

RE_REAKCE = re.compile(
    r"(?:reagov\w+|zareaguj\w+|reakc\w+|navázat|vyjádř\w*(?:\s+se)?|odpov\w+)\s+(?:na|k|ke)\s+"
    r"|\bk\s+(?:odpovědi|vystoupení|projevu)\s+",
    re.I,
)
RE_OSLOVENI = re.compile(
    r"^\s*(?:(?:Děkuji|Děkuju)[^.!?]{0,40}[.!?]\s*)?"
    r"(?:Vážen[áý]\s+)?(?:Paní|Pane|Pan)\s+"
    r"(?:poslankyně|poslanče|poslanec|ministryně|ministře|ministr|předsedo|kolego|kolegyně)\s+"
)
# funkční slova a tituly, které nejsou jméno, i když začínají velkým písmenem
NEJMENA = {
    "pan", "pani", "pana", "panu", "pane", "poslanec", "poslance", "poslanci", "poslankyne",
    "poslankyni", "ministr", "ministra", "ministryne", "ministryni", "kolega", "kolegu",
    "kolegyne", "kolegyni", "predsedajici", "predsedajiciho", "predseda", "predsedy",
    "mistopredseda", "mistopredsedy", "mistopredsedo", "snemovny", "ja", "ano", "ne",
}
OKNO_OSLOVENI = 400


def _jmeno_z_useku(usek: str, max_slov: int = 8) -> str | None:
    """První souvislá řada slov s velkým písmenem; vrací její poslední slovo (příjmení)."""
    konec = re.search(r"[.!?]", usek)
    if konec:
        usek = usek[: konec.start()]
    slova = RE_SLOVA.findall(usek)[:max_slov]
    rada: list[str] = []
    for s in slova:
        velke = s[0].isupper() and _bez_diakritiky(s) not in NEJMENA
        if velke:
            rada.append(s)
        elif rada:
            break
    return rada[-1] if rada else None


def osloveny_recnik(text: str | None) -> str | None:
    """Příjmení (tak, jak je v textu, tedy ohnuté), na koho řečník reaguje.

    Text je vlastní vystoupení faktické poznámky; hledá se jen v jeho začátku.
    „reagovat na Karla Haase“ -> „Haase“; „Paní poslankyně Kovářová, …“ -> „Kovářová“.
    Nenalezeno -> None.
    """
    if not text:
        return None
    zacatek = text[:OKNO_OSLOVENI]
    for m in RE_REAKCE.finditer(zacatek):
        jmeno = _jmeno_z_useku(zacatek[m.end():m.end() + 120])
        if jmeno:
            return jmeno
    m = RE_OSLOVENI.match(zacatek)
    if m:
        return _jmeno_z_useku(zacatek[m.end():m.end() + 60], max_slov=3)
    return None


# --- druh vystoupení -------------------------------------------------------

# všechny hodnoty, které vrací druh_vystoupeni (výčet platných hodnot filtru druh v nástrojích MCP)
DRUHY = ("recnik", "fakticka", "prednostni", "zpravodaj", "predkladatel", "ministr", "proceduralni", "predsedajici")

RE_VETY = re.compile(r"(?<=[.!?])\s+")
RE_ODDELOVAC = re.compile(
    r"\b(?:a\s+)?(?:potom|pak|poté|následně|připraví se|bude následovat)\b", re.I
)
RE_KLICOVE = re.compile(
    r"faktick|zpravodaj|navrhovatel|předkladatel|přednostn|procedurál", re.I
)
RE_OZNAMENI = re.compile(
    r"prosím|poprosím|slovo|vystoup|přihlá|máte|nyní|další|reagov|hlásí|uvede|ujal", re.I
)
RE_DEKUJI = re.compile(r"děkuj", re.I)
RE_FAKTICKA = re.compile(r"faktick", re.I)
RE_ZPRAVODAJ = re.compile(r"zpravodaj", re.I)
RE_PREDKLADATEL = re.compile(
    r"navrhovatel|předkladatel|za navrhovatele|uved\w*.{0,80}návrh", re.I
)
RE_MINISTR = re.compile(r"ministr|předsed(?:a|kyně) vlády", re.I)
RE_PREDNOSTNI = re.compile(r"přednostn", re.I)
RE_PREDNOSTNI_PREDLOZKA = re.compile(r"\b(s|bez)\s+přednostn", re.I)
RE_PROCEDURALNI = re.compile(r"procedurál", re.I)


# konec textu před příjmením je poděkování této osobě: „Děkuji panu poslanci (Josefu) “, „Děkuji, paní poslankyně “
# (nejvýš 4 slova za „děkuji“, bez čárky a bez „a“ / „s“, za kterými už začíná ohlášení)
RE_PODEKOVANI_OSOBE = re.compile(r"děkuj\w*,?(?:\s+(?!(?:a|s)\b)[^\W\d_]+){0,4}\s+$", re.I)


def _jen_podekovani(veta: str) -> bool:
    return bool(RE_DEKUJI.search(veta)) and not RE_OZNAMENI.search(veta)


def _usek_o_osobe(veta: str, prijmeni: str) -> tuple[str, int] | None:
    """Část věty o osobě s daným příjmením (mezi oddělovači „potom“, „a pak“ …) a pozice příjmení v ní."""
    shoda = None
    for m in RE_SLOVA.finditer(veta):
        if m.group()[0].isupper() and shoda_prijmeni(m.group(), prijmeni):
            shoda = m
    if shoda is None:
        return None
    zacatek = 0
    for o in RE_ODDELOVAC.finditer(veta):
        if o.end() <= shoda.start():
            zacatek = o.end()
    konec = len(veta)
    for o in RE_ODDELOVAC.finditer(veta):
        if o.start() >= shoda.end():
            konec = o.start()
            break
    return veta[zacatek:konec], shoda.start() - zacatek


def _rozhodujici_usek(text: str, prijmeni: str | None) -> tuple[str, int | None]:
    """Úsek řeči předsedajícího, který o druhu rozhoduje, a pozice příjmení v něm (None = bez příjmení).

    Poděkování nerozhoduje: s příjmením se vynechá úsek, kde je osoba ta, které se děkuje („Děkuji panu
    poslanci Markovi za faktickou poznámku.“); bez příjmení věta, která jen děkuje."""
    vety = [v for v in RE_VETY.split(text) if v.strip()]
    if prijmeni:
        useky = [u for u in (_usek_o_osobe(v, prijmeni) for v in reversed(vety))
                 if u is not None and not RE_PODEKOVANI_OSOBE.search(u[0][:u[1]])]
        # přednost má úsek s klíčovým slovem (krátké příjmení může omylem sedět na jiné slovo)
        for u in useky:
            if RE_KLICOVE.search(u[0]):
                return u
        if useky:
            return useky[0]
    for v in reversed(vety):
        if RE_KLICOVE.search(v) and not _jen_podekovani(v):
            return v, None
    return "", None


def _prednostni(usek: str, pozice: int | None) -> bool:
    """Ohlášení s přednostním právem, ne „bez přednostního práva“.

    Rozhoduje zmínka s předložkou „s“ / „bez“ nejbližší před příjmením (bez příjmení poslední, když před
    příjmením žádná není, první za ním): „s přednostním právem – respektive bez přednostního práva – pan
    předseda Hřib“ přednostní není. Zmínka bez předložky („pořadí přednostních práv: předseda Hřib“) je."""
    if not RE_PREDNOSTNI.search(usek):
        return False
    zminky = list(RE_PREDNOSTNI_PREDLOZKA.finditer(usek))
    if not zminky:
        return True
    if pozice is None:
        rozhodne = zminky[-1]
    else:
        pred = [m for m in zminky if m.start() < pozice]
        rozhodne = pred[-1] if pred else zminky[0]
    return rozhodne.group(1).lower() == "s"


def druh_vystoupeni(
    text_predsedajiciho: str | None,
    role: str | None,
    predsedajici: bool,
    prijmeni: str | None = None,
) -> str:
    """Druh vystoupení: recnik, fakticka, prednostni, zpravodaj, predkladatel,
    ministr, proceduralni nebo predsedajici.

    Z jedné věty řeči předsedajícího (s příjmením řečníka její úsek o něm, jinak
    poslední větu s klíčovým slovem; samotné poděkování nerozhoduje), pak z role.
    """
    if predsedajici:
        return "predsedajici"
    usek, pozice = _rozhodujici_usek(text_predsedajiciho, prijmeni) if text_predsedajiciho else ("", None)
    if RE_FAKTICKA.search(usek):
        return "fakticka"
    if RE_ZPRAVODAJ.search(usek):
        return "zpravodaj"
    if RE_PREDKLADATEL.search(usek):
        return "predkladatel"
    if role and RE_MINISTR.search(role):
        return "ministr"
    if _prednostni(usek, pozice):
        return "prednostni"
    if RE_PROCEDURALNI.search(usek):
        return "proceduralni"
    return "recnik"


# --- PN v textu ------------------------------------------------------------

_KLIC_PN = (
    r"(?:pozměňovac\w+\s+návrh\w*|sněmovní\w*\s+(?:dokument|číslo|čísl)\w*|\bSD\b)"
)
_MEZERA_PN = (
    r"(?:\s+(?:pod|s|číslem|čísly|číslo|čísla|č\.|sněmovním|sněmovního|sněmovní|dokumentem|"
    r"dokumentu|dokument|vedeným|vedený|vložený|vloženému|nahrán|nahraný|do|systému|jako|"
    r"mají|má|číslu)){0,8}"
)
_CISLO = r"\d+(?:[  ]\d{3}\b)*"
_ODDELOVAC_PN = r"(?:\s*,\s*|\s+a\s+|\s+až\s+|\s*[–-]\s*)"
RE_PN = re.compile(
    rf"{_KLIC_PN}{_MEZERA_PN}[\s:]*(?P<prvni>{_CISLO})"
    rf"(?P<retez>(?:{_ODDELOVAC_PN}{_CISLO})*)",
    re.I,
)
RE_RETEZ = re.compile(rf"({_ODDELOVAC_PN})({_CISLO})")
PN_MIN, PN_MAX, PN_ROZSAH_MAX = 100, 99999, 50


def _cislo(s: str) -> int:
    return int(re.sub(r"\D", "", s))


def pn_z_textu(text: str | None) -> list[int]:
    """Čísla PN zmíněná hned za klíčovým slovem (pozměňovací návrh, sněmovní dokument, SD).

    Řetěz čísel se prodlužuje jen těsnými oddělovači „, N“, „ a N“, „ až N“, „ – N“.
    Rozsah „2312 až 2317“ se rozbalí (nejvýš 50 čísel, jinak platí jen první číslo).
    Čísla mimo 100–99999 (řádky tabulky) se zahodí.
    """
    if not text:
        return []
    vysledek: set[int] = set()
    for m in RE_PN.finditer(text):
        predchozi = _cislo(m.group("prvni"))
        nalezena = [predchozi]
        for sep, cislo in RE_RETEZ.findall(m.group("retez")):
            n = _cislo(cislo)
            rozsah = re.search(r"až|[–-]", sep) is not None
            if rozsah and 0 < n - predchozi <= PN_ROZSAH_MAX:
                nalezena.extend(range(predchozi + 1, n + 1))
            elif not rozsah:
                nalezena.append(n)
            predchozi = n
        vysledek.update(x for x in nalezena if PN_MIN <= x <= PN_MAX)
    return sorted(vysledek)
