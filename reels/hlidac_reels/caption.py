"""Konce popisků Reels podle sítě (FB umí klikací odkaz, IG ne, Threads má limit 500 znaků)."""
from __future__ import annotations

import re
from typing import Literal

SITE = "https://cosepece.cz"
IG_ENDING = "\n\nOdkaz na celý rozbor v profilu."
THREADS_LIMIT = 500
THREADS_FIRST_MAX = 440
SENTENCE_ENDS = (". ", "? ", "! ", "…")

_PARAGRAPH_RE = re.compile(r"\n[ \t\r]*\n")


def _link_ending(ct: int | None) -> str:
    if ct is None:
        return f"\n\n{SITE}"
    return f"\n\nCelý rozbor: {SITE}/tisk/{ct}"


def _shorten(text: str, limit: int = THREADS_FIRST_MAX) -> str:
    """Zkrátí text na poslední celou větu do limit znaků; bez konce věty na slovo + „…“."""
    if len(text) <= limit:
        return text
    best = 0
    for end in SENTENCE_ENDS:
        # interpunkce (první znak značky) musí ležet do limit znaků
        pos = text.rfind(end, 0, limit - 1 + len(end))
        if pos >= 0:
            best = max(best, pos + 1)
    if best > 0:
        return text[:best]
    space = text.rfind(" ", 0, limit)
    cut = text[:space].rstrip() if space > 0 else ""
    if not cut:
        cut = text[: limit - 1]
    return cut + "…"


def threads_text(caption: str, ct: int | None) -> str:
    """Text pro Threads: první odstavec (do prázdného řádku) zkrácený do 440 znaků + odkaz, celkem ≤ 500."""
    first = _PARAGRAPH_RE.split(caption, maxsplit=1)[0].strip()
    text = _shorten(first) + _link_ending(ct)
    if len(text) > THREADS_LIMIT:
        raise ValueError(f"text pro Threads má {len(text)} znaků (limit {THREADS_LIMIT})")
    return text


def for_network(caption: str, ct: int | None, network: Literal["fb", "ig", "threads"]) -> str:
    """Popisek s koncem pro danou síť: FB odkaz na rozbor tisku (bez ct na web), IG odkaz v profilu,
    Threads zkrácený první odstavec s odkazem."""
    if network == "ig":
        return caption + IG_ENDING
    if network == "fb":
        return caption + _link_ending(ct)
    if network == "threads":
        return threads_text(caption, ct)
    raise ValueError(f"neznámá síť: {network!r}")
