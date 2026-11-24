"""Deterministická normalizace karty a popisku od modelu před kontrolou.

Fakta se nemění: text modelu se jen přeskládá nebo zkrátí a doplní se údaje ze serveru. Každý zásah
se vrací jako věta pro `variant-N/normalize.json`.
"""
from __future__ import annotations

import copy
import re

from .validate import CLOSING

# „800 mil. Kč“, „1,5 mld. Kč“, „2 000 mil. Kč“: číslo (tisíce oddělené mezerou, desetinná čárka) + jednotka
# začínající písmenem a bez číslic; „10 → 4“, „10 z 12“, „4 roky místo 10“ ani samotné „2 000“ se nerozdělí
# (jednotka s dalším číslem by změnila, jak se fakt čte)
_NUM_UNIT = re.compile(r"^\s*(\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(,\d+)?\s+([^\W\d_][^\d]*?)\s*$")


def _split_hook(card: dict, notes: list[str]) -> None:
    hook = card.get("hook")
    if not isinstance(hook, dict) or not isinstance(hook.get("big"), str):
        return
    m = _NUM_UNIT.match(hook["big"])
    if not m:
        return
    number, unit = m.group(1) + (m.group(2) or ""), m.group(3)
    old_unit = hook.get("unit") if isinstance(hook.get("unit"), str) else ""
    if not old_unit.strip():
        new_unit = unit
    elif unit in old_unit:
        new_unit = old_unit
    else:
        new_unit = f"{unit} {old_unit.strip()}"
    notes.append(f"hook.big „{hook['big']}“ rozdělen: big „{number}“, unit „{new_unit}“ (dříve „{old_unit}“)")
    hook["big"], hook["unit"] = number, new_unit


def _drop_uncited(card: dict, notes: list[str]) -> None:
    """Body `zmena` bez citace vynechá; když citaci nemá žádný bod, nechá je (kontrola pak vrátí chybu)."""
    block = card.get("zmena")
    if not isinstance(block, dict) or not isinstance(block.get("body"), list):
        return
    body = block["body"]
    cited = [b for b in body if isinstance(b, dict) and isinstance(b.get("citace"), str) and b["citace"].strip()]
    if not cited or len(cited) == len(body):
        return
    kept = {id(b) for b in cited}
    dropped = [i for i, b in enumerate(body, 1) if id(b) not in kept]
    notes.append(f"zmena: vynechány body bez citace ({', '.join(map(str, dropped))})")
    block["body"] = cited


def normalize(card: dict, caption: str, *, fmt: str | None,
              focus_info: list[dict]) -> tuple[dict, str, list[str]]:
    """Vrací novou kartu, nový popisek a seznam zásahů; vstupní karta zůstane beze změny.

    focus_info = údaje o zadaných PN z claimu serveru (/pn); zatím žádné pravidlo je nedoplňuje.
    """
    card = copy.deepcopy(card)
    notes: list[str] = []
    _split_hook(card, notes)
    if fmt == "zmena":
        _drop_uncited(card, notes)
    if CLOSING not in caption:
        caption = f"{caption.rstrip()}\n\n{CLOSING}" if caption.strip() else CLOSING
        notes.append(f"popisek: doplněna věta „{CLOSING}“")
    return card, caption, notes
