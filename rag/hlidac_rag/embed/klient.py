"""Klient OpenAI embeddings (POST /v1/embeddings) přes httpx2, který už je v závislostech přes mcp.

Klíč se bere jen z OPENAI_API_KEY a nikdy se neobjeví v textu výjimky, logu ani repr klienta: chybová hláška API
(401 vrací část klíče) se do výjimky nepřenáší, jen stavový kód a typ chyby.
"""
from __future__ import annotations

import logging
import os
import time

import httpx2 as httpx

URL = "https://api.openai.com/v1/embeddings"
DAVKA = 128          # nejvýš vstupů v jednom požadavku
MAX_ZNAKU = 7000     # pojistka: delší vstup se ořízne (limit modelu je 8 191 tokenů)
POKUSU = 5           # opakování při 429, 5xx a síťové chybě
VYCHOZI_MODEL = "text-embedding-3-large"
VYCHOZI_DIM = 1024

log = logging.getLogger("hlidac_rag.embed")


class EmbedChyba(Exception):
    """Embedding se nepodařilo získat (trvalá chyba API, síť, neplatná odpověď)."""


def _spi(sekund: float) -> None:
    """Čekání před opakováním (v testech se nahradí)."""
    time.sleep(sekund)


def _cekani(odpoved: httpx.Response | None, pokus: int) -> float:
    """Retry-After z odpovědi (sekundy, nejvýš 60), jinak exponenciální backoff 1, 2, 4, 8 s."""
    if odpoved is not None:
        try:
            return min(60.0, max(0.0, float(odpoved.headers.get("retry-after", ""))))
        except ValueError:
            pass
    return float(2 ** pokus)


class Klient:
    def __init__(self, api_key: str, model: str = VYCHOZI_MODEL, dim: int = VYCHOZI_DIM, timeout: float = 30.0,
                 transport: httpx.BaseTransport | None = None):
        self.model = model
        self.dim = dim
        self.timeout = timeout
        self._http = httpx.Client(timeout=timeout, transport=transport,
                                  headers={"Authorization": f"Bearer {api_key}"})

    def __repr__(self) -> str:
        return f"Klient(model={self.model!r}, dim={self.dim})"

    __str__ = __repr__

    @classmethod
    def z_prostredi(cls, timeout: float = 30.0) -> "Klient | None":
        """Klient podle OPENAI_API_KEY, EMBED_MODEL a EMBED_DIM; bez klíče None."""
        klic = (os.environ.get("OPENAI_API_KEY") or "").strip()
        if not klic:
            return None
        model = (os.environ.get("EMBED_MODEL") or "").strip() or VYCHOZI_MODEL
        dim = int((os.environ.get("EMBED_DIM") or "").strip() or VYCHOZI_DIM)
        return cls(klic, model=model, dim=dim, timeout=timeout)

    def _davka(self, texty: list[str]) -> tuple[list[list[float]], int]:
        body = {"model": self.model, "input": [t[:MAX_ZNAKU] for t in texty], "dimensions": self.dim}
        posledni = ""
        for pokus in range(POKUSU):
            odpoved = None
            try:
                odpoved = self._http.post(URL, json=body)
            except httpx.HTTPError as e:
                posledni = f"síťová chyba {type(e).__name__}"
            else:
                if odpoved.status_code == 200:
                    return self._vektory(odpoved, len(texty))
                if odpoved.status_code != 429 and odpoved.status_code < 500:
                    # 4xx kromě 429 se opakováním nespraví; text odpovědi (může obsahovat klíč) se nepřenáší
                    raise EmbedChyba(f"OpenAI vrátilo HTTP {odpoved.status_code}")
                posledni = f"HTTP {odpoved.status_code}"
            if pokus + 1 < POKUSU:
                cekat = _cekani(odpoved, pokus)
                log.warning("embeddingy: %s, opakuji za %.0f s", posledni, cekat)
                _spi(cekat)
        raise EmbedChyba(f"OpenAI neodpovědělo ani po {POKUSU} pokusech ({posledni})")

    @staticmethod
    def _vektory(odpoved: httpx.Response, pocet: int) -> tuple[list[list[float]], int]:
        try:
            j = odpoved.json()
            data = sorted(j["data"], key=lambda d: d["index"])
            vektory = [d["embedding"] for d in data]
            tokenu = int(j.get("usage", {}).get("total_tokens", 0))
        except (ValueError, KeyError, TypeError):
            raise EmbedChyba("neplatná odpověď OpenAI") from None
        if len(vektory) != pocet:
            raise EmbedChyba(f"OpenAI vrátilo {len(vektory)} vektorů místo {pocet}")
        return vektory, tokenu

    def embed(self, texty: list[str]) -> tuple[list[list[float]], int]:
        """Vektory v pořadí vstupu a počet tokenů (usage.total_tokens); dávky po DAVKA vstupech."""
        vektory: list[list[float]] = []
        tokenu = 0
        for i in range(0, len(texty), DAVKA):
            v, t = self._davka(texty[i:i + DAVKA])
            vektory.extend(v)
            tokenu += t
        return vektory, tokenu
