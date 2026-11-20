"""Karta hlavního nálezu: snímek dat, ze kterého vzniká video (sloupec reel_queue.card_json)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass
class Card:
    ct: int
    nazev: str
    faze: str
    score: int
    title: str
    perex: str
    quote: str
    quote_source: str
    verified: bool
    reasons: list[dict] = field(default_factory=list)
    url: str = ""
    data_date: str = ""
    # volitelná pole pro bloky šablony (typ_navrhu, predlozeno, timeline, pn, deadline, shift…)
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: str | dict) -> "Card":
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
        names = set(cls.__dataclass_fields__.keys()) - {"extras"}
        # neznámé klíče (volitelné bloky šablony) se nesou v extras; šablona, která je nezná, je ignoruje
        return cls(**{k: v for k, v in data.items() if k in names},
                   extras={k: v for k, v in data.items() if k not in names})

    def to_dict(self) -> dict:
        data = asdict(self)
        extras = data.pop("extras")
        return {**extras, **data}
