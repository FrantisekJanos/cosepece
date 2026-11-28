"""Ceník modelů a výpočet ceny volání z `usage` (USD za milion tokenů)."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import NamedTuple


class Cena(NamedTuple):
    """Ceny v USD za milion tokenů."""

    vstup: float
    vystup: float
    cache_cteni: float
    cache_zapis: float  # zápis do cache s TTL 5 minut


# Tabulka 10.1 ze spec (2026-11-03). Ceny cache u Haiku jsou dopočítané z násobků.
CENY: dict[str, Cena] = {
    "claude-sonnet-5-5": Cena(2.00, 10.00, 0.10, 2.50),
    "claude-haiku-5-5": Cena(0.10, 0.50, 0.01, 0.125),
}


def nacti_cenik(env: Mapping[str, str]) -> dict[str, Cena]:
    """Vestavěný ceník s přepisem z `CHAT_CENIK` (JSON `{model: [vstup, výstup, čtení, zápis]}`)."""
    cenik = dict(CENY)
    surove = env.get("CHAT_CENIK", "").strip()
    if not surove:
        return cenik
    try:
        data = json.loads(surove)
        if not isinstance(data, dict):
            raise TypeError("očekáván objekt")
        for model, ceny in data.items():
            if not isinstance(ceny, (list, tuple)) or len(ceny) != 4:
                raise TypeError(f"{model}: očekány 4 ceny")
            cenik[str(model)] = Cena(*(float(c) for c in ceny))
    except (ValueError, TypeError) as e:
        raise ValueError(f"CHAT_CENIK: neplatná hodnota ({e})") from e
    return cenik


def cena_usd(
    model: str,
    vstup: int,
    vystup: int,
    cache_cteni: int = 0,
    cache_zapis: int = 0,
    cenik: dict[str, Cena] | None = None,
) -> float:
    """Cena volání v USD. Neznámý model = `KeyError` (cenu nikdy neodhadujeme)."""
    c = (cenik if cenik is not None else CENY)[model]
    return (
        vstup * c.vstup
        + vystup * c.vystup
        + cache_cteni * c.cache_cteni
        + cache_zapis * c.cache_zapis
    ) / 1_000_000
