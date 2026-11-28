"""Registr zdrojů konverzace (spec 9.1, 9.3 vrstva B).

Každá položka, kterou model dostal, má v konverzaci stálé číslo N; stejný klíč má stejné číslo.
Model píše jen `[N]`, čísla mimo registr server z odpovědi odstraní.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from .nastroje import Polozka

_ODKAZ = re.compile(r"\[(\d+)\]")
# Neznámý odkaz se odstraní i s mezerami před ním (ne s koncem řádku).
_ODKAZ_S_MEZEROU = re.compile(r"[ \t]*\[(\d+)\]")


@dataclass
class Zdroj:
    n: int
    klic: str
    typ: str
    titulek: str
    meta: dict = field(default_factory=dict)
    citovan: bool = False


class Registr:
    def __init__(self, zdroje: Iterable[Zdroj] = ()):
        self._podle_n: dict[int, Zdroj] = {}
        self._podle_klice: dict[str, Zdroj] = {}
        self._nove: list[Zdroj] = []
        for z in zdroje:
            self._podle_n[z.n] = z
            self._podle_klice[z.klic] = z

    def pridej(self, p: Polozka) -> int:
        """Vrátí číslo zdroje; nový klíč dostane číslo o jedna vyšší než dosud nejvyšší."""
        z = self._podle_klice.get(p.klic)
        if z is not None:
            return z.n
        n = max(self._podle_n, default=0) + 1
        z = Zdroj(n, p.klic, p.typ, p.titulek, dict(p.meta), False)
        self._podle_n[n] = z
        self._podle_klice[p.klic] = z
        self._nove.append(z)
        return n

    def podle_n(self, n: int) -> Zdroj | None:
        return self._podle_n.get(n)

    def oznac_citovane(self, text: str) -> list[int]:
        """Označí zdroje citované v textu; vrátí jejich čísla v pořadí prvního výskytu (jen známá)."""
        vysledek: list[int] = []
        for m in _ODKAZ.finditer(text):
            n = int(m.group(1))
            z = self._podle_n.get(n)
            if z is not None and n not in vysledek:
                z.citovan = True
                vysledek.append(n)
        return vysledek

    def odstran_nezname(self, text: str) -> tuple[str, int]:
        """Odstraní `[N]`, které v registru nejsou; vrátí upravený text a počet odstraněných."""
        pocet = 0

        def nahrad(m: re.Match) -> str:
            nonlocal pocet
            if int(m.group(1)) in self._podle_n:
                return m.group(0)
            pocet += 1
            return ""

        return _ODKAZ_S_MEZEROU.sub(nahrad, text), pocet

    def radky(self, max_radku: int = 40) -> str:
        """Řádky `[N] typ, klíč, titulek` pro model: citované napřed, pak nejnovější, nejvýš max_radku."""
        poradi = sorted(self._podle_n.values(), key=lambda z: (not z.citovan, -z.n))
        return "\n".join(f"[{z.n}] {z.typ}, {z.klic}, {z.titulek}" for z in poradi[:max_radku])

    def nove(self) -> list[Zdroj]:
        """Zdroje přidané od vytvoření registru (k uložení); změny `citovan` u starých vrací oznac_citovane."""
        return list(self._nove)
