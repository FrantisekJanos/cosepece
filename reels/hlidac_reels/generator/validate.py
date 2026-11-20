"""Kontrola karty a popisku od modelu, než se video vyrenderuje (pravidla obsahu z runbooku)."""
from __future__ import annotations

from ..card import Card
from ..enqueue import CAPTION_LIMIT

CLOSING = "Hodnotíme postup, ne politický obsah."
V2_REQUIRED = ("ct", "nazev", "faze", "score", "title", "perex", "quote", "quote_source", "reasons")
V3_REQUIRED = V2_REQUIRED[:6] + ("reasons", "format", "hook", "zdroje")
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
        body = block.get("body") or []
        if not body:
            errs.append("zmena: chybí body")
        for i, b in enumerate(body, 1):
            for key in ("citace", "zdroj"):
                if _empty(b.get(key)):
                    errs.append(f"zmena: bod {i} nemá {key}")
    if fmt == "rozprava":
        for side in ("pro", "namitky", "odpovedi"):
            for i, b in enumerate((block.get(side) or {}).get("body") or [], 1):
                if _empty(b.get("url")):
                    errs.append(f"rozprava: {side} bod {i} nemá url")
    return errs


def validate_variant(card: dict, caption: str, *, template: str, ct: int, reason_keys: list[str],
                     fmt: str | None) -> list[str]:
    """Chyby karty a popisku česky; prázdný seznam = v pořádku."""
    errs: list[str] = []
    is_v3 = template == "template_v3"
    for key in V3_REQUIRED if is_v3 else V2_REQUIRED:
        if key not in card or (key != "reasons" and _empty(card[key])):
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
    return errs


def check_layout(card: dict, template: str) -> list[str]:
    """Bezpečná zóna a přetečení textu po vměstnání (potřebuje Chromium)."""
    from ..render import measure_blocks

    boxes = measure_blocks(Card.from_json(card), template)
    errs: list[str] = []
    for name, b in boxes.items():
        if not name.startswith("scene:"):
            continue
        if b.get("overflow"):
            errs.append(f"{name}: text přetéká")
        if b.get("cut_mid_word"):
            errs.append(f"{name}: text useknutý uprostřed slova")
        if b["top"] < SAFE["top"] or b["bottom"] > SAFE["bottom"] or b["left"] < SAFE["left"] or b["right"] > SAFE["right"]:
            errs.append(f"{name}: mimo bezpečnou zónu")
    return errs
