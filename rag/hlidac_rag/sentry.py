"""Hlášení chyb do Sentry (ingest a MCP server).

Pravidla filtru jsou shodná s PHP odesílačem (app/Sentry.php): citlivé klíče, e-maily,
IP adresy, tokeny a cesty serveru se z událostí odstraní dřív, než odejdou.
Bez SENTRY_DSN se nic neinicializuje a nic neodesílá.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

MAX_STRING = 8000  # delší řetězce se zkrátí

_SENSITIVE_KEY = re.compile(r"token|pass|secret|key|cookie|authorization|auth", re.I)

# Http::redact z PHP
_REDACT = [
    (re.compile(r"(api\.telegram\.org/bot)[^/\s]+", re.I), r"\1***"),
    (re.compile(r"([?&](?:token|access_token|key|api_key|password|secret)=)[^&\s\"']+", re.I), r"\1***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1***"),
    (re.compile(r"(\"(?:accessJwt|refreshJwt|access_token|token|password)\"\s*:\s*\")[^\"]*"), r"\1***"),
]

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IPV4 = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])")
# IPv6: se zkratkou "::" nebo aspoň 3 dvojtečky mezi hex skupinami (čas 12:30:45 tak zůstane)
_IPV6 = [
    re.compile(r"(?<![\w:])(?:[0-9a-f]{1,4}:){1,7}:(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){0,6})?(?![\w:])", re.I),
    re.compile(r"(?<![\w:])::(?:[0-9a-f]{1,4}:){0,6}[0-9a-f]{1,4}(?![\w:])", re.I),
    re.compile(r"(?<![\w:])(?:[0-9a-f]{1,4}:){3,7}[0-9a-f]{1,4}(?![\w:])", re.I),
]

# WORKDIR kontejneru rag; odstraní se jen od začátku cesty (ne uprostřed jiné, např. /srv/app/)
_PATH_PREFIXES = ["/app/"]


def _strip_paths(text: str) -> str:
    text = text.replace("\\", "/")
    for prefix in _PATH_PREFIXES:
        text = re.sub(r"(?<![\w./-])" + re.escape(prefix), "", text)
    return text


def scrub(value: Any, key: str | None = None) -> Any:
    # hodnota pod citlivým klíčem se zahodí celá
    if key is not None and _SENSITIVE_KEY.search(key):
        return "[Filtered]"
    if isinstance(value, dict):
        return {k: scrub(v, k if isinstance(k, str) else None) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if not isinstance(value, str):
        return value
    s = _strip_paths(value)
    for rx, repl in _REDACT:
        s = rx.sub(repl, s)
    s = _EMAIL.sub("[email]", s)
    s = _IPV4.sub("[ip]", s)
    for rx in _IPV6:
        s = rx.sub("[ip]", s)
    if len(s) > MAX_STRING:
        s = s[:MAX_STRING] + "…"
    return s


# Omezení objemu (free plán Sentry má 5 000 událostí měsíčně; ingest loguje výjimku za každý
# neúspěšný dokument). Stejný otisk se v okně pošle jen jednou a událostí je nejvýš MAX_EVENTS.
# MCP server běží trvale, proto se počítadla nuluje po hodině (okno podle time.monotonic());
# ingest je každou hodinu nový proces, takže okno platí stejně. Paralela k PHP app/Sentry.php.
MAX_EVENTS = 20
WINDOW_SECONDS = 3600.0

_seen: set[str] = set()
_sent = 0
_window_start: float | None = None


def reset() -> None:
    global _sent, _window_start
    _seen.clear()
    _sent = 0
    _window_start = None


def _fingerprint(event: dict) -> str:
    # typ výjimky + šablona zprávy (před dosazením argumentů), jinak text zprávy
    parts = []
    values = (event.get("exception") or {}).get("values") or []
    if values:
        parts.append(str(values[-1].get("type", "")))
    logentry = event.get("logentry") or {}
    msg = logentry.get("message") or event.get("message")
    if isinstance(msg, dict):
        msg = msg.get("formatted")
    if not msg and values:
        msg = values[-1].get("value")
    parts.append(str(msg or ""))
    return "|".join(parts)


def _allow(event: dict) -> bool:
    global _sent, _window_start
    now = time.monotonic()
    if _window_start is None or now - _window_start >= WINDOW_SECONDS:
        _seen.clear()
        _sent = 0
        _window_start = now
    fp = _fingerprint(event)
    if fp in _seen or _sent >= MAX_EVENTS:
        return False
    _seen.add(fp)
    _sent += 1
    return True


def before_send(event: dict, hint: dict) -> dict | None:
    # chyby nástrojů MCP (neznámé číslo tisku, krátký dotaz…) jsou očekávané odpovědi, ne závady
    exc_info = (hint or {}).get("exc_info")
    if exc_info and exc_info[0] is not None and issubclass(exc_info[0], ToolError):
        return None
    for field in ("message", "logentry", "exception", "request", "extra", "breadcrumbs"):
        if field in event:
            event[field] = scrub(event[field])
    return event if _allow(event) else None


def init(component: str) -> bool:
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT") or "production",
        release=os.environ.get("SENTRY_RELEASE") or None,
        send_default_pii=False,
        traces_sample_rate=0,
        max_breadcrumbs=20,
        before_send=before_send,
    )
    sentry_sdk.set_tag("component", component)
    return True
