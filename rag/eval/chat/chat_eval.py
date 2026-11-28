"""Kouřový eval chatu přes živý endpoint (spec 14.2, úloha 19).

Scénáře ze `scenare.json` jdou přes `POST /chat-api/zprava` s hlavičkou `X-Chat-Eval` (obejde jen Turnstile, limity
platí dál) a odpověď (SSE) se vyhodnotí stejnými kontrolami jako ve F0 (`kontroly.zkontroluj`). Služba v eval režimu
přidá do události `konec` pole `eval` {cena_usd, nastroje}: cena tahu a seznam volaných nástrojů.

- Klíč se čte jen z proměnné prostředí `CHAT_EVAL_KLIC` (ne z argumentu, ať nezůstane v historii příkazů),
  nikdy se nevypisuje ani neukládá. Konverzace po sobě eval maže (`/chat-api/smazat`).
- `--sada kour`: scénáře s aspoň jedním tahem `kour: true`, každý do posledního takového tahu (dřívější tahy nesou
  kontext konverzace). `--sada vse`: všechny scénáře včetně `jen_endpoint`.
- Cena: u každého tahu se vypíše cena a součet; po překročení `--max-usd` (výchozí 0,30) běh skončí.

Spuštění (z `rag/`):
    CHAT_EVAL_KLIC=… python -m eval.chat.chat_eval --url https://cosepece.cz [--sada kour|vse] [--cookie-beta …]
Návratový kód: 0 = vše prošlo, 1 = selhání kontrol, 2 = služba nedostupná, odmítla zprávu (limit, špatný klíč) nebo
byl překročen strop ceny (neúplný běh; při současném selhání kontrol vyhrává 1, protože je skutečné).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx2 as httpx

from .kontroly import nacti_scenare, zkontroluj

SCENARE = Path(__file__).resolve().parent / "scenare.json"
PREFIX = "/chat-api"
COOKIE_SID = "chat_sid"
COOKIE_BETA = "chat_beta"
MAX_USD = 0.30
TIMEOUT_S = 180.0
# stavy tahu ve výsledku: ok, selhani (kontroly), sluzba (služba tah neodbavila), cena (překročen strop)
OK, SELHANI, SLUZBA, CENA = "ok", "selhani", "sluzba", "cena"


def rozeber_sse(telo: str) -> list[tuple[str, dict]]:
    """Text streamu SSE → [(typ, data)]; komentáře (`: ping`) a nečitelné bloky se přeskočí."""
    out: list[tuple[str, dict]] = []
    for blok in telo.replace("\r\n", "\n").split("\n\n"):
        typ, data = None, None
        for radek in blok.split("\n"):
            if radek.startswith("event: "):
                typ = radek[7:]
            elif radek.startswith("data: "):
                try:
                    data = json.loads(radek[6:])
                except ValueError:
                    data = None
        if typ and isinstance(data, dict):
            out.append((typ, data))
    return out


def vyber_scenare(scenare: list[dict], sada: str) -> list[dict]:
    """`kour`: scénáře s tahem `kour`, zkrácené po poslední takový tah. `vse`: beze změny."""
    if sada == "vse":
        return scenare
    vybrane = []
    for s in scenare:
        kour = [i for i, t in enumerate(s["tahy"]) if t.get("kour")]
        if kour:
            vybrane.append({**s, "tahy": s["tahy"][:kour[-1] + 1]})
    return vybrane


class _Spojeni:
    """Hlavičky a cookie jedné eval relace. Klíč žije jen tady a v hlavičce požadavku."""

    def __init__(self, http: httpx.AsyncClient, url: str, klic: str, cookie_beta: str | None):
        self.http = http
        self.url = url.rstrip("/")
        self._klic = klic
        self._beta = cookie_beta
        self.sid: str | None = None

    def __repr__(self) -> str:
        return f"_Spojeni({self.url})"

    def _hlavicky(self, eval_: bool) -> dict:
        h = {"Origin": self.url, "Content-Type": "application/json"}
        if eval_:
            h["X-Chat-Eval"] = self._klic
        cookie = [f"{COOKIE_BETA}={self._beta}"] if self._beta else []
        if self.sid:
            cookie.append(f"{COOKIE_SID}={self.sid}")
        if cookie:
            h["Cookie"] = "; ".join(cookie)
        return h

    def _zapamatuj_sid(self, r) -> None:
        for c in r.headers.get_list("set-cookie"):
            m = re.match(rf"{COOKIE_SID}=([0-9a-f]{{32}})", c)
            if m:
                self.sid = m.group(1)

    async def zprava(self, text: str, kontext: dict | None, kid: str | None):
        r = await self.http.post(self.url + PREFIX + "/zprava", headers=self._hlavicky(True), timeout=TIMEOUT_S,
                                 content=json.dumps({"konverzace_id": kid, "text": text, "kontext": kontext,
                                                     "turnstile": None}).encode())
        self._zapamatuj_sid(r)
        return r

    async def smaz(self, kid: str) -> bool:
        """Smaže konverzaci (bez eval hlavičky: obslužná adresa ji nezná). Při 409 (tah ještě doznívá) zkusí znovu."""
        for pokus in range(4):
            try:
                r = await self.http.post(self.url + PREFIX + "/smazat", headers=self._hlavicky(False),
                                         content=json.dumps({"konverzace_id": kid}).encode(), timeout=30.0)
            except httpx.HTTPError:
                return False
            if r.status_code == 204:
                return True
            if r.status_code != 409:
                return False
            await asyncio.sleep(0.3 * (pokus + 1))
        return False


def _chyba_zpravy(r) -> str:
    try:
        kod = r.json().get("kod")
    except ValueError:
        kod = None
    return f"HTTP {r.status_code}" + (f" {kod}" if isinstance(kod, str) else "")


async def _tah(sp: _Spojeni, tah: dict, kontext: dict | None, kid: str | None) -> dict:
    """Jeden tah → {stav, kid, odpoved, cena_usd, nastroje, selhani, chyba}."""
    out: dict = {"stav": SLUZBA, "kid": kid, "odpoved": "", "cena_usd": 0.0, "nastroje": None, "selhani": [],
                 "chyba": None}
    try:
        r = await sp.zprava(tah["text"], kontext, kid)
    except httpx.HTTPError as e:
        out["chyba"] = f"spojení: {type(e).__name__}"
        return out
    if r.status_code != 200:
        out["chyba"] = _chyba_zpravy(r)
        return out
    udalosti = rozeber_sse(r.text)
    for typ, d in udalosti:
        if typ == "start" and isinstance(d.get("konverzace_id"), str):
            out["kid"] = d["konverzace_id"]
    out["odpoved"] = "".join(d.get("text", "") for typ, d in udalosti if typ == "delta")
    konec = next((d for typ, d in udalosti if typ == "konec"), None)
    chyba = next((d.get("kod") for typ, d in udalosti if typ == "chyba"), None)
    if konec is None or chyba is not None:
        out["chyba"] = f"tah skončil chybou {chyba}" if chyba else "stream bez události konec"
        return out
    info = konec.get("eval") if isinstance(konec.get("eval"), dict) else {}
    out["cena_usd"] = float(info.get("cena_usd") or 0.0)
    out["nastroje"] = info.get("nastroje") if isinstance(info.get("nastroje"), list) else None
    v = zkontroluj(tah, out["odpoved"], out["nastroje"], out["cena_usd"])
    out["selhani"] = v.selhani
    out["stav"] = OK if v.ok else SELHANI
    return out


async def spust(http: httpx.AsyncClient, url: str, scenare: list[dict], klic: str, *, cookie_beta: str | None = None,
                max_usd: float = MAX_USD, vypis=print) -> list[dict]:
    """Projde tahy scénářů a vrátí výsledek za každý tah:
    {scenar, tah (od 1), stav, selhani, cena_usd, soucet_usd, odpoved, chyba, smazano (jen u posledního tahu
    scénáře: konverzace po sobě smazána)}. Služba, která tah neodbaví (HTTP chyba, limit, výpadek), ukončí celý běh,
    překročení `max_usd` také. Klíč se nevypisuje."""
    sp = _Spojeni(http, url, klic, cookie_beta)
    vysledky: list[dict] = []
    soucet = 0.0
    konec_behu = False
    for s in scenare:
        kid: str | None = None
        kontext = s.get("kontext")
        zacatek = len(vysledky)
        try:
            for i, tah in enumerate(s["tahy"], 1):
                if "kontext" in tah:
                    kontext = tah["kontext"]
                t0 = time.monotonic()
                r = await _tah(sp, tah, kontext, kid)
                kid = r["kid"] or kid
                soucet += r["cena_usd"]
                stav = r["stav"]
                if stav != SLUZBA and soucet > max_usd:
                    stav = CENA
                vysledky.append({"scenar": s["id"], "tah": i, "stav": stav, "selhani": r["selhani"],
                                 "cena_usd": r["cena_usd"], "soucet_usd": soucet, "odpoved": r["odpoved"],
                                 "chyba": r["chyba"], "smazano": None})
                vypis(_radek(vysledky[-1], time.monotonic() - t0))
                if stav in (SLUZBA, CENA):
                    konec_behu = True
                    break
        finally:
            if kid and len(vysledky) > zacatek:
                vysledky[-1]["smazano"] = await sp.smaz(kid)
        if konec_behu:
            break
    return vysledky


def _radek(v: dict, s: float) -> str:
    r = f"{v['scenar']}#{v['tah']}: {v['stav'].upper()}  {v['cena_usd']:.4f} USD (celkem {v['soucet_usd']:.4f})  {s:.1f} s"
    if v["selhani"]:
        r += "\n    " + "\n    ".join(v["selhani"])
    if v["chyba"]:
        r += f"\n    {v['chyba']}"
    if v["stav"] == CENA:
        r += "\n    překročen strop ceny, běh končí"
    return r


def navratovy_kod(vysledky: list[dict]) -> int:
    if any(v["stav"] == SELHANI for v in vysledky):
        return 1
    if not vysledky or any(v["stav"] in (SLUZBA, CENA) for v in vysledky):
        return 2
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m eval.chat.chat_eval", allow_abbrev=False,
                                description="Kouřový eval chatu přes endpoint. Klíč se čte z CHAT_EVAL_KLIC.")
    p.add_argument("--url", default="https://cosepece.cz")
    p.add_argument("--sada", choices=("kour", "vse"), default="kour")
    p.add_argument("--cookie-beta", default=None, help="hodnota cookie chat_beta (skrytý režim)")
    p.add_argument("--max-usd", type=float, default=MAX_USD)
    p.add_argument("--scenare", default=str(SCENARE))
    return p


async def _hlavni(a, klic: str, http: httpx.AsyncClient | None) -> int:
    scenare = vyber_scenare(nacti_scenare(a.scenare), a.sada)
    if not scenare:
        print("Žádné scénáře k puštění.", file=sys.stderr)
        return 2
    vlastni = http is None
    http = http or httpx.AsyncClient()
    try:
        vysledky = await spust(http, a.url, scenare, klic, cookie_beta=a.cookie_beta, max_usd=a.max_usd)
    finally:
        if vlastni:
            await http.aclose()
    kod = navratovy_kod(vysledky)
    celkem = vysledky[-1]["soucet_usd"] if vysledky else 0.0
    nesmazano = [v["scenar"] for v in vysledky if v["smazano"] is False]
    print(f"Hotovo: {len(vysledky)} tahů, {sum(v['stav'] == SELHANI for v in vysledky)} selhání, "
          f"cena {celkem:.4f} USD, kód {kod}")
    if nesmazano:
        print(f"POZOR: konverzace se nepodařilo smazat: {', '.join(nesmazano)}", file=sys.stderr)
    return kod


def main(argv: list[str] | None = None, env=None, http: httpx.AsyncClient | None = None) -> int:
    a = _parser().parse_args(argv)
    klic = (os.environ if env is None else env).get("CHAT_EVAL_KLIC", "").strip()
    if not klic:
        print("Chybí CHAT_EVAL_KLIC v prostředí.", file=sys.stderr)
        return 2
    return asyncio.run(_hlavni(a, klic, http))


if __name__ == "__main__":
    sys.exit(main())
