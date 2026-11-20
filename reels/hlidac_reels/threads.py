"""Klient Threads API (graph.threads.net) pro zveřejnění Reelu jako videa na Threads.

Token se bere z token_provider() při každém volání (po prodloužení se mění) a posílá se jen
v hlavičce Authorization. Výjimka je prodloužení tokenu, které API přijímá jen s tokenem v URL;
proto se tokeny i parametr access_token z textu všech chyb odstraňují, aby se nedostaly
do logů ani do zpráv v Telegramu.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

API = "https://graph.threads.net"
FIND_LIMIT = 10
PREFIX_LEN = 80

_TOKEN_RE = re.compile(r"access_token=[^&\s\"]+")


class ThreadsError(Exception):
    """Chyba Threads API; message je už očištěná od tokenů."""

    def __init__(self, message: str, status: int | None = None, code: int | None = None):
        self.message = message
        self.status = status
        self.code = code
        super().__init__(message)


class ThreadsTimeout(ThreadsError):
    """Kontejner Threads nebyl hotový do vypršení času."""


class ThreadsNoToken(ThreadsError):
    """Token Threads chybí (provider vrátil prázdný); požadavek se vůbec neodeslal."""


class Threads:
    def __init__(
        self,
        user_id: str,
        token_provider: Callable[[], str],
        version: str = "v1.0",
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.user_id = user_id
        self._token_provider = token_provider
        self.version = version
        self.base = f"{API}/{version}"
        self._client = client or httpx.Client(timeout=60)
        self._sleep = sleep
        # tokeny, které klient viděl (pro očištění textu chyb)
        self._known: set[str] = set()

    # --- pomocné ---------------------------------------------------------

    def _token(self) -> str:
        token = self._token_provider()
        if not token:
            # bez tokenu by šel neautorizovaný požadavek; raději jasná chyba bez volání API
            raise ThreadsNoToken("chybí token Threads (prázdný token, požadavek se neodeslal)")
        self._known.add(token)
        return token

    def _clean(self, text: str) -> str:
        """Odstraní tokeny z textu (parametr access_token i tokeny, které klient zná)."""
        text = _TOKEN_RE.sub("access_token=***", text)
        for t in self._known:
            if t:
                text = text.replace(t, "***")
        return text

    def _request(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else None
        try:
            resp = self._client.request(method, url, params=params, data=data, headers=headers)
        except httpx.HTTPError as e:
            # text chyby httpx obsahuje URL; očistit a nezřetězit původní výjimku
            raise ThreadsError(self._clean(f"{type(e).__name__}: {e}")) from None
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code >= 400 or not isinstance(body, dict):
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict):
                msg = str(err.get("message") or "neznámá chyba")
                if err.get("error_user_msg"):
                    msg += f" ({err['error_user_msg']})"
                code = err.get("code") if isinstance(err.get("code"), int) else None
            else:
                msg, code = f"HTTP {resp.status_code}: {resp.text[:300]}", None
            raise ThreadsError(self._clean(msg), status=resp.status_code, code=code)
        return body

    def _graph(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        return self._request(method, f"{self.base}/{path}", token=self._token(), **kw)

    @staticmethod
    def _field(body: dict[str, Any], name: str) -> str:
        value = body.get(name)
        if not value:
            raise ThreadsError(f"odpověď bez pole {name}")
        return str(value)

    def _try_link(self, object_id: str) -> str:
        """Odkaz na už zveřejněný příspěvek; selhání dotazu nevadí (příspěvek existuje), vrátí ""."""
        try:
            info = self._graph("GET", object_id, params={"fields": "permalink"})
        except ThreadsError:
            return ""
        return str(info.get("permalink") or "")

    @staticmethod
    def _parse_time(value: str) -> datetime | None:
        # Threads vrací např. 2026-11-20T10:00:00+0000
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
        except (TypeError, ValueError):
            try:
                return datetime.fromisoformat(value)
            except (TypeError, ValueError):
                return None

    # --- publikace -------------------------------------------------------

    def create_video(self, video_url: str, text: str) -> str:
        """Založí kontejner s videem; Threads si video stáhne z video_url."""
        body = self._graph("POST", f"{self.user_id}/threads", data={
            "media_type": "VIDEO",
            "video_url": video_url,
            "text": text,
        })
        return self._field(body, "id")

    def wait(self, container_id: str, timeout: float = 600, interval: float = 10) -> None:
        """Čeká, až kontejner zpracuje (FINISHED). Čas se počítá z prospaných intervalů."""
        waited = 0.0
        while True:
            body = self._graph("GET", container_id, params={"fields": "status,error_message"})
            status = body.get("status")
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                detail = body.get("error_message")
                msg = f"kontejner Threads {container_id} skončil ve stavu {status}"
                if detail:
                    msg += f" ({detail})"
                raise ThreadsError(self._clean(msg))
            if status == "PUBLISHED":
                raise ThreadsError(f"kontejner Threads {container_id} už je zveřejněný (PUBLISHED)")
            if waited + interval > timeout:
                raise ThreadsTimeout(
                    f"kontejner Threads {container_id} není hotový ani po {int(waited)} s (stav {status})")
            self._sleep(interval)
            waited += interval

    def publish(self, container_id: str) -> tuple[str, str]:
        """Zveřejní hotový kontejner a vrátí (media_id, permalink).

        Po úspěšném threads_publish je příspěvek zveřejněný a vrácené media_id to znamená: chyba
        při dotazu na odkaz už nevyhodí výjimku, permalink je pak "".
        """
        body = self._graph("POST", f"{self.user_id}/threads_publish", data={"creation_id": container_id})
        media_id = self._field(body, "id")
        return media_id, self._try_link(media_id)

    def find_recent(self, text_prefix: str, since: datetime) -> tuple[str, str] | None:
        """Hledá už zveřejněný příspěvek (po výpadku uprostřed publikace); naivní since = UTC."""
        body = self._graph("GET", f"{self.user_id}/threads", params={
            "fields": "id,text,timestamp,permalink", "limit": FIND_LIMIT,
        })
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        prefix = text_prefix[:PREFIX_LEN]
        for item in body.get("data") or []:
            if not isinstance(item, dict):
                continue
            text = item.get("text") or ""
            created = self._parse_time(item.get("timestamp") or "")
            if not text.startswith(prefix) or created is None or created < since:
                continue
            return str(item.get("id")), str(item.get("permalink") or "")
        return None

    # --- token -----------------------------------------------------------

    def refresh_token(self, token: str) -> tuple[str, int]:
        """Prodlouží dlouhodobý token a vrátí (nový token, platnost v sekundách).

        API přijímá token jen jako parametr URL; adresa se proto nikde nevypisuje a text chyb
        se čistí od starého i nového tokenu.
        """
        if token:
            self._known.add(token)
        body = self._request("GET", f"{API}/refresh_access_token", params={
            "grant_type": "th_refresh_token", "access_token": token,
        })
        new_token = body.get("access_token")
        if isinstance(new_token, str) and new_token:
            self._known.add(new_token)
        expires_in = body.get("expires_in")
        if not new_token or not isinstance(new_token, str) or not isinstance(expires_in, int):
            raise ThreadsError("prodloužení tokenu: odpověď bez access_token nebo expires_in")
        return new_token, expires_in
