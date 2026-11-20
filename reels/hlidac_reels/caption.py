"""Konce popisků Reels podle sítě (FB umí klikací odkaz, IG ne)."""
from __future__ import annotations

from typing import Literal

SITE = "https://cosepece.cz"
IG_ENDING = "\n\nOdkaz na celý rozbor v profilu."


def for_network(caption: str, ct: int | None, network: Literal["fb", "ig"]) -> str:
    """Popisek s koncem pro danou síť: FB odkaz na rozbor tisku (bez ct na web), IG odkaz v profilu."""
    if network == "ig":
        return caption + IG_ENDING
    if network == "fb":
        if ct is None:
            return f"{caption}\n\n{SITE}"
        return f"{caption}\n\nCelý rozbor: {SITE}/tisk.php?ct={ct}"
    raise ValueError(f"neznámá síť: {network!r}")
