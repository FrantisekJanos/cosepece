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

    def progress(self, job_id: int, claim_token: str, stage: str | None, note: str | None = None) -> dict:
        """Průběh úlohy: prodlouží vydání (15 min) a zapíše fázi; 409 = úloha zrušena nebo vydání propadlo.

        stage a note jdou jen, když nejsou None (bez stage = jen prodloužení). Starší server akci nezná: 400.
        """
        payload: dict = {"job_id": job_id}
        if stage is not None:
            payload["stage"] = stage
        if note is not None:
            payload["note"] = note
        return self._request("POST", "progress", claim_token=claim_token, payload=payload)

    def fail(self, job_id: int, claim_token: str, error: str, kind: str | None = None,
             retry_after_s: int | None = None, reason: str | None = None) -> str:
        """Chyba úlohy; kind = content|timeout (konečné), env|infra (zpět do fronty po retry_after_s).

        Pole jdou jen, když nejsou None: fail bez kind je pro server dnešní chování (3 pokusy, bez odkladu).
        """
        payload: dict = {"job_id": job_id, "error": error}
        for key, value in (("kind", kind), ("retry_after_s", retry_after_s), ("reason", reason)):
            if value is not None:
                payload[key] = value
        r = self._request("POST", "fail", claim_token=claim_token, payload=payload)
        return str(r.get("status", ""))

    def heartbeat(self, state: dict | None = None) -> None:
        """Worker žije; state = stav workeru {"state": "running"|"paused", ...}, None = prázdné tělo."""
        self._request("POST", "heartbeat", payload=state)

    def x_pending(self) -> dict:
        """Reels na X: {"wanted": [{id, set_id, variant, ct}], "remove": [id…]}. Starší server akci nezná: 400."""
        r = self._request("GET", "x_pending")
        wanted = r.get("wanted") if isinstance(r.get("wanted"), list) else []
        remove = r.get("remove") if isinstance(r.get("remove"), list) else []
        return {"wanted": [w for w in wanted if isinstance(w, dict)],
                "remove": [i for i in remove if isinstance(i, int) and not isinstance(i, bool)]}

    def x_ready(self, reel_id: int, result: str, path: str | None = None) -> None:
        """Výsledek kopie videa do složky Pro X: "ready" nebo "missing" (video na PC není). path = celá cesta
        ke zkopírovanému videu, bot ji vypíše ve zprávě Pro X (starší server pole ignoruje)."""
        payload: dict = {"id": reel_id, "result": result}
        if path:
            payload["path"] = path
        self._request("POST", "x_ready", payload=payload)
