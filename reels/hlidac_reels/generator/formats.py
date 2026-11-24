"""Výběr dvou formátů v3 pro sadu: pravidlo podle klíčů důvodů skóre, ne úsudek modelu."""
from __future__ import annotations

FORMATS = ("cesta", "pn", "zmena", "rozprava")
# klíče důvodů skóre, které formát dokládají (data/reels-ukazky/v3/formaty.md)
FORMAT_TRIGGERS: dict[str, tuple[str, ...]] = {
    "cesta": ("deadline_cut", "short_gap", "p90_passed"),
    "pn": ("late_pn", "pn_other_law"),
}


# video k vybraným PN (/pn): oba formáty ukazují obsah PN (kdo co přidal, co se mění)
FOCUS_FORMATS = ["pn", "zmena"]


def pick_formats(reason_keys: list[str], has_amendments: bool, hint: str | None = None,
                 focus: bool = False) -> list[str]:
    """Dva různé formáty v3. reason_keys jsou seřazené podle váhy ve skóre (nejtěžší první).

    `rozprava` jen z ručního hintu; automatika ji nevybírá. focus = video k vybraným PN: vždy FOCUS_FORMATS,
    hint se nebere (/pn formát nezadává a `cesta` ani `rozprava` o obsahu PN nevypráví).
    """
    if hint is not None and hint not in FORMATS:
        raise ValueError(f"neznámý formát {hint!r}, povolené: {', '.join(FORMATS)}")
    if focus:
        return list(FOCUS_FORMATS)
    picked: list[str] = [hint] if hint else []
    for key in reason_keys:
        for fmt, keys in FORMAT_TRIGGERS.items():
            if key in keys and fmt not in picked:
                picked.append(fmt)
    fill = ["zmena", "pn"] if has_amendments else ["pn", "cesta"]
    for fmt in fill + ["cesta", "pn"]:
        if fmt not in picked:
            picked.append(fmt)
    return picked[:2]
