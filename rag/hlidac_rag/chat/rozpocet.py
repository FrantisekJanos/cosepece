"""Kontroly rozpočtu v USD a výběr modelu (spec 10.2, 11.1, 11.3).

Čisté funkce nad `Nastaveni` a `Utrata`; nic nečtou z DB ani z hodin.
Den, hodina a měsíc jsou tvrdé stropy (útrata + strop tahu ≤ strop), relace a IP
se pustí při útrata < strop. Částky se před porovnáním zaokrouhlí na 6 míst,
aby hranice nezávisely na chybě plovoucí čárky.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .cenik import CENY, Cena
from .config import Nastaveni
from .uloziste import TZ, Utrata

HODINA_MIN = 60  # okno hodinového stropu


def _r(x: float) -> float:
    return round(x, 6)


def zkontroluj(n: Nastaveni, u: Utrata) -> str | None:
    """Kód limitu (`vypnuto`, `limit_den`, `limit_hodina`, `limit_relace`, `limit_ip`) nebo `None`.

    Měsíční strop vrací `limit_den`. Pořadí vyhodnocení je pevné.
    """
    if not n.zapnuto:
        return "vypnuto"
    t = n.strop_tahu
    if _r(u.den + t) > _r(n.rozpocet_den) or _r(u.mesic + t) > _r(n.rozpocet_mesic):
        return "limit_den"
    if _r(u.hodina + t) > _r(n.podil_hodina * n.rozpocet_den):
        return "limit_hodina"
    if (
        _r(u.relace) >= _r(n.strop_relace)
        or u.zprav_min >= n.zprav_relace_min
        or u.zprav_den >= n.zprav_relace_den
    ):
        return "limit_relace"
    if _r(u.ip) >= _r(n.strop_ip):
        return "limit_ip"
    return None


def vyber_model(n: Nastaveni, u: Utrata) -> str:
    """Hlavní model, od `prah_nouze` denního rozpočtu nouzový (je-li nastaven)."""
    if n.model_nouzovy and _r(u.den) >= _r(n.prah_nouze * n.rozpocet_den):
        return n.model_nouzovy
    return n.model


def zbyva_otazek(n: Nastaveni, u: Utrata) -> int:
    """Odhad zbývajících otázek relace: zbytek stropu / (2/3 stropu tahu), dolů."""
    typicka = n.strop_tahu * 2 / 3
    if typicka <= 0:
        return 0
    zbytek = _r(n.strop_relace) - _r(u.relace)
    if zbytek <= 0:
        return 0
    return max(0, math.floor(zbytek / typicka + 1e-9))


def znovu_v(n: Nastaveni, u: Utrata, ted: datetime) -> datetime | None:
    """Kdy má smysl zkusit znovu: `limit_hodina` za 60 minut (horní odhad uvolnění okna),
    ostatní limity v pražskou půlnoc. Bez limitu nebo při `vypnuto` `None`."""
    if ted.tzinfo is None:
        raise ValueError("ted musí mít časové pásmo")
    kod = zkontroluj(n, u)
    if kod is None or kod == "vypnuto":
        return None
    if kod == "limit_hodina":
        return ted + timedelta(minutes=HODINA_MIN)
    dnes = ted.astimezone(ZoneInfo(TZ))
    zitra = (dnes + timedelta(days=1)).date()
    return datetime(zitra.year, zitra.month, zitra.day, tzinfo=ZoneInfo(TZ))


def prisny_rezim(n: Nastaveni, u: Utrata) -> bool:
    """Přísnější Turnstile: den nebo IP přesáhly `turnstile_prisny_prah` svého stropu."""
    p = n.turnstile_prisny_prah
    return _r(u.den) >= _r(p * n.rozpocet_den) or _r(u.ip) >= _r(p * n.strop_ip)


def over_modely(n: Nastaveni, cenik: dict[str, Cena] | None = None) -> None:
    """Při startu: `CHAT_MODEL` i neprázdný `CHAT_MODEL_NOUZOVY` musí být v ceníku, jinak `ValueError`."""
    c = cenik if cenik is not None else CENY
    if n.model not in c:
        raise ValueError(f"CHAT_MODEL: model {n.model!r} není v ceníku")
    if n.model_nouzovy and n.model_nouzovy not in c:
        raise ValueError(f"CHAT_MODEL_NOUZOVY: model {n.model_nouzovy!r} není v ceníku")
