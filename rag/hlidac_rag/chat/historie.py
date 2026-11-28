"""Skládání zpráv pro Messages API z historie konverzace (spec 5.5 a 9.3).

Historie jde jako čistý text (bez bloků tool_use, tool_result a přemýšlení), u odpovědi řádek
„použité zdroje: [N]“. Nejstarší tahy se při překročení stropu vypouštějí celé a model dostane větu,
že je nevidí. Karta kontextu jde za aktuální otázkou jako syntetický pár tool_use / tool_result
(záloha: ohraničený blok v uživatelské zprávě). `cache_control` sem nepatří, řeší ho llm.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .registr import Registr

# Odhad pro češtinu s hlavičkami: změřeno 2,2 znaku na token (19. 11. 2026, viz embed/indexer.py).
ZNAKU_NA_TOKEN = 2.2
# Po překročení stropu se historie zkrátí najednou na tento podíl stropu (rezerva na několik dalších tahů).
CIL_PO_ZKRACENI = 0.55
KONTEXT_ID = "kontext_1"
KONTEXT_NASTROJ = "kontext_obrazovky"


@dataclass
class Tah:
    text_uzivatele: str
    znacka: str
    text_bota: str
    zdroje: list[int] = field(default_factory=list)


def odhad_tokenu(text: str) -> int:
    return math.ceil(len(text) / ZNAKU_NA_TOKEN)


def _uzivatel(znacka: str, text: str) -> str:
    return f"{znacka}\n{text}" if znacka else text


def _bot(tah: Tah) -> str:
    text = tah.text_bota.strip() or "(bez odpovědi)"
    if tah.zdroje:
        text += "\n\npoužité zdroje: " + " ".join(f"[{n}]" for n in tah.zdroje)
    return text


def _ohranic(karta: str) -> str:
    cista = karta.replace("<data_stranky>", "").replace("</data_stranky>", "")
    return f"<data_stranky>\n{cista}\n</data_stranky>"


def _veta(vynechano: int) -> str:
    return f"[Starších {vynechano} zpráv této konverzace nevidíš doslova.]\n\n"


def _zacatek_historie(tokeny_tahu: list[int], max_tokenu: int) -> int:
    """Index prvního tahu, který model uvidí.

    Zkracuje se po blocích (spec 9.3): tahy se procházejí od nejstaršího a teprve když historie přeleze
    strop, vypustí se najednou nejstarší tahy, dokud nezbude nejvýš CIL_PO_ZKRACENI stropu. Další krátké
    tahy hranici nemění, takže první zpráva i věta o vypuštěných zprávách zůstanou stejné a prefix historie
    se dál čte z cache. Výsledek závisí jen na tazích: stejné tahy dají vždy stejnou hranici.
    Po prvním zkrácení se do stropu počítá i věta o vypuštěných zprávách.
    """
    rezerva = odhad_tokenu(_veta(10 ** 6))
    od, soucet = 0, 0
    for i, t in enumerate(tokeny_tahu):
        soucet += t
        if soucet + (rezerva if od else 0) <= max_tokenu:
            continue
        cil = max_tokenu * CIL_PO_ZKRACENI - rezerva
        while od <= i and soucet > cil:
            soucet -= tokeny_tahu[od]
            od += 1
    return od


def sestav(tahy: list[Tah], otazka: str, znacka: str, karta: str | None, registr: Registr,
           max_tokenu: int, karta_jako: str = "tool_result") -> list[dict]:
    """Zprávy pro API: historie (strop max_tokenu jen pro ni, zkracuje se po blocích), aktuální otázka
    s registrem zdrojů, karta."""
    if karta_jako not in ("tool_result", "blok"):
        raise ValueError(f"karta_jako: {karta_jako!r}")
    pary = [(_uzivatel(t.znacka, t.text_uzivatele), _bot(t)) for t in tahy]
    od = _zacatek_historie([odhad_tokenu(u) + odhad_tokenu(b) for u, b in pary], max_tokenu)
    pary = pary[od:]
    vynechano = 2 * od

    zpravy: list[dict] = []
    for u, b in pary:
        zpravy.append({"role": "user", "content": u})
        zpravy.append({"role": "assistant", "content": b})

    aktualni = _uzivatel(znacka, otazka)
    radky = registr.radky()
    if radky:
        aktualni = ("<zdroje_konverzace>\n" + radky + "\n</zdroje_konverzace>\n\n") + aktualni
    if karta is not None and karta_jako == "blok":
        aktualni += "\n\n" + _ohranic(karta)
    zpravy.append({"role": "user", "content": aktualni})

    if vynechano:
        zpravy[0]["content"] = _veta(vynechano) + zpravy[0]["content"]

    if karta is not None and karta_jako == "tool_result":
        zpravy.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": KONTEXT_ID, "name": KONTEXT_NASTROJ, "input": {}}]})
        zpravy.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": KONTEXT_ID, "content": karta}]})
    return zpravy
