"""Šetrné stahování z psp.cz: jeden požadavek za sekundu, slušný User-Agent."""
from __future__ import annotations

import time
import urllib.error
import urllib.request

BASE = "https://www.psp.cz"
USER_AGENT = "HlidacZakonu/1.0 (+https://cosepece.cz)"


class Fetcher:
    def __init__(self, delay: float = 1.0, timeout: float = 60.0):
        self.delay = delay
        self.timeout = timeout
        self._last = 0.0
        self.requests = 0

    def get(self, url: str) -> bytes | None:
        """Vrátí tělo odpovědi, None při 404. Jiné chyby vyhodí výjimku."""
        if url.startswith("/"):
            url = BASE + url
        wait = self._last + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        finally:
            self._last = time.monotonic()
            self.requests += 1
