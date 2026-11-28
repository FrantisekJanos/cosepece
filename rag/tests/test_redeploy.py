"""docker/redeploy.sh a služba chat: selhání chatu nesmí shodit ani vrátit nasazení webu, jen chat vypne.

Skript běží v dočasné kopii adresáře docker/ s falešnými příkazy git, docker, curl, sleep a timeout (zapisují volání
do logu), takže se nic skutečného nesestaví, nespustí ani nestáhne. Potřebuje bash. Na Windows (Git Bash) trvá každý běh
20–60 s (start procesu na každý falešný příkaz), proto tam běží jen s HLIDAC_TEST_BASH=1; v CI (Linux) vždy.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

KOREN = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
pytestmark = [
    pytest.mark.skipif(not BASH or "system32" in BASH.lower(), reason="bez bashe (Git Bash, Linux)"),
    pytest.mark.skipif(os.name == "nt" and os.environ.get("HLIDAC_TEST_BASH") != "1",
                       reason="na Windows pomalé, zapnout HLIDAC_TEST_BASH=1"),
]

FALESNE = {
    "git": """case "$*" in
  "rev-parse HEAD"|"rev-parse --short HEAD") echo abc123 ;;
esac""",
    "docker": """echo "docker $*" >> "$LOG"
case "$*" in
  "compose config --images") printf 'docker-web\\ndocker-chat\\n' ;;
  "compose config --services")
    [ "${FAKE_SERVICES_FAIL:-0}" = 1 ] && exit 1
    printf 'web\\nsync\\nmcp\\nchat\\ndb\\n' ;;
  "run --rm --network none"*) echo "VŠE OK" ;;
  "compose up -d chat") exit "${FAKE_CHAT_UP:-0}" ;;
  "compose run "*) exit "${FAKE_EVAL:-0}" ;;
esac""",
    # vrací jen stavový kód (-w '%{http_code}'); 8103 = chat, 8100 = web
    "curl": """case "$*" in
  *8103*) echo "${FAKE_CHAT_HTTP:-200}" ;;
  *8100*) echo "${FAKE_WEB_HTTP:-200}" ;;
  *) echo 200 ;;
esac""",
    "sleep": ":",
    # sed -i na .env může selhat (např. .env je jen pro čtení); ostatní volání nic nedělají
    "sed": '[ "${FAKE_SED_FAIL:-0}" = 1 ] && exit 1; exec /usr/bin/env sed "$@"',
    "timeout": 'shift; exec "$@"',
}


def _spust(tmp_path: Path, zapnuto: str = "1", **falesne) -> tuple[subprocess.CompletedProcess, list[str], str]:
    docker = tmp_path / "repo/docker"
    docker.mkdir(parents=True)
    shutil.copy(KOREN / "docker/redeploy.sh", docker / "redeploy.sh")
    (docker / ".env").write_text(f"BIND=127.0.0.1:8100\nCHAT_BIND=127.0.0.1:8103\nCHAT_ZAPNUTO={zapnuto}\n",
                                 encoding="utf-8", newline="\n")
    binar = tmp_path / "bin"
    binar.mkdir()
    for jmeno, telo in FALESNE.items():
        f = binar / jmeno
        f.write_text(f"#!/usr/bin/env bash\n{telo}\n", encoding="utf-8", newline="\n")
        f.chmod(0o755)
    log = tmp_path / "log.txt"
    log.touch()
    env = {k: v for k, v in os.environ.items() if k not in ("ROLLBACK", "SKIP_CHAT_EVAL", "SKIP_PREFLIGHT")}
    env.update(PATH=str(binar) + os.pathsep + env.get("PATH", ""), LOG=str(log), **falesne)
    r = subprocess.run([BASH, str(docker / "redeploy.sh")], cwd=docker, env=env, capture_output=True,
                       text=True, encoding="utf-8", timeout=300)
    return r, log.read_text(encoding="utf-8").splitlines(), (docker / ".env").read_text(encoding="utf-8")


def _evaly(log: list[str]) -> list[str]:
    return [r for r in log if r.startswith("docker compose run ")]


def test_web_se_spousti_bez_chatu_a_eval_projde(tmp_path):
    r, log, env = _spust(tmp_path)
    assert r.returncode == 0, r.stderr
    prvni_up = next(x for x in log if x.startswith("docker compose up -d"))
    assert prvni_up == "docker compose up -d web sync mcp db"   # chat až po ověření webu a MCP
    assert log.index(prvni_up) < log.index("docker compose up -d chat")
    assert _evaly(log) == ["docker compose run --rm --no-deps -T chat python -m eval.chat.chat_eval --sada kour "
                           "--beta-z-prostredi"]
    assert "CHAT_ZAPNUTO=1" in env and "kouřový eval prošel" in r.stdout


def test_selhani_evalu_chat_vypne_a_web_necha(tmp_path):
    r, log, env = _spust(tmp_path, FAKE_EVAL="1")
    assert r.returncode == 0, r.stderr
    assert "CHAT_ZAPNUTO=0" in env and "CHAT_ZAPNUTO=1" not in env
    assert log.count("docker compose up -d chat") == 2   # znovu s CHAT_ZAPNUTO=0 (služba vrací 503)
    assert "vypínám chat" in r.stderr and "vracím předchozí" not in r.stderr


def test_nedobehnuty_eval_jen_varuje(tmp_path):
    r, log, env = _spust(tmp_path, FAKE_EVAL="2")
    assert r.returncode == 0 and "CHAT_ZAPNUTO=1" in env and log.count("docker compose up -d chat") == 1
    assert "nedoběhl (kód 2" in r.stderr


def test_chat_neodpovida_zastavi_ho_bez_evalu(tmp_path):
    r, log, _ = _spust(tmp_path, FAKE_CHAT_HTTP="502")
    assert r.returncode == 0, r.stderr
    assert "docker compose stop chat" in log and _evaly(log) == []
    assert "chat: 502" in r.stdout and "vracím předchozí" not in r.stderr


def test_chat_nenastartuje_web_dal_bezi(tmp_path):
    r, log, _ = _spust(tmp_path, FAKE_CHAT_UP="1")
    assert r.returncode == 0 and _evaly(log) == [] and "nenastartovala" in r.stderr


@pytest.mark.parametrize("zapnuto", ["0", ""])
def test_vypnuty_chat_eval_nespousti(tmp_path, zapnuto):
    r, log, _ = _spust(tmp_path, zapnuto=zapnuto)
    assert r.returncode == 0 and _evaly(log) == [] and "eval se nespouští" in r.stdout


def test_skip_chat_eval(tmp_path):
    r, log, _ = _spust(tmp_path, SKIP_CHAT_EVAL="1")
    assert r.returncode == 0 and _evaly(log) == [] and "přeskočen" in r.stdout


def test_web_neodpovida_chat_se_nespousti(tmp_path):
    r, log, _ = _spust(tmp_path, FAKE_WEB_HTTP="500")
    assert r.returncode == 1   # stejný commit: bez návratu, ale chyba
    assert "docker compose up -d chat" not in log and _evaly(log) == []


def test_selhani_prepisu_env_chat_zastavi(tmp_path):
    """Neúspěšný sed -i (set -e) nesmí ukončit skript s chatem zapnutým: chat se zastaví a .env zůstane."""
    r, log, env = _spust(tmp_path, FAKE_EVAL="1", FAKE_SED_FAIL="1")
    assert r.returncode == 0, r.stderr
    assert "docker compose stop chat" in log and "CHAT_ZAPNUTO=1" in env
    assert "nepodařilo se přepsat" in r.stderr


def test_prazdny_seznam_sluzeb_je_chyba_a_nic_nespusti(tmp_path):
    """Bez seznamu služeb by `up -d` bez argumentů spustil i chat (před ověřením webu a MCP)."""
    r, log, _ = _spust(tmp_path, FAKE_SERVICES_FAIL="1")
    assert r.returncode != 0
    assert not [x for x in log if x.startswith("docker compose up")]
