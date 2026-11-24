"""Spuštění `claude -p` (Claude Code CLI na předplatném) se zúženými nástroji a prompty generátoru."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_MODEL = "claude-sonnet-5-5"
# model čte cizí texty z psp.cz: žádné spouštění příkazů ani web, soubory jen v pracovní složce (--restricted)
ALLOWED_TOOLS = ["mcp__cosepece__*", "Read", "Write", "Agent"]
DISALLOWED_TOOLS = ["Bash", "PowerShell", "WebFetch", "WebSearch"]
V3_FORMATS = ("cesta", "pn", "zmena", "rozprava")


@dataclass
class ClaudeRun:
    ok: bool
    returncode: int | None
    timed_out: bool
    log_path: Path


def _ct_of(path: Path) -> int:
    return int(re.search(r"-(\d+)(?:-[a-z]+)?\.(?:json|txt)$", path.name).group(1))


def _pick(candidates: list[Path], ct: int) -> Path:
    """Vzor z jiného tisku než zadání (vzor ze stejného tisku model opíše); jinak ten jediný."""
    other = [c for c in sorted(candidates) if _ct_of(c) != ct]
    return (other or sorted(candidates))[0]


def prepare_workdir(workdir: Path, ct: int) -> None:
    """Zkopíruje návody, pravidla a vzory do `workdir/podklady/` (model čte jen pracovní složku).

    Vzory karet a popisku vybere z jiného tisku než `ct` a uloží pod obecnými jmény (vzor-v2.json,
    vzor-v3-<formát>.json, vzor-popisek.txt).
    """
    src, dest = HERE / "examples", Path(workdir) / "podklady"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("pravidla.md", "formaty.md", "lhuty.md"):
        shutil.copy2(src / name, dest / name)
    for name in ("v2.md", "v3.md"):
        shutil.copy2(HERE / "prompts" / name, dest / name)
    shutil.copy2(_pick(list(src.glob("card-v2-*.json")), ct), dest / "vzor-v2.json")
    shutil.copy2(_pick(list(src.glob("caption-*.txt")), ct), dest / "vzor-popisek.txt")
    for fmt in V3_FORMATS:
        shutil.copy2(_pick(list(src.glob(f"v3-{fmt}-*.json")), ct), dest / f"vzor-v3-{fmt}.json")


def write_published(workdir: Path, published: list[dict]) -> None:
    """Zapíše `podklady/publikovano.md` s popisky videí, která k tisku už vyšla (bez nich soubor smaže).

    Návody v2 a v3 soubor čtou: nová varianta nemá opakovat hlavní fakt a úhel už vydaných videí.
    """
    path = Path(workdir) / "podklady" / "publikovano.md"
    items = [p for p in published if isinstance(p, dict) and str(p.get("caption") or "").strip()]
    if not items:
        path.unlink(missing_ok=True)
        return
    parts = ["# Videa k tomuto tisku, která už vyšla",
             "Popisky jsou od nejnovějšího. Jejich hlavní fakt a úhel už diváci viděli, neopakuj je.", ""]
    for n, p in enumerate(items, start=1):
        fmt = p.get("format") or "v2"
        when = str(p.get("published_at") or "")[:10]
        parts += [f"## Video {n} (formát {fmt}{', ' + when if when else ''})", str(p["caption"]).strip(), ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")


PSP_PN_URL = "https://www.psp.cz/sqw/sd.sqw?cd={cislo}&o=10"  # záloha, když server odkaz nepošle (10. období)


def _pn_time(cas) -> str:
    """„2026-11-23 20:09:00“ → „23. 11. 2026 20:09“; jiný tvar beze změny."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2})", str(cas or ""))
    return f"{int(m[3])}. {int(m[2])}. {m[1]} {m[4]}" if m else str(cas or "")


def write_focus(workdir: Path, focus_pn: list[int] | None, info: list[dict]) -> None:
    """Zapíše `podklady/zadani.md` pro video k vybraným PN (/pn); bez focus_pn soubor smaže.

    Návody orchestrátoru, v2 a v3 soubor čtou: hlavní fakt, titulek a háček všech variant jsou o těchto PN.
    """
    path = Path(workdir) / "podklady" / "zadani.md"
    if not focus_pn:
        path.unlink(missing_ok=True)
        return
    by_num = {int(i["cislo"]): i for i in info if isinstance(i, dict) and str(i.get("cislo", "")).isdigit()}
    which = ", ".join(f"PN {n}" for n in focus_pn)
    parts = ["# Zadání: video k vybraným pozměňovacím návrhům",
             f"Video je jen o těchto PN: {which}. Hlavní fakt, titulek i háček jsou o nich, tisk je jen kontext.",
             "Data o PN zjisti přes MCP: `get_pn(cislo)`, `osnova_textu(pn=…)`, `get_cel(pn=…)`, citace ověř přes "
             "`over_citaci` s `pn`. Titulek níže je shrnutí AI, ne fakt.", ""]
    for n in focus_pn:
        i = by_num.get(n, {})
        parts.append(f"## PN {n}")
        parts.append(f"- autor: {i.get('osoba') or 'zjisti přes get_pn'}")
        if i.get("cas"):
            parts.append(f"- podáno: {_pn_time(i['cas'])}")
        if i.get("titulek"):
            parts.append(f"- titulek: {i['titulek']} (shrnutí AI)")
        parts.append(f"- url_psp: {i.get('url_psp') or PSP_PN_URL.format(cislo=n)}")
        parts.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")


def _task_line(n: int, fmt: str | None) -> str:
    if fmt is None:
        return f"- varianta {n}: „Přečti podklady/v2.md a postupuj podle něj. Výstup do variant-{n}/.“"
    return f"- varianta {n}: „Přečti podklady/v3.md a postupuj podle něj. Varianta N = {n}, formát F = {fmt}. Výstup do variant-{n}/.“"


def build_prompt(*, ct: int, formats: list[str], workdir: Path, repair: dict[int, list[str]] | None = None) -> str:
    """Prompt orchestrátoru. Varianta 1 = v2, varianty 2 a 3 = v3 ve formátech `formats`; `repair` = jen opravy."""
    plan = {1: None, 2: formats[0], 3: formats[1]}
    numbers = sorted(repair) if repair else [1, 2, 3]
    tasks = "\n".join(_task_line(n, plan[n]) for n in numbers)
    fix = ""
    if repair:
        lines = [f"- variant-{n}/: " + "; ".join(errs) for n, errs in sorted(repair.items())]
        fix = ("\nOPRAVA: kontrola našla v těchto variantách chyby. Subagent má přečíst svůj návod, "
               "opravit soubory ve své složce a chyby odstranit:\n" + "\n".join(lines) + "\n")
    text = (HERE / "prompts" / "orchestrator.md").read_text(encoding="utf-8")
    text = text.replace("{ct}", str(ct)).replace("{tasks}", tasks).replace("{repair}", fix)
    if repair:
        text = text.replace("tři subagenty", f"tolik subagentů, kolik je níže variant ({len(numbers)})")
    return text


def _bin() -> list[str]:
    raw = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"
    return raw.split() if os.environ.get("CLAUDE_BIN") else [raw]


def claude_argv(*, workdir: Path, model: str, mcp_config: Path) -> list[str]:
    """Argumenty `claude -p`; prompt jde na stdin (Windows omezuje délku příkazové řádky)."""
    return [*_bin(), "-p", "--model", model, "--output-format", "json", "--restricted",
            "--permission-mode", "dontAsk", "--strict-mcp-config", "--mcp-config", str(mcp_config),
            "--add-dir", str(workdir),
            "--allowedTools", ",".join(ALLOWED_TOOLS), "--disallowedTools", ",".join(DISALLOWED_TOOLS)]


def _kill_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


def run_claude(prompt: str, *, workdir: Path, mcp_config: Path, model: str = DEFAULT_MODEL,
               timeout_s: int = 1200) -> ClaudeRun:
    """Spustí `claude -p` v `workdir`; výstup a chyby do `workdir/claude.log`. Po timeoutu proces ukončí."""
    workdir = Path(workdir).resolve()  # claude běží s cwd=workdir, relativní cesty by se rozbily
    mcp_config = Path(mcp_config).resolve()
    log_path = workdir / "claude.log"
    # model smí do workdir zapisovat; projektová nastavení ani CLAUDE.md z minulého běhu nesmí platit
    shutil.rmtree(workdir / ".claude", ignore_errors=True)
    (workdir / "CLAUDE.md").unlink(missing_ok=True)
    proc = subprocess.Popen(claude_argv(workdir=workdir, model=model, mcp_config=mcp_config), cwd=workdir,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            encoding="utf-8", errors="replace")
    timed_out = False
    try:
        out, err = proc.communicate(prompt, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        out, err = proc.communicate()
    with log_path.open("a", encoding="utf-8") as log:  # oprava variant běží podruhé, první log zůstane
        log.write(f"=== běh ===\n{out or ''}\n--- stderr ---\n{err or ''}\n" + ("--- timeout ---\n" if timed_out else ""))
    rc = None if timed_out else proc.returncode
    return ClaudeRun(ok=not timed_out and rc == 0, returncode=rc, timed_out=timed_out, log_path=log_path)
