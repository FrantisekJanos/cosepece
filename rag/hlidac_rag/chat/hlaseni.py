"""Hlášení majiteli do Telegramu (spec 11.1 a 11.3): útrata dne přes 60 % a vyčerpaný den, každé jednou za den.

Bot a chat jsou vlastní pro chatbota (`CHAT_TELEGRAM_TOKEN`, `CHAT_TELEGRAM_CHAT`). Bez nich se nic neposílá.
Výpadek Telegramu službu neshodí: chyba se zaloguje bez tokenu (ten je součástí adresy API).
"""
from __future__ import annotations

import logging
from datetime import date

import httpx2 as httpx

log = logging.getLogger("hlidac_rag.chat.hlaseni")

API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT_S = 5
PRAHY = (60, 100)   # procenta denního rozpočtu, při kterých se hlásí


async def posli(http: httpx.AsyncClient, tajemstvi, text: str) -> bool:
    """Pošle zprávu do Telegramu. True = odesláno; bez nastavení nebo při chybě False (nikdy nevyhodí)."""
    if not tajemstvi.telegram_token or not tajemstvi.telegram_chat:
        return False
    try:
        r = await http.post(API.format(token=tajemstvi.telegram_token), timeout=TIMEOUT_S,
                            json={"chat_id": tajemstvi.telegram_chat, "text": text, "disable_web_page_preview": True})
    except httpx.HTTPError as e:
        log.warning("telegram: zprávu nejde odeslat (%s)", type(e).__name__)
        return False
    if r.status_code != 200:
        log.warning("telegram: odpověď HTTP %s", r.status_code)
        return False
    return True


class Hlidac:
    """Pamatuje si v procesu, který práh se v daném dni už hlásil (po restartu služby se hlásí znovu)."""

    def __init__(self):
        self._nahlaseno: set[tuple[date, int]] = set()

    def prekrocene(self, den: date, procent: float, vycerpano: bool) -> list[int]:
        """Prahy, které se mají nahlásit teď (každý jen jednou za den). `vycerpano` = den je tvrdě vyčerpaný."""
        nove = []
        for prah in PRAHY:
            dosazen = procent >= prah or (prah == 100 and vycerpano)
            if dosazen and (den, prah) not in self._nahlaseno:
                self._nahlaseno.add((den, prah))
                nove.append(prah)
        self._nahlaseno = {(d, p) for d, p in self._nahlaseno if d >= den}
        return nove


def text_hlaseni(prah: int, utraceno: float, rozpocet: float, dotazu: int, relaci: int) -> str:
    if prah >= 100:
        uvod = "Denní rozpočet chatu cosepece.cz je vyčerpaný, do půlnoci neodpovídá."
    else:
        uvod = f"Chat cosepece.cz dnes utratil přes {prah} % denního rozpočtu."
    return f"{uvod} Útrata ${utraceno:.2f} z ${rozpocet:.2f}, {dotazu} dotazů z {relaci} relací."
