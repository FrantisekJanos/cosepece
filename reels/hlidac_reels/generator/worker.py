"""Příkaz reels-worker: worker na PC vyzvedává úlohy z reels_api.php a generuje k nim sady variant.

Smyčka: heartbeat → claim → generate() z části 1 (claude -p na předplatném + render) → nahrání platných videí →
result. Video na šířku 16:9 se nahraje před videem na výšku: server, který šířku ještě neumí (parametr orientation
ignoruje), ho tak přepíše videem na výšku a sada projde bez šířky. Chyba šířky variantu nevyřadí. Bez úlohy nebo při chybě sítě čeká 5 minut. Vždy jen jedna úloha a jedna instance (zámek root/worker.lock),
naplánovaná úloha při přihlášení a ruční spuštění se tak nepotkají.

Chyba prostředí (EnvError: vyčerpaný limit předplatného, odhlášení) úlohu nahlásí jako chybu a worker se pozastaví
s odstupy PAUSE_STEPS; další úlohu vezme až po úspěšné sondě (krátký claude -p). Bez nástrojů (claude, Chromium,
ffmpeg/ffprobe, konfigurace MCP) úlohy nebere vůbec. Výpadek sítě nebo 5xx při nahrávání se opakuje (RETRY_DELAYS).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .api import ApiError, ReelsApi
from .claude import DEFAULT_MODEL, EnvError, _bin, parse_output
from .generate import MIN_OK, generate

log = logging.getLogger("hlidac_reels.worker")

IDLE_S = 300
FAIL_PAUSE_S = 60  # pauza po nezdařené úloze, ať vadná úloha nespálí tři drahé běhy claude za sebou
ERROR_LIMIT = 500
TIMEOUT_S = 1200  # s na jeden běh claude -p
MAX_VIDEO_BYTES = 30 * 1024 * 1024  # limit reels_api.php i ingesteru bota
PAUSE_STEPS = (900, 3600, 10800)  # s: odstupy po chybě prostředí; poslední krok se opakuje
PROBE_TIMEOUT_S = 120
PROBE_PROMPT = "odpověz OK"
RETRY_DELAYS = (60, 300, 900)  # s: opakování nahrávání a result při výpadku sítě nebo 5xx

_chromium_path: str | None = None  # nalezené Chromium Playwrightu (driver se pak už nespouští)


def _chromium_ok() -> bool:
    """Je nainstalované Chromium pro Playwright? Cestu zjistí driver Playwrightu, nalezenou si pamatuje."""
    global _chromium_path
    if _chromium_path and Path(_chromium_path).is_file():
        return True
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            path = p.chromium.executable_path
    except Exception:  # noqa: BLE001 — chybějící balík i nefunkční driver = Chromium není
        return False
    if path and Path(path).is_file():
        _chromium_path = path
        return True
    return False


def check_tools(mcp_config: Path) -> list[str]:
    """Co workeru chybí k vygenerování sady (prázdný seznam = vše je); bez nástrojů úlohy nebere."""
    missing = []
    # CLAUDE_BIN (stejně jako run_claude) má přednost; jinak claude na PATH
    exe = os.environ.get("CLAUDE_BIN", "").split()[:1] or ["claude"]
    if not (shutil.which(exe[0]) or Path(exe[0]).is_file()):
        missing.append("claude (Claude Code CLI na PATH)")
    if not _chromium_ok():
        missing.append("Chromium pro Playwright (playwright install chromium)")
    for name in ("ffmpeg", "ffprobe"):
        if not shutil.which(name):
            missing.append(name)
    if not Path(mcp_config).is_file():
        missing.append(f"konfigurace MCP {Path(mcp_config).name}")
    return missing


def probe(model: str) -> bool:
    """Sonda po chybě prostředí: jeden krátký claude -p bez nástrojů a MCP; True = model zase odpovídá."""
    argv = [*_bin(), "-p", "--model", model, "--output-format", "json", "--tools", "", "--strict-mcp-config"]
    try:
        # prázdná dočasná složka: žádný CLAUDE.md ani projektová nastavení
        with tempfile.TemporaryDirectory(prefix="reels-probe-") as cwd:
            proc = subprocess.run(argv, cwd=cwd, input=PROBE_PROMPT, capture_output=True, encoding="utf-8",
                                  errors="replace", timeout=PROBE_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("sonda: claude -p neproběhl: %s", e)
        return False
    is_error, result = parse_output(proc.stdout)
    if proc.returncode != 0 or is_error:
        log.warning("sonda: claude -p skončil s kódem %s: %s", proc.returncode, result or "bez výstupu")
        return False
    return True


def _retrying(call, what: str, sleep):
    """Zavolá call(); při výpadku sítě (OSError) nebo 5xx opakuje po RETRY_DELAYS, pak výjimku pustí dál.

    409 (claim vypršel) ani jiné 4xx se neopakují: další pokus by skončil stejně.
    """
    for delay in (*RETRY_DELAYS, None):
        try:
            return call()
        except ApiError as e:
            if delay is None or e.status < 500:
                raise
            err: Exception = e
        except OSError as e:
            if delay is None:
                raise
            err = e
        log.warning("%s selhalo (%s), zkusím znovu za %s s", what, err, delay)
        sleep(delay)


def load_env(path: Path) -> dict[str, str]:
    """Jednoduchý soubor KEY=VALUE: komentáře #, prázdné řádky, volitelně export a uvozovky."""
    out: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def clean_error(text: str, root: Path) -> str:
    """Text chyby pro server: bez cest k PC (kořen → <root>, domovská složka → ~), nejvýš ERROR_LIMIT znaků."""
    replacements: list[tuple[str, str]] = []
    for base, mark in ((Path(root), "<root>"), (Path.home(), "~")):
        variants = {str(base), str(base.resolve())}
        for v in list(variants):
            variants.add(v.replace("\\", "/"))
        replacements += [(v, mark) for v in variants if len(v) > 3]
    # nejdřív delší cesty (kořen bývá uvnitř domovské složky)
    for old, new in sorted(replacements, key=lambda p: len(p[0]), reverse=True):
        text = text.replace(old, new)
    return text.strip()[:ERROR_LIMIT] or "neznámá chyba"


def _report_fail(api, job_id: int, claim_token: str, error: str, root: Path) -> bool:
    error = clean_error(error, root)
    log.warning("úloha %s: chyba – %s", job_id, error)
    try:
        status = api.fail(job_id, claim_token, error)
        log.info("úloha %s: chyba nahlášena, stav %s", job_id, status)
    except ApiError as e:
        if e.status == 409:
            log.warning("úloha %s: už nepatří workeru (409), chyba se nehlásí", job_id)
        else:
            log.error("úloha %s: nahlášení chyby selhalo: %s", job_id, e)
    except OSError as e:
        log.error("úloha %s: nahlášení chyby selhalo (síť): %s", job_id, e)
    return False


def _upload_landscape(api, job_id: int, claim: str, v, sleep=time.sleep) -> None:
    """Nahraje video na šířku varianty, když je; chyba (mimo 409) jen varování, varianta jde dál na výšku."""
    video = getattr(v, "video_landscape", None)
    if not video:
        return
    size = Path(video).stat().st_size
    if size > MAX_VIDEO_BYTES:
        v.warnings = [*v.warnings, f"video na šířku má {size / 1024 / 1024:.1f} MB, limit je 30 MB"]
        log.warning("úloha %s: video na šířku %s je větší než 30 MB, vynechávám", job_id, v.n)
        return
    try:
        r = _retrying(lambda: api.upload_video(job_id, claim, v.n, video, orientation="landscape"),
                      f"úloha {job_id}: nahrání videa na šířku {v.n}", sleep)
    except (ApiError, OSError) as e:
        if isinstance(e, ApiError) and e.status == 409:
            raise
        v.warnings = [*v.warnings, f"nahrání videa na šířku selhalo: {e}"]
        log.warning("úloha %s: nahrání videa na šířku %s selhalo: %s", job_id, v.n, e)
        return
    if isinstance(r, dict) and r.get("orientation") == "landscape":
        log.info("úloha %s: video na šířku %s nahráno", job_id, v.n)
    else:  # starší server: soubor přepíše následující video na výšku
        log.warning("úloha %s: server video na šířku nepřijal (starší verze), varianta %s jen na výšku", job_id, v.n)


def process(api, job: dict, *, root: Path, mcp_config: Path, model: str, generate=generate,
            sleep=time.sleep) -> bool:
    """Zpracuje jednu vydanou úlohu; True, když server sadu přijal.

    Chybu prostředí (EnvError) nahlásí serveru jako chybu úlohy (do F3 stojí jeden pokus) a vyhodí dál:
    smyčka se podle ní pozastaví. `sleep` čeká mezi opakováními nahrávání.
    """
    root = Path(root)
    try:
        job_id = int(job["job_id"])
        claim = str(job["claim_token"])
        if not claim:
            raise ValueError("prázdný claim_token")
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        # bez job_id a claim_token nejde chybu nahlásit, úloha po 60 min sama vyprší
        log.error("neplatná úloha od serveru (%s: %s), přeskakuji", type(e).__name__, e)
        return False
    workdir = root / str(job_id)
    log.info("úloha %s: tisk %s%s (%s), generuji", job_id, job.get("ct"),
             f", PN {job['focus_pn']}" if job.get("focus_pn") else "", job.get("source"))
    try:
        res = generate(ct=int(job["ct"]), card_v2=job["card_v2"], reason_keys=list(job.get("reason_keys") or []),
                       has_amendments=bool(job.get("has_amendments")), out=workdir, mcp_config=Path(mcp_config),
                       model=model, hint=job.get("format_hint"), render=True, timeout_s=TIMEOUT_S,
                       published=list(job.get("published") or []),
                       # video k vybraným PN (/pn); starší server pole neposílá = celý tisk
                       focus_pn=[int(n) for n in job.get("focus_pn") or []] or None,
                       focus_info=list(job.get("focus_pn_info") or []),
                       # úloha autopilota: jen 2 varianty (v2 + v3 pn); starší server pole neposílá
                       autopilot=job.get("autopilot") is True)
        good = []
        for v in res.variants:
            if not (v.ok and v.video):
                continue
            size = Path(v.video).stat().st_size
            if size > MAX_VIDEO_BYTES:
                # server by video odmítl (413) a celá sada by padla: varianta vypadne jako nepovedená
                v.errors = [*v.errors, f"video má {size / 1024 / 1024:.1f} MB, limit je 30 MB"]
                log.warning("úloha %s: video %s je větší než 30 MB, vynechávám", job_id, v.n)
                continue
            good.append(v)
        if not res.ok or len(good) < MIN_OK:
            detail = "; ".join(f"{v.n}: {', '.join(v.errors) or 'bez videa'}" for v in res.variants
                               if v not in good)
            return _report_fail(api, job_id, claim,
                                f"málo platných variant ({len(good)}/{len(res.variants)}): {detail}", root)
        variants = []
        for v in good:
            card = json.loads((workdir / f"variant-{v.n}" / "card.json").read_text(encoding="utf-8"))
            variants.append({"n": v.n, "template": v.template,
                             # template_v2 formát nemá (inbox jiný než null odmítne), template_v3 ho nese
                             "format": v.format if v.template == "template_v3" else None,
                             "caption": v.caption, "card": card})
        for v in good:
            for w in v.warnings:
                log.warning("úloha %s: varianta %s: %s", job_id, v.n, w)
            _upload_landscape(api, job_id, claim, v, sleep)
            _retrying(lambda: api.upload_video(job_id, claim, v.n, v.video),
                      f"úloha {job_id}: nahrání videa {v.n}", sleep)
            log.info("úloha %s: video %s nahráno", job_id, v.n)
        # nevzniklé varianty: bot je ohlásí u sady, server je uloží jako poznámku úlohy
        skipped = [{"n": v.n, "format": v.format,
                    "errors": [clean_error(e, root) for e in v.errors] or ["bez videa"]}
                   for v in res.variants if v not in good]
        _retrying(lambda: api.result(job_id, claim, variants, skipped), f"úloha {job_id}: odeslání sady", sleep)
    except EnvError as e:
        _report_fail(api, job_id, claim, f"prostředí: {e.reason}", root)
        raise
    except ApiError as e:
        if e.status == 409:
            # úloha mezitím vypršela a vrátila se do fronty: server ji už vydal znovu, fail by nic nezměnil
            log.warning("úloha %s: už nepatří workeru (409), končím bez hlášení", job_id)
            return False
        return _report_fail(api, job_id, claim, f"API {e}", root)
    except Exception as e:  # noqa: BLE001 — každá chyba úlohy se hlásí serveru, worker běží dál
        return _report_fail(api, job_id, claim, f"{type(e).__name__}: {e}", root)
    log.info("úloha %s: sada %s variant odeslána", job_id, len(variants))
    return True


def run(api, *, root: Path, mcp_config: Path, model: str, sleep=time.sleep, once: bool = False,
        probe=None) -> None:
    """Smyčka workeru; once=True = jeden průchod (i s čekáním, když nic není).

    Bez nástrojů (check_tools) úlohy nebere. Po chybě prostředí je pozastavený: před dalším claim počká krok
    z PAUSE_STEPS a pustí sondu; neúspěch = delší odstup, úspěch = odstup se nuluje a worker bere úlohy.
    `probe` jde podvrhnout v testech (výchozí = skutečná sonda).
    """
    probe = probe or globals()["probe"]
    pause: int | None = None  # index do PAUSE_STEPS, None = worker není pozastavený
    while True:
        try:
            api.heartbeat()
            missing = check_tools(mcp_config)
            if missing:
                log.error("chybí nástroje, úlohy neberu: %s", ", ".join(missing))
                sleep(IDLE_S)
            elif pause is not None and not _resume(pause, model, sleep, probe):
                pause = min(pause + 1, len(PAUSE_STEPS) - 1)
            else:
                if pause is not None:
                    log.info("sonda prošla, pozastavení končí")
                    pause = None
                job = api.claim()
                if job is None:
                    log.debug("žádná úloha")
                    sleep(IDLE_S)
                elif not process(api, job, root=root, mcp_config=mcp_config, model=model, generate=generate,
                                 sleep=sleep):
                    sleep(FAIL_PAUSE_S)
        except EnvError as e:
            pause = 0
            log.error("chyba prostředí (%s), pozastavuji, sonda za %s s", e.reason, PAUSE_STEPS[0])
        except (ApiError, OSError) as e:  # URLError a timeout jsou OSError
            log.error("API nedostupné: %s", e)
            sleep(IDLE_S)
        except Exception as e:  # noqa: BLE001 — jedna vadná iterace nesmí ukončit worker do příštího přihlášení
            log.exception("neočekávaná chyba ve smyčce: %s: %s", type(e).__name__, e)
            sleep(IDLE_S)
        if once:
            return


def _resume(step: int, model: str, sleep, probe) -> bool:
    """Pozastavený worker: počká odstup PAUSE_STEPS[step] a pustí sondu; True = může zase brát úlohy."""
    log.info("pozastaveno po chybě prostředí, čekám %s s, pak sonda", PAUSE_STEPS[step])
    sleep(PAUSE_STEPS[step])
    ok = probe(model)
    if not ok:
        log.warning("sonda neprošla, pozastavení pokračuje")
    return ok


def acquire_lock(root: Path):
    """Výhradní zámek root/worker.lock; vrací otevřený soubor (držet po celý běh), nebo None, když je obsazený."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    fh = open(root / "worker.lock", "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def release_lock(fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        fh.close()


def _setup_logging(root: Path) -> list[logging.Handler]:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr),
                                       logging.FileHandler(root / "worker.log", encoding="utf-8")]
    for h in handlers:
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)
    return handlers


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Worker generátoru Reels: úlohy z reels_api.php, sady přes claude -p.")
    ap.add_argument("--env", type=Path, required=True,
                    help="soubor s REELS_API_URL, REELS_WORKER_TOKEN, volitelně REELS_MODEL (mimo repo)")
    ap.add_argument("--root", type=Path, required=True, help="pracovní složka (úlohy, worker.lock, worker.log)")
    ap.add_argument("--mcp-config", type=Path, required=True, help="JSON s MCP serverem cosepece (mimo repo)")
    ap.add_argument("--once", action="store_true", help="jeden průchod bez čekání (zkouška)")
    args = ap.parse_args(argv)

    try:
        env = load_env(args.env)
    except OSError as e:
        print(f"nejde načíst --env: {e}", file=sys.stderr)
        return 1
    url, token = env.get("REELS_API_URL", ""), env.get("REELS_WORKER_TOKEN", "")
    if not url or not token:
        print("v --env chybí REELS_API_URL nebo REELS_WORKER_TOKEN", file=sys.stderr)
        return 1
    if not args.mcp_config.is_file():
        print(f"chybí konfigurace MCP: {args.mcp_config}", file=sys.stderr)
        return 1

    lock = acquire_lock(args.root)
    if lock is None:  # druhá instance nesmí ani sestavit klienta API, natož vyzvednout úlohu
        print("worker už běží")
        return 0
    try:
        api = ReelsApi(url, token)
    except ValueError as e:
        release_lock(lock)
        print(f"chybné REELS_API_URL: {e}", file=sys.stderr)
        return 1
    handlers = _setup_logging(args.root)
    try:
        model = env.get("REELS_MODEL") or DEFAULT_MODEL
        log.info("worker start: API %s, model %s", getattr(api, "host", "?"), model)
        run(api, root=args.root, mcp_config=args.mcp_config, model=model,
            sleep=(lambda _s: None) if args.once else time.sleep, once=args.once)
        return 0
    except KeyboardInterrupt:
        log.info("worker ukončen")
        return 0
    finally:
        for h in handlers:
            log.removeHandler(h)
            h.close()
        release_lock(lock)


if __name__ == "__main__":
    sys.exit(main())
