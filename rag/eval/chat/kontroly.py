"""Programové kontroly odpovědí chatu a načtení eval scénářů (F0 i chat-eval přes endpoint).

Scénář: {"id", "sada", "kontext": {...}|null, "tahy": [tah, ...], "rucne", nepovinně "jen_endpoint", "kour"}.
Tah: {"text", nepovinně "kontext", "must", "must_not", "must_re", "nastroje_any", "cituje", "max_cena_usd"}.

- must / must_not: podřetězec bez ohledu na diakritiku a velikost písmen.
- must_re: regulární výraz, stačí shoda v původním textu nebo v textu bez diakritiky (malými písmeny).
- must_not_re: regulární výraz, který se nesmí najít ani v původním textu, ani v textu bez diakritiky. Pro zakázaná
  tvrzení, kde by podřetězec shodil i správnou odpověď („nebyl přijat“ obsahuje „byl přijat“): hranice slov \\b,
  záporná předpona (?<!ne), začátek věty. Vzory se píšou bez diakritiky a malými písmeny.
- cituje: true = odpověď obsahuje odkaz [N], false = neobsahuje, null = nekontroluje se.
- nastroje_any: aspoň jeden z nástrojů byl zavolán (předpona mcp__server__ se ignoruje).
- max_cena_usd: cena tahu nesmí stropu přesáhnout.
- ZAKAZANE_ODVOLANI se kontrolují v každém tahu (bot nesmí odvolat dřívější odpověď jen proto, že nevidí zdroj).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from hlidac_rag.tools import unaccent_py

SADY = ("zaklad", "kontext", "pamet", "poctivost", "citace", "injekce", "neutralita", "naklady")

# Fráze, kterými model odvolává vlastní dřívější odpověď (L1). Samotné „bez opory“ tu není:
# patří i do správné opravy tvrzení uživatele („to je bez opory v datech“).
ZAKAZANE_ODVOLANI: list[str] = [
    "omlouvám se, v předchozí",
    "jsem uvedl bez opory",
    "jsem to uvedl bez opory",
    "mýlil jsem se, když",
    "beru zpět",
]

_ODKAZ = re.compile(r"\[\d+\]")

_KLICE_SCENARE = {"id", "sada", "kontext", "tahy", "rucne", "jen_endpoint", "kour"}
_POVINNE_SCENARE = {"id", "sada", "kontext", "tahy", "rucne"}
_KLICE_TAHU = {"text", "kontext", "must", "must_not", "must_re", "must_not_re", "nastroje_any", "cituje",
               "max_cena_usd"}


@dataclass
class Vysledek:
    ok: bool
    selhani: list[str] = field(default_factory=list)


def _norm(s: str) -> str:
    return unaccent_py(s).lower()


def _nazev_nastroje(n: str) -> str:
    return n.rsplit("__", 1)[-1]


def _najde(vzor: str, odpoved: str, text: str) -> bool:
    """Shoda v původním textu nebo v textu bez diakritiky; ^ a $ platí i na začátku a konci řádku."""
    priznaky = re.IGNORECASE | re.MULTILINE
    return bool(re.search(vzor, odpoved, priznaky) or re.search(vzor, text, priznaky))


def zkontroluj(tah: dict, odpoved: str, nastroje: list[str], cena_usd: float) -> Vysledek:
    """Projde kontroly jednoho tahu. selhani: „must: x“, „must_not: x“, „must_re: x“, „must_not_re: x“,
    „cituje“, „nastroje_any“, „max_cena_usd“, „odvolani: x“ (v tomto pořadí)."""
    text = _norm(odpoved)
    selhani: list[str] = []
    for m in tah.get("must") or []:
        if _norm(m) not in text:
            selhani.append(f"must: {m}")
    for m in tah.get("must_not") or []:
        if _norm(m) in text:
            selhani.append(f"must_not: {m}")
    for vzor in tah.get("must_re") or []:
        if not _najde(vzor, odpoved, text):
            selhani.append(f"must_re: {vzor}")
    for vzor in tah.get("must_not_re") or []:
        if _najde(vzor, odpoved, text):
            selhani.append(f"must_not_re: {vzor}")
    cituje = tah.get("cituje")
    if cituje is not None and bool(_ODKAZ.search(odpoved)) != cituje:
        selhani.append("cituje")
    chtene = tah.get("nastroje_any") or []
    if chtene and not {_nazev_nastroje(n) for n in nastroje} & {_nazev_nastroje(n) for n in chtene}:
        selhani.append("nastroje_any")
    strop = tah.get("max_cena_usd")
    if strop is not None and cena_usd > strop:
        selhani.append("max_cena_usd")
    for fraze in ZAKAZANE_ODVOLANI:
        if _norm(fraze) in text:
            selhani.append(f"odvolani: {fraze}")
    return Vysledek(ok=not selhani, selhani=selhani)


def _chyba(kde: str, co: str) -> ValueError:
    return ValueError(f"scénář {kde}: {co}")


def _over_kontext(kde: str, kontext) -> None:
    if kontext is None:
        return
    if not isinstance(kontext, dict) or not isinstance(kontext.get("typ"), str) or not kontext["typ"]:
        raise _chyba(kde, "kontext musí být null nebo objekt s neprázdným typ")
    for k, v in kontext.items():
        if isinstance(v, bool) or not isinstance(v, (int, str)):
            raise _chyba(kde, f"kontext.{k} musí být číslo nebo text")


def _seznam_textu(kde: str, klic: str, hodnota) -> None:
    if not isinstance(hodnota, list) or not all(isinstance(x, str) and x for x in hodnota):
        raise _chyba(kde, f"{klic} musí být seznam neprázdných textů")


def _over_tah(kde: str, tah) -> None:
    if not isinstance(tah, dict):
        raise _chyba(kde, "tah musí být objekt")
    navic = set(tah) - _KLICE_TAHU
    if navic:
        raise _chyba(kde, f"neznámé klíče tahu {sorted(navic)}")
    if not isinstance(tah.get("text"), str) or not tah["text"].strip():
        raise _chyba(kde, "text tahu chybí")
    _over_kontext(kde, tah.get("kontext"))
    for klic in ("must", "must_not", "must_re", "must_not_re", "nastroje_any"):
        if klic in tah:
            _seznam_textu(kde, klic, tah[klic])
    for klic in ("must_re", "must_not_re"):
        for vzor in tah.get(klic, []):
            try:
                re.compile(vzor)
            except re.error as e:
                raise _chyba(kde, f"{klic} {vzor!r} není platný regulární výraz ({e})") from None
    if tah.get("cituje") is not None and not isinstance(tah["cituje"], bool):
        raise _chyba(kde, "cituje musí být true, false nebo null")
    cena = tah.get("max_cena_usd")
    if cena is not None and (isinstance(cena, bool) or not isinstance(cena, (int, float)) or cena <= 0):
        raise _chyba(kde, "max_cena_usd musí být kladné číslo nebo null")


def _over_scenar(s) -> None:
    if not isinstance(s, dict):
        raise ValueError("scénář musí být objekt")
    kde = str(s.get("id", "?"))
    chybi = _POVINNE_SCENARE - set(s)
    if chybi:
        raise _chyba(kde, f"chybí klíče {sorted(chybi)}")
    navic = set(s) - _KLICE_SCENARE
    if navic:
        raise _chyba(kde, f"neznámé klíče {sorted(navic)}")
    if not isinstance(s["id"], str) or not s["id"]:
        raise _chyba(kde, "id musí být neprázdný text")
    if s["sada"] not in SADY:
        raise _chyba(kde, f"sada {s['sada']!r} není z {SADY}")
    _over_kontext(kde, s["kontext"])
    if not isinstance(s["tahy"], list) or not s["tahy"]:
        raise _chyba(kde, "tahy musí být neprázdný seznam")
    for tah in s["tahy"]:
        _over_tah(kde, tah)
    if not isinstance(s["rucne"], str) or not s["rucne"].strip():
        raise _chyba(kde, "rucne musí být neprázdný text (zdroj ověření)")
    for klic in ("jen_endpoint", "kour"):
        if klic in s and not isinstance(s[klic], bool):
            raise _chyba(kde, f"{klic} musí být true nebo false")


def nacti_scenare(cesta) -> list[dict]:
    """Načte scénáře z JSON a ověří formát; při chybě ValueError s id scénáře."""
    data = json.loads(Path(cesta).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("soubor scénářů musí obsahovat seznam")
    videne: set[str] = set()
    for s in data:
        _over_scenar(s)
        if s["id"] in videne:
            raise _chyba(s["id"], "duplicitní id")
        videne.add(s["id"])
    return data
