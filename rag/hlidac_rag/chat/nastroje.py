"""Adaptér nástrojů chatbota (úloha 4). Zatím jen datové typy, které používá registr zdrojů (úloha 6)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Polozka:
    """Jedna položka, kterou model dostal z nástroje (tisk, PN, celek textu, projev…), pod kanonickým klíčem."""
    klic: str
    typ: str
    titulek: str
    text: str | None
    meta: dict = field(default_factory=dict)


@dataclass
class Vysledek:
    """Výsledek volání nástroje: text pro model, položky do registru a příznak chyby."""
    text: str
    polozky: list[Polozka] = field(default_factory=list)
    chyba: bool = False
