"""Klient Meta Graph API pro zveřejnění Reels na stránce FB a na profesionálním účtu IG.

Tokeny se posílají jen v hlavičce Authorization (nikdy v URL) a z textu chyb se odstraňují,
aby se nedostaly do logů ani do zpráv v Telegramu.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

RUPLOAD = "https://rupload.facebook.com/video-upload"
FB_WEB = "https://www.facebook.com"
FIND_LIMIT = 10
PREFIX_LEN = 80

_TOKEN_RE = re.compile(r"access_token=[^&\s\"]+")


class MetaError(Exception):
    """Chyba Graph API; message je už očištěná od tokenů."""

    def __init__(self, message: str, status: int | None = None, code: int | None = None):
        self.message = message
        self.status = status
        self.code = code
        super().__init__(message)


class MetaTimeout(MetaError):
    """IG kontejner nebyl hotový do vypršení času."""


class Meta:
    def __init__(
        self,
        token: str,
        page_id: str,
        ig_user_id: str,
        version: str = "v23.0",
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._token = token
        self.page_id = page_id
        self.ig_user_id = ig_user_id
        self.version = version
        self.base = f"https://graph.facebook.com/{version}"
        self._client = client or httpx.Client(timeout=60)
        self._sleep = sleep
        self._page_token: str | None = None

    # --- pomocné ---------------------------------------------------------

    def _clean(self, text: str) -> str:
        """Odstraní tokeny z textu (parametr access_token i tokeny, které klient zná)."""
        text = _TOKEN_RE.sub("access_token=***", text)
        for t in (self._token, self._page_token):
            if t:
                text = text.replace(t, "***")
        return text

    def _request(
        self,
        method: str,
        url: str,
        token: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        h = {"Authorization": f"OAuth {token}", **(headers or {})}
        try:
            resp = self._client.request(method, url, params=params, data=data, content=content, headers=h)
        except httpx.HTTPError as e:
            # text chyby httpx obsahuje URL; očistit a nezřetězit původní výjimku
            raise MetaError(self._clean(f"{type(e).__name__}: {e}")) from None
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
            raise MetaError(self._clean(msg), status=resp.status_code, code=code)
        return body

    def _graph(self, method: str, path: str, token: str, **kw: Any) -> dict[str, Any]:
        return self._request(method, f"{self.base}/{path}", token, **kw)

    @staticmethod
    def _field(body: dict[str, Any], name: str) -> str:
        value = body.get(name)
        if not value:
            raise MetaError(f"odpověď bez pole {name}")
        return str(value)

    def _try_link(self, object_id: str, token: str, field: str) -> str:
        """Odkaz na už zveřejněný příspěvek; selhání dotazu nevadí (příspěvek existuje), vrátí ""."""
        try:
            info = self._graph("GET", object_id, token, params={"fields": field})
        except MetaError:
            return ""
        return str(info.get(field) or "")

    @staticmethod
    def _fb_url(link: str) -> str:
        """Relativní permalink FB doplní na celou adresu."""
        if link.startswith("/"):
            return FB_WEB + link
        return link

    @staticmethod
    def _parse_time(value: str) -> datetime | None:
        # Graph vrací např. 2026-11-20T10:00:00+0000
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
        except (TypeError, ValueError):
            try:
                return datetime.fromisoformat(value)
            except (TypeError, ValueError):
                return None

    def _find(
        self, items: list[Any], text_key: str, time_key: str, link_key: str,
        caption_prefix: str, since: datetime,
    ) -> list[tuple[str, str]]:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        prefix = caption_prefix[:PREFIX_LEN]
        found = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = item.get(text_key) or ""
            created = self._parse_time(item.get(time_key) or "")
            if not text.startswith(prefix) or created is None or created < since:
                continue
            found.append((str(item.get("id")), str(item.get(link_key) or "")))
        return found

    # --- stránka FB ------------------------------------------------------

    def page_token(self) -> str:
        """Token stránky z tokenu systémového uživatele (uložený v paměti)."""
        if self._page_token is None:
            body = self._graph("GET", self.page_id, self._token, params={"fields": "access_token"})
            self._page_token = self._field(body, "access_token")
        return self._page_token

    def fb_upload_reel(self, path: str | Path, description: str) -> tuple[str, str]:
        """Nahraje a zveřejní Reel na stránce (start → rupload → finish) a vrátí (video_id, permalink).

        Po úspěšném finish je Reel zveřejněný a vrácené video_id to znamená: chyba při dotazu
        na odkaz už nevyhodí výjimku, odkaz se nahradí https://www.facebook.com/reel/<video_id>.
        """
        data = Path(path).read_bytes()
        token = self.page_token()
        start = self._graph("POST", f"{self.page_id}/video_reels", token, data={"upload_phase": "start"})
        video_id = self._field(start, "video_id")
        upload_url = start.get("upload_url") or f"{RUPLOAD}/{self.version}/{video_id}"
        up = self._request(
            "POST", upload_url, token, content=data,
            headers={"offset": "0", "file_size": str(len(data))},
        )
        if not up.get("success"):
            raise MetaError(self._clean(f"nahrání videa {video_id} selhalo: {str(up)[:300]}"))
        fin = self._graph("POST", f"{self.page_id}/video_reels", token, data={
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": description,
        })
        if not fin.get("success"):
            raise MetaError(self._clean(f"zveřejnění videa {video_id} selhalo: {str(fin)[:300]}"))
        link = self._try_link(video_id, token, "permalink_url")
        return video_id, self._fb_url(link) if link else f"{FB_WEB}/reel/{video_id}"

    def fb_find_recent(self, caption_prefix: str, since: datetime) -> tuple[str, str] | None:
        """Hledá už zveřejněný Reel stránky (po výpadku uprostřed publikace)."""
        body = self._graph("GET", f"{self.page_id}/video_reels", self.page_token(), params={
            "fields": "id,description,created_time,permalink_url", "limit": FIND_LIMIT,
        })
        found = self._find(body.get("data") or [], "description", "created_time", "permalink_url",
                           caption_prefix, since)
        if not found:
            return None
        video_id, link = found[0]
        return video_id, self._fb_url(link)

    # --- Instagram -------------------------------------------------------

    def ig_create(self, video_url: str, caption: str) -> str:
        """Založí kontejner Reels; IG si video stáhne z video_url."""
        body = self._graph("POST", f"{self.ig_user_id}/media", self._token, data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",
        })
        return self._field(body, "id")

    def ig_wait(self, container_id: str, timeout: float = 600, interval: float = 10) -> None:
        """Čeká, až IG kontejner zpracuje (FINISHED). Čas se počítá z prospaných intervalů."""
        waited = 0.0
        while True:
            body = self._graph("GET", container_id, self._token, params={"fields": "status_code"})
            status = body.get("status_code")
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise MetaError(f"IG kontejner {container_id} skončil ve stavu {status}")
            if status == "PUBLISHED":
                raise MetaError(f"IG kontejner {container_id} už je zveřejněný (PUBLISHED)")
            if waited + interval > timeout:
                raise MetaTimeout(f"IG kontejner {container_id} není hotový ani po {int(waited)} s (stav {status})")
            self._sleep(interval)
            waited += interval

    def ig_publish(self, container_id: str) -> tuple[str, str]:
        """Zveřejní hotový kontejner a vrátí (media_id, permalink).

        Po úspěšném media_publish je Reel zveřejněný a vrácené media_id to znamená: chyba při dotazu
        na odkaz už nevyhodí výjimku, permalink je pak "".
        """
        body = self._graph("POST", f"{self.ig_user_id}/media_publish", self._token,
                           data={"creation_id": container_id})
        media_id = self._field(body, "id")
        return media_id, self._try_link(media_id, self._token, "permalink")

    def ig_find_recent(self, caption_prefix: str, since: datetime) -> tuple[str, str] | None:
        """Hledá už zveřejněný Reel na IG (po výpadku uprostřed publikace)."""
        body = self._graph("GET", f"{self.ig_user_id}/media", self._token, params={
            "fields": "id,caption,timestamp,permalink", "limit": FIND_LIMIT,
        })
        found = self._find(body.get("data") or [], "caption", "timestamp", "permalink", caption_prefix, since)
        return found[0] if found else None
