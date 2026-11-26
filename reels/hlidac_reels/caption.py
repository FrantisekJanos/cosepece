"""Konce popisků Reels podle sítě (FB umí klikací odkaz, IG ne, Threads má limit 500 znaků, X 280 vážených znaků)."""
from __future__ import annotations

import re
import urllib.parse
from typing import Literal

SITE = "https://cosepece.cz"
IG_ENDING = "\n\nOdkaz na celý rozbor v profilu."
THREADS_LIMIT = 500
THREADS_FIRST_MAX = 440
SENTENCE_ENDS = (". ", "? ", "! ", "…")
X_LIMIT = 280
X_URL_LENGTH = 23  # každý odkaz X počítá jako 23 znaků (t.co)
X_INTENT = "https://x.com/intent/post?text="
# twitter-text v3: kódové body v těchto rozsazích váží 1, ostatní (CJK, emoji…) 2
_X_LIGHT = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
_URL_RE = re.compile(r"https?://\S+")

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


def x_length(text: str) -> int:
    """Délka textu, jak ji počítá X: odkaz http(s) = 23, latinka včetně české diakritiky 1, ostatní znaky 2.

    Emoji složená ze sekvence (ZWJ) se tu počítají po kódových bodech, tedy spíš víc než na X (bezpečná strana)."""
    total = 0
    pos = 0
    for m in _URL_RE.finditer(text):
        total += _x_chars(text[pos:m.start()]) + X_URL_LENGTH
        pos = m.end()
    return total + _x_chars(text[pos:])


def _x_chars(text: str) -> int:
    return sum(1 if any(a <= ord(ch) <= b for a, b in _X_LIGHT) else 2 for ch in text)


def x_text(caption: str, ct: int | None) -> str:
    """Text pro X: první odstavec zkrácený tak, aby celek s odkazem měl nejvýš 280 znaků podle počítání X
    (stejná logika jako threads_text)."""
    first = _PARAGRAPH_RE.split(caption, maxsplit=1)[0].strip()
    ending = _link_ending(ct)
    limit = X_LIMIT - x_length(ending)
    body = _shorten(first, limit)
    # _shorten počítá znaky; emoji a odkazy v textu váží jinak, proto zkracovat, dokud se celek nevejde
    while x_length(body) > limit and limit > 1:
        limit -= max(1, x_length(body) - (X_LIMIT - x_length(ending)))
        body = _shorten(first, limit)
    text = body + ending
    if x_length(text) > X_LIMIT:
        raise ValueError(f"text pro X má {x_length(text)} znaků (limit {X_LIMIT})")
    return text


def x_intent_url(text: str) -> str:
    """Odkaz, který otevře nový příspěvek na X s předvyplněným textem."""
    return X_INTENT + urllib.parse.quote(text, safe="")
