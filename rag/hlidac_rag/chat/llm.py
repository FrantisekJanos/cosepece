"""Rozhraní k modelu (spec 8.2, 10.1). Jediné místo, které importuje `anthropic`.

Volání jde přes `client.beta.messages.stream`: text se streamuje přes `na_text`, výsledek je z
`stream.get_final_message()`. Systémový prompt a poslední blok poslední zprávy nesou `cache_control`
(posuvná zarážka: další volání tahu čte celou předchozí konverzaci z cache). Bez `thinking` (modely 5.5
přemýšlejí adaptivně, `disabled` je 400), bez `temperature`, `top_p`, `top_k`; hloubku řídí `effort`.
Opakování při 429, 5xx a výpadku spojení dělá SDK (`max_retries`, výchozí 2); co nevyjde ani potom,
je `Pretizeno`. Ostatní chyby API (400, 401…) se propustí, smyčka z nich udělá stav `chyba`.
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

import anthropic

# Server-side fallback při odmítnutí (refusal) jen u Sonnetu; Haiku 5.5 ho nemá.
BETA_FALLBACK = "server-side-fallback-2026-07-01"
MODELY_S_FALLBACKEM = ("claude-sonnet-5-5",)
_CACHE = {"type": "ephemeral"}
# Chyba ve streamu (událost `error`) přijde se stavem HTTP 200; o přetížení rozhoduje typ chyby v těle.
_TYPY_PRETIZENI = frozenset({"overloaded_error", "rate_limit_error", "api_error"})


class Pretizeno(Exception):
    """API je přetížené nebo nedostupné (429, 529, 5xx, výpadek spojení) i po opakování v SDK."""


@dataclass(frozen=True)
class Usage:
    vstup: int
    vystup: int
    cache_cteni: int
    cache_zapis: int


@dataclass(frozen=True)
class Odpoved:
    content: list[dict]   # bloky odpovědi beze změny (thinking s podpisem, fallback, tool_use) k vrácení do messages
    stop_reason: str
    usage: Usage
    model: str            # model, který skutečně odpověděl (po fallbacku jiný než požadovaný)
    ms: int


class LLM(Protocol):
    async def volej(self, *, model: str, effort: str, system: str, tools: list[dict], messages: list[dict],
                    max_tokens: int, na_text: Callable[[str], Awaitable[None]],
                    tool_choice: dict | None = None) -> Odpoved: ...


def _se_zarazkou(messages: list[dict]) -> list[dict]:
    """Kopie zpráv s `cache_control` na posledním bloku poslední zprávy; zprávy volajícího se nemění."""
    if not messages:
        return []
    posledni = dict(messages[-1])
    obsah = posledni["content"]
    if isinstance(obsah, str):
        bloky = [{"type": "text", "text": obsah}]
    else:
        bloky = [dict(b) for b in obsah]
    if bloky:
        bloky[-1] = {**bloky[-1], "cache_control": dict(_CACHE)}
    posledni["content"] = bloky
    return list(messages[:-1]) + [posledni]


def _pretizeni(e: anthropic.APIStatusError) -> bool:
    if e.status_code == 429 or e.status_code >= 500:
        return True
    typ = getattr(e, "type", None)
    if typ is None and isinstance(e.body, dict):
        typ = (e.body.get("error") or {}).get("type")
    return str(typ) in _TYPY_PRETIZENI


def _usage(u) -> Usage:
    return Usage(int(getattr(u, "input_tokens", 0) or 0), int(getattr(u, "output_tokens", 0) or 0),
                 int(getattr(u, "cache_read_input_tokens", 0) or 0),
                 int(getattr(u, "cache_creation_input_tokens", 0) or 0))


class AnthropicLLM:
    def __init__(self, client: anthropic.AsyncAnthropic):
        self.client = client

    async def volej(self, *, model: str, effort: str, system: str, tools: list[dict], messages: list[dict],
                    max_tokens: int, na_text: Callable[[str], Awaitable[None]],
                    tool_choice: dict | None = None) -> Odpoved:
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": dict(_CACHE)}],
            "messages": _se_zarazkou(messages),
            "output_config": {"effort": effort},
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if model in MODELY_S_FALLBACKEM:
            kwargs["betas"] = [BETA_FALLBACK]
            kwargs["fallbacks"] = "default"
        zacatek = time.monotonic()
        try:
            async with self.client.beta.messages.stream(**kwargs) as stream:
                async for udalost in stream:
                    if udalost.type == "content_block_delta" and getattr(udalost.delta, "type", None) == "text_delta":
                        await na_text(udalost.delta.text)
                zprava = await stream.get_final_message()
        except anthropic.RateLimitError as e:
            raise Pretizeno(f"429: {e}") from e
        except anthropic.APIStatusError as e:
            if _pretizeni(e):
                raise Pretizeno(f"{e.status_code}: {e}") from e
            raise
        except anthropic.APIConnectionError as e:
            raise Pretizeno(f"spojení: {e}") from e
        ms = int((time.monotonic() - zacatek) * 1000)
        content = [b.model_dump(mode="json", exclude_none=True) for b in zprava.content]
        return Odpoved(content, str(zprava.stop_reason), _usage(zprava.usage), str(zprava.model or model), ms)
