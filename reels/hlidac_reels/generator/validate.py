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


def _surname_stem(jmeno: str) -> str:
    """Kmen příjmení, který zůstane i ve skloňovaném tvaru: poslední slovo jména (u spojovníku první část)
    bez posledních dvou znaků, nejméně tři znaky („Poslanec“ → „Poslan“, „Malá“ → „Mal“)."""
    surname = jmeno.split()[-1].split("-")[0]
    return surname[:max(3, len(surname) - 2)]


def _author_errors(card: dict, caption: str) -> list[str]:
    """Video k vybraným PN: popisek jmenuje autora (stačí kmen příjmení) a klub, když ho server uvádí."""
    errs: list[str] = []
    for a in caption_authors(card):
        which = "PN " + ", ".join(str(n) for n in a["cisla"]) if a["cisla"] else "PN"
        if _surname_stem(a["jmeno"]) not in caption:
            errs.append(f"popisek neuvádí autora {which} ({a['jmeno']})")
        if a["klub"] and not has_word(caption, a["klub"]):
            errs.append(f"popisek neuvádí klub autora {which}: „{a['jmeno']} ({a['klub']})“")
    return errs


def validate_variant(card: dict, caption: str, *, template: str, ct: int, reason_keys: list[str],
                     fmt: str | None, focus_pn: list[int] | None = None) -> list[str]:
    """Chyby karty a popisku česky; prázdný seznam = v pořádku. focus_pn = video k vybraným PN (/pn)."""
    errs: list[str] = []
    is_v3 = template == "template_v3"
    for key in V3_REQUIRED if is_v3 else V2_REQUIRED:
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
    if is_v3:
        if card.get("format") != fmt:
            errs.append(f"formát karty {card.get('format')!r}, zadání {fmt!r}")
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


def check_layout(card: dict, template: str) -> list[str]:
    """Bezpečná zóna a přetečení textu po vměstnání (potřebuje Chromium)."""
    from ..render import measure_blocks

    boxes = measure_blocks(Card.from_json(card), template)
    errs: list[str] = []
    for name, b in boxes.items():
        if not name.startswith("scene:"):
            continue
        if b.get("overflow"):
            errs.append(f"{name}: {_overflow_text(b)}")
        if b.get("cut_mid_word"):
            errs.append(f"{name}: text useknutý uprostřed slova")
        if b["top"] < SAFE["top"] or b["bottom"] > SAFE["bottom"] or b["left"] < SAFE["left"] or b["right"] > SAFE["right"]:
            errs.append(f"{name}: mimo bezpečnou zónu")
    return errs
