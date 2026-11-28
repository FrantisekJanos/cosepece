"""Běžící tah a jeho stream SSE (spec 3, 9.2): události v paměti procesu, opětovné připojení a ověřovaný výstup.

- `BehTahu` drží události tahu (už zakódované jako SSE s pořadovým `id`), aby se klient mohl znovu připojit
  od offsetu. Tah běží jako samostatná úloha, odpojení klienta ho nezruší: když nikdo neposlouchá
  `odpojeni_s` sekund, nastaví se `prerusit` a smyčka skončí po rozběhnutém kole (cena se zapíše).
- `ProudTahu` je odpověď SSE nad `BehTahu`: od offsetu `od`, komentář `: ping` každých `ping_s` sekund.
- `Vystup` převádí text modelu na delty: odstavec s citací (uvozovka nebo řádek `>`) jde ven až po ověření
  (`citace.over_odstavec`, událost `overeni` před ním), ostatní text průběžně; neznámá `[N]` se odstraní.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable

from starlette.responses import Response

from .citace import _APOSTROFY, _ZNAKY_UVOZOVEK, Odstavce, Overeni, ma_citat
from .nastroje import Polozka
from .registr import Registr

log = logging.getLogger("hlidac_rag.chat.beh")

PING = b": ping\n\n"
HLAVICKY_SSE = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


def sse(i: int, typ: str, data: dict) -> bytes:
    return f"id: {i}\nevent: {typ}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


class BehTahu:
    def __init__(self, tah_id: str, relace_id: str, odpojeni_s: float):
        self.id = tah_id
        self.relace_id = relace_id
        self.odpojeni_s = odpojeni_s
        self.udalosti: list[bytes] = []
        self.typy: list[str] = []
        self.hotovo = False
        self.prerusit = asyncio.Event()
        self._signal = asyncio.Event()
        self._posluchacu = 0
        self._casovac: asyncio.TimerHandle | None = None

    def pridej(self, typ: str, data: dict) -> None:
        self.udalosti.append(sse(len(self.udalosti), typ, data))
        self.typy.append(typ)
        self._probud()

    def skonci(self) -> None:
        self.hotovo = True
        self._zrus_casovac()
        self._probud()

    def _probud(self) -> None:
        stary, self._signal = self._signal, asyncio.Event()
        stary.set()

    def _zrus_casovac(self) -> None:
        if self._casovac is not None:
            self._casovac.cancel()
            self._casovac = None

    def prihlas(self) -> None:
        self._posluchacu += 1
        self._zrus_casovac()

    def odhlas(self) -> None:
        self._posluchacu -= 1
        if self._posluchacu <= 0 and not self.hotovo and self._casovac is None:
            self._casovac = asyncio.get_running_loop().call_later(self.odpojeni_s, self._odpojeno)

    def _odpojeno(self) -> None:
        self._casovac = None
        if self._posluchacu <= 0 and not self.hotovo:
            log.info("tah %s: klient se odpojil a nevrátil, tah skončí po rozběhnutém kole", self.id)
            self.prerusit.set()

    @property
    def signal(self) -> asyncio.Event:
        return self._signal


class ProudTahu(Response):
    """Stream SSE událostí tahu od offsetu `od`. Odpojení klienta tah nezruší, jen se odhlásí posluchač."""

    media_type = "text/event-stream"

    def __init__(self, beh: BehTahu, od: int, ping_s: float, headers: dict | None = None):
        super().__init__(status_code=200, headers={**HLAVICKY_SSE, **(headers or {})})
        # Response bez těla doplní content-length: 0 a skutečný server (h11) pak stream odmítne
        self.raw_headers = [(k, v) for k, v in self.raw_headers if k != b"content-length"]
        self.beh, self.od, self.ping_s = beh, max(0, od), ping_s

    async def __call__(self, scope, receive, send) -> None:
        beh = self.beh
        beh.prihlas()
        odpojeno = asyncio.Event()

        async def poslouchej():
            while True:
                m = await receive()
                if m["type"] == "http.disconnect":
                    odpojeno.set()
                    return

        posluchac = asyncio.ensure_future(poslouchej())
        try:
            await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
            i = self.od
            while not odpojeno.is_set():
                while i < len(beh.udalosti):
                    await send({"type": "http.response.body", "body": beh.udalosti[i], "more_body": True})
                    i += 1
                if beh.hotovo:
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                    break
                cekani = asyncio.ensure_future(beh.signal.wait())
                hotove, _ = await asyncio.wait({cekani, posluchac}, timeout=self.ping_s,
                                               return_when=asyncio.FIRST_COMPLETED)
                cekani.cancel()
                if not hotove:
                    await send({"type": "http.response.body", "body": PING, "more_body": True})
        except OSError:
            pass       # klient je pryč (ASGI 2.4: send po odpojení)
        finally:
            posluchac.cancel()
            beh.odhlas()


class RegistrTahu(Registr):
    """Registr, který si v tahu pamatuje texty položek (zdroj pro ověření citací bez dalšího dotazu)."""

    def __init__(self, zdroje=()):
        super().__init__(zdroje)
        self.texty: dict[str, str] = {}

    def pridej(self, p: Polozka) -> int:
        if p.text:
            self.texty[p.klic] = p.text
        return super().pridej(p)


# Odstavec se průběžně posílá jen po první znak, kterým může začít citace (uvozovka, apostrof, řádek `>`).
_ZACATEK_CITACE = re.compile("[" + re.escape(_ZNAKY_UVOZOVEK + _APOSTROFY) + r"]|^[ \t]*>", re.M)
_ROZEPSANY_ODKAZ = re.compile(r"\[\d*$")


def _bezpecny_zacatek(s: str) -> str:
    """Začátek rozepsaného odstavce, který už se nezmění: bez citace, bez koncových mezer a rozepsaného `[N`."""
    m = _ZACATEK_CITACE.search(s)
    s = (s[:m.start()] if m else s).rstrip(" \t\n")
    return _ROZEPSANY_ODKAZ.sub("", s).rstrip(" \t\n")


class Vystup:
    """Text modelu → (typ, data) událostí `delta` a `overeni`; hotový text = ověřené odstavce."""

    def __init__(self, registr: Registr, over: Callable[[str], Awaitable[Overeni]]):
        self._registr = registr
        self._over = over
        self._odstavce = Odstavce()
        self._hotove: list[str] = []
        self._odeslano = ""         # začátek (vyčištěného) rozepsaného odstavce, který už odešel
        self.citaty: list[dict] = []
        self.odstraneno = 0         # neznámých [N] (metrika)

    @property
    def text(self) -> str:
        return "\n\n".join(self._hotove)

    def _delta(self, kus: str, novy_odstavec: bool) -> list[tuple[str, dict]]:
        if not kus:
            return []
        oddelovac = "\n\n" if novy_odstavec and self._hotove else ""
        return [("delta", {"text": oddelovac + kus})]

    async def pridej(self, delta: str) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        for odstavec in self._odstavce.pridej(delta):
            out += await self._dokonci(odstavec)
        cisty, _ = self._registr.odstran_nezname(_bezpecny_zacatek(self._odstavce.zbytek()))
        if len(cisty) > len(self._odeslano):
            out += self._delta(cisty[len(self._odeslano):], not self._odeslano)
            self._odeslano = cisty
        return out

    async def konec(self) -> list[tuple[str, dict]]:
        zbytek = self._odstavce.zbytek()
        return await self._dokonci(zbytek) if zbytek.strip() else []

    async def _dokonci(self, odstavec: str) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        text = odstavec
        if ma_citat(odstavec):
            # ověření PŘED odstraněním neznámých [N]: citát s vymyšleným číslem tak zůstane přiřazený k číslu
            ov = await self._over(odstavec)
            text = ov.text
            if ov.citaty:
                self.citaty += ov.citaty
                out.append(("overeni", {"citaty": ov.citaty}))
        cisty, odstraneno = self._registr.odstran_nezname(text)
        self.odstraneno += odstraneno
        cisty = cisty.strip("\n")
        if not cisty.strip():
            cisty = self._odeslano      # co už odešlo, zůstává i v uloženém textu
            if not cisty:
                return out
        if not cisty.startswith(self._odeslano):
            # nemá nastat: ověření mění jen text od první uvozovky, ta se průběžně neposílá
            log.warning("ověřený odstavec nezačíná odeslaným textem")
            cisty = self._odeslano + cisty[len(self._odeslano):]
        out += self._delta(cisty[len(self._odeslano):], not self._odeslano)
        self._hotove.append(cisty)
        self._odeslano = ""
        return out
