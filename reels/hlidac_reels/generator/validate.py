"""Kontrola karty a popisku od modelu, než se video vyrenderuje (pravidla obsahu z runbooku)."""
from __future__ import annotations

import re

from ..card import Card
from ..enqueue import CAPTION_LIMIT

CLOSING = "Hodnotíme postup, ne politický obsah."
V2_REQUIRED = ("ct", "nazev", "faze", "score", "title", "perex", "quote", "quote_source", "reasons")
V3_REQUIRED = V2_REQUIRED[:6] + ("reasons", "format", "hook", "zdroje")
# pole, která Card.from_json vyžaduje; smí být prázdná (v3 citaci nemusí mít)
PRESENT = ("verified", "quote", "quote_source")
# bezpečné zóny na výšku (Instagram): 150 px nahoře, 340 px dole, 60 px po stranách
SAFE = {"top": 150, "bottom": 1920 - 340, "left": 60, "right": 1020}


def _empty(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value in ([], {})


def _v3_block(card: dict, fmt: str) -> list[str]:
    block = card.get(fmt)
    if not isinstance(block, dict) or not block:
        return [f"chybí blok formátu {fmt!r}"]
    errs: list[str] = []
    if fmt == "cesta" and _empty(block.get("kroky")):
        errs.append("cesta: chybí kroky")
    if fmt == "pn" and _empty(block.get("count")):
        errs.append("pn: chybí count")
    if fmt == "zmena":
        # body bez citace vynechá normalizace (normalize.py); chyba je až bez jediné citace
        body = block.get("body") or []
        if not body:
            errs.append("zmena: chybí body")
        elif all(_empty(b.get("citace")) for b in body):
            errs.append("zmena: žádný bod nemá citaci")
        for i, b in enumerate(body, 1):
            if _empty(b.get("zdroj")):
                errs.append(f"zmena: bod {i} nemá zdroj")
    if fmt == "rozprava":
        for side in ("pro", "namitky", "odpovedi"):
            for i, b in enumerate((block.get(side) or {}).get("body") or [], 1):
                if _empty(b.get("url")):
                    errs.append(f"rozprava: {side} bod {i} nemá url")
    return errs


# příběh jednoho PN (template_pn_v1): blok pribeh místo hook
PRIBEH_REQUIRED = V2_REQUIRED[:6] + ("reasons", "format", "pribeh")
PN_STATES = ("prijat", "castecne", "zamitnut", "ceka", "nehlasovatelny", "nezaznel")
DECIDED = ("prijat", "castecne", "zamitnut")
VOTE_URL = re.compile(r"^https://www\.psp\.cz/sqw/hlasy\.sqw\?g=\d+$")


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _pribeh_block(card: dict, focus_pn: list[int] | None, focus_info: list[dict] | None) -> list[str]:
    """Pravidla šablony template_pn_v1 (scenar.md): PN je zadaný, změna jen s doslovnou citací a zdrojem,
    znaky se zdrojem a hodnocení AI označené, verdikt přijat / zamítnut jen s hlasováním s počty a odkazem
    na psp.cz a stavem ze serveru, dopad bez štítku AI jen se zdrojem."""
    p = card.get("pribeh")
    if not isinstance(p, dict) or not p:
        return []  # chybějící pole hlásí kontrola povinných polí
    errs: list[str] = []
    if focus_pn and len(focus_pn) == 1 and str(p.get("cislo")) != str(focus_pn[0]):
        errs.append(f"pribeh: cislo {p.get('cislo')!r}, zadání PN {focus_pn[0]}")
    autor = p.get("autor")
    if not isinstance(autor, dict) or _empty(autor.get("jmeno")):
        errs.append("pribeh: chybí autor.jmeno")
    co = p.get("co")
    if not isinstance(co, dict):
        errs.append("pribeh: chybí co (co PN mění, s doslovnou citací)")
    else:
        for key, what in (("citace", "doslovnou citaci z textu PN"), ("zdroj", "zdroj citace"),
                          ("po", "znění po PN")):
            if _empty(co.get(key)):
                errs.append(f"pribeh: co nemá {what} ({key})")
    for i, sign in enumerate(p.get("znaky") or [], 1):
        if not isinstance(sign, dict):
            errs.append(f"pribeh: znak {i} není objekt")
            continue
        if _empty(sign.get("zdroj")):
            errs.append(f"pribeh: znak {i} nemá zdroj")
        if sign.get("typ") == "ai" and sign.get("ai") is not True:
            errs.append(f"pribeh: znak {i} je hodnocení AI bez štítku (ai: true)")
    v = p.get("vysledek")
    if v is not None:
        stav = v.get("stav") if isinstance(v, dict) else None
        if stav not in PN_STATES:
            errs.append(f"pribeh: neznámý stav výsledku {stav!r}")
        elif stav in DECIDED:
            votes = [h for h in v.get("hlasovani") or [] if isinstance(h, dict)
                     and isinstance(h.get("url"), str) and VOTE_URL.match(h["url"])
                     and _number(h.get("pro")) and _number(h.get("proti"))]
            if not votes:
                errs.append(f"pribeh: verdikt {stav!r} bez hlasování s počty a odkazem na psp.cz")
        server = next((i for i in focus_info or [] if isinstance(i, dict) and "vysledek" in i
                       and str(i.get("cislo")) == str(p.get("cislo"))), None)
        if server is not None and stav in PN_STATES:
            srv = server["vysledek"].get("stav") if isinstance(server["vysledek"], dict) else None
            if stav != srv and not (srv not in PN_STATES and stav == "ceka"):
                errs.append(f"pribeh: stav výsledku {stav!r}, server uvádí {srv!r}")
    d = p.get("dopad")
    if isinstance(d, dict) and not _empty(d.get("text")) and d.get("ai") is False and _empty(d.get("zdroj")):
        errs.append("pribeh: dopad bez štítku AI nemá zdroj")
    return errs


def _mentions(value, nums: set[int]) -> bool:
    """Zmiňuje hodnota (i vnořená) některé z čísel PN: pole s číslem, nebo samostatné číslo v textu?"""
    if isinstance(value, dict):
        return any(_mentions(v, nums) for v in value.values())
    if isinstance(value, list):
        return any(_mentions(v, nums) for v in value)
    if isinstance(value, int) and not isinstance(value, bool):
        return value in nums
    if isinstance(value, str):
        return any(int(m) in nums for m in re.findall(r"(?<!\d)\d+(?!\d)", value))
    return False


def _focus_errors(card: dict, caption: str, focus_pn: list[int]) -> list[str]:
    """Video k vybraným PN: karta i popisek musí zmínit číslo aspoň jednoho z nich.

    `pn_autor` doplňuje normalizace ze zadání, a tak se nepočítá: o PN má mluvit obsah karty od modelu.
    """
    nums = set(focus_pn)
    which = ", ".join(str(n) for n in sorted(nums))
    errs = []
    if not _mentions({k: v for k, v in card.items() if k != "pn_autor"}, nums):
        errs.append(f"karta nezmiňuje zadaný PN ({which}): video má být o něm")
    if not _mentions(caption, nums):
        errs.append(f"popisek nezmiňuje číslo zadaného PN ({which})")
    return errs


def has_word(text: str, word: str) -> bool:
    """Je `word` v textu samostatným výrazem? „ANO“ v „(ANO)“ ano, v „ANO2011“ ne; i víceslovné „TOP 09“."""
    return bool(word) and re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) is not None


# kolik znaků před jménem autora smí stát klub („za ANO Jan Novák“)
CLUB_BEFORE = 40
NAME_FOLLOWING = r"[^\S\n]*\(([^()\n]*)\)?"
# jméno uvnitř závorky: text za ním končí na `)`, `(` nebo na konci řádku; klub je první údaj za čárkou
PAREN_STOP = re.compile(r"[()\n]")
NAME_COMMA = r"[^\S\n]*,[^\S\n]*"


def _club_pattern(klub: str) -> str:
    """Klub jako samostatný výraz; popisek „nezařazení“ splní i „nezařazená“ a „nezařazený“."""
    if klub == "nezařazení":
        return r"(?<!\w)[nN]ezařazen[áíý](?!\w)"
    return rf"(?<!\w){re.escape(klub)}(?!\w)"


def club_matches(text: str, klub: str) -> bool:
    """Stojí klub v textu jako samostatný výraz (jako `has_word`, jen s tvary „nezařazený“, „nezařazená“)?"""
    return bool(klub) and re.search(_club_pattern(klub), text) is not None


def find_name(text: str, jmeno: str) -> tuple[int, int, str | None] | None:
    """První výskyt celého jména v textu: `(začátek, konec, obsah závorky hned za jménem)`; bez závorky
    je obsah `None`, bez jména vrací `None`.

    Celé jméno: části oddělené libovolnými mezerami, ne část delšího slova ani dvojitého příjmení
    („Novák-Dlouhý“). Skloňovaný tvar („Jana Nováka“) se nenajde. Normalizace podle toho klub vkládá,
    kontrola ho podle toho hledá.
    """
    name = r"\s+".join(re.escape(part) for part in jmeno.split())
    m = re.search(rf"(?<![\w-]){name}(?![\w-])", text) if name else None
    if not m:
        return None
    paren = re.match(NAME_FOLLOWING, text[m.end():])
    return m.start(), m.end(), paren.group(1) if paren else None


def name_in_paren(text: str, start: int, end: int) -> str | None:
    """Stojí jméno (`start`–`end`) uvnitř otevřené závorky („PN 2376 (Jan Novák) mění …“)? Vrací text od konce
    jména po nejbližší `)`, `(` nebo konec řádku (`""`, `", ANO"`); `None`, když poslední `(` před jménem má před
    ním svou `)`, když před jménem na jeho řádku žádná `(` není, nebo když za jménem nic z toho nenásleduje.

    Klub pak patří do téže závorky za čárku („(Jan Novák, ANO)“), ne do vnořené závorky. Konec na `(` a na konci
    řádku: klub jiného autora ve vnořené závorce nebo za neuzavřenou závorkou na dalším řádku se nepočítá. Stejně
    se `(` hledá jen na řádku jména: neuzavřená závorka nebo „:(“ na dřívějším řádku jméno do závorky nedává.
    """
    line_start = text.rfind("\n", 0, start) + 1
    opened = text.rfind("(", line_start, start)
    if opened < 0 or text.find(")", opened, start) >= 0:
        return None
    stop = PAREN_STOP.search(text, end)
    return text[end:stop.start()] if stop else None


def club_after_name(inner: str, klub: str) -> bool:
    """Je klub prvním údajem za jménem v závorce („, ANO“ i „, ANO, předkladatel“)? Klub dál v závorce
    („, ODS, ANO“, „, ODS a Jana Malá, ANO“) se nepočítá."""
    return re.match(rf"{NAME_COMMA}(?:{_club_pattern(klub)})", inner) is not None


def club_before(text: str, start: int, klub: str) -> bool:
    """Stojí klub v `CLUB_BEFORE` znacích před pozicí `start` (před jménem)? Klub v závorce, která v tom úseku
    končí nebo začíná (klub jiného autora, „Jan Novák (ANO), Jana Malá“), se nepočítá."""
    lo = max(0, start - CLUB_BEFORE)
    cut = max(text.rfind("(", lo, start), text.rfind(")", lo, start))
    lo = cut + 1 if cut >= 0 else lo
    return any(m.start() >= lo for m in re.finditer(_club_pattern(klub), text[:start]))


def caption_authors(card: dict) -> list[dict]:
    """Autoři zadaných PN z `card["pn_autor"]`, které má jmenovat popisek.

    Nejvýš dva různí autoři (dvojice jméno + klub) v pořadí karty, každý se všemi svými čísly PN:
    `{"jmeno", "klub", "cisla"}`. Klub chybí (`None`), když ho server neuvádí.
    """
    rows = card.get("pn_autor")
    authors: list[dict] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("jmeno"), str) or not row["jmeno"].strip():
            continue
        jmeno = row["jmeno"].strip()
        klub = row["klub"].strip() if isinstance(row.get("klub"), str) and row["klub"].strip() else None
        author = next((a for a in authors if (a["jmeno"], a["klub"]) == (jmeno, klub)), None)
        if author is None:
            if len(authors) == 2:
                continue
            author = {"jmeno": jmeno, "klub": klub, "cisla": []}
            authors.append(author)
        cislo = row.get("cislo")
        if isinstance(cislo, int) and not isinstance(cislo, bool) and cislo not in author["cisla"]:
            author["cisla"].append(cislo)
    return authors


VOWELS = "aeiouyáéíóúůýě"


def _surname_stem(jmeno: str) -> str:
    """Kmen příjmení, který zůstane i ve skloňovaném tvaru; poslední slovo jména, u spojovníku první část.

    Pohyblivé e na konci (e + souhláska) zmizí s poslední souhláskou („Pavel“ → „Pav“, „Zedníček“ → „Zedníč“,
    „Poslanec“ → „Poslan“), koncová samohláska zmizí („Jurečka“ → „Jurečk“, „Malá“ → „Mal“), jinak zůstane celé
    příjmení („Novák“, aby „Novela“ autora nesplnila). Nejméně tři znaky.
    """
    surname = jmeno.split()[-1].split("-")[0]
    low = surname.lower()
    if len(low) >= 2 and low[-2] == "e" and low[-1].isalpha() and low[-1] not in VOWELS:
        stem = surname[:-2]
    elif low and low[-1] in VOWELS:
        stem = surname[:-1]
    else:
        stem = surname
    return stem if len(stem) >= 3 else surname[:3]


def _names_author(caption: str, jmeno: str) -> bool:
    """Jmenuje popisek autora? Slovo, které začíná kmenem příjmení (rozlišuje velikost písmen)."""
    return re.search(rf"(?<!\w){re.escape(_surname_stem(jmeno))}", caption) is not None


def _club_error(caption: str, jmeno: str, klub: str, which: str) -> str | None:
    """Klub autora u jeho jména: v závorce za prvním výskytem celého jména, jako první údaj za jménem v téže
    závorce, ve které jméno stojí („(Jan Novák, ANO)“, „(Jan Novák, ANO, předkladatel)“), nebo v `CLUB_BEFORE`
    znacích před ním („za ANO Jan Novák“). Bez celého jména (skloňovaný tvar) stačí klub kdekoli v popisku.

    Jméno v závorce, za kterým následuje jiný text než klub („(Jan Novák, ODS)“, „(Jan Novák, ODS a Jana Malá,
    ANO)“), je chyba i se správným klubem dál v závorce nebo před jménem: klub patří k nejbližšímu jménu."""
    missing = f"popisek neuvádí klub autora {which}: „{jmeno} ({klub})“"
    found = find_name(caption, jmeno)
    if found is None:
        return None if club_matches(caption, klub) else missing
    start, end, paren = found
    if paren is not None:
        return None if club_matches(paren, klub) else (
            f"popisek uvádí u autora {which} jiný klub: „{jmeno} ({paren})“, ze serveru ({klub})")
    inner = name_in_paren(caption, start, end)
    if inner is not None and inner.strip():
        return None if club_after_name(inner, klub) else (
            f"popisek uvádí u autora {which} jiný klub nebo text za jménem: „{jmeno}{inner.rstrip()}“, "
            f"ze serveru ({klub})")
    if club_before(caption, start, klub):
        return None
    # jméno v závorce: chyba radí tvar bez vnořené závorky
    return missing if inner is None else f"popisek neuvádí klub autora {which}: „{jmeno}, {klub}“"


def _author_errors(card: dict, caption: str) -> list[str]:
    """Video k vybraným PN: popisek jmenuje autora (stačí kmen příjmení) a u jeho jména klub, když ho server
    uvádí."""
    errs: list[str] = []
    for a in caption_authors(card):
        which = "PN " + ", ".join(str(n) for n in a["cisla"]) if a["cisla"] else "PN"
        if not _names_author(caption, a["jmeno"]):
            errs.append(f"popisek neuvádí autora {which} ({a['jmeno']})")
        club_err = _club_error(caption, a["jmeno"], a["klub"], which) if a["klub"] else None
        if club_err:
            errs.append(club_err)
    return errs


def validate_variant(card: dict, caption: str, *, template: str, ct: int, reason_keys: list[str],
                     fmt: str | None, focus_pn: list[int] | None = None,
                     focus_info: list[dict] | None = None) -> list[str]:
    """Chyby karty a popisku česky; prázdný seznam = v pořádku. focus_pn = video k vybraným PN (/pn),
    focus_info = jejich údaje z claimu (u formátu pribeh výsledek hlasování ze serveru)."""
    errs: list[str] = []
    is_v3 = template == "template_v3"
    is_pribeh = template == "template_pn_v1"
    required = V3_REQUIRED if is_v3 else PRIBEH_REQUIRED if is_pribeh else V2_REQUIRED
    for key in required:
        if key not in card or (key != "reasons" and _empty(card[key])):
            errs.append(f"chybí pole {key}")
    for key in PRESENT:
        if key not in card:
            errs.append(f"chybí pole {key}")
    if card.get("ct") != ct:
        errs.append(f"karta má ct {card.get('ct')!r}, zadání ct {ct}")
    allowed = set(reason_keys)
    for reason in card.get("reasons") or []:
        key = reason.get("key", "") if isinstance(reason, dict) else ""
        if key not in allowed:
            errs.append(f"klíč důvodu {key!r} tisk nemá (razítko by nebylo doložené)")
    if is_v3 or is_pribeh:
        if card.get("format") != fmt:
            errs.append(f"formát karty {card.get('format')!r}, zadání {fmt!r}")
        elif is_pribeh:
            errs += _pribeh_block(card, focus_pn, focus_info)
        elif fmt:
            errs += _v3_block(card, fmt)
    if len(caption) > CAPTION_LIMIT:
        errs.append(f"popisek má {len(caption)} znaků, limit je {CAPTION_LIMIT}")
    if CLOSING not in caption:
        errs.append(f"popisek nemá větu „{CLOSING}“")
    if focus_pn:
        errs += _focus_errors(card, caption, focus_pn)
    if card.get("pn_autor"):  # doplňuje normalizace z údajů serveru o zadaných PN
        errs += _author_errors(card, caption)
    return errs


def _overflow_text(box: dict) -> str:
    """„přetéká o 3 px (hook.unit 7 znaků, title 41 znaků)“: o kolik a která pole karty to způsobují."""
    px = box.get("overflow_px") or 0
    text = f"přetéká o {px} px" if px > 0 else "text přetéká"
    fields = [f"{o['m']} {o['len']} znaků" for o in box.get("over") or []]
    return f"{text} ({', '.join(fields)})" if fields else text


def check_layout(card: dict, template: str, orientation: str = "portrait") -> list[str]:
    """Bezpečná zóna a přetečení textu po vměstnání (potřebuje Chromium).

    Na výšku platí zóny Instagramu (SAFE), na šířku (1920 × 1080) render.LANDSCAPE_SAFE px dokola; texty chyb stejné.
    """
    from ..render import LANDSCAPE_SAFE, SIZES, measure_blocks

    if orientation == "landscape":
        width, height = SIZES["landscape"]
        safe = {"top": LANDSCAPE_SAFE, "bottom": height - LANDSCAPE_SAFE,
                "left": LANDSCAPE_SAFE, "right": width - LANDSCAPE_SAFE}
    else:
        safe = SAFE
    boxes = measure_blocks(Card.from_json(card), template, orientation=orientation)
    errs: list[str] = []
    for name, b in boxes.items():
        if not name.startswith("scene:"):
            continue
        if b.get("overflow"):
            errs.append(f"{name}: {_overflow_text(b)}")
        if b.get("cut_mid_word"):
            errs.append(f"{name}: text useknutý uprostřed slova")
        if b["top"] < safe["top"] or b["bottom"] > safe["bottom"] or b["left"] < safe["left"] or b["right"] > safe["right"]:
            errs.append(f"{name}: mimo bezpečnou zónu")
    return errs
