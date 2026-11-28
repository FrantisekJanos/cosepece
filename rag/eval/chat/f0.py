"""F0: srovnání modelů chatu přes `claude -p` na předplatném (spec 14.2, úloha 8).

Každý tah scénáře = jeden běh Claude Code CLI se systémovým promptem chatu (`prompt.SYSTEM`), bez vestavěných nástrojů
a jen s nástroji veřejného MCP `mcp__cosepece__*`. Další tah téže konverzace navazuje přes `--resume`. API klíč se
nepoužívá: s `ANTHROPIC_API_KEY` v prostředí běh odmítne začít (CLI by jinak platilo API).

Cizí pokyny (CLAUDE.md, paměť, hooky uživatele) odřízne `--setting-sources ""` a pracovní složka mimo strom
s `CLAUDE.md` (výchozí `%TEMP%/cosepece-f0`); `--bare` se nepoužívá (čte jen `ANTHROPIC_API_KEY`).
Jedna pracovní složka na (konfigurace, scénář, běh), sdílená všemi tahy scénáře: `--resume` hledá relaci podle cwd.

Cena tahu = `usage` × ceník (`cenik.cena_usd`). Je to horní odhad: CLI přidává vlastní režii a nástroje MCP nejsou
oříznuté jako v chatu. Výstup `--output-format json` neuvádí volané nástroje, kontrola `nastroje_any` se ve F0 vynechá.

Spuštění (z `rag/`):
    python -m eval.chat.f0 --mcp-config <mcp.json> --vystup <složka> [--konfigurace model:effort,…] [--jen-sada X]
Po limitu předplatného skončí kódem 3; další spuštění stejného příkazu přeskočí tahy ze `stav.jsonl`.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from hlidac_rag.chat import prompt
from hlidac_rag.chat.cenik import cena_usd

from .kontroly import SADY, nacti_scenare, zkontroluj

PREDPONA = "mcp__cosepece__"
# spec 8.1 bez nových nástrojů chatu (nacti_zdroj, upozorneni, pripravovane); over_citaci a stav_dat model nedostane
NASTROJE_MCP: list[str] = [PREDPONA + n for n in (
    "get_tisk", "seznam_tisku", "tisky_menici_zakon", "tisk_podle_sbirky",
    "pn_k_tisku", "get_pn",
    "osnova_textu", "get_cel", "hledej_v_textech",
    "rozprava", "projevy_k_tisku", "projevy_recnika", "get_projev", "hledej_v_projevech", "najdi_osobu",
    "hlasovani_k_tisku", "get_hlasovani",
)]
# nástroje MCP mimo chat: --disallowedTools je odebere i z kontextu modelu (sonda 28. 11.), --allowedTools jen schvaluje
NASTROJE_ZAKAZANE: list[str] = [PREDPONA + n for n in ("over_citaci", "stav_dat", "program_schuze", "zmeny_projevu")]

KONFIGURACE = "claude-sonnet-5-5:low,claude-haiku-5-5:low,claude-haiku-5-5:medium"
# sady s kritériem „žádné selhání must_not ve třech bězích“ (spec 14.2, bod 1)
BEHU: dict[str, int] = {"poctivost": 3, "citace": 3, "injekce": 3, "neutralita": 3}
SCENARE = Path(__file__).resolve().parent / "scenare.json"
STAV = "stav.jsonl"
PAUZA_S = 2.0

# hláška limitu předplatného: stejné vzory jako worker Reels (reels/…/generator/worker.py PAUSE_PATTERNS, důvod
# „limit“); „usage“ bez „Usage Policy“ (odmítnutí požadavku není limit)
_LIMIT_VZORY = (r"limit", r"usage(?! polic)", r"quota", r"rate.?limit", r"too many requests", r"\b429\b")


class LimitPredplatneho(Exception):
    """Vyčerpaný limit předplatného Claude; `zbyva` = počet tahů, které ještě neproběhly."""

    def __init__(self, zprava: str, zbyva: int | None = None):
        super().__init__(zprava)
        self.zbyva = zbyva


class ChybaBehu(Exception):
    """Běh `claude -p` selhal jinak než limitem (nečitelný výstup, chyba API, timeout)."""

    def __init__(self, zprava: str, session_id: str | None = None):
        super().__init__(zprava)
        self.session_id = session_id


def _je_limit(text: str) -> bool:
    return any(re.search(v, text or "", re.IGNORECASE) for v in _LIMIT_VZORY)


def _bez_api_klice() -> None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY je v prostředí: F0 běží jen na předplatném, claude -p by platilo API. "
                         "Odeber proměnnou a spusť znovu.")


def binarka() -> str:
    """Spustitelný soubor CLI: `CLAUDE_BIN`, jinak `claude` z PATH (na Windows celá cesta k claude.exe)."""
    return os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"


def prikaz(model: str, effort: str, system: str, mcp_config: str, resume: str | None) -> list[str]:
    """Argumenty jednoho běhu; prompt tahu jde na stdin. `--allowedTools` je poslední, nástroj = prvek seznamu."""
    p = [binarka(), "-p", "--model", model, "--effort", effort, "--output-format", "json",
         "--system-prompt", system, "--restricted", "--tools", "",
         "--strict-mcp-config", "--mcp-config", str(mcp_config),
         "--permission-mode", "dontAsk", "--setting-sources", ""]
    if resume:
        p += ["--resume", resume]
    return p + ["--disallowedTools", *NASTROJE_ZAKAZANE, "--allowedTools", *NASTROJE_MCP]


def rozeber(stdout: str, model: str) -> dict:
    """Výstup `--output-format json` → odpověď, tokeny, kola, doba a cena tahu. Limit → `LimitPredplatneho`,
    jiná chyba → `ChybaBehu`. Seznam událostí (starší CLI) se čte od konce jako ve workeru Reels."""
    try:
        data = json.loads((stdout or "").strip())
    except ValueError:
        raise ChybaBehu("výstup claude -p není JSON") from None
    if isinstance(data, list):
        data = next((d for d in reversed(data) if isinstance(d, dict) and "is_error" in d), None)
    if not isinstance(data, dict):
        raise ChybaBehu("výstup claude -p není objekt s výsledkem")
    sid = data.get("session_id") if isinstance(data.get("session_id"), str) else None
    if data.get("is_error") is True:
        text = str(data.get("result") or data.get("subtype") or "")
        if data.get("api_error_status") == 429 or _je_limit(text):
            raise LimitPredplatneho(text[:300])
        raise ChybaBehu(f"is_error: {text[:300]}", sid)
    u = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    tokeny = {k: int(u.get(pole) or 0) for k, pole in (("vstup", "input_tokens"), ("vystup", "output_tokens"),
                                                         ("cache_cteni", "cache_read_input_tokens"),
                                                         ("cache_zapis", "cache_creation_input_tokens"))}
    return {"odpoved": str(data.get("result") or ""), "session_id": sid or "", **tokeny,
            "kol": int(data.get("num_turns") or 0), "ms": int(data.get("duration_ms") or 0),
            "cena_usd": cena_usd(model, tokeny["vstup"], tokeny["vystup"], tokeny["cache_cteni"], tokeny["cache_zapis"])}


def _stranka(k: dict) -> tuple:
    # stránka webu = typ a tisk; PN a hlasování jsou místa na stránce tisku (jako kontext.znacka, úloha 12)
    return k.get("typ"), k.get("ct")


def znacka(kontext: dict | None, predchozi: dict | None = None) -> str:
    """Značka serveru do uživatelské zprávy (spec 5.5), jen typ stránky a čísla; volný text z kontextu nikdy."""
    if not kontext:
        return ""
    typ = kontext.get("typ")
    typ = typ if isinstance(typ, str) and re.fullmatch(r"[a-z_]{1,30}", typ) else "jina"

    def cislo(klic: str) -> int | None:
        v = kontext.get(klic)
        return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None

    popis = typ + (f" {cislo('ct')}" if cislo("ct") else "")
    if cislo("pn"):
        popis += f", PN {cislo('pn')}"
    if cislo("hlasovani"):
        popis += f", hlasování {cislo('hlasovani')}"
    zmena = predchozi is not None and _stranka(predchozi) != _stranka(kontext)
    return f"[stránka: {popis} · stranka_zmenena: {'ano' if zmena else 'ne'}]"


def _nazev(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)


def _konf(model: str, effort: str) -> str:
    return f"{model}:{effort}"


def _klic(r: dict) -> tuple:
    return r["id"], r["model"], r["effort"], r["beh"], r["tah"]


def _nacti_stav(cesta: Path) -> dict[tuple, dict]:
    if not cesta.exists():
        return {}
    radky = (json.loads(r) for r in cesta.read_text(encoding="utf-8").splitlines() if r.strip())
    return {_klic(r): r for r in radky}


def _plan(scenare: list[dict], konfigurace: list[tuple[str, str]], behu: dict[str, int]) -> list[tuple]:
    """Tahy v pořadí běhu: (scénář, model, effort, běh, číslo tahu od 1, tah, kontext tahu, kontext předchozího)."""
    plan = []
    for model, effort in konfigurace:
        for s in scenare:
            if s.get("jen_endpoint"):
                continue  # limit tahů a podobné vlastnosti služby ověří až chat-eval přes endpoint
            for beh in range(1, behu.get(s["sada"], 1) + 1):
                kontext, predchozi = s.get("kontext"), None
                for i, tah in enumerate(s["tahy"], 1):
                    if i > 1:
                        predchozi = kontext
                    if "kontext" in tah:
                        kontext = tah["kontext"]
                    plan.append((s, model, effort, beh, i, tah, kontext, predchozi))
    return plan


def _over_pracovni(pracovni: Path) -> None:
    for d in (pracovni, *pracovni.parents):
        if (d / "CLAUDE.md").exists():
            raise SystemExit(f"Pracovní složka {pracovni} leží ve stromu s CLAUDE.md ({d}); "
                             "F0 musí měřit jen systémový prompt chatu.")


def spust(scenare, konfigurace: list[tuple[str, str]], behu: dict[str, int], vystup: Path,
          spust_claude=subprocess.run, *, mcp_config: str = "", pracovni: Path | None = None,
          system: str = prompt.SYSTEM, pauza_s: float = PAUZA_S, timeout_s: int = 300) -> list[dict]:
    """Projde scénáře pro každou konfiguraci; řádek na tah do `vystup/stav.jsonl` a vrátí všechny řádky plánu.
    Tahy už zapsané ve `stav.jsonl` přeskočí. Limit předplatného → `LimitPredplatneho` se `zbyva`."""
    vystup = Path(vystup)
    pracovni = Path(pracovni) if pracovni else Path(tempfile.gettempdir()) / "cosepece-f0"
    pracovni = pracovni.resolve()
    _over_pracovni(pracovni)
    vystup.mkdir(parents=True, exist_ok=True)
    stav_cesta = vystup / STAV
    hotove = _nacti_stav(stav_cesta)
    plan = _plan(scenare, konfigurace, behu)
    prvni = True
    for poradi, (s, model, effort, beh, i, tah, kontext, predchozi) in enumerate(plan):
        klic = (s["id"], model, effort, beh, i)
        if klic in hotove:
            continue
        _bez_api_klice()
        resume = (hotove.get((s["id"], model, effort, beh, i - 1)) or {}).get("session_id") if i > 1 else None
        cwd = pracovni / _nazev(f"{model}-{effort}") / _nazev(f"{s['id']}-b{beh}")
        cwd.mkdir(parents=True, exist_ok=True)
        z = znacka(kontext, predchozi)
        vstup = f"{z}\n\n{tah['text']}" if z else tah["text"]
        if not prvni:
            time.sleep(pauza_s)
        prvni = False
        radek = {"id": s["id"], "sada": s["sada"], "model": model, "effort": effort, "beh": beh, "tah": i,
                 "otazka": tah["text"], "znacka": z}
        surove = vystup / "surove" / _nazev(f"{model}-{effort}")
        surove.mkdir(parents=True, exist_ok=True)
        try:
            proc = spust_claude(prikaz(model, effort, system, mcp_config, resume), input=vstup, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", cwd=str(cwd), timeout=timeout_s)
            jmeno = _nazev(f"{s['id']}-b{beh}-t{i}")
            (surove / f"{jmeno}.json").write_text(proc.stdout or "", encoding="utf-8")
            if proc.stderr:
                (surove / f"{jmeno}.err").write_text(proc.stderr, encoding="utf-8")
            try:
                v = rozeber(proc.stdout, model)
            except ChybaBehu:
                try:
                    json.loads((proc.stdout or "").strip())
                except ValueError:  # nečitelný stdout: hlášku limitu může nést jen stderr
                    if _je_limit(proc.stderr):
                        raise LimitPredplatneho((proc.stderr or "")[:300]) from None
                raise
            radek.update(v)
            radek["selhani"] = zkontroluj(tah, v["odpoved"], None, v["cena_usd"]).selhani
        except LimitPredplatneho as e:
            zbyva = sum(1 for p in plan[poradi:] if (p[0]["id"], *p[1:5]) not in hotove)
            raise LimitPredplatneho(str(e), zbyva) from None
        except (ChybaBehu, subprocess.TimeoutExpired) as e:
            chyba = f"timeout {timeout_s} s" if isinstance(e, subprocess.TimeoutExpired) else str(e)
            radek.update({"odpoved": "", "session_id": getattr(e, "session_id", None) or "", "vstup": 0, "vystup": 0,
                          "cache_cteni": 0, "cache_zapis": 0, "kol": 0, "ms": 0, "cena_usd": 0.0,
                          "selhani": [f"beh: {chyba}"]})
        with stav_cesta.open("a", encoding="utf-8") as f:
            f.write(json.dumps(radek, ensure_ascii=False) + "\n")
        hotove[klic] = radek
    return [hotove[(p[0]["id"], *p[1:5])] for p in plan]


def _skore(radky: list[dict]) -> float:
    """Podíl tahů bez selhání kontrol; `max_cena_usd` se nepočítá (F12: cena F0 je horní odhad)."""
    if not radky:
        return 0.0
    return 100 * sum(1 for r in radky if not [x for x in r["selhani"] if x != "max_cena_usd"]) / len(radky)


def _po_konfiguracich(radky: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in radky:
        out.setdefault(_konf(r["model"], r["effort"]), []).append(r)
    return out


def souhrn(radky: list[dict]) -> str:
    """Markdown: řádek na konfiguraci se skóre celkem a po sadách, selháními must_not (spec 14.2 bod 1),
    selháními max_cena_usd zvlášť, chybami běhu a mediány ceny, kol a doby tahu."""
    sady = [s for s in SADY if any(r["sada"] == s for r in radky)]
    hlavicka = ["konfigurace", "tahů", "skóre", *sady, "must_not", "max_cena_usd", "chyby běhu",
                "medián ceny", "medián kol", "medián ms"]
    md = ["| " + " | ".join(hlavicka) + " |", "|" + "---|" * len(hlavicka)]
    for konf, rr in _po_konfiguracich(radky).items():
        ok = [r for r in rr if not any(x.startswith("beh:") for x in r["selhani"])]
        bunky = [konf, str(len(rr)), f"{_skore(rr):.1f} %"]
        bunky += [f"{_skore([r for r in rr if r['sada'] == s]):.1f} %" for s in sady]
        bunky += [str(sum(1 for r in rr if any(x.startswith("must_not") for x in r["selhani"]))),
                  str(sum(1 for r in rr if "max_cena_usd" in r["selhani"])),
                  str(len(rr) - len(ok))]
        if ok:
            bunky += [f"${statistics.median(r['cena_usd'] for r in ok):.4f}",
                      f"{statistics.median(r['kol'] for r in ok):g}",
                      f"{statistics.median(r['ms'] for r in ok):.0f}"]
        else:
            bunky += ["–", "–", "–"]
        md.append("| " + " | ".join(bunky) + " |")
    return "\n".join(md) + "\n"


def _bez_modelu(text: str) -> str:
    return re.sub(r"sonnet|haiku|opus", "[model]", text or "", flags=re.IGNORECASE)


def slepe_pary(radky: list[dict], pocet: int = 20, seed: int = 1) -> tuple[str, str]:
    """Páry odpovědí Sonnetu low a lepší konfigurace Haiku (vyšší skóre) na stejný tah prvního běhu, v náhodném
    pořadí X/Y. Vrátí (páry pro majitele bez názvů modelů, klíč)."""
    konf = _po_konfiguracich(radky)
    sonnet = next((k for k in konf if "sonnet" in k and k.endswith(":low")), None)
    haiku = sorted((k for k in konf if "haiku" in k), key=lambda k: -_skore(konf[k]))
    if not sonnet or not haiku:
        raise ValueError("slepé páry potřebují výsledky Sonnetu low a aspoň jedné konfigurace Haiku")
    a = {(r["id"], r["tah"]): r for r in konf[sonnet] if r["beh"] == 1 and r["odpoved"]}
    b = {(r["id"], r["tah"]): r for r in konf[haiku[0]] if r["beh"] == 1 and r["odpoved"]}
    rng = random.Random(seed)
    vyber = rng.sample(sorted(a.keys() & b.keys()), min(pocet, len(a.keys() & b.keys())))
    pary = ["# Slepé porovnání odpovědí", "",
            "U každého páru napiš, která odpověď je lepší: X, Y, nebo stejné.", ""]
    klic = ["| pár | scénář | tah | X | Y |", "|---|---|---|---|---|"]
    for n, k in enumerate(vyber, 1):
        x, y = (a[k], b[k]) if rng.random() < 0.5 else (b[k], a[k])
        pary += [f"## Pár {n} ({k[0]}, tah {k[1]})", ""]
        if x.get("znacka"):
            pary += [f"Stránka: `{x['znacka']}`", ""]
        pary += [f"**Otázka:** {x.get('otazka', '')}", "", "### X", "", _bez_modelu(x["odpoved"]), "",
                 "### Y", "", _bez_modelu(y["odpoved"]), ""]
        klic.append(f"| {n} | {k[0]} | {k[1]} | {_konf(x['model'], x['effort'])} | {_konf(y['model'], y['effort'])} |")
    return "\n".join(pary), "\n".join(klic) + "\n"


def _konfigurace(text: str) -> list[tuple[str, str]]:
    out = []
    for cast in text.split(","):
        model, _, effort = cast.strip().partition(":")
        if not model or not effort:
            raise SystemExit(f"konfigurace {cast!r} musí mít tvar model:effort")
        out.append((model, effort))
    return out


def main(argv: list[str] | None = None) -> int:
    _bez_api_klice()
    ap = argparse.ArgumentParser(prog="python -m eval.chat.f0", description=__doc__.splitlines()[0])
    ap.add_argument("--mcp-config", required=True, help="konfigurace MCP se serverem cosepece (mimo repo)")
    ap.add_argument("--vystup", required=True, type=Path, help="surové výstupy, stav.jsonl a souhrn.md")
    ap.add_argument("--konfigurace", default=KONFIGURACE, help="model:effort oddělené čárkou")
    ap.add_argument("--jen-sada", choices=SADY)
    ap.add_argument("--timeout", type=int, default=300, help="s na jeden tah")
    ap.add_argument("--pracovni", type=Path, help="pracovní složky běhů (výchozí %%TEMP%%/cosepece-f0)")
    ap.add_argument("--scenare", type=Path, default=SCENARE)
    a = ap.parse_args(argv)
    scenare = nacti_scenare(a.scenare)
    if a.jen_sada:
        scenare = [s for s in scenare if s["sada"] == a.jen_sada]
    try:
        radky = spust(scenare, _konfigurace(a.konfigurace), BEHU, a.vystup,
                      mcp_config=str(Path(a.mcp_config).resolve()), pracovni=a.pracovni, timeout_s=a.timeout)
    except LimitPredplatneho as e:
        print(f"Limit předplatného: zbývá {e.zbyva} tahů. Po obnovení limitu spusť znovu stejný příkaz, "
              f"hotové tahy ze {STAV} se přeskočí. ({e})", file=sys.stderr)
        return 3
    md = souhrn(radky)
    (a.vystup / "souhrn.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
