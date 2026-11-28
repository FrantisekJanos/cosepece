"""Ověření citací a přisouzení v odpovědi modelu po odstavcích (spec 9.2), bez LLM a sítě.

Model píše doslovnou citaci v uvozovkách (kterékoli z citace_shoda.UVOZOVKY kromě ` ´ ″ ′, i smíšené
a přes jedno zalomení řádku) nebo jako blokovou citaci (řádky začínající `>`) a hned za ní `[N]`. Server
odpověď drží po odstavcích (Odstavce) a odstavec s citací (ma_citat) pustí až po kontrole (over_odstavec)
proti textu zdroje N z registru:

- `presne`, `priblizne`: text zůstane, citát nese offsety do textu zdroje; u projevu `zaznelo_v`
  (řečník zdroje, klíč projevu, datum, url) pro čip „zaznělo v projevu J. Nováka (datum)“;
- `nenalezeno` (výrok ve zdroji není) a `neovereno` (chybí [N], zdroj bez textu, odstavec přisuzuje citát
  jiné osobě, nespárovaná uvozovka před [N]): uvozovky zmizí, přibude ZNACKA_NEOVERENO a citát se nikomu
  nepřisoudí. Krátký pojem v uvozovkách bez [N] zůstane, jen když u něj (od předchozího citátu do konce
  věty) není jméno osoby ani uvozovací slovo („Babiš řekl: „zloději“.“, „podle premiéra „zloději““ = citát).

Co výsledek tvrdí (rozhodnutí controlleru r4): jen to, že ZNĚNÍ ZAZNĚLO v daném projevu, ne že je to
názor řečníka (proto prompt říká „v projevu zaznělo“). Jméno řečníka v odstavci se nevyžaduje, čip ho
dodá. Kontrola odstavce brání jen zjevně chybnému přisouzení: citát z projevu je `neovereno`, když odstavec
u citátu (věta s citátem, další věty bez vlastního citátu; když věta řečníka nejmenuje, celý odstavec)
- jmenuje jinou osobu (jmena_osob z DB, jiné křestní jméno, Nováková/Novákův u řečníka Nováka), i takovou,
  kterou DB nezná (r5): slovo s velkým písmenem uprostřed věty, dvě taková slova za sebou, nebo první slovo
  věty před slovesem mluvení („Pavel Kříž řekl“, „citoval Kříže“, „Kříž said“), kromě běžných slov,
  institucí, titulů, zkratek a jména řečníka,
- jmenuje roli, funkci, instituci, jiný klub nebo zájmeno („ministr“, „vláda“, „ODS“, „podle něj“), kromě
  přístavku u jména řečníka („ministr financí Novák“, „Novák, zpravodaj,“),
- má řečníka jen jako předmět („Na Nováka reagoval“, „Novákovi odpověděla“); jiný pád projde jen v rámci
  „podle Nováka“, „slovy Nováka“, „v projevu Nováka“, „Novákova slova“ (ne po předložce na, proti, s, po…),
- má sloveso mluvení v jiném rodě nebo čísle, než má řečník („řekla“ u Nováka, „řekli“).
Příjmení se počítá jen s velkým písmenem; běžné slovo na začátku věty („Malá změna“, „Válka“) řečníka
nejmenuje. Citát z textu zákona nebo PN (`cel:`), který věta přisuzuje osobě, je `neovereno` (K2).

Zdroj: místo v uvozovkách nebo u „cituji / citace“ (OKNO_CITUJE před ním, OKNO_CITUJE_ZA za ním) je
nejvýš `priblizne` s poznámkou POZNAMKA_CITUJE. Znění s výpustkou je nejvýš `priblizne`, stejně jako
místo, kterému ve zdroji těsně předchází zápor nebo popření („Netvrdím, že …“, poznámka POZNAMKA_POPRENO).
Role se počítají i s předponou ex- a „bývalý“ (expremiér). Zápor nebo slovo
obracející smysl v textu přeskočeném výpustkou = `nenalezeno`; přibližná shoda, která ve zdroji spojí
víc vět než citace, taky.
"""
from __future__ import annotations

import re
import string
import unicodedata
from bisect import bisect_left, bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache

from ..citace_shoda import _ZAPORNA, MIN_SLOV, UVOZOVKY, najdi_citaci, zkontroluj_delku
from .registr import Registr, Zdroj

ZNACKA_NEOVERENO = "(neověřeno ve zdroji)"
POZNAMKA_CITUJE = "řečník zde cituje"
POZNAMKA_POPRENO = "ve zdroji popřeno"
TYPY_S_TEXTEM = ("cel", "projev")
STROP = 2_000           # delší část citace se ověří po kusech
KUS = 1_000             # délka kusu dlouhé části
MEZERA_KUSU = 100       # nejvýš tolik znaků zdroje mezi sousedními kusy dlouhé části
MEZERA_VYPUSTKY = 300   # nejvýš tolik znaků zdroje přeskočí výpustka mezi částmi citace
OKNO_ZAPORU = 60        # zápor nebo popření těsně před nalezeným místem ve zdroji (ve stejné větě)
OKNO_CITUJE = 200       # „cituji / citace“ nejvýš tolik znaků před nalezeným místem ve zdroji
OKNO_CITUJE_ZA = 150    # a za ním („…, jenom cituji pana poslance“)
OKNO_UVOZOVEK = 300     # nespárovaná uvozovka nejvýš tolik znaků před místem = místo v uvozovkách
OKNO_PRISTAVKU = 6      # kolik slov přístavku („ministr pro místní rozvoj Jan“) se nejvýš projde

# ` ´ jsou značky markdownu a akcent, ″ ′ palce a minuty
_ZNAKY_UVOZOVEK = "".join(c for c in UVOZOVKY if c not in "`´″′")
_JEN_OTVIRACI = "„‟"
_APOSTROFY = "'’‛"

_KONEC_ODSTAVCE = re.compile(r"\n(?:[ \t]*\n)+")
_BLOK_RADEK = re.compile(r"^[ \t]*>[ \t]?(.*)$", re.M)
_ODKAZ = re.compile(r"\[(\d+)\]")
_ODKAZ_ZA = re.compile(r"[ \t]*[,.;:]?[ \t]*\[(\d+)\]")
_ODKAZ_NA_KONCI = re.compile(r"[ \t]*\[(\d+)\][ \t]*[.,;:]?[ \t]*$")
_ODKAZ_NA_ZACATKU = re.compile(r"[ \t]*\[(\d+)\]")
_KONEC_VETY = re.compile(r"[.!?](?=\s|$)")
# tečka za zkratkou, iniciálou nebo číslem („16. září“) větu neukončuje
_ZKRATKY = {"posl", "ing", "mgr", "mudr", "judr", "phdr", "rndr", "bc", "doc", "prof", "p", "pi", "c", "odst",
            "pism", "cl", "tzv", "napr", "resp", "atd", "sb", "mil", "mld", "tis", "kc", "min", "max"}
_SLOVO_PRED = re.compile(r"(\w+)$")
_OKNO_ZKRATKY = 40      # jak daleko před tečkou se hledá slovo (zkratka, číslo)
_OKRAJ_ZACATEK = string.whitespace + "…."
_OKRAJ_KONEC = string.whitespace + "….,;:!?"
_VYPUSTKA = re.compile(r"\[…\]|\(…\)|\[\.\.\.\]|\(\.\.\.\)|…|\.\.\.")
_SLOVO = re.compile(r"\w+")
_DATUM_TITULKU = re.compile(r",\s*(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})$")
_ZAVORKA = re.compile(r"\s*\([^()]*\)$")
_KLUB = re.compile(r"\(([^()]*)\)\s*$")
_UVOZOVACI = re.compile(r"(?<!\w)(rekl|rekla|rekli|rika|rikal|rikala|uvedl|uvedla|uvedli|citoval|citovala|cituji"
                        r"|cituje|napsal|napsala|prohlasil|prohlasila|tvrdil|tvrdila|tvrdi|dodal|dodala|nazval"
                        r"|nazvala|nazyva|oznacil|oznacila|oznacuje|povazuje|povazuji|povazoval|povazovala|podle"
                        r"|slovy|dle|said|stated|says|states|told)(?!\w)")
# přisouzení osobě u textu zákona nebo PN: sloveso mluvení v minulém čase, role, zájmeno
_OSOBA_SLOVA = re.compile(
    r"(?<!\w)(rekl|rekla|rekli|prohlasil\w*|uvedl\w*|napsal\w*|tvrdil\w*|dodal\w*|citoval\w*|nazval\w*"
    r"|oznacil\w*|odpovedel\w*|reagoval\w*|zduraznil\w*|vyjadril\w*|rikal\w*|cituje|cituji|slovy|podle|dle"
    r"|said|stated|says|states|told"
    r"|premier\w*|ministr\w*|poslan\w*|predsed\w*|mistopredsed\w*|zpravodaj\w*|navrhovatel\w*"
    r"|predkladatel\w*|recnik\w*|recnic\w*|mluvci|prezident\w*|senator\w*|hejtman\w*|starost[ak]\w*"
    r"|kolega|kolegu|kolegy|kolegyne|kolegyni|on|ona|oni|ony|jeho|jeji|jejich)(?!\w)")
# ve zdroji: řečník cituje jiného
_CITUJE = re.compile(r"(?<!\w)o?cit(?:uj|ov|ac|at)\w*")
# zápor: samostatná záporná slova a sloveso s předponou ne- (kromě běžných slov, která záporem nejsou)
_ZAPOR_SLOVA = _ZAPORNA | {"nikdy", "nic", "nikdo", "nikoho", "nicim", "zadny", "zadna", "zadne", "zadneho",
                            "ani", "proti", "lez", "lzi", "lhal", "lhala", "ohradil", "ohradila", "ohrazuji"}
# začátky slov, která obracejí smysl (odmítám, popřel, nepravda)
_ZAPOR_ZACATKY = ("odmit", "popr", "popir", "nepravd")
_NE_NEZAPOR = ("nej", "nekd", "neko", "neco", "nebo", "nez", "nedel", "nemoc", "nemec", "nerv", "neur", "nevest",
               "neter", "neon")

# --- slova pro kontrolu přisouzení (malými bez diakritiky, není-li řečeno jinak) ----------------------

# role, funkce a instituce: mluvčí, který není jmenovaný řečník zdroje (kromě přístavku u jeho jména)
_ROLE = re.compile(r"""(?x)
    (?:ex-?)?(?:
    (?:vice)?premier\w* | ministr\w* | ministerstv\w* | poslan(?:ec|c[eiu]\w*|kyn\w*) | europoslan\w*
  | (?:mistop|p)redsed\w* | zpravodaj\w* | navrhovatel\w* | predkladatel\w*
  | autor(?:a|ovi|em|ka|ky|ce|ku|kou|i|e|u|ove|um|ech)? | recni[kc]\w* | mluvci | prezident\w* | senator\w*
  | hejtman\w* | primator\w* | starost(?:a|y|ovi|ou|o|ove|ka|ky|ce|ku|kou) | koleg(?:a|y|ovi|u|ou|o|ove|um|ech|yn\w*)
  | namest(?:ek|k\w*) | guverner\w* | ombudsman\w* | ochran(?:ce|ci|cem|kyn\w*) | kancler\w*
  | sef(?:a|ovi|em|e|ka|ky|ce|ku|kou|ove)? | lidr(?:a|ovi|em|yne|yni)? | clen(?:a|ovi|em|ka|ky|ce|ku|kou|ove)?
  | zastup(?:ce|ci|cem|cum|kyn\w*) | reditel\w* | tajemnik\w* | soudc\w* | soudkyn\w*
  | expert(?:a|ovi|em|ka|ky|ove|i)? | analytik\w* | ekonom(?:a|ovi|em|ka|ky|ove)? | novinar\w* | redaktor\w*
  | moderator\w* | kandidat(?:a|ovi|em|ka|ky|i|ove)? | predak\w* | velvyslan\w* | general(?:a|ovi|em|ove)?
  | policist\w*
  | vlad(?:a|y|e|u|ou) | opozic(?:e|i) | koalic(?:e|i) | hnuti | stran(?:a|y|e|u|ou) | soud(?:u|em|y)?
  | urad(?:u|em)? | lidovc\w* | komunist\w* | pirat\w* | motorist\w* | demokrat(?:e|i|u|um|y|ech|a|ovi)?
  | byval\w*
    )
""")
_ZAJMENA = {"on", "ona", "ono", "oni", "ony", "nej", "neho", "nemu", "nem", "nim", "ni", "nich", "nimi", "jeho",
            "jeji", "jejich"}
# zkratky klubů (jak se píšou); klub řečníka zdroje se nepočítá
_STRANY = {"ANO", "ODS", "SPD", "STAN", "KDU", "ČSL", "TOP", "SOCDEM", "ČSSD", "KSČM", "SPOLU", "SZ"}
# slova přístavku mezi rolí a jménem („ministr pro místní rozvoj“, „předseda poslaneckého klubu hnutí“)
_DOPLNKY = {"financi", "zemedelstvi", "vnitra", "spravedlnosti", "zdravotnictvi", "skolstvi", "obrany", "dopravy",
            "prumyslu", "obchodu", "prace", "socialnich", "veci", "zahranicnich", "kultury", "zivotniho",
            "prostredi", "mistniho", "mistni", "rozvoje", "rozvoj", "pro", "evropske", "evropskych", "zalezitosti",
            "digitalizace", "regionalniho", "sportu", "vedy", "vyzkumu", "inovaci", "legislativy", "poslaneckeho",
            "poslanecky", "klubu", "klub", "hnuti", "strany", "vyboru", "vybor", "rozpoctoveho", "rozpoctovy",
            "ustavne", "pravniho", "kontrolniho", "hospodarskeho", "zahranicniho", "bezpecnostniho", "snemovny",
            "senatu", "vlady", "za", "ceske", "republiky", "cr", "statni", "financni", "spravy", "narodni", "banky",
            "verejny", "prav", "hlavniho", "mesta", "prahy", "kraje", "prvni"}
_OSLOVENI = {"pan", "pana", "panu", "panem", "pane", "pani", "panove", "slecna"}
_TITULY = {"ing", "mgr", "mudr", "judr", "phdr", "rndr", "bc", "doc", "prof", "posl", "ph", "d", "phd", "csc", "mba",
           "dr", "paedr", "thdr", "mvdr", "mddr", "dis", "llm", "msc", "ba", "ma", "drsc"}
# jiný pád řečníka je mluvčí jen v rámci těchto slov („podle slov Nováka“, „v projevu Nováka“, „Novákova slova“)
_RAMCE = {"slova", "slov", "slovech", "slovy", "projev", "projevu", "projevem", "projevech", "projevy",
          "vystoupeni", "vystoupenim", "vyrok", "vyroku", "vyroky", "vyrocich", "rec", "reci", "citat", "citatu",
          "stanovisko", "stanoviska", "stanovisku", "nazor", "nazoru", "odpoved", "odpovedi", "replika", "repliky",
          "replice"}
# … a ne po těchto předložkách („Na projev Nováka reagoval“, „po vystoupení Nováka“, „se slovy Nováka“)
_PREDLOZKY = {"na", "proti", "k", "ke", "vuci", "o", "po", "pred", "s", "se", "za", "kvuli", "oproti", "misto",
              "krome", "mimo", "nez", "u", "do", "od", "nad", "pod", "mezi"}
# slova s velkým písmenem, která nejsou jménem osoby („Včera Novák“, „Ve Sněmovně Novák“, „Stenozáznam uvádí“)
_NEJSOU_KRESTNI = {
    "a", "i", "ale", "avsak", "vsak", "pak", "pote", "potom", "nato", "take", "taky", "tak", "rovnez", "nasledne",
    "nakonec", "zaroven", "hned", "totiz", "pritom", "dale", "opet", "znovu", "proto", "uz", "jiz", "jak", "kdyz",
    "kde", "zde", "tady", "tam", "ten", "ta", "to", "ti", "ty", "tento", "tato", "toto", "tehdy", "nyni", "ted",
    "vcera", "dnes", "zitra", "letos", "loni", "nedavno", "pozdeji", "drive", "nejprve", "nejdriv", "nakonec",
    "na", "v", "ve", "k", "ke", "s", "se", "z", "ze", "o", "u", "do", "od", "po", "pri", "pro", "pred", "za",
    "nad", "pod", "mezi", "behem", "kvuli", "diky", "misto", "podle", "slovy", "ani", "nebo", "ci", "ze", "aby",
    "ktery", "ktera", "ktere", "jenz", "sam", "sama", "podobne", "naopak", "jeste", "dokonce", "napriklad",
    "treba", "pry", "udajne", "zrejme", "jen", "jenom", "pouze", "stejne", "zejmena", "hlavne", "predevsim",
    "konkretne", "doslova", "ano", "ne", "snemovna", "snemovny", "snemovne", "snemovnu", "snemovnou", "senat",
    "senatu", "senatem", "parlament", "parlamentu", "parlamentem", "rozprava", "rozprave", "rozpravy", "dle",
    "opakovane", "strucne", "verejne", "jasne", "otevrene", "stenozaznam", "stenoprotokol", "zaznam", "text",
    "zakon", "navrh", "tisk", "zprava", "dokument", "usneseni"}
# běžná slova se stejným tvarem jako časté příjmení: na začátku věty řečníka nejmenují (N2)
_BEZNA_SLOVA = {
    "mala", "maly", "male", "maleho", "malemu", "malem", "malym", "malou", "malo", "mali", "malych",
    "nova", "novy", "nove", "noveho", "novemu", "novem", "novym", "novou", "novi", "novych",
    "valka", "valky", "valce", "valku", "valkou", "valek", "fialovy", "fialova", "fialove", "fialy",
    "holka", "holky", "holce", "holku", "holkou", "cerny", "cerna", "cerne", "bily", "bila", "bile",
    "zeleny", "zelena", "zelene", "vesely", "vesela", "vesele", "dobry", "dobra", "dobre", "stary", "stara",
    "stare", "hruby", "hruba", "tichy", "ticha", "pokorny", "pokorna", "stastny", "stastna", "mlady", "mlada",
    "velky", "velka", "velke", "kral", "krale", "kralove", "svoboda", "svobody", "svobode", "svobodu",
    "svobodou", "zima", "zimy", "zime", "zimu", "mraz", "liska", "holub", "vlk", "kos", "sova", "zajic",
    "beran", "kovar", "vrana", "straka", "sokol", "orel", "jelen", "lev", "prochazka", "prochazky",
    "prochazku", "ruzicka", "hajek", "sykora", "zeman", "mlynar", "pekar", "sedlak", "rybar", "tesar",
    "kocka", "kohout", "jezek", "havran", "slavik", "vrabec", "kozel", "pospisil", "kratochvil", "hrdina",
    "skala", "skaly", "kamen", "krejci", "kovac", "vodicka", "vojak", "kucera", "hora", "hory", "les", "dvur",
    "lasko", "laska", "vitez", "zlaty", "zlata", "spacek", "sladek", "bartos", "nemec", "nemcova", "polak",
    "rus", "slovak", "madar", "vlach", "cech", "cesky", "ceska"}
# sloveso mluvení v minulém čase: koncovka říká rod a číslo mluvčího
_MLUVENI = re.compile(
    r"(rekl|uvedl|prohlasil|dodal|odpovedel|reagoval|zareagoval|tvrdil|napsal|psal|zduraznil|vyjadril|poznamenal"
    r"|namitl|namital|kontroval|upozornil|konstatoval|sdelil|oznamil|citoval|ocitoval|zminil|pripomnel|vysvetlil"
    r"|argumentoval|varoval|kritizoval|podotkl|doplnil|shrnul|zopakoval|vzkazal|pronesl|nazval|oznacil|hovoril"
    r"|mluvil|rikal|opacil|oponoval|replikoval|zduraznoval|uvadel|prohlasoval|vyzval|slibil|ujistil|odmitl|poprel"
    r"|navrhl|vystoupil|odvetil|zvolal|pripustil|priznal|zeptal|ptal|prohodil|ohradil|obhajoval|hajil)(a|i|y|o)?")
# sloveso mluvení bez rodu (přítomný čas, anglicky): „Kříž tvrdí“, „Kříž said“
_MLUVENI_BEZ_RODU = {"rika", "tvrdi", "uvadi", "prohlasuje", "dodava", "odpovida", "reaguje", "pise", "zduraznuje",
                     "mini", "konstatuje", "upozornuje", "cituje", "said", "stated", "says", "states", "told",
                     "added", "noted", "wrote"}
# instituce, místa a texty s velkým písmenem, které nejsou jménem osoby („ve Sněmovně“, „č. 218/2000 Sb.“)
_INSTITUCE = re.compile(r"""(?x)
    snemovn\w* | poslaneck\w* | senat\w* | parlament\w* | ustav\w* | listin\w* | sbir\w* | sb | cesk\w* | cr
  | evrop\w* | unie | praz\w* | prah\w* | brn\w* | ostrav\w* | ukrajin\w* | rus\w* | nemec\w* | nemeck\w*
  | slovens\w* | polsk\w* | rakous\w* | brusel\w* | americ\w* | kyjev\w* | moskv\w* | washington\w*
""")
_PRIVLASTNOVACI = ("ův", "ova", "ovo", "ovy", "ově", "ovu", "ovou", "ových", "ovým", "ovými")
_MEZERA_TECKY = re.compile(r"\.\s*")


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
    {n, shoda, od, do, recnik, neautorizovano, zaznelo_v, poznamka} v pořadí v odstavci (od/do = offsety
    v textu zdroje; recnik a zaznelo_v = {recnik, klic, datum, url} jen u projevu se shodou)."""
    bloky = _bloky(odstavec)
    pary, nesparovane = _uvozovky(odstavec, [(b.od, b.do) for b in bloky])
    vety = _Vety(odstavec)
    citaty = sorted(bloky + _inline(odstavec, pary, jmena_osob, vety), key=lambda c: c.od)
    kontext = _Kontext(odstavec, citaty, vety, jmena_osob)
    zdroje = _Zdroje(text_zdroje)
    vysledky = [(c.od, _over_citat(i, c, registr, zdroje, kontext)) for i, c in enumerate(citaty)]

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
            vysledky.append((p, _neovereno(int(odkaz.group(1)))))
    casti, posledni = [], 0
    for od, do, nahrada in sorted(upravy):
        casti += [odstavec[posledni:od], nahrada]
        posledni = do
    casti.append(odstavec[posledni:])
    return Overeni("".join(casti), [v for _, v in sorted(vysledky, key=lambda x: x[0])])


def _neovereno(n: int | None) -> dict:
    return {"n": n, "shoda": "neovereno", "od": None, "do": None, "recnik": None, "neautorizovano": False,
            "zaznelo_v": None, "poznamka": None}


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


def _konce_vet(s: str, od: int, do: int):
    """Konce vět v s[od:do]: .!? před mezerou nebo koncem; tečka za zkratkou, iniciálou nebo číslem ne."""
    for m in _KONEC_VETY.finditer(s, od, do):
        if m.group() == ".":
            slovo = _SLOVO_PRED.search(s, max(0, m.start() - _OKNO_ZKRATKY), m.start())
            w = _bez_diakritiky(slovo.group(1)) if slovo else ""
            if w in _ZKRATKY or w.isdigit() or (len(w) == 1 and w.isalpha()):
                continue
        yield m.end()


class _Vety:
    """Konce vět odstavce spočítané jednou (bisect místo procházení od začátku pro každý citát)."""

    def __init__(self, s: str):
        self.konce = list(_konce_vet(s, 0, len(s)))
        self.delka = len(s)

    def zacatek(self, p: int, od: int = 0) -> int:
        """Začátek věty, ve které je pozice p (ne dřív než od)."""
        k = bisect_right(self.konce, p) - 1
        return max(od, self.konce[k]) if k >= 0 else od

    def konec(self, p: int) -> int:
        """Konec věty, ve které je pozice p (první konec za p)."""
        k = bisect_right(self.konce, p)
        return self.konce[k] if k < len(self.konce) else self.delka


def _inline(s: str, pary: list[tuple[int, int]], jmena_osob: Callable[[str], list[str]], vety: _Vety
            ) -> list[_Citat]:
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
            okoli = [s[predchozi:o], s[z + 1:vety.konec(z + 1)]]
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


def _bez_uvozovek(c: _Citat) -> str:
    return c.vnitrek.rstrip() if c.blok else c.vnitrek


# --- ověření jednoho citátu --------------------------------------------------------------------

def _over_citat(i: int, c: _Citat, registr: Registr, zdroje: _Zdroje, kontext: _Kontext) -> dict:
    vysledek = _neovereno(c.n)
    zdroj = registr.podle_n(c.n) if c.n is not None else None
    if zdroj is None:
        return vysledek
    if zdroj.typ == "projev":
        vysledek["neautorizovano"] = zdroj.meta.get("autorizovano") is False
    text = zdroje.text(c.n) if zdroj.typ in TYPY_S_TEXTEM else None
    if not text:
        return vysledek
    shoda, od, do = _najdi(c.vnitrek, text)
    if shoda == "nenalezeno":
        vysledek["shoda"] = "nenalezeno"
        return vysledek
    poznamky = []
    if _popreno_pred(text, od):
        # ve zdroji „Netvrdím, že …“: znění se nepodává jako přesné
        shoda = "priblizne"
        poznamky.append(POZNAMKA_POPRENO)
    if zdroj.typ != "projev":
        # text zákona nebo PN přisouzený osobě (K2)
        return ({**vysledek, "shoda": shoda, "od": od, "do": do, "poznamka": "; ".join(poznamky) or None}
                if kontext.bez_osoby(i) else vysledek)
    osoba = _osoba(_recnik(zdroj))
    if not kontext.nikdo_jiny(i, osoba):
        return vysledek
    if zdroje.recnik_cituje(c.n, od, do):
        shoda = "priblizne"
        poznamky.append(POZNAMKA_CITUJE)
    return {**vysledek, "shoda": shoda, "od": od, "do": do, "recnik": osoba.cip,
            "zaznelo_v": _zaznelo_v(zdroj, osoba.cip), "poznamka": "; ".join(poznamky) or None}


_NENALEZENO = ("nenalezeno", None, None)


def _najdi(citace: str, text: str) -> tuple[str, int | None, int | None]:
    """Najde citaci v textu zdroje. Části mezi výpustkami po sobě, nejvýš MEZERA_VYPUSTKY znaků od sebe,
    bez záporu v přeskočeném textu a (je-li jich víc) aspoň dvouslovné; část nad STROP po kusech KUS znaků
    těsně za sebou. `presne` jen bez výpustky a když jsou přesné všechny kusy."""
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
    return ("presne" if vsechny_presne and len(casti) == 1 else "priblizne"), zacatek, konec


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
    písmen a mezery, delší přes najdi_citaci. Přibližná shoda, která ve zdroji spojí víc vět, než má citace
    („Já souhlasím s tím. Jinde: zrušením daně“ za „Já souhlasím se zrušením daně“), neplatí.
    Offsety jsou do celého textu."""
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
    if r["shoda"] == "priblizne" and _pocet_vet(r["nalezeny_text"]) > _pocet_vet(cast):
        return _NENALEZENO
    return r["shoda"], od + r["od_znaku"], od + r["do_znaku"]


def _pocet_vet(s: str) -> int:
    """Kolik konců vět je uvnitř úseku (konec na samém konci se nepočítá)."""
    return sum(1 for k in _konce_vet(s, 0, len(s)) if s[k:].strip())


def _zapor(s: str) -> bool:
    """Je v úseku zápor (nikdy, není, nebudu, nesouhlasím…)?"""
    for w in _SLOVO.findall(_bez_diakritiky(s)):
        if (w in _ZAPOR_SLOVA or w.startswith(_ZAPOR_ZACATKY)
                or (w.startswith("ne") and len(w) >= 4 and not w.startswith(_NE_NEZAPOR))):
            return True
    return False


def _popreno_pred(text: str, od: int) -> bool:
    """Těsně před místem ve zdroji (do OKNO_ZAPORU znaků, ve stejné větě) je zápor nebo popření."""
    pred = text[max(0, od - OKNO_ZAPORU):od]
    konce = list(_konce_vet(pred, 0, len(pred)))
    return _zapor(pred[konce[-1]:] if konce else pred)


class _Zdroje:
    """Texty zdrojů a jejich páry uvozovek, každý zdroj načtený a spočítaný jednou na odstavec."""

    def __init__(self, text_zdroje: Callable[[int], str | None]):
        self._text_zdroje = text_zdroje
        self._texty: dict[int, str | None] = {}
        self._uvozovky: dict[int, tuple[list[int], list[tuple[int, int]], list[int]]] = {}

    def text(self, n: int) -> str | None:
        if n not in self._texty:
            self._texty[n] = self._text_zdroje(n)
        return self._texty[n]

    def recnik_cituje(self, n: int, od: int, do: int) -> bool:
        """Místo leží ve zdroji v uvozovkách (i jen začátkem nebo koncem, nebo za nespárovanou uvozovkou
        do OKNO_UVOZOVEK), nebo je u něj „cituji / citace / citoval“."""
        text = self._texty[n]
        if n not in self._uvozovky:
            pary, nesparovane = _uvozovky(text, [])
            self._uvozovky[n] = ([o for o, _ in pary], pary, nesparovane)
        otviraci, pary, nesparovane = self._uvozovky[n]
        k = bisect_left(otviraci, od) - 1
        if k >= 0 and pary[k][1] > od:
            return True             # začátek místa je v uvozovkách
        k = bisect_left(otviraci, do - 1) - 1
        if k >= 0 and pary[k][0] >= od and pary[k][1] > do - 1:
            return True             # konec místa je v uvozovkách, které začínají uvnitř
        j = bisect_left(nesparovane, od) - 1
        if j >= 0 and nesparovane[j] >= od - OKNO_UVOZOVEK:
            return True
        okoli = text[max(0, od - OKNO_CITUJE):od] + " " + text[do:do + OKNO_CITUJE_ZA]
        return bool(_CITUJE.search(_bez_diakritiky(okoli)))


# --- přisouzení v odstavci ---------------------------------------------------------------------

def _recnik(zdroj: Zdroj) -> str:
    """Řečník projevu pro čip: „Jméno (klub)“ z meta, jinak z titulku bez data."""
    if zdroj.meta.get("recnik"):
        klub = zdroj.meta.get("klub")
        return f"{zdroj.meta['recnik']} ({klub})" if klub else str(zdroj.meta["recnik"])
    return _DATUM_TITULKU.sub("", zdroj.titulek).strip()


def _zaznelo_v(zdroj: Zdroj, recnik: str) -> dict:
    """Pro čip „zaznělo v projevu J. Nováka (datum)“ s odkazem do projevu."""
    datum = zdroj.meta.get("datum")
    if not datum and (m := _DATUM_TITULKU.search(zdroj.titulek)):
        datum = m.group(1)
    return {"recnik": recnik, "klic": zdroj.klic, "datum": datum, "url": zdroj.meta.get("url")}


def _slova_jmena(jmeno: str) -> list[str]:
    return [_bez_diakritiky(w) for w in _SLOVO.findall(jmeno) if len(w) > 1]


def _bez_diakritiky(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if not unicodedata.combining(c))


def _je_osoba(jmeno: str, osoba: str) -> bool:
    """Jméno z textu patří osobě: všechna jeho slova jsou ve jméně osoby (bez klubu v závorce)."""
    slova = _slova_jmena(jmeno)
    return bool(slova) and set(slova) <= set(_slova_jmena(_ZAVORKA.sub("", osoba)))


@dataclass(frozen=True)
class _Osoba:
    """Řečník zdroje: tvary příjmení (malými, s diakritikou) a co je potřeba ke kontrole odstavce."""
    cip: str                        # „Jan Novák (ANO)“
    prvni_pad: frozenset[str]       # Novák
    pady: frozenset[str]            # Nováka, Novákovi, Novákem…
    privlastnovaci: frozenset[str]  # Novákův, Novákova…
    kmeny: tuple[str, ...]          # začátek jiných tvarů = jiná osoba (Nováková, Novákovic)
    krestni: tuple[str, ...]        # začátky křestních jmen bez diakritiky (jan, alen)
    zena: bool
    klub: frozenset[str]            # slova klubu, jak se píšou (ANO; TOP, 09)

    def tvar(self, male: str) -> str | None:
        if male in self.prvni_pad:
            return "1"
        if male in self.pady:
            return "pad"
        return "priv" if male in self.privlastnovaci else None

    def je_krestni(self, w: _Token) -> bool:
        if len(w.puvodni) == 1:     # iniciála „J. Novák“
            return any(k[:1] == w.holy for k in self.krestni)
        return any(w.holy.startswith(k) and len(w.holy) - len(k) <= 4 for k in self.krestni)


def _tvary_prijmeni(p: str) -> tuple[set[str], set[str], set[str], tuple[str, ...]]:
    """(1. pád, ostatní pády, přivlastňovací tvary, kmeny jiných tvarů) příjmení malými s diakritikou."""
    if p.endswith("á"):                              # Schillerová, Malá
        k = p[:-1]
        return {p}, {k + "é", k + "ou"}, set(), (p[:-3] if p.endswith("ová") else k,)
    if p.endswith("ý"):                              # Malý
        k = p[:-1]
        return {p}, {k + e for e in ("ého", "ému", "ém", "ým")}, set(), (k,)
    if p.endswith("í"):                              # Krejčí
        return {p}, {p + e for e in ("ho", "mu", "m")}, set(), (p,)
    if p.endswith("a"):                              # Fiala, Juchelka
        k = p[:-1]
        return {p}, {k + e for e in ("y", "ovi", "u", "o", "ou")}, {k + e for e in _PRIVLASTNOVACI}, (k,)
    if p[-1:] in "eiouyéěůú":                        # cizí a nesklonná
        return {p}, {p + e for e in ("ho", "mu", "m", "ovi", "em")}, set(), (p,)
    kmeny = [p] + ([p[:-2] + p[-1]] if re.search(r"e[kcl]$", p) else [])   # Válek → Válka, Holec → Holce
    return ({p}, {k + e for k in kmeny for e in ("a", "e", "ovi", "u", "i", "em")},
            {k + e for k in kmeny for e in _PRIVLASTNOVACI}, tuple(kmeny))


@lru_cache(maxsize=256)
def _osoba(cip: str) -> _Osoba:
    m = _KLUB.search(cip)
    slova = [w for w in _SLOVO.findall(_ZAVORKA.sub("", cip)) if len(w) > 1]
    p = unicodedata.normalize("NFC", slova[-1].lower()) if slova else ""
    jmena = [_bez_diakritiky(w) for w in slova[:-1]]
    krestni = tuple(j[:-1] if len(j) > 3 and j[-1] in "aeiouy" else j for j in jmena)
    prvni, pady, priv, kmeny = _tvary_prijmeni(p) if p else (set(), set(), set(), ())
    return _Osoba(cip, frozenset(prvni), frozenset(pady), frozenset(priv), tuple(k for k in kmeny if len(k) >= 3),
                  krestni, p.endswith("á") or any(j.endswith("a") for j in jmena),
                  frozenset(_SLOVO.findall(unicodedata.normalize("NFC", m.group(1)))) if m else frozenset())


@dataclass
class _Token:
    puvodni: str
    male: str       # malými, NFC
    holy: str       # malými bez diakritiky
    od: int         # pozice v odstavci
    do: int


def _nesedi_rod(koncovka: str, zena: bool) -> bool:
    """Sloveso mluvení nesedí na řečníka: množné číslo, nebo jiný rod („řekla“ u muže)."""
    if koncovka in ("i", "y"):
        return True
    if koncovka == "a":
        return not zena
    return zena if koncovka == "" else False    # -o: „zaznělo“


def _soucty(hodnoty: list[bool]) -> list[int]:
    out = [0]
    for h in hodnoty:
        out.append(out[-1] + h)
    return out


class _Kontext:
    """Odstavec rozdělený na úseky mimo citáty (každý úsek leží v jedné větě); rozbor úseku se počítá
    jednou na odstavec (a řečníka), okno citátu se sčítá z prefixových součtů."""

    def __init__(self, s: str, citaty: list[_Citat], vety: _Vety, jmena_osob: Callable[[str], list[str]]):
        self.s = s
        self._jmena_osob = jmena_osob
        rozsahy = [(c.od, c.konec) for c in citaty]
        # konce vět mimo citáty (tečka uvnitř citátu větu odstavce nekončí)
        self.konce: list[int] = []
        j = 0
        for k in vety.konce:
            while j < len(rozsahy) and rozsahy[j][1] < k:
                j += 1
            if not (j < len(rozsahy) and rozsahy[j][0] < k <= rozsahy[j][1]):
                self.konce.append(k)
        hranice = sorted({0, len(s), *self.konce, *(x for r in rozsahy for x in r)})
        self.useky: list[tuple[int, int, int]] = []     # (od, do, věta)
        j = 0
        for a, b in zip(hranice, hranice[1:]):
            while j < len(rozsahy) and rozsahy[j][1] <= a:
                j += 1
            if not (j < len(rozsahy) and rozsahy[j][0] <= a):
                self.useky.append((a, b, bisect_right(self.konce, a)))
        starty = [u[0] for u in self.useky]
        vety_useku = [u[2] for u in self.useky]
        vety_citatu = [bisect_right(self.konce, c.od) for c in citaty]
        s_citatem = set(vety_citatu)
        self._maska_dal = [u[2] not in s_citatem for u in self.useky]
        # okno citátu: (úseky věty před citátem, úseky za ním do dalšího citátu nebo konce věty, další věty)
        self._okna: list[tuple[int, int, int, int, int]] = []
        for i, c in enumerate(citaty):
            v = vety_citatu[i]
            dalsi_veta = bisect_left(vety_useku, v + 1)
            konec_za = citaty[i + 1].od if i + 1 < len(citaty) else len(s)
            za = bisect_left(starty, c.konec)
            prvni = bisect_left(vety_useku, v)
            self._okna.append((prvni, max(prvni, bisect_left(starty, c.od)), za,
                               max(za, min(bisect_left(starty, konec_za), dalsi_veta)), dalsi_veta))
        self._jmena: dict[int, list[str]] = {}
        self._tokeny: dict[int, list[_Token]] = {}
        self._rozbory: dict[str, tuple[list[int], list[int], list[int]]] = {}
        self._osoby_cel: tuple[list[int], list[int]] | None = None

    # --- okna ---

    def nikdo_jiny(self, i: int, o: _Osoba) -> bool:
        """Odstavec u citátu i nejmenuje nikoho jiného než řečníka o (viz popis modulu)."""
        recnik, jiny, jiny_dal = self._rozbor_osoby(o)
        a, b, c, d, e = self._okna[i]
        if recnik[b] - recnik[a] + recnik[d] - recnik[c]:
            # věta s citátem řečníka jmenuje: stačí ona a další věty bez vlastního citátu
            return jiny[b] - jiny[a] + jiny[d] - jiny[c] + jiny_dal[e] == 0
        return jiny[-1] == 0            # nejmenuje: kdokoli v odstavci může být mluvčí

    def bez_osoby(self, i: int) -> bool:
        """Věta s citátem textu zákona (a další věty bez citátu) ho nepřisuzuje osobě (K2)."""
        if self._osoby_cel is None:
            priznaky = [bool(_OSOBA_SLOVA.search(_bez_diakritiky(self.s[u[0]:u[1]])) or self._jmena_useku(k)
                             or self._nezname_jmeno(k, None))
                        for k, u in enumerate(self.useky)]
            self._osoby_cel = (_soucty(priznaky), self._soucty_dal(priznaky))
        osoba, dal = self._osoby_cel
        a, b, c, d, e = self._okna[i]
        return osoba[b] - osoba[a] + osoba[d] - osoba[c] + dal[e] == 0

    def _soucty_dal(self, priznaky: list[bool]) -> list[int]:
        """Součty od úseku k do konce, jen úseky ve větách bez citátu."""
        out = [0] * (len(priznaky) + 1)
        for k in range(len(priznaky) - 1, -1, -1):
            out[k] = out[k + 1] + (priznaky[k] and self._maska_dal[k])
        return out

    def _rozbor_osoby(self, o: _Osoba) -> tuple[list[int], list[int], list[int]]:
        if o.cip not in self._rozbory:
            rozbor = [self._rozbor_useku(k, o) for k in range(len(self.useky))]
            jiny = [j for _, j in rozbor]
            self._rozbory[o.cip] = (_soucty([r for r, _ in rozbor]), _soucty(jiny), self._soucty_dal(jiny))
        return self._rozbory[o.cip]

    # --- úsek ---

    def _jmena_useku(self, k: int) -> list[str]:
        if k not in self._jmena:
            text = self.s[self.useky[k][0]:self.useky[k][1]]
            self._jmena[k] = self._jmena_osob(text) if _SLOVO.search(text) else []
        return self._jmena[k]

    def _tokeny_useku(self, k: int) -> list[_Token]:
        if k not in self._tokeny:
            od, do, _ = self.useky[k]
            self._tokeny[k] = [_Token(m.group(), unicodedata.normalize("NFC", m.group().lower()),
                                      _bez_diakritiky(m.group()), m.start(), m.end())
                               for m in _SLOVO.finditer(self.s, od, do)]
        return self._tokeny[k]

    def _rozbor_useku(self, k: int, o: _Osoba) -> tuple[bool, bool]:
        """(úsek jmenuje řečníka jako mluvčího, úsek jmenuje někoho jiného) pro řečníka o."""
        t = self._tokeny_useku(k)
        jiny = any(not _je_osoba(j, o.cip) for j in self._jmena_useku(k))
        recnik = False
        pristavek: set[int] = set()     # role a kluby v přístavku u jména řečníka
        for j, w in enumerate(t):
            if not w.puvodni[0].isupper():
                continue
            druh = o.tvar(w.male)
            if druh is None:
                if any(w.male.startswith(km) for km in o.kmeny):
                    jiny = True         # Nováková, Novákovic: jiná osoba
                continue
            zacatek = self._pristavek_pred(t, j, o)
            pristavek.update(range(zacatek, j))
            pristavek.update(self._pristavek_za(t, j, o))
            # klub řečníka hned za jménem: „Novák (ANO)“, „Novák za ANO“
            pristavek.update(x for x in range(j + 1, min(j + 4, len(t))) if t[x].puvodni in o.klub)
            pred = self._predchozi(t, zacatek, inicialy=True)
            if pred is not None and t[pred].puvodni[0].isupper() and (
                    self._cizi_krestni(t[pred]) or not self.s[t[pred].do:t[zacatek].od].isspace()):
                jiny = True             # Karel Novák, K. Novák (iniciála s tečkou) u řečníka Jana Nováka
            elif druh != "1" and not self._ramec(t, zacatek, j):
                jiny = True             # řečník jako předmět: „Na Nováka reagoval“, „Novákovi odpověděl“
            elif not (w.holy in _BEZNA_SLOVA and self._na_zacatku_vety(k, w)):
                recnik = True
        for j, w in enumerate(t):
            if j in pristavek or (w.puvodni[0].isupper() and o.tvar(w.male)):
                continue
            if (_ROLE.fullmatch(w.holy) or w.holy in _ZAJMENA or w.puvodni in _STRANY
                    or w.puvodni in o.klub):
                jiny = True             # role, instituce, zájmeno nebo klub místo jména („ANO tvrdí“)
            elif (m := _MLUVENI.fullmatch(w.holy)) and _nesedi_rod(m.group(2) or "", o.zena):
                jiny = True             # „řekla“ u řečníka Nováka, „řekli“
        return recnik, jiny or self._nezname_jmeno(k, o)

    def _nezname_jmeno(self, k: int, o: _Osoba | None) -> bool:
        """Úsek jmenuje osobu, kterou jmena_osob nezná (ministr mimo Sněmovnu, zahraniční politik): slovo
        s velkým písmenem uprostřed věty, dvě taková slova za sebou (i na začátku věty), nebo u řečníka o první
        slovo věty, za kterým do tří slov stojí sloveso mluvení („Kříž řekl“, „Trump prohlásil“, „Kříž said“).
        Nepočítají se běžná slova, instituce, tituly, role, zkratky, iniciály a jméno řečníka o."""
        t = self._tokeny_useku(k)
        nezname = [self._nezname(w, o) for w in t]
        for j, w in enumerate(t):
            if not nezname[j]:
                continue
            if not self._na_zacatku_vety(k, w):
                return True
            dalsi = j + 1 if j + 1 < len(t) and self.s[w.do:t[j + 1].od].isspace() else None
            if dalsi is not None and nezname[dalsi]:
                return True         # „Pavel Kříž řekl“
            if o is not None and self._mluveni_za(t, j):
                return True         # „Kříž řekl“ (u textu zákona hlídá sloveso už _OSOBA_SLOVA)
        return False

    def _nezname(self, w: _Token, o: _Osoba | None) -> bool:
        p, h = w.puvodni, w.holy
        if not p[0].isupper() or len(p) == 1 or p.isupper() or any(ch.isdigit() for ch in p):
            return False            # malé písmeno, iniciála, zkratka (ČNB, DPH), číslo
        if (h in _NEJSOU_KRESTNI or h in _TITULY or h in _OSLOVENI or h in _RAMCE or _INSTITUCE.fullmatch(h)
                or _ROLE.fullmatch(h) or p in _STRANY):
            return False            # role a jiné kluby hlídá _rozbor_useku zvlášť
        return o is None or not (o.tvar(w.male) or o.je_krestni(w) or p in o.klub
                                 or any(w.male.startswith(km) for km in o.kmeny))

    def _mluveni_za(self, t: list[_Token], j: int) -> bool:
        """Do tří slov za slovem j (jen mezery mezi nimi) je sloveso mluvení."""
        for x in range(j + 1, min(j + 4, len(t))):
            if not self.s[t[x - 1].do:t[x].od].isspace():
                return False
            if _MLUVENI.fullmatch(t[x].holy) or t[x].holy in _MLUVENI_BEZ_RODU:
                return True
        return False

    def _predchozi(self, t: list[_Token], j: int, inicialy: bool = False) -> int | None:
        """Slovo těsně před slovem j (jen mezera mezi nimi; s inicialy i iniciála s tečkou „K. Novák“)."""
        if j == 0:
            return None
        mezera = self.s[t[j - 1].do:t[j].od]
        if mezera.isspace():
            return j - 1
        jedno = inicialy and len(t[j - 1].puvodni) == 1 and _MEZERA_TECKY.fullmatch(mezera)
        return j - 1 if jedno else None

    def _pristavek_pred(self, t: list[_Token], j: int, o: _Osoba) -> int:
        """Začátek přístavku před jménem řečníka („ministr financí Jan“, „pan poslanec“, „Ing.“)."""
        zacatek = j
        while zacatek > 0 and j - zacatek < OKNO_PRISTAVKU:
            w = t[zacatek - 1]
            mezera = self.s[w.do:t[zacatek].od]
            tecka = w.holy in _TITULY or (len(w.puvodni) == 1 and w.puvodni.isupper())
            if not (mezera.isspace() or (tecka and _MEZERA_TECKY.fullmatch(mezera)) or mezera == "-"):
                break
            if not (o.je_krestni(w) or _ROLE.fullmatch(w.holy) or w.holy in _DOPLNKY or w.puvodni in _STRANY
                    or w.puvodni in o.klub or w.holy in _OSLOVENI or w.holy in _TITULY):
                break
            zacatek -= 1
        return zacatek

    def _pristavek_za(self, t: list[_Token], j: int, o: _Osoba) -> range:
        """Přístavek za jménem řečníka: „Novák, ministr financí, …“, „Novák jako zpravodaj …“."""
        if j + 1 >= len(t):
            return range(0)
        mezera = self.s[t[j].do:t[j + 1].od]
        if re.fullmatch(r"\s*,\s*", mezera):
            zacatek = j + 1
        elif t[j + 1].holy == "jako" and mezera.isspace():
            zacatek = j + 2
        else:
            return range(0)
        konec = zacatek
        while (konec < len(t) and konec - zacatek < OKNO_PRISTAVKU
               and (konec == zacatek or self.s[t[konec - 1].do:t[konec].od].isspace())
               and (_ROLE.fullmatch(t[konec].holy) or t[konec].holy in _DOPLNKY or t[konec].puvodni in _STRANY
                    or t[konec].puvodni in o.klub)):
            konec += 1
        # přístavek za čárkou končí čárkou („Novák, ministryně Malá: …“ přístavek není)
        if t[j + 1].holy != "jako" and konec < len(t) and "," not in self.s[t[konec - 1].do:t[konec].od]:
            return range(0)
        return range(zacatek, konec)

    def _cizi_krestni(self, w: _Token) -> bool:
        """Slovo s velkým písmenem těsně před přístavkem nebo jménem řečníka je jiné křestní jméno."""
        return not (w.holy in _NEJSOU_KRESTNI or w.holy in _RAMCE or _ROLE.fullmatch(w.holy)
                    or w.holy in _ZAJMENA or w.puvodni in _STRANY or _INSTITUCE.fullmatch(w.holy))

    def _ramec(self, t: list[_Token], zacatek: int, j: int) -> bool:
        """Řečník v jiném pádě je mluvčí jen v rámci „podle / slovy Nováka“, „v projevu Nováka“,
        „Schillerové slova“, „Novákův projev“, a ne po předložce („Na projev Nováka reagoval“)."""
        pred = self._predchozi(t, zacatek)
        if pred is not None and t[pred].holy in ("podle", "dle"):
            return True
        if pred is not None and t[pred].holy in _RAMCE:        # i „slovy“ („se slovy Nováka“ ne)
            return not self._po_predlozce(t, pred)
        za = j + 1 if j + 1 < len(t) and self.s[t[j].do:t[j + 1].od].isspace() else None
        return za is not None and t[za].holy in _RAMCE and not self._po_predlozce(t, zacatek)

    def _po_predlozce(self, t: list[_Token], j: int) -> bool:
        """Některé ze tří slov těsně před slovem j je předložka z _PREDLOZKY."""
        for _ in range(3):
            p = self._predchozi(t, j)
            if p is None:
                return False
            if t[p].holy in _PREDLOZKY:
                return True
            j = p
        return False

    def _na_zacatku_vety(self, k: int, w: _Token) -> bool:
        """Slovo je první ve větě nebo na řádku (velké písmeno nic neříká)."""
        veta = self.useky[k][2]
        zacatek = max(self.konce[veta - 1] if veta else 0, self.s.rfind("\n", 0, w.od) + 1)
        return not _SLOVO.search(self.s, zacatek, w.od)
