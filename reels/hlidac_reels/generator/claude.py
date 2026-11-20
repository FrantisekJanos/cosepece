"""Spuštění `claude -p` (Claude Code CLI na předplatném) se zúženými nástroji a prompty generátoru."""
from __future__ import annotations

import os
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


def prepare_workdir(workdir: Path) -> None:
    """Zkopíruje návody, pravidla a vzory do `workdir/podklady/` (model čte jen pracovní složku)."""
    dest = Path(workdir) / "podklady"
    dest.mkdir(parents=True, exist_ok=True)
    for src in (HERE / "examples").iterdir():
        shutil.copy2(src, dest / src.name)
    for name in ("v2.md", "v3.md"):
        shutil.copy2(HERE / "prompts" / name, dest / name)


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
    log_path.write_text(f"{out or ''}\n--- stderr ---\n{err or ''}\n" + ("--- timeout ---\n" if timed_out else ""),
                        encoding="utf-8")
    rc = None if timed_out else proc.returncode
    return ClaudeRun(ok=not timed_out and rc == 0, returncode=rc, timed_out=timed_out, log_path=log_path)
