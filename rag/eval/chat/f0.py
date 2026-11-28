"""F0: srovnání modelů chatu přes `claude -p` na předplatném (spec 14.2, úloha 8).

Každý tah scénáře = jeden běh Claude Code CLI se systémovým promptem chatu (`prompt.SYSTEM`), bez vestavěných nástrojů
a jen s nástroji veřejného MCP `mcp__cosepece__*`. Další tah téže konverzace navazuje přes `--resume`. API klíč se
nepoužívá: s `ANTHROPIC_API_KEY` a dalšími proměnnými ze `ZAKAZANE_PROMENNE` běh odmítne začít a podproces je nedostane.

Cizí pokyny (CLAUDE.md, paměť, hooky uživatele) odřízne `--setting-sources ""` a pracovní složka mimo strom
s `CLAUDE.md` (výchozí `%TEMP%/cosepece-f0`); `--bare` se nepoužívá (čte jen `ANTHROPIC_API_KEY`).
Jedna pracovní složka na (konfigurace, scénář, běh), sdílená všemi tahy scénáře: `--resume` hledá relaci podle cwd.

Cena tahu = `usage` × ceník (`cenik.cena_usd`). Je to horní odhad: CLI přidává vlastní režii a nástroje MCP nejsou
oříznuté jako v chatu. Výstup `--output-format json` neuvádí volané nástroje; `nastroje_any` se ve F0 hodnotí jen
přibližně: tah s `nastroje_any` a jedním kolem (žádné volání nástroje) selže. Na začátku běhu sonda MCP ověří,
že server cosepece je připojený a model má právě NASTROJE_MCP.
Selhání `max_cena_usd` a `cituje` se do skóre nepočítají (formát [N] dává registr zdrojů serveru, F0 ho nemá);
souhrn je vypíše zvlášť i se způsobem citace ([N], jen URL, bez citace).

Spuštění (z `rag/`):
    python -m eval.chat.f0 --mcp-config <mcp.json> --vystup <složka> [--konfigurace model:effort,…] [--jen-sada X]
Po limitu předplatného skončí kódem 3, po odhlášení, výpadku MCP nebo 3 chybách běhu po sobě kódem 4. Další
spuštění stejného příkazu přeskočí hotové tahy ze `stav.jsonl` a zopakuje tahy s chybou běhu.
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
from hlidac_rag.chat.cenik import CENY, cena_usd

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

EFFORTY = ("low", "medium", "high", "xhigh", "max")
MAX_CHYB_PO_SOBE = 3  # tolik chyb běhu po sobě = systémová chyba (přepínač, výpadek API), běh se zastaví
SONDA_MODEL = "claude-haiku-5-5"  # sonda MCP na začátku běhu: na modelu nezáleží, nejlevnější pro kvótu
# proměnné, se kterými by CLI platilo API nebo šlo jinam než na předplatné; běh s nimi odmítne začít (i prázdnými)
ZAKAZANE_PROMENNE = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                     "CLAUDE_CODE_USE_VERTEX")

# druh chyby podle hlášky CLI. Limit úzce (jen hláška limitu předplatného, ne „context limit“ ani nápověda „Usage:“);
# login a mcp jako worker Reels (reels/…/generator/worker.py PAUSE_PATTERNS)
_DRUHY_CHYB: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("limit", (r"usage limit", r"limit reached", r"hit your (?:usage )?limit", r"\bresets? (?:at )?\d")),
    ("login", (r"\blog ?in", r"logged out", r"authenticat", r"\b401\b", r"credential", r"oauth")),
    ("mcp", (r"\bmcp\b",)),
)


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


class ZastaveniBehu(Exception):
    """Chyba, po které nemá smysl pokračovat: odhlášení, výpadek MCP, opakované chyby běhu. CLI vrátí kód 4."""


def druh_chyby(text: str) -> str | None:
    """„limit“, „login“, „mcp“ podle hlášky CLI, jinak None (jednotlivá chyba tahu)."""
    for druh, vzory in _DRUHY_CHYB:
        if any(re.search(v, text or "", re.IGNORECASE) for v in vzory):
            return druh
    return None


def _vyhod_podle_druhu(text: str, sid: str | None = None, *, status=None) -> None:
    druh = "limit" if status == 429 else druh_chyby(text)
    if druh == "limit":
        raise LimitPredplatneho(text[:300])
    if druh:
        raise ZastaveniBehu(f"{druh}: {text[:300]}")
    raise ChybaBehu(f"is_error: {text[:300]}", sid)


def _over_prostredi() -> None:
    nalezene = [p for p in ZAKAZANE_PROMENNE if p in os.environ]
    if nalezene:
        raise SystemExit(f"V prostředí je {', '.join(nalezene)}: F0 běží jen na předplatném, claude -p by platilo "
                         "API nebo šlo mimo předplatné. Odeber proměnné a spusť znovu.")


def _prostredi() -> dict[str, str]:
    """Prostředí podprocesu bez proměnných, se kterými by CLI platilo API (pojistka k _over_prostredi)."""
    return {k: v for k, v in os.environ.items() if k not in ZAKAZANE_PROMENNE}


def over_konfigurace(konfigurace: list[tuple[str, str]]) -> None:
    """Neznámý model nebo effort se má poznat před prvním voláním, ne po zaplaceném tahu."""
    for model, effort in konfigurace:
        if model not in CENY:
            raise ValueError(f"neznámý model {model!r} (ceník zná {sorted(CENY)})")
        if effort not in EFFORTY:
            raise ValueError(f"neznámý effort {effort!r} (povolené {EFFORTY})")


def binarka() -> str:
    """Spustitelný soubor CLI: `CLAUDE_BIN`, jinak `claude` z PATH (na Windows celá cesta k claude.exe)."""
    return os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"


def prikaz(model: str, effort: str, system: str, mcp_config: str, resume: str | None,
           resume_at: str | None = None, *, format_vystupu: str = "json") -> list[str]:
    """Argumenty jednoho běhu; prompt tahu jde na stdin. `--allowedTools` je poslední, nástroj = prvek seznamu.

    `resume_at` = uuid poslední odpovědi asistenta v přepisu relace (`--resume-session-at`): CLI zapíše otázku do
    přepisu dřív, než přijde odpověď, takže po přerušení (limit, timeout) by holé `--resume` poslalo otázku podruhé
    (ověřeno na CLI 2.1.332 28. 11.). S `--resume-session-at` relace navazuje za poslední dokončenou odpovědí."""
    p = [binarka(), "-p", "--model", model, "--effort", effort, "--output-format", format_vystupu,
         *(["--verbose"] if format_vystupu == "stream-json" else []),
         "--system-prompt", system, "--restricted", "--tools", "",
         "--strict-mcp-config", "--mcp-config", str(mcp_config),
         "--permission-mode", "dontAsk", "--setting-sources", ""]
    if resume:
        p += ["--resume", resume]
        if resume_at:
            p += ["--resume-session-at", resume_at]
    return p + ["--disallowedTools", *NASTROJE_ZAKAZANE, "--allowedTools", *NASTROJE_MCP]


def posledni_odpoved(session_id: str) -> str | None:
    """uuid poslední odpovědi asistenta v přepisu relace (`~/.claude/projects/<cwd>/<session_id>.jsonl`);
    None, když přepis chybí. Syntetické chybové zprávy CLI (`isApiErrorMessage`) se přeskočí."""
    if not session_id or not re.fullmatch(r"[0-9a-fA-F-]{36}", session_id):
        return None
    koren = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    uuid = None
    for prepis in koren.glob(f"*/{session_id}.jsonl"):
        for radek in prepis.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                d = json.loads(radek)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("type") == "assistant" and not d.get("isApiErrorMessage") and d.get("uuid"):
                uuid = d["uuid"]
    return uuid


def rozeber(stdout: str, model: str) -> dict:
    """Výstup `--output-format json` → odpověď, tokeny, kola, doba a cena tahu. Limit → `LimitPredplatneho`,
    odhlášení nebo chyba MCP → `ZastaveniBehu`, jiná chyba → `ChybaBehu`. Seznam událostí (starší CLI) se čte
    od konce jako ve workeru Reels."""
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
        _vyhod_podle_druhu(str(data.get("result") or data.get("subtype") or ""), sid,
                           status=data.get("api_error_status"))
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


def _selhal_beh(r: dict) -> bool:
    return any(x.startswith("beh:") for x in r.get("selhani") or [])


def _nacti_stav(cesta: Path) -> dict[tuple, dict]:
    """Hotové tahy ze `stav.jsonl`. Řádky s chybou běhu (`beh: …`) nejsou hotové, další spuštění je zopakuje.
    Nečitelný poslední řádek (přerušený zápis) se zahodí i ze souboru, nečitelný řádek uprostřed = ValueError."""
    if not cesta.exists():
        return {}
    radky = [r for r in cesta.read_text(encoding="utf-8").splitlines() if r.strip()]
    nactene = []
    for n, r in enumerate(radky, 1):
        try:
            nactene.append(json.loads(r))
        except ValueError:
            if n != len(radky):
                raise ValueError(f"{cesta}: nečitelný řádek {n}") from None
            cesta.write_text("".join(x + "\n" for x in radky[:-1]), encoding="utf-8")
    hotove: dict[tuple, dict] = {}
    for r in nactene:  # pozdější řádek téhož tahu (opakování po chybě) platí
        if _selhal_beh(r):
            hotove.pop(_klic(r), None)
        else:
            hotove[_klic(r)] = r
    return hotove


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


def over_mcp(spust_claude, mcp_config: str, cwd: Path, timeout_s: int = 120) -> None:
    """Sonda na začátku běhu: v události `init` (`stream-json`) musí být server cosepece `connected` a nástroje
    přesně NASTROJE_MCP. Bez toho by CLI běželo bez nástrojů (`is_error: false`) a model odpovídal zpaměti."""
    cwd.mkdir(parents=True, exist_ok=True)
    argv = prikaz(SONDA_MODEL, "low", "Jsi test.", mcp_config, None, format_vystupu="stream-json")
    try:
        proc = spust_claude(argv, input="Odpověz jen OK.", capture_output=True, text=True, encoding="utf-8",
                            errors="replace", cwd=str(cwd), timeout=timeout_s, env=_prostredi())
    except subprocess.TimeoutExpired:
        raise ZastaveniBehu(f"mcp: sonda MCP nedoběhla do {timeout_s} s") from None
    init, vysledek = None, None
    for radek in (proc.stdout or "").splitlines():
        try:
            d = json.loads(radek)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("type") == "system" and d.get("subtype") == "init":
            init = d
        elif isinstance(d, dict) and d.get("type") == "result":
            vysledek = d
    try:  # limit zůstane limitem (kód 3), jiná chyba sondy běh zastaví
        if vysledek and vysledek.get("is_error") is True:
            _vyhod_podle_druhu(str(vysledek.get("result") or ""), status=vysledek.get("api_error_status"))
        if init is None and proc.returncode:
            _vyhod_podle_druhu((proc.stderr or "").strip() or f"návratový kód {proc.returncode}")
    except ChybaBehu as e:
        raise ZastaveniBehu(f"sonda MCP selhala: {e}") from None
    if init is None:
        raise ZastaveniBehu("mcp: sonda MCP nevrátila událost init")
    stav = {s.get("name"): s.get("status") for s in init.get("mcp_servers") or [] if isinstance(s, dict)}
    if stav.get("cosepece") != "connected":
        raise ZastaveniBehu(f"mcp: server cosepece není připojený (stav {stav.get('cosepece')!r}); "
                            "zkontroluj token v konfiguraci MCP a dostupnost serveru")
    nastroje = set(init.get("tools") or [])
    if nastroje != set(NASTROJE_MCP):
        raise ZastaveniBehu(f"mcp: model by měl jiné nástroje než NASTROJE_MCP (navíc "
                            f"{sorted(nastroje - set(NASTROJE_MCP))}, chybí {sorted(set(NASTROJE_MCP) - nastroje)})")


def _vyhodnot(proc, model: str) -> dict:
    """Výstup jednoho běhu; nečitelný stdout s nenulovým kódem → druh chyby podle stderr."""
    try:
        json.loads((proc.stdout or "").strip())
    except ValueError:
        if proc.returncode:
            _vyhod_podle_druhu((proc.stderr or "").strip() or f"návratový kód {proc.returncode}")
    return rozeber(proc.stdout, model)


def _radek_chyby(radek: dict, chyba: str, session_id: str = "") -> dict:
    radek.update({"odpoved": "", "session_id": session_id, "uuid_odpovedi": None, "vstup": 0, "vystup": 0,
                  "cache_cteni": 0, "cache_zapis": 0, "kol": 0, "ms": 0, "cena_usd": 0.0,
                  "selhani": [f"beh: {chyba}"]})
    return radek


def _zapis(stav_cesta: Path, hotove: dict, klic: tuple, radek: dict) -> None:
    with stav_cesta.open("a", encoding="utf-8") as f:
        f.write(json.dumps(radek, ensure_ascii=False) + "\n")
    hotove[klic] = radek


def spust(scenare, konfigurace: list[tuple[str, str]], behu: dict[str, int], vystup: Path,
          spust_claude=subprocess.run, *, mcp_config: str = "", pracovni: Path | None = None,
          system: str = prompt.SYSTEM, pauza_s: float = PAUZA_S, timeout_s: int = 300,
          najdi_odpoved=posledni_odpoved, sonda_mcp: bool = True) -> list[dict]:
    """Projde scénáře pro každou konfiguraci; řádek na tah do `vystup/stav.jsonl` a vrátí všechny řádky plánu.

    Tahy hotové ve `stav.jsonl` přeskočí, tahy s chybou běhu zopakuje. Tah navazuje přes `--resume` a
    `--resume-session-at` na poslední odpověď předchozího tahu; když předchozí tah selhal, zbylé tahy scénáře se
    nespustí (řádek `beh: navazuje na selhaný tah`). Limit předplatného → `LimitPredplatneho` se `zbyva`;
    odhlášení, výpadek MCP nebo MAX_CHYB_PO_SOBE chyb po sobě → `ZastaveniBehu`."""
    over_konfigurace(konfigurace)
    vystup = Path(vystup)
    pracovni = Path(pracovni) if pracovni else Path(tempfile.gettempdir()) / "cosepece-f0"
    pracovni = pracovni.resolve()
    _over_pracovni(pracovni)
    vystup.mkdir(parents=True, exist_ok=True)
    stav_cesta = vystup / STAV
    hotove = _nacti_stav(stav_cesta)
    plan = _plan(scenare, konfigurace, behu)
    klice = [(p[0]["id"], *p[1:5]) for p in plan]
    if sonda_mcp and any(k not in hotove for k in klice):
        _over_prostredi()
        over_mcp(spust_claude, mcp_config, pracovni / "sonda-mcp")
    prvni, chyb_po_sobe = True, 0
    for poradi, (s, model, effort, beh, i, tah, kontext, predchozi) in enumerate(plan):
        klic = klice[poradi]
        if klic in hotove and not _selhal_beh(hotove[klic]):
            continue
        z = znacka(kontext, predchozi)
        radek = {"id": s["id"], "sada": s["sada"], "model": model, "effort": effort, "beh": beh, "tah": i,
                 "otazka": tah["text"], "znacka": z, "cituje_ocekavano": tah.get("cituje")}
        resume = resume_at = None
        if i > 1:
            pred = hotove.get((s["id"], model, effort, beh, i - 1)) or {}
            resume, resume_at = pred.get("session_id"), pred.get("uuid_odpovedi")
            if _selhal_beh(pred) or not resume or not resume_at:
                # bez navázání by tah běžel jako nová konverzace bez kontextu a hodnotil se, jako by navazoval
                _zapis(stav_cesta, hotove, klic, _radek_chyby(radek, "navazuje na selhaný tah"))
                continue
        _over_prostredi()
        cwd = pracovni / _nazev(f"{model}-{effort}") / _nazev(f"{s['id']}-b{beh}")
        cwd.mkdir(parents=True, exist_ok=True)
        if not prvni:
            time.sleep(pauza_s)
        prvni = False
        surove = vystup / "surove" / _nazev(f"{model}-{effort}")
        surove.mkdir(parents=True, exist_ok=True)
        try:
            proc = spust_claude(prikaz(model, effort, system, mcp_config, resume, resume_at),
                                input=f"{z}\n\n{tah['text']}" if z else tah["text"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", cwd=str(cwd), timeout=timeout_s, env=_prostredi())
            jmeno = _nazev(f"{s['id']}-b{beh}-t{i}")
            (surove / f"{jmeno}.json").write_text(proc.stdout or "", encoding="utf-8")
            if proc.stderr:
                (surove / f"{jmeno}.err").write_text(proc.stderr, encoding="utf-8")
            v = _vyhodnot(proc, model)
            radek.update(v)
            radek["uuid_odpovedi"] = najdi_odpoved(v["session_id"])
            selhani = zkontroluj(tah, v["odpoved"], None, v["cena_usd"]).selhani
            if tah.get("nastroje_any") and v["kol"] <= 1:
                selhani.append("nastroje_any")  # jedno kolo = žádné volání nástroje (výpadek MCP, odpověď zpaměti)
            radek["selhani"] = selhani
            chyb_po_sobe = 0
        except LimitPredplatneho as e:
            zbyva = sum(1 for k in klice[poradi:] if k not in hotove or _selhal_beh(hotove[k]))
            raise LimitPredplatneho(str(e), zbyva) from None
        except (ChybaBehu, subprocess.TimeoutExpired) as e:
            chyba = f"timeout {timeout_s} s" if isinstance(e, subprocess.TimeoutExpired) else str(e)
            _radek_chyby(radek, chyba, getattr(e, "session_id", None) or "")
            chyb_po_sobe += 1
        _zapis(stav_cesta, hotove, klic, radek)
        if chyb_po_sobe >= MAX_CHYB_PO_SOBE:
            raise ZastaveniBehu(f"{chyb_po_sobe} chyby běhu po sobě, poslední: {radek['selhani'][0]}")
    return [hotove[k] for k in klice]


# selhání, která se ve F0 do skóre nepočítají a souhrn je vypíše zvlášť: cena F0 je horní odhad (F12), formát [N]
# vzniká z registru zdrojů na serveru, který F0 nemá
MIMO_SKORE = ("max_cena_usd", "cituje")
_ODKAZ = re.compile(r"\[\d+\]")  # stejný vzor jako kontrola cituje v kontroly.py
_URL = re.compile(r"https?://")


def _skore(radky: list[dict]) -> float:
    """Podíl tahů bez selhání kontrol; selhání z MIMO_SKORE se nepočítají."""
    if not radky:
        return 0.0
    return 100 * sum(1 for r in radky if not [x for x in r["selhani"] if x not in MIMO_SKORE]) / len(radky)


def _zpusob_citace(odpoved: str) -> str:
    """„[N]“, „URL“ (jen adresy, bez [N]), nebo „bez“ (necituje vůbec)."""
    if _ODKAZ.search(odpoved or ""):
        return "[N]"
    return "URL" if _URL.search(odpoved or "") else "bez"


def _po_konfiguracich(radky: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in radky:
        out.setdefault(_konf(r["model"], r["effort"]), []).append(r)
    return out


def souhrn(radky: list[dict]) -> str:
    """Markdown: řádek na konfiguraci se skóre celkem a po sadách, selháními must_not (spec 14.2 bod 1),
    selháními mimo skóre zvlášť (max_cena_usd, cituje), způsobem citace u tahů s cituje: true ([N], jen URL, bez
    citace), chybami běhu a mediány ceny, kol a doby tahu."""
    sady = [s for s in SADY if any(r["sada"] == s for r in radky)]
    hlavicka = ["konfigurace", "tahů", "skóre", *sady, "must_not", "max_cena_usd", "cituje", "citace [N]",
                "citace URL", "bez citace", "chyby běhu", "medián ceny", "medián kol", "medián ms"]
    md = ["| " + " | ".join(hlavicka) + " |", "|" + "---|" * len(hlavicka)]
    for konf, rr in _po_konfiguracich(radky).items():
        ok = [r for r in rr if not any(x.startswith("beh:") for x in r["selhani"])]
        bunky = [konf, str(len(rr)), f"{_skore(rr):.1f} %"]
        bunky += [f"{_skore([r for r in rr if r['sada'] == s]):.1f} %" for s in sady]
        bunky += [str(sum(1 for r in rr if any(x.startswith("must_not") for x in r["selhani"]))),
                  str(sum(1 for r in rr if "max_cena_usd" in r["selhani"])),
                  str(sum(1 for r in rr if "cituje" in r["selhani"]))]
        # způsob citace u tahů, které citovat mají (cituje: true); necitování je údaj o kvalitě, URL místo [N] ne
        zpusoby = [_zpusob_citace(r["odpoved"]) for r in ok if r.get("cituje_ocekavano") is True]
        bunky += [str(zpusoby.count(z)) for z in ("[N]", "URL", "bez")]
        bunky.append(str(len(rr) - len(ok)))
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
    try:
        over_konfigurace(out)
    except ValueError as e:
        raise SystemExit(f"konfigurace: {e}") from None
    return out


def main(argv: list[str] | None = None) -> int:
    _over_prostredi()
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
    konfigurace = _konfigurace(a.konfigurace)
    try:
        radky = spust(scenare, konfigurace, BEHU, a.vystup,
                      mcp_config=str(Path(a.mcp_config).resolve()), pracovni=a.pracovni, timeout_s=a.timeout)
    except LimitPredplatneho as e:
        print(f"Limit předplatného: zbývá {e.zbyva} tahů. Po obnovení limitu spusť znovu stejný příkaz, "
              f"hotové tahy ze {STAV} se přeskočí. ({e})", file=sys.stderr)
        return 3
    except ZastaveniBehu as e:
        print(f"Běh zastaven: {e}. Oprav příčinu a spusť znovu stejný příkaz; tahy s chybou běhu se zopakují.",
              file=sys.stderr)
        return 4
    md = souhrn(radky)
    (a.vystup / "souhrn.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
