"""Kontrola, že texty Reels nepřisuzují výrok ani motiv osobě a u navrhovatelů uvádějí „a N dalších“.

Návrh docs/reels-citace-bezpecne.md (U1, R1–R7). Rozhodnutí majitele: text z projevu jen ve tvaru
„V projevu J. Nováka 12. 11. zaznělo: „…““ se záznamem v `citace_projevu`; nikdy „X řekl“, „X tvrdí“,
„podle X“. U více navrhovatelů vždy „a N dalších“. Text se strojově nepřepisuje: chyba jde do opravného běhu
generátoru, nebo varianta vypadne; bot podle téže kontroly položku autopilota nezveřejní.

Kontrola je heuristika nad slovy, ne porozumění textu: falešný nález opraví opravný běh (přepíše větu).
Modul volá generátor i bot, a proto závisí jen na `re` a `caption.py`.
"""
from __future__ import annotations

import re

from . import caption as _cap

VOWELS = "aeiouyáéíóúůýě"
UPPER = "A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"

# slovesa mluvení (kmeny; všechny rody a čísla). Úkony (podal, předložil, navrhl, hlasoval…) zůstávají povolené.
SAY = re.compile(
    r"(?<!\w)(?:řekl|říká|říkal|uvedl|uvádí|uváděj|tvrdí|tvrdil|tvrdit|prohlásil|prohlašuj|dodal|dodáv|"
    r"upozornil|upozorňuj|varoval|varuj|namítl|namítá|namítal|kritizoval|kritizuj|označil|nazval|obvinil|"
    r"slíbil|slibuj|přiznal|připustil|připouští|vysvětlil|vysvětluj|argumentuj|argumentoval|zdůraznil|"
    r"zdůrazňuj|poznamenal|oznámil|vzkázal|odpověděl|reagoval|reaguj|hájí|hájil|obhajuj|obhajoval|"
    r"zdůvodňuj|zdůvodnil|odůvodňuj|odůvodnil)\w*", re.IGNORECASE)
# role osob a institucí („vládní“, „poslanecký“, „senátní“ jsou přívlastky návrhu, ne osoba)
ROLE = re.compile(r"(?<!\w)(?:poslan(?!eck)|ministr|premiér|senátor(?!sk)|zpravodaj|předkladatel|navrhovatel|"
                  r"opozic|koalic|vlád(?!ní))\w*", re.IGNORECASE)
CLUBS = ("ANO", "ODS", "STAN", "Piráti", "Pirátů", "SPD", "TOP 09", "KDU-ČSL", "Motoristé", "Motoristů",
         "KSČM", "STAČILO!")
# podmět dokument: výjimka z R1, když ve větě osoba není a dokument stojí před slovesem
DOC = re.compile(r"(?<!\w)(?:zákon|návrh|novel|ustanoven|čl\.|§|důvodov|jednací|ústav|PN|tisk|usnesen|rozbor|"
                 r"AI|text|znění|nařízen|směrnic|odůvodněn|zpráv|data|evidenc|harmonogram|program|pravidl|"
                 r"předpis|smlouv|odstav|paragraf|bod)", re.IGNORECASE)
# „podle …“ u dokumentu, dat nebo AI je v pořádku
PODLE = re.compile(r"(?<!\w)podle\s+(\S+)", re.IGNORECASE)
PODLE_OK = ("§", "zákon", "jednací", "ústav", "nařízen", "čl", "ai", "dat", "histori", "psp", "důvodov", "rozbor",
            "evidenc", "text", "znění", "pravid", "směrnic", "usnesen", "předpis", "smlouv", "odst", "bod",
            "harmonogram", "program", "stav", "hlasován", "počt", "výsledk", "čísl", "kalendář", "lhůt", "tisk",
            "sbírk", "věstník", "metodik", "stenozáznam", "záznam")
PRONOUNS = ("něj", "ní", "nich", "jeho", "její", "jejích", "jejich", "svých", "svého", "mne", "nás", "vás")
# R3: jméno nebo role a za ním dvojtečka („Ministryně: marže vyrostly…“) nebo pomlčka s uvozovkou
COLON_QUOTE = re.compile(r"\s*:|\s*[–—-]\s*[„\"]")
QUOTE = re.compile(r"„([^„“]*(?:„[^„“]*“[^„“]*)*)“|\"([^\"]+)\"")
QUOTE_CHARS = re.compile(r"[„“”\"‚‘’']")
# R7: motiv
MOTIVE = re.compile(r"(?<!\w)(?:obej[dí]\w*|obešl\w*|obešel|obcház\w*|protlač\w*|propaš\w*|potichu|tajně|"
                    r"nenápadně|účelově|schválně|záměrně)(?!\w)", re.IGNORECASE)
INTENT = re.compile(r"(?<!\w)(?:aby(?:ch|s|chom|ste)?|chce|chtějí|chtěl\w*|snaží\s+se|snažil\w*\s+se|"
                    r"se\s+snaží|se\s+snažil\w*)(?!\w)", re.IGNORECASE)
# úvod projevu (kanonický tvar)
LEAD = re.compile(
    r"(?<!\w)[Vv] projevu (?P<kdo>[^\d\n„“]{2,60}?) (?P<den>\d{1,2})\.\s?(?P<mesic>\d{1,2})\.(?:\s?\d{4})?"
    r"(?P<neaut>\s*\(neautorizovaný záznam\))?\s+zaznělo"
    r"(?:(?P<dosl>:\s*„(?P<citat>[^“]+)“)|(?P<shr>,\s*že\b))?")
MARKER = re.compile(r"(?<!\w)(\d+)\s+dalš\w*", re.IGNORECASE)
SOME_MORE = re.compile(r"(?<!\w)(?:a|s|se)\s+dalš\w*", re.IGNORECASE)
EVIDENCE = "podle evidence psp.cz"
AI_WORDS = re.compile(r"(?<!\w)(?:přílep\w*|závažnost\w*|mimo\s+předmět)", re.IGNORECASE)
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
    """Věty textu jako (začátek, věta). Konec věty: . ! ? … před mezerou a velkým písmenem nebo uvozovkou,
    nový řádek; ne za zkratkou ani za jedním písmenem („J. Nováka“, „čl. VI“)."""
    out: list[tuple[int, str]] = []
    start = 0
    for m in re.finditer(rf"(?<=[.!?…“])\s+(?=[{UPPER}„])|\n+", text):
        if not m.group().startswith("\n"):
            before = text[start:m.start()]
            word = re.search(r"(\w+)\.$", before)
            if word and (len(word.group(1)) == 1 or word.group(1).lower() in ABBREV or word.group(1).isdigit()):
                continue
        if text[start:m.start()].strip():
            out.append((start, text[start:m.start()]))
        start = m.end()
    if text[start:].strip():
        out.append((start, text[start:]))
    return out


def _club_hit(sentence: str) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(c)}(?!\w)", sentence) for c in CLUBS)


def _person_spans(sentence: str, people: list[str]) -> list[int]:
    """Pozice, na kterých věta jmenuje osobu (kmen příjmení z `people`), roli nebo klub."""
    pos = [m.start() for p in people if p and p.strip() for m in _stem_re(p).finditer(sentence)]
    pos += [m.start() for m in ROLE.finditer(sentence)]
    pos += [m.start() for c in CLUBS for m in re.finditer(rf"(?<!\w){re.escape(c)}(?!\w)", sentence)]
    return sorted(pos)


def _snippet(text: str, limit: int = 90) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


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


def attribution_errors(text: str, *, where: str, people: list[str], docs: list[str],
                       speeches: list[dict]) -> list[str]:
    """Pravidla R1–R5 a R7 nad jedním textem; chyby česky s kódem pravidla na začátku."""
    if not isinstance(text, str) or not text.strip():
        return []
    errs: list[str] = []
    leads = speech_leads(text)
    lead_spans = [(ld["start"], ld["end"]) for ld in leads]
    for start, sent in _sentences(text):
        persons = _person_spans(sent, people)
        # R1: sloveso mluvení u osoby; bez osoby jen s dokumentem jako podmětem (před slovesem)
        for say in SAY.finditer(sent):
            doc_before = any(d.start() < say.start() for d in DOC.finditer(sent))
            if persons or not doc_before:
                errs.append(f"R1 {where}: výrok přisouzený osobě („{_snippet(sent)}“, sloveso „{say.group()}“). "
                            "Nepiš, kdo co řekl nebo tvrdí. Text z projevu jen jako „V projevu J. Nováka 12. 11. "
                            "zaznělo …“ se záznamem v citace_projevu, u dokumentu „Důvodová zpráva uvádí …“.")
                break
        # R2: „podle X“ u osoby
        for m in PODLE.finditer(sent):
            word = m.group(1).strip("„“\"(),.;:")
            low = word.lower()
            allowed = any(low.startswith(ok) for ok in PODLE_OK)
            bad = not allowed and (low in PRONOUNS or ROLE.match(word) is not None or word[:1].isupper()
                                   or any(_stem_re(p).match(word) for p in people if p and p.strip()))
            if bad:
                errs.append(f"R2 {where}: „podle {word}“ přisuzuje tvrzení osobě („{_snippet(sent)}“). "
                            "Piš, co stojí v dokumentu („Důvodová zpráva uvádí …“), nebo „V projevu … zaznělo“.")
                break
        # R3: „Jméno: „…““
        for m in COLON_QUOTE.finditer(sent):
            head = sent[max(0, m.start() - 40):m.start()].rstrip()
            last = re.findall(r"[\w-]+", head)[-2:]
            two_caps = (len(last) == 2 and all(w[:1].isupper() for w in last)
                        and not any(DOC.match(w) for w in last))
            if any(ROLE.match(w) or any(_stem_re(p).fullmatch(w) for p in people if p and p.strip())
                   for w in last) or two_caps:
                errs.append(f"R3 {where}: citace se jménem osoby („{_snippet(sent)}“). Výrok nepřisuzuj; z projevu "
                            "jen „V projevu J. Nováka 12. 11. zaznělo: „…““.")
                break
        # R7: motiv
        motive = MOTIVE.search(sent)
        intent = INTENT.search(sent)
        if motive or (intent and persons):
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
                    "zákona, návrhu nebo PN z pole citace/quote, nebo za úvodem „V projevu … zaznělo:“.")
    # R5: úvod projevu jen se záznamem v kartě
    for ld in leads:
        reasons = [_speech_match(ld, sp) for sp in speeches if isinstance(sp, dict)]
        hit = next((sp for sp, r in zip([s for s in speeches if isinstance(s, dict)], reasons) if r is None), None)
        lead_text = text[ld["start"]:ld["end"]]
        if hit is None:
            why = ", ".join(sorted({r for r in reasons if r})) or "karta nemá citace_projevu"
            errs.append(f"R5 {where}: úvod projevu bez odpovídající položky citace_projevu ({why}): "
                        f"„{_snippet(lead_text)}“. Řečník, den, měsíc a doslovný text musí sedět s položkou.")
        elif hit.get("autorizovano") is not True and "neautoriz" not in lead_text:
            errs.append(f"R5 {where}: projev je neautorizovaný, úvod musí mít „(neautorizovaný záznam)“: "
                        f"„{_snippet(lead_text)}“.")
    return errs


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


def _marker_errors(text: str, where: str, jmeno: str, n, unknown: str) -> list[str]:
    """Za prvním jmenováním osoby musí ve stejné větě stát značka dalších navrhovatelů.

    n > 0 = „a N dalších“ se správným N; n None = neznámý počet: unknown "evidence" (PN: „podle evidence psp.cz“)
    nebo "dalsi" (tisk: „a další poslanci“)."""
    if not isinstance(text, str) or not isinstance(jmeno, str) or not jmeno.strip():
        return []
    if n == 0:
        return []
    m = _stem_re(jmeno).search(text)
    if m is None:
        return []
    win = _window(text, m.end())
    if n is None:
        if unknown == "evidence":
            if EVIDENCE in win:
                return []
            return [f"{where}: u „{jmeno}“ počet spolupodepsaných nejde určit; hned za jméno (a klub) patří "
                    f"„{EVIDENCE}“ ve stejné větě, např. „{jmeno} (ANO, {EVIDENCE})“."]
        if SOME_MORE.search(win) or MARKER.search(win):
            return []
        return [f"{where}: u „{jmeno}“ chybí „a další poslanci“ hned za jménem (a klubem) ve stejné větě."]
    nums = [int(x) for x in MARKER.findall(win)]
    if n in nums:
        return []
    if nums:
        return [f"{where}: u „{jmeno}“ je špatný počet dalších ({nums[0]}), podle dat Sněmovny {n}."]
    return [f"{where}: u „{jmeno}“ chybí „a {n} {_dalsi_word(n)}“ hned za jménem (a klubem) ve stejné větě."]


def _dalsi_word(n: int) -> str:
    return "další poslanec" if n == 1 else "další poslanci" if 2 <= n <= 4 else "dalších poslanců"


def cosigner_errors(text: str, *, where: str, authors: list[dict]) -> list[str]:
    """Autoři PN ze serveru (`pn_autor[]` s klíčem `spolu`): u jména „a N dalších“, při neznámém počtu
    „podle evidence psp.cz“. Bez klíče `spolu` (starší server) se nic nekontroluje."""
    errs: list[str] = []
    seen = set()
    for a in authors or []:
        if not isinstance(a, dict) or "spolu" not in a or a.get("jmeno") in seen:
            continue
        seen.add(a.get("jmeno"))
        n = a["spolu"]
        n = n if isinstance(n, int) and not isinstance(n, bool) else None
        errs += _marker_errors(text, where, a.get("jmeno"), n, "evidence")
    return errs


def proposer_errors(text: str, *, where: str, proposer: dict | None) -> list[str]:
    """Předkladatel poslaneckého návrhu se skupinou (`vice`): u jména „a N dalších“ (`dalsi`),
    při neznámém počtu „a další poslanci“."""
    if not isinstance(proposer, dict) or proposer.get("vice") is not True:
        return []
    n = proposer.get("dalsi")
    n = n if isinstance(n, int) and not isinstance(n, bool) else None
    return _marker_errors(text, where, proposer.get("jmeno"), n, "dalsi")


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
    """Doslovné citace dokumentů z karty (quote, všechna pole citace mimo predkladatel)."""
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
        elif isinstance(value, str) and key in ("quote", "citace") and value.strip():
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
    authors = [a for a in card.get("pn_autor") or [] if isinstance(a, dict)]
    proposer = card.get("predkladatel") if isinstance(card.get("predkladatel"), dict) else None
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
        errs += attribution_errors(text, where=where, people=people, docs=docs, speeches=speeches)
        errs += cosigner_errors(text, where=where, authors=authors)
        errs += proposer_errors(text, where=where, proposer=proposer)
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
