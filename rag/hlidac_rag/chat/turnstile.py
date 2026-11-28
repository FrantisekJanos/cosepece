"""Cloudflare Turnstile (spec 11.2): kdy ověřit znovu a ověření tokenu přes `siteverify`.

Platí „žádné placené volání bez důkazu“: token projde jen při `success: true`, správném `hostname` (výchozí
`cosepece.cz`, pro lokální vývoj přepis `CHAT_TURNSTILE_HOSTNAME`) a `action = "chat"`. Výpadek `siteverify`
(timeout, chyba sítě, 5xx, nečitelná odpověď), chyba na straně Cloudflare (`internal-error`) a špatné
tajemství vyhodí `OvereniNedostupne` (služba vrátí 503 `overeni_nedostupne`), nikdy úspěch.

Token je jednorázový: Cloudflare druhé použití odmítne (`timeout-or-duplicate`) a služba si navíc v procesu
pamatuje otisky použitých tokenů po dobu jejich platnosti (300 s), takže opakovaný token neprojde ani bez dotazu.

Tajemství (`CHAT_TURNSTILE_SECRET`) předává volající; do logu ani do výjimek nejde, stejně jako token.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import datetime, timedelta

import httpx2 as httpx

from .config import Nastaveni

log = logging.getLogger(__name__)

SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
HOSTNAME = "cosepece.cz"
ACTION = "chat"
TIMEOUT_S = 4
PLATNOST_TOKENU_S = 300
MAX_DELKA_TOKENU = 2048
PRISNY_ZPRAV = 2   # přísný režim: nové ověření každé 2 zprávy

# Chyby, které nový token od uživatele nevyřeší (porucha Cloudflare nebo naše nastavení).
_CHYBY_SERVERU = frozenset({"internal-error", "missing-input-secret", "invalid-input-secret"})

_pouzite: dict[str, float] = {}   # sha256 tokenu → monotónní čas, kdy vyprší


class OvereniNedostupne(Exception):
    """Ověření teď nejde provést (výpadek nebo chyba nastavení); placené volání nesmí proběhnout."""


def platnost_overeni(n: Nastaveni, ted: datetime) -> datetime:
    """Hodnota `overeno_do` po úspěšném ověření: teď + `CHAT_TURNSTILE_HODIN`."""
    if ted.tzinfo is None:
        raise ValueError("ted musí mít časové pásmo")
    return ted + timedelta(hours=n.turnstile_hodin)


def potreba_overeni(n: Nastaveni, rel: dict | None, otisk: str, prisny: bool, ted: datetime) -> bool:
    """Musí relace před touto zprávou projít Turnstile?

    Ano, když relace ještě ověřená nebyla, ověření vypršelo (`overeno_do` ≤ teď), od ověření přišlo
    `turnstile_zprav` zpráv (v přísném režimu 2), nebo je otisk IP jiný než při ověření. Otisk smazaný
    úklidem (None) se bere jako jiný."""
    if ted.tzinfo is None:
        raise ValueError("ted musí mít časové pásmo")
    if not rel or rel.get("overeno_do") is None:
        return True
    if ted >= rel["overeno_do"]:
        return True
    ulozeny = rel.get("otisk")
    if ulozeny is None or ulozeny != otisk:
        return True
    zprav = rel.get("zprav_od_overeni") or 0
    limit = min(n.turnstile_zprav, PRISNY_ZPRAV) if prisny else n.turnstile_zprav
    return zprav >= limit


def _vycisti_pamet() -> None:
    _pouzite.clear()


def _spotrebuj(token: str) -> bool:
    """Zapíše token jako použitý; False, když už použitý byl."""
    ted = time.monotonic()
    for k in [k for k, do in _pouzite.items() if do <= ted]:
        del _pouzite[k]
    klic = hashlib.sha256(token.encode()).hexdigest()
    if klic in _pouzite:
        return False
    _pouzite[klic] = ted + PLATNOST_TOKENU_S
    return True


def _hostname() -> str:
    return (os.environ.get("CHAT_TURNSTILE_HOSTNAME") or "").strip() or HOSTNAME


async def over_token(http: httpx.AsyncClient, token: str, ip: str, tajemstvi: str) -> bool:
    """Ověří token Turnstile u Cloudflare. True jen při `success`, správném hostname a action.

    Neplatný, použitý nebo prázdný token: False. Výpadek, chyba Cloudflare nebo chybějící tajemství:
    `OvereniNedostupne`."""
    if not tajemstvi:
        raise OvereniNedostupne("chybí tajemství Turnstile")
    if not isinstance(token, str) or not token.strip() or len(token) > MAX_DELKA_TOKENU:
        return False
    if not _spotrebuj(token):
        log.info("turnstile: token už byl použit")
        return False
    data = {"secret": tajemstvi, "response": token}
    if ip:
        data["remoteip"] = ip
    try:
        r = await http.post(SITEVERIFY, data=data, timeout=TIMEOUT_S)
    except httpx.HTTPError as e:
        log.warning("turnstile: siteverify nedostupné (%s)", type(e).__name__)
        raise OvereniNedostupne("siteverify nedostupné") from None
    if r.status_code != 200:
        log.warning("turnstile: siteverify vrátilo HTTP %s", r.status_code)
        raise OvereniNedostupne("siteverify nedostupné")
    try:
        v = r.json()
    except ValueError:
        v = None
    if not isinstance(v, dict):
        log.warning("turnstile: nečitelná odpověď siteverify")
        raise OvereniNedostupne("siteverify nedostupné")
    kody = v.get("error-codes") or []
    kody = [str(k) for k in kody] if isinstance(kody, list) else [str(kody)]
    if v.get("success") is not True:
        if _CHYBY_SERVERU.intersection(kody):
            log.error("turnstile: chyba na straně serveru %s", kody)
            raise OvereniNedostupne("ověření teď nefunguje")
        log.info("turnstile: token neprošel %s", kody)
        return False
    if v.get("hostname") != _hostname() or v.get("action") != ACTION:
        log.warning("turnstile: nesedí hostname nebo action (%r, %r)", v.get("hostname"), v.get("action"))
        return False
    return True
