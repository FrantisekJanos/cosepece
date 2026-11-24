"""HTTP klient rozhraní reels_api.php pro worker na PC (jen urllib, bez další závislosti).

Token workeru jde v hlavičce X-Hlidac-Token, token vydané úlohy v X-Claim-Token. Chyby vrací server jako JSON
{"error": ...}, ale Apache (413) nebo Cloudflare (403 s kontrolou prohlížeče) vrací HTML: každá ne-JSON odpověď
nebo status ≥ 400 skončí jako ApiError se statusem a prvními 200 znaky těla.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SNIPPET = 200
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Přesměrování se nesleduje: urllib by z POST udělal GET a poslal token na cílovou adresu."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # urllib pak vyhodí HTTPError s 3xx → ApiError


class ApiError(Exception):
    def __init__(self, status: int, snippet: str):
        self.status = status
        self.snippet = snippet
        super().__init__(f"HTTP {status}: {snippet}")


class ReelsApi:
    def __init__(self, base_url: str, token: str, timeout_s: int = 60):
        url = base_url.rstrip("/")
        # stačí adresa webu, cesta k reels_api.php se doplní
        self.url = url if url.endswith(".php") else url + "/reels_api.php"
        parts = urllib.parse.urlsplit(self.url)
        if not parts.hostname or (parts.scheme != "https"
                                  and not (parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS)):
            raise ValueError("adresa API musí být https:// (http:// jen pro localhost)")
        self._opener = urllib.request.build_opener(_NoRedirect)
        self._token = token
        self.timeout_s = timeout_s

    @property
    def host(self) -> str:
        """Jen host (do logu, bez tokenu)."""
        return urllib.parse.urlsplit(self.url).netloc

    def _request(self, method: str, action: str, *, query: dict | None = None, claim_token: str | None = None,
                 payload: dict | None = None, file: Path | None = None) -> dict:
        params = {"action": action, **(query or {})}
        url = self.url + "?" + urllib.parse.urlencode(params)
        headers = {"X-Hlidac-Token": self._token, "Accept": "application/json",
                   "User-Agent": "hlidac-reels-worker/1"}
        if claim_token is not None:
            headers["X-Claim-Token"] = claim_token
        fh = None
        data = None
        if file is not None:
            fh = open(file, "rb")  # tělo se posílá z disku po částech, ne celé do paměti
            data = fh
            headers["Content-Type"] = "video/mp4"
            headers["Content-Length"] = str(Path(file).stat().st_size)
        elif payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        elif method != "GET":
            data = b""
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=self.timeout_s) as resp:
                status, body = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read() if e.fp is not None else b""
        finally:
            if fh is not None:
                fh.close()
        text = body.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            raise ApiError(status, text.strip()[:SNIPPET]) from None
        if status >= 400 or not isinstance(parsed, dict):
            raise ApiError(status, text.strip()[:SNIPPET])
        return parsed

    def claim(self) -> dict | None:
        job = self._request("GET", "claim").get("job")
        return job if isinstance(job, dict) else None

    def upload_video(self, job_id: int, claim_token: str, n: int, path: Path, orientation: str = "portrait") -> dict:
        """Nahraje video varianty; orientation="landscape" = video na šířku 16:9 (odpověď pak nese orientation)."""
        query = {"job_id": job_id, "n": n}
        if orientation != "portrait":
            query["orientation"] = orientation
        return self._request("PUT", "video", query=query, claim_token=claim_token, file=Path(path))

    def result(self, job_id: int, claim_token: str, variants: list[dict], skipped: list[dict] | None = None) -> None:
        """Sada 1–3 variant; skipped = nevzniklé varianty [{n, format, errors}] (starší server pole ignoruje)."""
        payload: dict = {"job_id": job_id, "variants": variants}
        if skipped is not None:
            payload["skipped"] = skipped
        self._request("POST", "result", claim_token=claim_token, payload=payload)

    def fail(self, job_id: int, claim_token: str, error: str) -> str:
        r = self._request("POST", "fail", claim_token=claim_token, payload={"job_id": job_id, "error": error})
        return str(r.get("status", ""))

    def heartbeat(self) -> None:
        self._request("POST", "heartbeat")
