"""Eval sada pro MCP server cosepece.cz.

Spuštění z adresáře rag/:
    MCP_TOKEN=... python -m eval.run --url https://mcp.cosepece.cz/mcp [--jen-faze dnes,pr2]

Token se čte jen z proměnné prostředí MCP_TOKEN, nikdy z argumentu ani ze souboru.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from datetime import date
from pathlib import Path

ZDE = Path(__file__).parent
OTAZKY = ZDE / "otazky.json"
VYSLEDKY = ZDE / "vysledky"
SOUHRN_RE = re.compile(r"<!-- souhrn: (\{.*?\}) -->")


def _hodnoty(data, casti: list[str]) -> list:
    """Projde tečkovou cestu; `*` rozvětví přes prvky seznamu. Vrátí všechny nalezené hodnoty."""
    if not casti:
        return [data]
    cast, zbytek = casti[0], casti[1:]
    if cast == "*":
        if not isinstance(data, list):
            return []
        return [v for prvek in data for v in _hodnoty(prvek, zbytek)]
    if isinstance(data, dict) and cast in data:
        return _hodnoty(data[cast], zbytek)
    if isinstance(data, list) and cast.isdigit() and int(cast) < len(data):
        return _hodnoty(data[int(cast)], zbytek)
    return []


def _text(hodnota) -> str:
    return hodnota if isinstance(hodnota, str) else json.dumps(hodnota, ensure_ascii=False)


def check(odpoved: dict | str, kontrola: dict) -> tuple[bool, str]:
    """Vyhodnotí jednu kontrolu. Chybová odpověď (isError) přichází jako {"__error__": text}."""
    je_error = isinstance(odpoved, dict) and "__error__" in odpoved
    if "je_chyba" in kontrola:
        chtena = bool(kontrola["je_chyba"])
        if je_error == chtena:
            return True, ""
        return False, f"je_chyba: očekáváno {chtena}, odpověď {'je' if je_error else 'není'} isError"
    if je_error:
        return False, f"odpověď je chyba: {odpoved['__error__']}"
    cesta = kontrola.get("cesta", "")
    nalezene = _hodnoty(odpoved, cesta.split(".")) if cesta else [odpoved]
    popis = cesta or "(celá odpověď)"
    if not nalezene:
        return False, f"{popis}: cesta v odpovědi neexistuje"
    if "rovna_se" in kontrola:
        cil = kontrola["rovna_se"]
        if any(h == cil for h in nalezene):
            return True, ""
        return False, f"{popis}: očekáváno {cil!r}, nalezeno {nalezene[:3]!r}"
    if "jeden_z" in kontrola:
        # otázka s více správnými odpověďmi v korpusu (např. kdo kritizoval…): stačí kterákoli z nich
        cile = kontrola["jeden_z"]
        if any(h in cile for h in nalezene):
            return True, ""
        return False, f"{popis}: očekáváno jedno z {cile!r}, nalezeno {nalezene[:3]!r}"
    if "obsahuje" in kontrola:
        cil = kontrola["obsahuje"]
        if any(cil in _text(h) for h in nalezene):
            return True, ""
        return False, f"{popis}: neobsahuje {cil!r}"
    return True, ""


def _faze(jen_faze: str | None) -> list[str]:
    return sorted({f.strip() for f in (jen_faze or "").split(",") if f.strip()})


def vyber_otazky(otazky: list[dict], jen_faze: str | None) -> list[dict]:
    faze = _faze(jen_faze)
    if not faze:
        return otazky
    return [o for o in otazky if o.get("faze") in faze]


def nazev_vysledku(dnes: str, jen_faze: str | None) -> str:
    """Jméno souboru bez přípony: celý běh RRRR-MM-DD, výběr fází RRRR-MM-DD-<fáze>."""
    faze = _faze(jen_faze)
    if not faze:
        return dnes
    return dnes + "-" + "+".join(re.sub(r"[^\w.]", "_", f) for f in faze)


def souhrn(radky: list[dict]) -> dict:
    znaky = [r["znaku"] for r in radky]
    return {
        "ok": sum(1 for r in radky if r["ok"]),
        "celkem": len(radky),
        "median_znaku": int(statistics.median(znaky)) if znaky else 0,
        "max_znaku": max(znaky) if znaky else 0,
    }


def porovnej(predchozi: dict, nove: dict) -> str:
    return (
        f"Předchozí běh: ok {predchozi['ok']}/{predchozi['celkem']}, "
        f"medián znaků {predchozi['median_znaku']}, max znaků {predchozi['max_znaku']}. "
        f"Nyní: ok {nove['ok']}/{nove['celkem']}, medián znaků {nove['median_znaku']}, "
        f"max znaků {nove['max_znaku']}."
    )


def render_tabulka(radky: list[dict], datum: str, predchozi: dict | None) -> str:
    s = souhrn(radky)
    out = [f"# Eval MCP {datum}", "", "| Otázka | Volání | Výsledek | ms | znaků | Detail |", "|---|---|---|---|---|---|"]
    for r in radky:
        detail = r.get("detail", "").replace("|", "/").replace("\n", " ")
        out.append(f"| {r['otazka']} | {r['volani']} | {'ok' if r['ok'] else 'FAIL'} | {r['ms']} | {r['znaku']} | {detail} |")
    out += [
        "",
        f"Souhrn: ok: {s['ok']}/{s['celkem']}, medián znaků: {s['median_znaku']}, max znaků: {s['max_znaku']}",
    ]
    if predchozi:
        out += ["", porovnej(predchozi, s)]
    out += ["", f"<!-- souhrn: {json.dumps(s)} -->", ""]
    return "\n".join(out)


def nacti_predchozi(slozka: Path, dnes: str, jen_faze: str | None = None) -> dict | None:
    """Souhrn z posledního souboru stejného druhu (celý běh, nebo stejný výběr fází) jiného než dnešního data.
    Běh jen s několika fázemi se s celým během nesrovnává (jiný počet otázek)."""
    pripona = nazev_vysledku(dnes, jen_faze)[len(dnes):]
    soubory = sorted(p for p in slozka.glob("*.md")
                     if re.fullmatch(r"\d{4}-\d{2}-\d{2}" + re.escape(pripona), p.stem) and not p.stem.startswith(dnes))
    if not soubory:
        return None
    m = SOUHRN_RE.search(soubory[-1].read_text(encoding="utf-8"))
    return json.loads(m.group(1)) if m else None


def odpoved_z_vysledku(vysledek) -> dict | str:
    """Převede mcp CallToolResult na dict (úspěch), text, nebo {"__error__": text} při is_error."""
    text = "".join(getattr(c, "text", "") for c in vysledek.content)
    if vysledek.is_error:
        return {"__error__": text}
    if isinstance(vysledek.structured_content, dict):
        return vysledek.structured_content
    try:
        return json.loads(text)
    except ValueError:
        return text


async def _spust(url: str, token: str, otazky: list[dict], pauza: float = 0.0) -> list[dict]:
    """Projde volání otázek. `pauza` = sekundy mezi voláními (server má limit volání za minutu na token)."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    radky: list[dict] = []
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with http, streamable_http_client(url, http_client=http) as (cteni, zapis, *_):
        async with ClientSession(cteni, zapis) as sess:
            await sess.initialize()
            for o in otazky:
                for v in o.get("volani", []):
                    if radky and pauza:
                        await asyncio.sleep(pauza)
                    t0 = time.perf_counter()
                    try:
                        vysl = await sess.call_tool(v["nastroj"], v.get("argumenty", {}))
                        odpoved = odpoved_z_vysledku(vysl)
                    except Exception as e:  # síťová chyba apod. = FAIL, běh pokračuje
                        odpoved = {"__error__": f"{type(e).__name__}: {e}"}
                    ms = int((time.perf_counter() - t0) * 1000)
                    znaku = len(_text(odpoved["__error__"] if isinstance(odpoved, dict) and "__error__" in odpoved else odpoved))
                    chyby = [m for ok, m in (check(odpoved, k) for k in v.get("kontroly", [])) if not ok]
                    radky.append({
                        "otazka": o["id"], "volani": v["nastroj"], "ok": not chyby,
                        "ms": ms, "znaku": znaku, "detail": "; ".join(chyby),
                    })
    return radky


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.run", description="Eval sada pro MCP server cosepece.cz (token v MCP_TOKEN).")
    ap.add_argument("--url", required=True, help="adresa MCP endpointu, např. https://mcp.cosepece.cz/mcp")
    ap.add_argument("--jen-faze", help="čárkou oddělené fáze, např. dnes,pr2")
    ap.add_argument("--pauza", type=float, default=1.2,
                    help="sekundy mezi voláními (výchozí 1,2 s = pod limitem serveru 60 volání/min)")
    args = ap.parse_args(argv)
    token = os.environ.get("MCP_TOKEN")
    if not token:
        print("Chybí proměnná prostředí MCP_TOKEN.", file=sys.stderr)
        return 2
    otazky = vyber_otazky(json.loads(OTAZKY.read_text(encoding="utf-8")), args.jen_faze)
    if not otazky:
        print(f"Žádná otázka nemá fázi z --jen-faze {args.jen_faze!r}.", file=sys.stderr)
        return 2
    radky = asyncio.run(_spust(args.url, token, otazky, args.pauza))
    dnes = date.today().isoformat()
    md = render_tabulka(radky, dnes, nacti_predchozi(VYSLEDKY, dnes, args.jen_faze))
    VYSLEDKY.mkdir(exist_ok=True)
    (VYSLEDKY / f"{nazev_vysledku(dnes, args.jen_faze)}.md").write_text(md, encoding="utf-8")
    print(md)
    return 0 if all(r["ok"] for r in radky) else 1


if __name__ == "__main__":
    sys.exit(main())
