"""Kontrola, že texty Reels nepřisuzují výrok ani motiv osobě a u navrhovatelů uvádějí „a N dalších“.

Rozhodnutí majitele (2026-11-28): text z projevu jen ve tvaru „V projevu J. Nováka 12. 11. zaznělo: „…““ se
záznamem v `citace_projevu`; nikdy „X řekl“, „X tvrdí“, „podle X“. U více navrhovatelů vždy „a N dalších“.

Pravidlo je pozitivní (seznamy zakázaných obratů nekonvergují):
- R8: volný text od modelu nesmí jmenovat osobu. Za jméno se bere slovo s velkým písmenem mimo začátek věty, které
  není instituce, zkratka ani dokument, a každé jméno z karty v jakémkoli pádě. Osoby se do textu dostanou jen
  strukturovaně ze serveru: autor PN nebo předkladatel celým jménem se značkou „a N dalších“ (údaje serveru) a úvod
  projevu se záznamem v `citace_projevu`.
- R1: sloveso mluvení nebo hodnocení smí stát jen ve větě, jejímž podmětem je dokument nebo instituce na začátku
  věty („Návrh uvádí…“, „ČNB varuje…“), a ve větě nesmí být osoba ani role.
- R2: „podle“ jen u dokumentu, dat nebo instituce („Podle ČSÚ“, „podle PN 2376“), ne u osoby nebo role.
- R3 „Jméno: …“, R4 uvozovky bez zdroje, R5 úvod projevu bez záznamu, R6 citace projevu v prvním odstavci, R7 motiv.

Text se strojově nepřepisuje: chyba jde do opravného běhu generátoru, nebo varianta vypadne; bot podle téže
kontroly položku nezveřejní. Modul volá generátor i bot, a proto závisí jen na `re` a `caption.py`.
"""
from __future__ import annotations

import re

from . import caption as _cap

VOWELS = "aeiouyáéíóúůýě"
UPPER = "A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"

# slovesa mluvení a hodnocení (kmeny). Úkony (podal, předložil, navrhl, hlasoval, schválil, zamítl…) a budoucí
# povinnosti („oznámí“, „upozornit“, „uvádět“) zůstávají povolené.
SAY = re.compile(
    r"(?<!\w)(?:řekl|říká|říkal|říci|uvedl|uvádí|uváděj|tvrdí|tvrdil|tvrdit|prohlásil|prohlašuj|"
    r"dodal[aiy]?\b|dodává|dodávají|upozornil|upozorňuj|varoval|varuj|namítl|namítá|namítal|kritizoval|kritizuj|"
    r"kritizoval|označil|označuj|nazval|nazýv|obvinil|obviňuj|slíbil|slibuj|přiznal|přiznáv|připustil|připouští|"
    r"vysvětlil|vysvětluj|vysvětloval|argumentuj|argumentoval|zdůraznil|zdůrazňuj|poznamenal|oznámil|"
    r"vzkázal|vzkazuj|odpověděl|odpovídal|reagoval|reaguj|hájí|hájil|obhajuj|obhajoval|zdůvodňuj|zdůvodnil|"
    r"odůvodňuj|odůvodnil|míní|mínil|domnív|píše|psal|věří|věřil|mluvil|mluví|hovořil|hovoří|zmínil|"
    r"odmítá|odmítl|odmítal|chválí|chválil|zaznělo|zaznívá|zaznívalo|vidí\s+jako)\w*",
    re.IGNORECASE)
# role osob („vládní“, „poslanecký“, „senátní“ jsou přívlastky návrhu, ne osoba; „autorizovaný“ není autor)
ROLE = re.compile(r"(?<!\w)(?:poslan(?!eck)|europoslan|ministr|premiér|senátor(?!sk)|zpravodaj|předkladatel|"
                  r"navrhovatel|opozic|opozič|koalic|koalič|mluvčí|expert|šéf|předsed|místopředsed|hejtman|starost|náměst|lídr|"
                  r"autor(?!iz))\w*", re.IGNORECASE)
CLUBS = ("ANO", "ODS", "STAN", "Piráti", "Pirátů", "Pirátům", "SPD", "TOP 09", "KDU-ČSL", "Motoristé", "Motoristů",
         "KSČM", "STAČILO!")
CLUB_WORDS = {"ANO", "ODS", "STAN", "SPD", "TOP", "KDU-ČSL", "KSČM", "STAČILO"}
# podmět dokument nebo instituce (R1)
DOC = re.compile(r"(?:zákon|návrh|novel|ustanoven|čl\.|§|důvodov|jednací|ústav|PN|tisk|usnesen|rozbor|AI|"
                 r"text|znění|nařízen|směrnic|odůvodněn|zpráv|data|evidenc|harmonogram|program|pravidl|předpis|"
                 r"smlouv|odstav|paragraf|bod|změn|úprav|rozpoč|vyhlášk|metodik|studi|analýz|statistik|"
                 r"příloh|tabulk|přechodn)", re.IGNORECASE)
INSTITUTION = re.compile(r"(?:soud|úřad|komis|bank|výbor|sněmovn|senát|rad[ay]?\b|fond|inspekc|agentur|správ|"
                         r"eurostat|oecd|kancelář|ombudsman)", re.IGNORECASE)
# slova s velkým písmenem, která nejsou jména osob (kmeny malými písmeny): instituce, místa, dokumenty, jednotky
ALLOW_CAPS = ("ústav", "sněmovn", "senát", "parlament", "vlád", "ministerstv", "soud", "úřad", "komis", "evrop",
              "česk", "čr", "republik", "rad", "fond", "ban", "nejvyšš", "národn", "státn", "krajsk", "kraj",
              "zastupitel", "sbírk", "kč", "sb", "eur", "prah", "brn", "ostrav", "plz", "olomou", "liber",
              "polsk", "polsku", "německ", "slovensk", "rakousk", "maďar", "ukrajin", "rusk", "unie", "eurostat",
              "nato", "osn", "covid", "hodnotíme", "pozměňovac", "zákon", "zákoník", "nové", "agentur", "inspekc",
              "správ", "kancelář", "generální", "finanční", "celní", "policie", "armád", "hasič", "česká",
              "středočesk", "jihomoravsk", "moravskoslezsk", "ústeck", "plzeňsk", "jihočesk", "královéhradeck",
              "pardubick", "olomouck", "zlínsk", "karlovarsk", "libereck", "vysočin", "facebook", "instagram",
              "threads", "telegram", "lidic", "státního", "dotac", "společn", "velk", "malý", "malé", "hlavní",
              "intervenční", "zemědělsk", "bezpečnostn", "informační", "vojensk", "sociáln", "zdravotn", "všeobecn",
              "pojišťovn", "česko", "rozpočtov", "ústřední", "obecn", "veřejn", "ochránc", "kontroln", "statistick",
              "energetick", "regulační", "telekomunikační", "rozhlas", "televiz", "pošt", "dráh", "lesy", "povodí",
              "vodní", "silnic", "dálnic", "ředitelstv", "inspektorát", "vězeňsk", "probační", "akademi",
              "univerzit", "vysok", "nemocnic", "fakultní", "lékařsk", "advokátní", "notářsk", "exekutorsk",
              "komor", "svaz", "asociac", "hospodářsk", "obchodní", "průmysl", "agrární", "odborov", "konfederac",
              "gdpr", "dph", "ičo", "es", "eu", "mfčr", "uk", "usa", "sk", "de", "at", "pl", "hu", "ua", "ru")
# podstatná jména institucí a míst: slovo s velkým písmenem před nimi je součást názvu, ne jméno osoby
INSTITUTION_HEAD = ("agentu", "rad", "úřad", "fond", "komis", "soud", "správ", "inspekc", "nadac", "spol",
                    "památník", "univerzit", "nemocnic", "ústav", "centr", "institut", "knihovn", "muze", "divadl",
                    "nádraž", "služb", "společnost", "svaz", "komor", "výbor", "kancelář", "ministerstv", "škol",
                    "akademi", "galeri", "archiv", "park", "ředitelstv", "inspektorát", "obec", "obc", "měst", "kraj",
                    "řek", "řec", "zákon", "program", "projekt", "strategi", "plán", "hnutí", "sektor", "operac")
PODLE = re.compile(r"(?<!\w)(?P<kw>podle|dle|slovy|z\s+úst)\s+(?P<w1>[^\s]+)(?:\s+(?P<w2>[^\s]+))?", re.IGNORECASE)
PODLE_OK = ("§", "zákon", "jednací", "ústav", "nařízen", "čl", "ai", "dat", "histori", "psp", "důvodov", "rozbor",
            "evidenc", "text", "znění", "pravid", "směrnic", "usnesen", "předpis", "smlouv", "odst", "bod",
            "harmonogram", "program", "stav", "hlasován", "počt", "výsledk", "čísl", "kalendář", "lhůt", "tisk",
            "sbírk", "věstník", "metodik", "stenozáznam", "záznam", "návrh", "novel", "úprav", "změn", "odůvodněn",
            "pn", "rozpočt", "přílohy", "tabulk", "odhad", "propočt", "statistik", "údaj", "všeho", "plán")
PRONOUNS = ("něj", "ní", "nich", "jeho", "její", "jejích", "jejich", "svých", "svého", "mne", "nás", "vás", "něho")
SPEECH_NOUNS = ("slov", "názor", "vyjádř", "mínění", "projev", "výrok", "tvrzen")
PREPOSITIONS = {"pro", "k", "ke", "o", "u", "s", "se", "v", "ve", "na", "za", "od", "do", "po", "při", "z", "ze"}
COLON_QUOTE = re.compile(r"\s*:|\s*[–—-]\s*[„\"]")
QUOTE = re.compile(r"„([^„“]*(?:„[^„“]*“[^„“]*)*)“|\"([^\"]+)\"")
QUOTE_CHARS = re.compile(r"[„“”\"‚‘’']")
# R7: motiv (obcházení zákona, „aby“ a „chce“ u dokumentu jsou věcný popis, ne motiv)
MOTIVE = re.compile(r"(?<!\w)(?:obešl\w*|obešel|protlač\w*|propaš\w*|potichu|tajně|nenápadně|účelově|schválně|"
                    r"záměrně)(?!\w)", re.IGNORECASE)
INTENT = re.compile(r"(?<!\w)(?:chce|chtějí|chtěl\w*|snaží\s+se|snažil\w*\s+se|se\s+snaží|se\s+snažil\w*)(?!\w)",
                    re.IGNORECASE)
INTENT_ACTOR = re.compile(r"(?<!\w)(?:vlád(?!ní)|koalic|koalič|opozic|opozič)\w*", re.IGNORECASE)
# úvod projevu (kanonický tvar)
LEAD = re.compile(
    r"(?<!\w)[Vv] projevu (?P<kdo>[^\d\n„“]{2,60}?) (?P<den>\d{1,2})\.\s?(?P<mesic>\d{1,2})\.(?:\s?\d{4})?"
    r"(?P<neaut>\s*\(neautorizovaný záznam\))?\s+zaznělo"
    r"(?:(?P<dosl>:\s*„(?P<citat>[^“]+)“)|(?P<shr>,\s*že\b))?")
MARKER = re.compile(r"(?<!\w)(?:(\d+)\s+dalš\w*|dalš\w*\s+(\d+))", re.IGNORECASE)
SOME_MORE = re.compile(r"(?<!\w)(?:a|s|se)\s+dalš\w*", re.IGNORECASE)
EVIDENCE = "podle evidence psp.cz"
AI_WORDS = re.compile(r"(?<!\w)(?:přílep\w*|závažnost\w*|mimo\s+předmět)", re.IGNORECASE)
WORD = re.compile(r"[^\W\d_][\w-]*")
MARKER_WINDOW = 60
MIN_QUOTE_WORDS = 4
ABBREV = {"čl", "odst", "písm", "tj", "tzv", "mld", "mil", "např", "resp", "č", "sb", "tis", "str", "ing", "mgr",
          "doc", "prof", "dr", "bc", "kč", "hod", "min", "zejm", "atd", "apod", "příp", "max"}

# pole karty, která nejsou volný text modelu: odkazy, klíče, data, údaje ze serveru a doslovné citace
SKIP_KEYS = {"url", "url_psp", "klic", "key", "quote", "citace", "reasons", "predkladatel", "pn_autor", "nazev",
             "citace_projevu", "ct", "format", "faze", "score", "verified", "data_date", "quote_source",
             "typ_navrhu", "predlozeno", "kdo", "autor", "jmeno", "klub", "kluby", "recnik", "datum", "date",
             "cislo", "podano", "stav", "typ", "zdroj", "hlasovani", "spolu", "count", "ai", "pn",
             "own", "hot"}
# blok `pn` (formát pn): volný text jen tyto položky; pn.top[].autor a pruhy autorů jsou jména
PN_TEXT_KEYS = {"top", "titulek", "note", "sub", "pozn", "popis", "lab", "text"}


def _norm(text: str) -> str:
    """Sjednocení pro porovnání citací: bez uvozovek, mezery po jedné, malá písmena, bez koncové interpunkce."""
    text = QUOTE_CHARS.sub("", text)
    return re.sub(r"\s+", " ", text).strip(" .,;:…").casefold()


def _surname_stem(jmeno: str) -> str:
    """Kmen příjmení, který zůstane i ve skloňovaném tvaru (stejně jako generator.validate._surname_stem)."""
    surname = jmeno.split()[-1].split("-")[0]
    low = surname.lower()
    if len(low) >= 2 and low[-2] == "e" and low[-1].isalpha() and low[-1] not in VOWELS:
        stem = surname[:-2]
    elif low and low[-1] in VOWELS:
        stem = surname[:-1]
    else:
        stem = surname
    return stem if len(stem) >= 3 else surname[:3]


def _stem_re(jmeno: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(_surname_stem(jmeno))}\w*")


def _sentences(text: str) -> list[tuple[int, str]]:
    """Věty textu jako (začátek, věta). Konec věty: . ! ? … před mezerou a velkým písmenem nebo uvozovkou
    (i za číslem: „v roce 2027. Hrnčíř…“), nový řádek; ne za zkratkou ani za jedním písmenem („J. Nováka“,
    „čl. VI“). Datum „12. 11.“ se nedělí (za tečkou je číslice)."""
    out: list[tuple[int, str]] = []
    start = 0
    for m in re.finditer(rf"(?<=[.!?…“])\s+(?=[{UPPER}„])|\n+", text):
        if not m.group().startswith("\n"):
            before = text[start:m.start()]
            word = re.search(r"(\w+)\.$", before)
            if word and not word.group(1).isdigit() and (len(word.group(1)) == 1
                                                          or word.group(1).lower() in ABBREV):
                continue
        if text[start:m.start()].strip():
            out.append((start, text[start:m.start()]))
        start = m.end()
    if text[start:].strip():
        out.append((start, text[start:]))
    return out


def _snippet(text: str, limit: int = 90) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _is_club(word: str) -> bool:
    return word in CLUB_WORDS or word.rstrip("!") in CLUB_WORDS or any(word == c for c in CLUBS)


def _allowed_caps(word: str) -> bool:
    """Slovo s velkým písmenem, které není jméno osoby: zkratka, klub, instituce, místo, dokument."""
    if len(word) <= 1 or word.isupper() or _is_club(word):
        return True
    low = word.lower()
    return any(low.startswith(a) for a in ALLOW_CAPS)


def speech_leads(text: str) -> list[dict]:
    """Výskyty úvodu projevu: {start, end, kdo, den, mesic, doslovne, citat, neautorizovano}."""
    out = []
    for m in LEAD.finditer(text):
        out.append({"start": m.start(), "end": m.end(), "kdo": m["kdo"].strip(), "den": int(m["den"]),
                    "mesic": int(m["mesic"]), "doslovne": m["dosl"] is not None, "citat": m["citat"] or "",
                    "neautorizovano": m["neaut"] is not None})
    return out


def _speech_match(lead: dict, sp: dict) -> str | None:
    """Odpovídá úvod projevu položce `citace_projevu`? None = ano, jinak důvod."""
    names = sp.get("recnik")
    names = names if isinstance(names, list) else [names]
    names = [n for n in names if isinstance(n, str) and n.strip()]
    if not names or not any(_stem_re(n).search(lead["kdo"]) for n in names):
        return "jiný řečník"
    m = re.match(r"^\d{4}-(\d{2})-(\d{2})$", str(sp.get("datum") or ""))
    if not m or (int(m[2]), int(m[1])) != (lead["den"], lead["mesic"]):
        return "jiné datum"
    if lead["doslovne"]:
        if sp.get("doslovne") is not True or _norm(lead["citat"]) not in _norm(str(sp.get("text") or "")):
            return "jiný text"
    return None


def _window(text: str, end: int) -> str:
    """Text za jménem do MARKER_WINDOW znaků, jen do konce věty nebo čárky mimo závorku (vedlejší věta)."""
    depth = 0
    out = []
    for i, ch in enumerate(text[end:end + MARKER_WINDOW]):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "\n" or (depth == 0 and ch in ",;:"):
            break
        elif ch in ".!?…" and depth == 0:
            nxt = text[end + i + 1:end + i + 2]
            if nxt in ("", " ", "\n"):
                break
        out.append(ch)
    return "".join(out)


def _dalsi_word(n: int) -> str:
    return "další poslanec" if n == 1 else "další poslanci" if 2 <= n <= 4 else "dalších poslanců"


def _marker_at(text: str, end: int, where: str, jmeno: str, n, unknown: str) -> list[str]:
    """Značka dalších navrhovatelů za jménem končícím na `end` (ve stejné větě, mimo vedlejší větu).

    n > 0 = „a N dalších“ se správným N; n None = neznámý počet: unknown "evidence" (PN: „podle evidence psp.cz“)
    nebo "dalsi" (tisk: „a další poslanci“); n 0 = bez značky."""
    if n == 0:
        return []
    win = _window(text, end)
    if n is None:
        if unknown == "evidence":
            if EVIDENCE in win:
                return []
            return [f"{where}: u „{jmeno}“ počet spolupodepsaných nejde určit; hned za jméno (a klub) patří "
                    f"„{EVIDENCE}“ ve stejné větě, např. „{jmeno} (ANO, {EVIDENCE})“."]
        if SOME_MORE.search(win) or MARKER.search(win):
            return []
        return [f"{where}: u „{jmeno}“ chybí „a další poslanci“ hned za jménem (a klubem) ve stejné větě."]
    nums = [int(a or b) for a, b in MARKER.findall(win)]
    if n in nums:
        return []
    if nums:
        return [f"{where}: u „{jmeno}“ je špatný počet dalších ({nums[0]}), podle dat Sněmovny {n}."]
    return [f"{where}: u „{jmeno}“ chybí „a {n} {_dalsi_word(n)}“ hned za jménem (a klubem) ve stejné větě."]


def _marker_errors(text: str, where: str, jmeno: str, n, unknown: str) -> list[str]:
    """Značka u prvního jmenování osoby (normalizace podle toho doplňuje popisek)."""
    if not isinstance(text, str) or not isinstance(jmeno, str) or not jmeno.strip() or n == 0:
        return []
    m = _stem_re(jmeno).search(text)
    return [] if m is None else _marker_at(text, m.end(), where, jmeno, n, unknown)


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def cosigner_errors(text: str, *, where: str, authors: list[dict]) -> list[str]:
    """Autoři PN ze serveru (`pn_autor[]` s klíčem `spolu`): u jména „a N dalších“, při neznámém počtu
    „podle evidence psp.cz“. Bez klíče `spolu` (starší server) se nic nekontroluje."""
    errs: list[str] = []
    seen = set()
    for a in authors or []:
        if not isinstance(a, dict) or "spolu" not in a or a.get("jmeno") in seen:
            continue
        seen.add(a.get("jmeno"))
        errs += _marker_errors(text, where, a.get("jmeno"), _count(a["spolu"]), "evidence")
    return errs


def proposer_errors(text: str, *, where: str, proposer: dict | None) -> list[str]:
    """Předkladatel poslaneckého návrhu se skupinou (`vice`): u jména „a N dalších“ (`dalsi`),
    při neznámém počtu „a další poslanci“."""
    if not isinstance(proposer, dict) or proposer.get("vice") is not True:
        return []
    return _marker_errors(text, where, proposer.get("jmeno"), _count(proposer.get("dalsi")), "dalsi")


def mentions_of_card(card: dict) -> list[dict]:
    """Osoby, které smí text jmenovat (strukturovaně ze serveru): autoři PN (`pn_autor`) a poslanecký předkladatel.
    {"jmeno", "n" (počet dalších; 0 = bez značky), "unknown" ("evidence" | "dalsi")}."""
    out: list[dict] = []
    if not isinstance(card, dict):
        return out
    for a in card.get("pn_autor") or []:
        if isinstance(a, dict) and isinstance(a.get("jmeno"), str) and a["jmeno"].strip():
            n = _count(a["spolu"]) if "spolu" in a else 0
            out.append({"jmeno": a["jmeno"].strip(), "n": n, "unknown": "evidence"})
    who = card.get("predkladatel")
    if (isinstance(who, dict) and str(who.get("typ", "")).startswith("Poslanec")
            and isinstance(who.get("jmeno"), str) and who["jmeno"].strip()):
        n = _count(who.get("dalsi")) if who.get("vice") is True else 0
        out.append({"jmeno": who["jmeno"].strip(), "n": n, "unknown": "dalsi"})
    return out


def _full_name_before(text: str, start: int, jmeno: str) -> int | None:
    """Začátek celého jména, když před příjmením na `start` stojí ostatní části jména osoby v jakémkoli pádě
    („Ireny Ferčíkové Konečné“); každá část se porovná prvními třemi písmeny."""
    parts = jmeno.split()
    pos = start
    for part in reversed(parts[:-1]):
        m = re.search(r"([^\W\d_][\w-]*)\s+$", text[:pos])
        if not m or m.group(1)[:3] != part[:3]:
            return None
        pos = m.start(1)
    return pos


def _person_check(text: str, where: str, people: list[str], mentions: list[dict],
                  leads: list[tuple[int, int]]) -> tuple[list[str], list[tuple[int, int]]]:
    """R8 pro jména z karty: každý výskyt (v jakémkoli pádě) jen strukturovaně. Vrací (chyby, úseky jmen)."""
    errs: list[str] = []
    spans: list[tuple[int, int]] = []
    by_name = {m["jmeno"]: m for m in mentions}
    for p in people:
        if not p or not p.strip():
            continue
        for m in _stem_re(p).finditer(text):
            first = _full_name_before(text, m.start(), p)
            if first is None and ROLE.fullmatch(m.group()):
                continue  # „Poslanci“ u příjmení „Poslanec“ bez křestního jména: slovo role, ne jméno
            spans.append((first if first is not None else m.start(), m.end()))
            if any(s <= m.start() < e for s, e in leads):
                continue
            spec = by_name.get(p)
            if spec is None:
                errs.append(f"R8 {where}: jméno osoby „{m.group()}“ ve volném textu. Osoby uvádí jen systém "
                            "(autor PN nebo předkladatel ze serveru, citace projevu přes citace_projevu); "
                            "piš, co se stalo s návrhem, ne kdo co dělá.")
                continue
            if first is None:
                errs.append(f"R8 {where}: „{m.group()}“ jen celým jménem ve tvaru ze serveru, např. "
                            f"„{p}{' ' + dalsi_label(spec['n'], spec['unknown']) if spec['n'] != 0 else ''}“; "
                            "jinak osobu nejmenuj.")
                continue
            errs += _marker_at(text, m.end(), where, p, spec["n"], spec["unknown"])
    return errs, spans


def dalsi_label(n, unknown: str) -> str:
    if n is None:
        return f"(…, {EVIDENCE})" if unknown == "evidence" else "a další poslanci"
    return f"a {n} {_dalsi_word(n)}"


def _name_candidates(sent: str, skip: list[tuple[int, int]]) -> list[re.Match]:
    """Slova s velkým písmenem mimo začátek věty, která nejsou instituce, zkratka ani dokument (možné jméno)."""
    out = []
    quotes = [(m.start(), m.end()) for m in QUOTE.finditer(sent)]
    for i, m in enumerate(WORD.finditer(sent)):
        w = m.group()
        if i == 0 or not w[:1].isupper() or _allowed_caps(w):
            continue
        before = sent[:m.start()].rstrip()
        if not before or before[-1] in ":(„\"–—-#/.!?…" or before.endswith(("http", "www")):
            continue
        if any(s <= m.start() < e for s, e in quotes + skip):
            continue
        # přívlastek v názvu instituce („Technologické agentury“, „Legislativní rada“, „Památník Lidice“)
        nxt = WORD.search(sent, m.end())
        if w.lower().startswith(INSTITUTION_HEAD) or (
                nxt and nxt.start() - m.end() <= 1 and nxt.group()[:1].islower()
                and nxt.group().lower().startswith(INSTITUTION_HEAD)):
            continue
        out.append(m)
    return out


SURNAME_END = ("ová", "ové", "ovou", "ský", "ská", "ské", "skou", "ckou", "cký", "cká", "ovi", "em")


def _strong_name(sent: str, m: re.Match) -> bool:
    """Jméno osoby s vysokou jistotou: dvě slova s velkým písmenem vedle sebe („Lukášem Vlčkem“), nebo tvar
    příjmení („Svárovská“, „Řehkovou“)."""
    w = m.group()
    if w.lower().endswith(SURNAME_END):
        return True
    prev = re.search(r"([^\W\d_][\w-]*)\s+$", sent[:m.start()])
    nxt = re.match(r"\s+([^\W\d_][\w-]*)", sent[m.end():])
    near = [x.group(1) for x in (prev, nxt) if x]
    return any(x[:1].isupper() and not _allowed_caps(x) for x in near)


def _subject_ok(sent: str, say: re.Match) -> bool:
    """Podmětem je dokument nebo instituce na začátku věty (nejvýš dva přívlastky před ním), nebo dokument stojí
    hned za slovesem („…, uvádí odůvodnění PN“)."""
    words = [w.group() for w in WORD.finditer(sent[:say.start()])]
    i, adjectives = 0, 0
    while i < len(words):
        t = words[i]
        low = t.lower()
        if low in PREPOSITIONS or low in ("podle", "dle", "kromě", "vedle", "během", "díky"):
            i += 2  # předložková vazba na začátku („Podle odůvodnění návrh…“, „K důchodům odůvodnění…“)
            continue
        if DOC.match(t) or INSTITUTION.match(t) or (t.isupper() and len(t) >= 2 and not _is_club(t)):
            return True
        if adjectives < 2 and low.endswith(("á", "é", "í", "ý", "ích", "ého", "ou")):
            adjectives += 1
            i += 1
            continue
        break
    # inverze ve vedlejší větě („…, uvádí odůvodnění PN“): sloveso hned za čárkou, dokument hned za ním
    if not re.search(r",\s*$", sent[:say.start()]):
        return False
    after = WORD.search(sent, say.end())
    return after is not None and after.start() - say.end() <= 2 and DOC.match(after.group()) is not None


def attribution_errors(text: str, *, where: str, people: list[str], docs: list[str],
                       speeches: list[dict], mentions: list[dict] | None = None) -> list[str]:
    """Pravidla R1–R5, R7 a R8 nad jedním textem; chyby česky s kódem pravidla na začátku.
    mentions = osoby, které smí text jmenovat strukturovaně (mentions_of_card); bez nich žádná."""
    if not isinstance(text, str) or not text.strip():
        return []
    errs: list[str] = []
    leads = speech_leads(text)
    lead_spans = [(ld["start"], ld["end"]) for ld in leads]
    person_errs, name_spans = _person_check(text, where, people, mentions or [], lead_spans)
    errs += person_errs
    for start, sent in _sentences(text):
        local_skip = [(s - start, e - start) for s, e in name_spans + lead_spans if e > start and s < start + len(sent)]
        candidates = _name_candidates(sent, local_skip)
        for c in candidates:
            if not _strong_name(sent, c):
                continue  # jedno slovo (položka rozpočtu, místo): jen signál osoby pro R1 a R7
            errs.append(f"R8 {where}: „{c.group()}“ vypadá jako jméno osoby („{_snippet(sent)}“). Osoby uvádí jen "
                        "systém; instituci nebo dokument napiš celým názvem, jinak osobu nejmenuj.")
        in_lead = lambda pos: any(s <= start + pos < e for s, e in lead_spans)  # noqa: E731
        persons = (bool(candidates) or ROLE.search(sent) is not None
                   or any(_stem_re(p).search(sent) for p in people if p and p.strip())
                   or any(re.search(rf"(?<!\w){re.escape(c)}(?!\w)", sent) for c in CLUBS))
        # R1: sloveso mluvení nebo hodnocení jen s dokumentem či institucí jako podmětem a bez osoby ve větě
        for say in SAY.finditer(sent):
            if in_lead(say.start()):
                continue
            if persons or not _subject_ok(sent, say):
                errs.append(f"R1 {where}: výrok nebo hodnocení přisouzené osobě („{_snippet(sent)}“, sloveso "
                            f"„{say.group()}“). Piš, co stojí v dokumentu („Návrh uvádí…“, „Důvodová zpráva "
                            "uvádí…“); text z projevu jen přes citace_projevu.")
                break
        # R2: „podle X“ u osoby nebo role
        for m in PODLE.finditer(sent):
            w1 = m["w1"].strip("„“\"(),.;:")
            w2 = (m["w2"] or "").strip("„“\"(),.;:")
            low1, low2 = w1.lower(), w2.lower()
            kw = m["kw"].lower()
            if kw not in ("podle", "dle"):  # „slovy X“, „z úst X“: vždy výrok osoby
                bad = True
            elif any(low1.startswith(s) for s in SPEECH_NOUNS):  # „podle slov ministryně“
                bad = True
            elif low1 in PRONOUNS:  # „podle něj“ (= podle PN) ano, „podle jejích slov“ ne
                bad = any(low2.startswith(s) for s in SPEECH_NOUNS)
            elif any(low1.startswith(ok) for ok in PODLE_OK) or (w1.isupper() and not _is_club(w1)):
                bad = False
            else:
                bad = (ROLE.match(w1) is not None or _is_club(w1) or any(_stem_re(p).match(w1) for p in people if p and p.strip())
                       or (w1[:1].isupper() and not _allowed_caps(w1)))
            if bad:
                errs.append(f"R2 {where}: „{m['kw']} {w1}“ přisuzuje tvrzení osobě („{_snippet(sent)}“). "
                            "„Podle“ jen u dokumentu, dat nebo instituce („podle PN 2376“, „podle ČSÚ“).")
                break
        # R3: „Jméno: …“, „Ministryně – „…““
        for m in COLON_QUOTE.finditer(sent):
            if in_lead(m.start()):
                continue
            if re.match(r"\d", sent[m.end():m.end() + 1]) and re.search(r"\d$", sent[:m.start()]):
                continue  # čas „16:13“
            head = sent[:m.start()].rstrip()
            club_paren = re.search(r"\(([^()]*)\)\s*$", head)
            if club_paren:
                head = head[:club_paren.start()].rstrip()
            last = [w.group() for w in WORD.finditer(head)][-2:]
            if not last:
                continue
            if len(last) == 2 and last[0].lower() in PREPOSITIONS:
                continue
            caps = [w for w in last if w[:1].isupper() and not _allowed_caps(w) and not DOC.match(w)]
            if (any(ROLE.match(w) for w in last)
                    or any(_stem_re(p).fullmatch(w) for p in people if p and p.strip() for w in last)
                    or (last[-1][:1].isupper() and last[-1].lower().endswith(("ová", "ská", "cká"))
                        and (len(last) == 1 or last[0][:1].isupper()))
                    or len(caps) == 2 or (caps and club_paren and any(_is_club(x.strip())
                                                                       for x in club_paren.group(1).split(",")))):
                errs.append(f"R3 {where}: text přisouzený osobě („{_snippet(sent)}“). Výrok nepřisuzuj; z projevu "
                            "jen „V projevu J. Nováka 12. 11. zaznělo: „…““ s citace_projevu.")
                break
        # R7: motiv
        motive = MOTIVE.search(sent)
        intent = INTENT.search(sent)
        if motive or (intent and (persons or INTENT_ACTOR.search(sent))):
            word = (motive or intent).group()
            errs.append(f"R7 {where}: připisuje motiv („{word}“ v „{_snippet(sent)}“). Popisuj, co se stalo "
                        "a kdy, ne proč to kdo chce.")
    # R4: uvozovky bez zdroje
    norm_docs = [_norm(d) for d in docs if isinstance(d, str) and d.strip()]
    for m in QUOTE.finditer(text):
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        if len(inner.split()) < MIN_QUOTE_WORDS:
            continue
        if any(s <= m.start() < e for s, e in lead_spans):
            continue
        if any(_norm(inner) in d for d in norm_docs):
            continue
        errs.append(f"R4 {where}: uvozovky bez zdroje („{_snippet(inner, 60)}“). Uvozovky jen pro doslovný text "
                    "zákona, návrhu nebo PN z pole citace/quote, název tisku, nebo za úvodem „V projevu … zaznělo:“.")
    # R5: úvod projevu jen se záznamem v kartě
    valid = [s for s in speeches if isinstance(s, dict)]
    for ld in leads:
        reasons = [_speech_match(ld, sp) for sp in valid]
        hit = next((sp for sp, r in zip(valid, reasons) if r is None), None)
        lead_text = text[ld["start"]:ld["end"]]
        if hit is None:
            why = ", ".join(sorted({r for r in reasons if r})) or "karta nemá citace_projevu"
            errs.append(f"R5 {where}: úvod projevu bez odpovídající položky citace_projevu ({why}): "
                        f"„{_snippet(lead_text)}“. Řečník, den, měsíc a doslovný text musí sedět s položkou.")
        elif hit.get("autorizovano") is not True and "neautoriz" not in lead_text:
            errs.append(f"R5 {where}: projev je neautorizovaný, úvod musí mít „(neautorizovaný záznam)“: "
                        f"„{_snippet(lead_text)}“.")
    return errs


def texts_of_card(card: dict) -> list[tuple[str, str]]:
    """(cesta, text) volných textů karty od modelu; bez odkazů, klíčů, dat, údajů serveru a doslovných citací."""
    out: list[tuple[str, str]] = []

    def walk(value, path: str, in_pn: bool) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if in_pn and k not in PN_TEXT_KEYS:
                    continue
                if k in SKIP_KEYS and not (k == "pn" and not path):
                    continue
                walk(v, f"{path}.{k}" if path else k, in_pn or (k == "pn" and not path))
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, f"{path}[{i}]", in_pn)
        elif isinstance(value, str) and value.strip() and not re.fullmatch(r"[\d\s.,:%–-]*", value):
            out.append((path, value))

    if isinstance(card, dict):
        walk(card, "", False)
    return out


def _doc_quotes(card: dict) -> list[str]:
    """Doslovné citace dokumentů z karty (quote, všechna pole citace mimo predkladatel, název tisku)."""
    out: list[str] = []

    def walk(value, key: str | None) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if k in ("predkladatel", "citace_projevu"):
                    continue
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
        elif isinstance(value, str) and key in ("quote", "citace", "nazev", "pred", "po", "bez_pred") and value.strip():
            out.append(value)

    walk(card, None)
    return out


def known_people(card: dict) -> list[str]:
    """Jména osob z karty: autoři PN, předkladatel, autoři v pn.top a pn.autori, pribeh.autor, řečníci."""
    names: list[str] = []

    def add(value) -> None:
        if isinstance(value, list):
            for v in value:
                add(v)
        elif isinstance(value, str) and value.strip() and value.strip() not in names:
            names.append(value.strip())

    if not isinstance(card, dict):
        return names
    for row in card.get("pn_autor") or []:
        if isinstance(row, dict):
            add(row.get("jmeno"))
    who = card.get("predkladatel")
    if isinstance(who, dict) and str(who.get("typ", "")).startswith("Poslanec"):
        add(who.get("jmeno"))
    pn = card.get("pn") if isinstance(card.get("pn"), dict) else {}
    for row in pn.get("top") or []:
        if isinstance(row, dict):
            add(row.get("autor"))
    for row in pn.get("autori") or []:
        if isinstance(row, dict):
            add(row.get("jmeno"))
    story = card.get("pribeh") if isinstance(card.get("pribeh"), dict) else {}
    if isinstance(story.get("autor"), dict):
        add(story["autor"].get("jmeno"))
    for sp in card.get("citace_projevu") or []:
        if isinstance(sp, dict):
            add(sp.get("recnik"))
    rozprava = card.get("rozprava") if isinstance(card.get("rozprava"), dict) else {}
    for side in rozprava.values():
        for b in (side.get("body") or []) if isinstance(side, dict) else []:
            if isinstance(b, dict):
                add(b.get("recnik"))
    return names


def _needs_ai(card: dict) -> bool:
    """Karta stojí na hodnocení AI: v2 (titulek a perex z rozboru AI), pn.top s titulky AI, znak nebo dopad AI."""
    if not isinstance(card, dict) or not card:
        return False
    if not card.get("format") and card.get("title"):
        return True
    pn = card.get("pn") if isinstance(card.get("pn"), dict) else {}
    if pn.get("top"):
        return True
    story = card.get("pribeh") if isinstance(card.get("pribeh"), dict) else {}
    if any(isinstance(z, dict) and (z.get("typ") == "ai" or z.get("ai") is True) for z in story.get("znaky") or []):
        return True
    dopad = story.get("dopad")
    return isinstance(dopad, dict) and dopad.get("ai") is True


def _has_ai(text: str) -> bool:
    return re.search(r"(?<!\w)AI(?!\w)", text) is not None


def public_text_errors(card: dict | None, caption: str, ct: int | None) -> list[str]:
    """Všechna pravidla na texty karty, popisek a odvozené texty pro Threads a X; prázdné = v pořádku."""
    card = card if isinstance(card, dict) else {}
    people = known_people(card)
    docs = _doc_quotes(card)
    speeches = [s for s in card.get("citace_projevu") or [] if isinstance(s, dict)]
    mentions = mentions_of_card(card)
    texts = texts_of_card(card)
    caption = caption.strip() if isinstance(caption, str) else ""
    derived: list[tuple[str, str]] = []
    errs: list[str] = []
    if caption:
        texts.append(("popisek", caption))
        for name, fn in (("text pro Threads", _cap.threads_text), ("text pro X", _cap.x_text)):
            try:
                derived.append((name, fn(caption, ct)))
            except ValueError as e:
                errs.append(f"{name}: {e}")
    for where, text in texts + derived:
        errs += attribution_errors(text, where=where, people=people, docs=docs, speeches=speeches,
                                   mentions=mentions)
    for where, text in derived:
        if text.count("„") > text.count("“"):
            errs.append(f"{where}: useknutá citace (otevírací „ bez zavírací “). První odstavec popisku zkrať "
                        "tak, aby citace v něm nebyla, nebo se do 440 znaků vešla celá.")
    # R6: citace projevu nepatří do titulku, háčku ani do prvního odstavce popisku (jde zkrácený na Threads a X)
    first = re.split(r"\n[ \t\r]*\n", caption, maxsplit=1)[0] if caption else ""
    heads = [("první odstavec popisku", first)] + [(w, t) for w, t in texts if w == "title" or w.startswith("hook")]
    for where, text in heads:
        if speech_leads(text):
            errs.append(f"R6 {where}: citace projevu sem nepatří (titulek, háček a první odstavec popisku jdou "
                        "zkrácené na Threads a X). Přesuň ji do dalšího odstavce.")
    if caption:
        if _needs_ai(card) and not _has_ai(caption):
            errs.append("popisek musí zmínit, že hodnocení nebo titulky jsou od AI (slovo „AI“, např. „shrnutí AI“).")
        if AI_WORDS.search(first) and not _has_ai(first):
            errs.append(f"první odstavec popisku: hodnocení („{AI_WORDS.search(first).group()}“) bez označení AI "
                        "v témže odstavci (např. „podle rozboru AI“).")
    return list(dict.fromkeys(errs))
