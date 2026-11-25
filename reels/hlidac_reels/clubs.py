"""Popisky poslaneckých klubů ze zkratek psp.cz (pn.klub): stejné páry jako Clubs::label() na webu
a záloha KLUBY v template_v3/reel.js (shodu hlídá tests/test_clubs.py). Klub se jen zobrazuje."""
from __future__ import annotations

LABELS: dict[str, str] = {
    "ANO2011": "ANO",
    "TOP09": "TOP 09",
    "MS": "Motoristé",
    "Nezařaz": "nezařazení",
    "Nezařazení": "nezařazení",
}


def label(club: str | None) -> str:
    """Zkratka klubu v podobě, jak se běžně píše; bez klubu prázdný řetězec, neznámá zkratka beze změny."""
    club = (club or "").strip()
    return LABELS.get(club, club)
