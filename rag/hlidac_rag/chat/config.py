"""Konfigurace chatbota z proměnných prostředí `CHAT_*` (spec 10.2 a 11.1).

Výchozí hodnoty závisejí na modelu (profil). Proměnná prostředí vždy přebije profil.
Neplatná hodnota shodí službu už při startu (`ValueError` se jménem proměnné).
"""
from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass

_PRAVDA = frozenset({"1", "true", "ano", "yes", "on"})
_NEPRAVDA = frozenset({"0", "false", "ne", "no", "off"})
EFFORTY = ("low", "medium", "high", "xhigh", "max")

MODEL_VYCHOZI = "claude-sonnet-5-5"
TURNSTILE_HOSTNAME = "cosepece.cz"   # hostname, který musí vrátit siteverify (spec 11.2)

log = logging.getLogger(__name__)

# Sloupce „Sonnet $2 (výchozí)“ a „Haiku $2“ ze spec 10.2 a 11.1.
# Effort u Haiku spec nefixuje („podle F0, low nebo medium“); do rozhodnutí F0 je low.
PROFILY: dict[str, dict] = {
    "claude-sonnet-5-5": {
        "effort": "low",
        "max_kol": 3,
        "rozpocet_vysledku_tokenu": 12000,
        "max_tokenu_odpovedi": 1200,
        "historie_max_tokenu": 6000,
        "max_tahu_konverzace": 6,
        "strop_tahu": 0.06,
        "strop_relace": 0.25,
        "strop_ip": 0.50,
        "podil_hodina": 0.25,
        "zprav_relace_min": 3,
        "zprav_relace_den": 10,
        "soubeh_celkem": 2,
        "soubeh_ip": 1,
        "turnstile_zprav": 5,
    },
    "claude-haiku-5-5": {
        "effort": "low",
        "max_kol": 5,
        "rozpocet_vysledku_tokenu": 40000,
        "max_tokenu_odpovedi": 2000,
        "historie_max_tokenu": 20000,
        "max_tahu_konverzace": 20,
        "strop_tahu": 0.02,
        "strop_relace": 0.06,
        "strop_ip": 0.15,
        "podil_hodina": 0.15,
        "zprav_relace_min": 5,
        "zprav_relace_den": 20,
        "soubeh_celkem": 4,
        "soubeh_ip": 1,
        "turnstile_zprav": 10,
    },
}

# Hodnoty nezávislé na modelu.
_SPOLECNE = {
    "rozpocet_den": 2.00,
    "rozpocet_mesic": 60.0,
    "prah_nouze": 0.6,
    "max_znaku_zpravy": 500,
    "karta_max_tokenu": 1200,
    "turnstile_hodin": 6.0,
    "turnstile_prisny_prah": 0.5,
    "drzeni_tahu_s": 300,   # příznak běžícího tahu vyprší po této době (musí přesáhnout nejdelší tah)
}


@dataclass(frozen=True)
class Nastaveni:
    zapnuto: bool
    rezim: str  # skryte | verejne
    model: str
    effort: str
    model_nouzovy: str
    prah_nouze: float
    rozpocet_den: float
    rozpocet_mesic: float
    podil_hodina: float
    strop_tahu: float
    strop_relace: float
    strop_ip: float
    zprav_relace_min: int
    zprav_relace_den: int
    max_kol: int
    rozpocet_vysledku_tokenu: int
    max_tokenu_odpovedi: int
    historie_max_tokenu: int
    max_tahu_konverzace: int
    max_znaku_zpravy: int
    karta_max_tokenu: int
    soubeh_celkem: int
    soubeh_ip: int
    turnstile_zprav: int
    turnstile_hodin: float
    turnstile_prisny_prah: float
    karta_jako: str  # tool_result | blok
    drzeni_tahu_s: int = 300
    turnstile_hostname: str = TURNSTILE_HOSTNAME   # CHAT_TURNSTILE_HOSTNAME jen pro lokální vývoj
    max_nahlaseni_den: int = 200   # CHAT_MAX_NAHLASENI_DEN: globální denní strop nahlášení odpovědí
    provozovatel: str = ""   # CHAT_PROVOZOVATEL: provozovatel a kontakt na stránce zásad (do repa žádné osobní údaje)


def _hodnota(env: Mapping[str, str], jmeno: str, vychozi, typ, minimum=0):
    """Přečte proměnnou `jmeno` a převede ji na `typ`; prázdná = výchozí.

    Čísla musí být konečná a nejméně `minimum` (výchozí 0), jinak by záporný
    nebo nekonečný strop vypnul ochranu rozpočtu.
    """
    surove = env.get(jmeno)
    if surove is None or surove.strip() == "":
        return vychozi
    surove = surove.strip()
    try:
        if typ is bool:
            if surove.lower() in _PRAVDA:
                return True
            if surove.lower() in _NEPRAVDA:
                return False
            raise ValueError(
                "očekáváno " + ", ".join(sorted(_PRAVDA | _NEPRAVDA))
            )
        if typ in (int, float):
            cislo = typ(surove)
            if typ is float and not math.isfinite(cislo):
                raise ValueError("očekáváno konečné číslo")
            if cislo < minimum:
                raise ValueError(f"očekáváno alespoň {minimum}")
            return cislo
        return surove
    except ValueError as e:
        raise ValueError(f"{jmeno}: neplatná hodnota {surove!r} ({e})") from e


def _vyber(env: Mapping[str, str], jmeno: str, vychozi: str, povolene: tuple[str, ...]) -> str:
    h = _hodnota(env, jmeno, vychozi, str)
    if h not in povolene:
        raise ValueError(f"{jmeno}: neplatná hodnota {h!r} (povoleno: {', '.join(povolene)})")
    return h


def nacti(env: Mapping[str, str]) -> Nastaveni:
    """Sestaví nastavení z prostředí; neznámý model dostane profil Sonnetu."""
    model = _hodnota(env, "CHAT_MODEL", MODEL_VYCHOZI, str)
    p = PROFILY.get(model, PROFILY[MODEL_VYCHOZI])
    s = _SPOLECNE
    hostname = _hodnota(env, "CHAT_TURNSTILE_HOSTNAME", TURNSTILE_HOSTNAME, str)
    if hostname != TURNSTILE_HOSTNAME:
        log.warning("CHAT_TURNSTILE_HOSTNAME=%r: Turnstile přijme tokeny jiného webu než %s (jen pro vývoj)",
                    hostname, TURNSTILE_HOSTNAME)
    return Nastaveni(
        zapnuto=_hodnota(env, "CHAT_ZAPNUTO", False, bool),
        rezim=_vyber(env, "CHAT_REZIM", "skryte", ("skryte", "verejne")),
        model=model,
        effort=_vyber(env, "CHAT_EFFORT", p["effort"], EFFORTY),
        model_nouzovy=_hodnota(env, "CHAT_MODEL_NOUZOVY", "", str),
        prah_nouze=_hodnota(env, "CHAT_PRAH_NOUZE", s["prah_nouze"], float),
        rozpocet_den=_hodnota(env, "CHAT_ROZPOCET_DEN_USD", s["rozpocet_den"], float),
        rozpocet_mesic=_hodnota(env, "CHAT_ROZPOCET_MESIC_USD", s["rozpocet_mesic"], float),
        podil_hodina=_hodnota(env, "CHAT_PODIL_HODINA", p["podil_hodina"], float),
        strop_tahu=_hodnota(env, "CHAT_STROP_TAHU_USD", p["strop_tahu"], float),
        strop_relace=_hodnota(env, "CHAT_STROP_RELACE_USD", p["strop_relace"], float),
        strop_ip=_hodnota(env, "CHAT_STROP_IP_USD", p["strop_ip"], float),
        zprav_relace_min=_hodnota(env, "CHAT_ZPRAV_RELACE_MIN", p["zprav_relace_min"], int),
        zprav_relace_den=_hodnota(env, "CHAT_ZPRAV_RELACE_DEN", p["zprav_relace_den"], int),
        max_kol=_hodnota(env, "CHAT_MAX_KOL", p["max_kol"], int, 1),
        rozpocet_vysledku_tokenu=_hodnota(
            env, "CHAT_ROZPOCET_VYSLEDKU_TOKENU", p["rozpocet_vysledku_tokenu"], int
        ),
        max_tokenu_odpovedi=_hodnota(
            env, "CHAT_MAX_TOKENU_ODPOVEDI", p["max_tokenu_odpovedi"], int
        ),
        historie_max_tokenu=_hodnota(
            env, "CHAT_HISTORIE_MAX_TOKENU", p["historie_max_tokenu"], int
        ),
        max_tahu_konverzace=_hodnota(
            env, "CHAT_MAX_TAHU_KONVERZACE", p["max_tahu_konverzace"], int, 1
        ),
        max_znaku_zpravy=_hodnota(env, "CHAT_MAX_ZNAKU_ZPRAVY", s["max_znaku_zpravy"], int),
        karta_max_tokenu=_hodnota(env, "CHAT_KARTA_MAX_TOKENU", s["karta_max_tokenu"], int, 1),
        soubeh_celkem=_hodnota(env, "CHAT_SOUBEH_CELKEM", p["soubeh_celkem"], int),
        soubeh_ip=_hodnota(env, "CHAT_SOUBEH_IP", p["soubeh_ip"], int),
        turnstile_zprav=_hodnota(env, "CHAT_TURNSTILE_ZPRAV", p["turnstile_zprav"], int),
        turnstile_hodin=_hodnota(env, "CHAT_TURNSTILE_HODIN", s["turnstile_hodin"], float),
        turnstile_prisny_prah=_hodnota(
            env, "CHAT_TURNSTILE_PRISNY_PRAH", s["turnstile_prisny_prah"], float
        ),
        karta_jako=_vyber(env, "CHAT_KARTA_JAKO", "tool_result", ("tool_result", "blok")),
        drzeni_tahu_s=_hodnota(env, "CHAT_DRZENI_TAHU_S", s["drzeni_tahu_s"], int, 30),
        turnstile_hostname=hostname,
        max_nahlaseni_den=_hodnota(env, "CHAT_MAX_NAHLASENI_DEN", 200, int),
        provozovatel=_hodnota(env, "CHAT_PROVOZOVATEL", "", str),
    )
