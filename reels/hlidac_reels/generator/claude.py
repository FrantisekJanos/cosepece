"""Spuštění `claude -p` (Claude Code CLI na předplatném) se zúženými nástroji a prompty generátoru."""
from __future__ import annotations

import json
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
ALLOWED_TOOLS = ["mcp__cosepece__*", "Read", "Write", "Edit", "Agent"]  # Edit: oprava variant (D1)
DISALLOWED_TOOLS = ["Bash", "PowerShell", "WebFetch", "WebSearch"]
V3_FORMATS = ("cesta", "pn", "zmena", "rozprava")
PRIBEH = "pribeh"  # formats.PRIBEH (claude.py formats neimportuje, formats je bez závislostí)


@dataclass
class ClaudeRun:
    ok: bool
    returncode: int | None
    timed_out: bool
    log_path: Path
    kind: str = "ok"  # "ok", "env" (rc ≠ 0 nebo is_error: limit, přihlášení…), "timeout"
    result: str = ""  # prvních RESULT_MAX znaků pole result z JSON výstupu


class EnvError(Exception):
    """Běh claude -p selhal kvůli prostředí (limit, přihlášení…) a nic nezapsal; oprava nemá smysl."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


RESULT_MAX = 500


def parse_output(out: str) -> tuple[bool, str]:
    """Z výstupu `--output-format json` vrátí (is_error, result[:RESULT_MAX]); nečitelný výstup = (False, "").

    Výstup je jeden objekt; seznam událostí (starší verze CLI) se čte od konce po prvním objektu s is_error.
    """
    try:
        data = json.loads((out or "").strip())
    except ValueError:
        return False, ""
    if isinstance(data, list):
        data = next((d for d in reversed(data) if isinstance(d, dict) and "is_error" in d), {})
    if not isinstance(data, dict):
        return False, ""
    return data.get("is_error") is True, str(data.get("result") or "")[:RESULT_MAX]


def _ct_of(path: Path) -> int:
    return int(re.search(r"-(\d+)(?:-[a-z]+)?\.(?:json|txt)$", path.name).group(1))


def _pick(candidates: list[Path], ct: int) -> Path:
    """Vzor z jiného tisku než zadání (vzor ze stejného tisku model opíše); jinak ten jediný."""
    other = [c for c in sorted(candidates) if _ct_of(c) != ct]
    return (other or sorted(candidates))[0]


def prepare_workdir(workdir: Path, ct: int) -> None:
    """Zkopíruje návody, pravidla a vzory do `workdir/podklady/` (model čte jen pracovní složku).

    Vzory karet a popisku vybere z jiného tisku než `ct` a uloží pod obecnými jmény (vzor-v2.json,
    vzor-v3-<formát>.json, vzor-pribeh.json, vzor-popisek.txt).
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
    # příběh jednoho PN (šablona template_pn_v1): návod a vzor karty
    shutil.copy2(HERE / "prompts" / f"{PRIBEH}.md", dest / f"{PRIBEH}.md")
    shutil.copy2(_pick(list(src.glob(f"{PRIBEH}-*.json")), ct), dest / f"vzor-{PRIBEH}.json")


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


def _pn_day(day) -> str:
    """„2026-03-15“ → „15. 3. 2026“; jiný tvar beze změny."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(day or ""))
    return f"{int(m[3])}. {int(m[2])}. {m[1]}" if m else str(day or "")


def _author_line(i: dict) -> str:
    """Řádek autora PN v zadání. Klub ke dni podání posílá server (pn.klub); bez údaje se klub neuvádí.

    Starší server klíče `klub` a `klub_ke_dni` neposílá, chybějící klíč platí jako null.
    """
    name = i.get("osoba")
    if not name:
        return "- autor: zjisti přes get_pn (klub neuváděj)"
    club = i.get("klub")
    if not club:
        return f"- autor: {name} (klub v datech Sněmovny ke dni podání není, klub neuváděj)"
    day = _pn_day(i.get("klub_ke_dni"))
    return f"- autor: {name} (klub {club} ke dni podání{' ' + day if day else ''})"


# stavy pn_vysledek (app/PnOutcome.php) slovy pro zadání modelu
OUTCOME_WORDS = {"prijat": "přijat", "castecne": "přijat zčásti", "zamitnut": "zamítnut",
                 "ceka": "čeká na hlasování ve 3. čtení", "nehlasovatelny": "nehlasovalo se o něm (nehlasovatelný)",
                 "nezaznel": "nezazněl ve 2. čtení", "neurceno": "výsledek nelze určit"}


def _outcome_lines(vysledek) -> list[str]:
    """Řádky zadání o výsledku PN ve 3. čtení ze serveru (claim focus_pn_info.vysledek)."""
    if not isinstance(vysledek, dict) or not vysledek.get("stav"):
        return ["- výsledek hlasování: zatím není (server ho neuvádí); verdikt přijat ani zamítnut nepiš"]
    stav = str(vysledek["stav"])
    lines = [f"- výsledek hlasování: {OUTCOME_WORDS.get(stav, stav)} (stav `{stav}`, ze serveru; "
             "do karty ho doplní systém, nepřepisuj ho)"]
    if vysledek.get("stanovisko"):
        lines.append(f"- stanovisko výboru: {vysledek['stanovisko']}")
    for h in vysledek.get("hlasovani") or []:
        if not isinstance(h, dict):
            continue
        what = f" o návrhu {h['pismeno']}" if h.get("pismeno") else ""
        lines.append(f"- hlasování č. {h.get('cislo')}{what} ({_pn_day(h.get('datum'))}): {h.get('vysledek')}, "
                     f"pro {h.get('pro')}, proti {h.get('proti')}; {h.get('url_psp')}")
    return lines


def write_focus(workdir: Path, focus_pn: list[int] | None, info: list[dict], outcome: bool = False) -> None:
    """Zapíše `podklady/zadani.md` pro video k vybraným PN (/pn); bez focus_pn soubor smaže.

    Návody orchestrátoru, v2, v3 a pribeh soubor čtou: hlavní fakt, titulek a háček všech variant jsou o těchto
    PN. outcome = příběh jednoho PN: zadání uvede i výsledek hlasování ze serveru.
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
        parts.append(_author_line(i))
        if i.get("cas"):
            parts.append(f"- podáno: {_pn_time(i['cas'])}")
        if i.get("titulek"):
            parts.append(f"- titulek: {i['titulek']} (shrnutí AI)")
        parts.append(f"- url_psp: {i.get('url_psp') or PSP_PN_URL.format(cislo=n)}")
        if outcome:
            parts += _outcome_lines(i.get("vysledek"))
        parts.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")


def _task_line(n: int, fmt: str | None) -> str:
    if fmt == PRIBEH:
        return f"- varianta {n}: „Přečti podklady/{PRIBEH}.md a postupuj podle něj. Varianta N = {n}. Výstup do variant-{n}/.“"
    if fmt is None:
        return f"- varianta {n}: „Přečti podklady/v2.md a postupuj podle něj. Výstup do variant-{n}/.“"
    return f"- varianta {n}: „Přečti podklady/v3.md a postupuj podle něj. Varianta N = {n}, formát F = {fmt}. Výstup do variant-{n}/.“"


def build_prompt(*, ct: int, formats: list[str], workdir: Path, repair: dict[int, list[str]] | None = None) -> str:
    """Prompt orchestrátoru. Varianta 1 = v2, varianty 2, 3… = v3 ve formátech `formats`; `repair` = jen opravy.

    formats = ["pribeh"]: jediná varianta 1, příběh jednoho PN (šablona template_pn_v1).
    """
    plan: dict[int, str | None]
    if formats == [PRIBEH]:
        plan = {1: PRIBEH}
    else:
        plan = {1: None, **{i: f for i, f in enumerate(formats, 2)}}
    numbers = sorted(repair) if repair else sorted(plan)
    tasks = "\n".join(_task_line(n, plan[n]) for n in numbers)
    fix = ""
    if repair:
        lines = [f"- variant-{n}/: " + "; ".join(errs) for n, errs in sorted(repair.items())]
        fix = ("\nOPRAVA: kontrola našla v těchto variantách chyby. Subagent má přečíst svůj návod, "
               "opravit soubory ve své složce a chyby odstranit. Soubory card.json a caption.txt oprav "
               "nástrojem Edit, nebo je přepiš celé nástrojem Write. Chyby:\n" + "\n".join(lines) + "\n")
    text = (HERE / "prompts" / "orchestrator.md").read_text(encoding="utf-8")
    text = text.replace("{ct}", str(ct)).replace("{tasks}", tasks).replace("{repair}", fix)
    if repair or len(numbers) != 3:
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


LOG_KEEP = 5  # kolik logů minulých vydání (claude-<n>.log) zůstane ve složce úlohy


def rotate_log(workdir: Path, keep: int = LOG_KEEP) -> None:
    """Přesune `claude.log` minulého vydání do `claude-<n>.log` (n = nejvyšší existující + 1).

    Ze starých logů nechá `keep` s nejvyšším n. Volá se na začátku vydání, takže každé vydání píše do nového
    `claude.log`. Chyba souborového systému (log otevřený jiným programem) generování nezastaví.
    """
    workdir = Path(workdir)
    old: dict[int, Path] = {}
    for path in workdir.glob("claude-*.log"):
        m = re.fullmatch(r"claude-(\d+)\.log", path.name)
        if m:
            old[int(m[1])] = path
    log = workdir / "claude.log"
    if log.is_file():
        n = max(old, default=0) + 1
        try:
            log.replace(workdir / f"claude-{n}.log")
            old[n] = workdir / f"claude-{n}.log"
        except OSError:
            pass
    for n in sorted(old)[:max(len(old) - keep, 0)]:
        try:
            old[n].unlink()
        except OSError:
            pass


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
    with log_path.open("a", encoding="utf-8") as log:  # běhy jednoho vydání (generování, opravy) do jednoho logu
        log.write(f"=== běh ===\n{out or ''}\n--- stderr ---\n{err or ''}\n" + ("--- timeout ---\n" if timed_out else ""))
    rc = None if timed_out else proc.returncode
    is_error, result = parse_output(out)
    # druh běhu jen z návratového kódu a is_error; text hlášky se mezi verzemi CLI mění
    kind = "timeout" if timed_out else ("env" if rc != 0 or is_error else "ok")
    return ClaudeRun(ok=kind == "ok", returncode=rc, timed_out=timed_out, log_path=log_path,
                     kind=kind, result=result)
