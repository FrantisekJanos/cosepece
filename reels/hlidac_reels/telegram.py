"""Minimální klient Telegram Bot API (holé httpx).

Token bota je součástí URL (https://api.telegram.org/bot<token>/…), proto se URL nikdy
nevypisuje: chyby httpx se hlásí jen názvem metody a typem výjimky, texty chyb API se
čistí od tokenu.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

API = "https://api.telegram.org"


class TelegramError(Exception):
    """Chyba Bot API nebo spojení; text neobsahuje token ani URL. status = HTTP kód, None = bez odpovědi."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def rejected(self) -> bool:
        """Telegram požadavek odmítl (4xx kromě 429): opakování nepomůže."""
        return self.status is not None and 400 <= self.status < 500 and self.status != 429


def _markup(buttons: list[tuple[str, str]] | None) -> dict[str, Any]:
    # každé tlačítko na vlastním řádku (dlouhé popisky se na telefonu nezkracují)
    return {"inline_keyboard": [[{"text": text, "callback_data": data}] for text, data in buttons or []]}


class Telegram:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self._token = token
        self._base = f"{API}/bot{token}"
        self._client = client or httpx.Client(timeout=60)

    def _clean(self, text: str) -> str:
        return text.replace(self._token, "***") if self._token else text

    def _call(self, method: str, *, payload: dict[str, Any] | None = None,
              data: dict[str, Any] | None = None, files: dict[str, Any] | None = None,
              timeout: float | None = None) -> Any:
        kw: dict[str, Any] = {}
        if timeout is not None:
            kw["timeout"] = timeout
        try:
            if files is not None:
                resp = self._client.post(f"{self._base}/{method}", data=data, files=files, **kw)
            else:
                resp = self._client.post(f"{self._base}/{method}", json=payload or {}, **kw)
        except httpx.HTTPError as e:
            # text výjimky httpx obsahuje URL s tokenem: jen typ, původní výjimku nezřetězit
            raise TelegramError(f"{method}: {type(e).__name__}") from None
        try:
            body = resp.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not body.get("ok"):
            desc = body.get("description") if isinstance(body, dict) else None
            raise TelegramError(self._clean(f"{method}: HTTP {resp.status_code}: {desc or 'neznámá chyba'}"),
                                status=resp.status_code)
        return body.get("result")

    def get_updates(self, offset: int, timeout: int = 50) -> list[dict]:
        result = self._call("getUpdates", payload={
            "offset": offset, "timeout": timeout, "allowed_updates": ["message", "callback_query"],
        }, timeout=timeout + 15)
        return result if isinstance(result, list) else []

    def send_video(self, chat_id: int, path: str | Path, caption: str) -> int:
        """Pošle video jako multipart; popisek videa má v Telegramu limit 1 024 znaků."""
        p = Path(path)
        with p.open("rb") as fh:
            result = self._call(
                "sendVideo",
                data={"chat_id": str(chat_id), "caption": caption[:1024], "supports_streaming": "true"},
                files={"video": (p.name, fh, "video/mp4")},
                timeout=300,
            )
        return int(result["message_id"])

    def send_text(self, chat_id: int, text: str, buttons: list[tuple[str, str]] | None = None,
                  reply_to: int | None = None, parse_mode: str | None = None) -> int:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "link_preview_options": {"is_disabled": True}}
        if buttons:
            payload["reply_markup"] = _markup(buttons)
        if reply_to is not None:
            payload["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        result = self._call("sendMessage", payload=payload)
        return int(result["message_id"])

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        self._call("answerCallbackQuery", payload=payload)

    def edit_buttons(self, chat_id: int, msg_id: int, buttons: list[tuple[str, str]] | None) -> None:
        """Vymění tlačítka zprávy; None = tlačítka odebrat."""
        self._call("editMessageReplyMarkup", payload={
            "chat_id": chat_id, "message_id": msg_id, "reply_markup": _markup(buttons),
        })

