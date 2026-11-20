"""Prompty generátoru a spuštění claude -p se zúženými nástroji (plán Task 3)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from hlidac_reels.generator.claude import (ALLOWED_TOOLS, DISALLOWED_TOOLS, build_prompt, claude_argv,
                                           prepare_workdir, run_claude)

FAKE = Path(__file__).parent / "fake_claude.py"


def _fake(monkeypatch, mode: str) -> None:
    monkeypatch.setenv("CLAUDE_BIN", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)


def test_argv_zuzene_nastroje(tmp_path):
    argv = claude_argv(workdir=tmp_path, model="claude-sonnet-5-5", mcp_config=tmp_path / "mcp.json")
    assert "-p" in argv and "claude-sonnet-5-5" in argv and "--restricted" in argv
    assert "--strict-mcp-config" in argv and str(tmp_path / "mcp.json") in argv
    allowed = argv[argv.index("--allowedTools") + 1]
    disallowed = argv[argv.index("--disallowedTools") + 1]
    assert "mcp__cosepece__*" in allowed and "Bash" not in allowed
    assert "Bash" in disallowed and "WebFetch" in disallowed
    assert set(ALLOWED_TOOLS) >= {"Read", "Write", "Agent"} and "Bash" in DISALLOWED_TOOLS


def test_prompt_obsahuje_ct_formaty(tmp_path):
    p = build_prompt(ct=311, formats=["cesta", "pn"], workdir=tmp_path)
    assert "311" in p and "variant-2" in p and "variant-3" in p and "cesta" in p and "pn" in p
    assert "rozprava" not in p


def test_podklady_obsahuji_pravidla(tmp_path):
    prepare_workdir(tmp_path, ct=311)
    for name in ("v2.md", "v3.md", "pravidla.md", "formaty.md", "lhuty.md", "vzor-v2.json", "vzor-popisek.txt",
                 "vzor-v3-cesta.json", "vzor-v3-pn.json", "vzor-v3-zmena.json", "vzor-v3-rozprava.json"):
        assert (tmp_path / "podklady" / name).is_file(), name
    assert "Hodnotíme postup, ne politický obsah." in (tmp_path / "podklady" / "pravidla.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("ct", [311, 221, 115, 174])
def test_vzory_jsou_z_jineho_tisku(tmp_path, ct):
    # model jinak vzor ze stejného tisku opíše (zkouška 311, 2026-11-20)
    prepare_workdir(tmp_path, ct=ct)
    pod = tmp_path / "podklady"
    for fmt in ("cesta", "pn", "zmena"):
        assert json.loads((pod / f"vzor-v3-{fmt}.json").read_text(encoding="utf-8"))["ct"] != ct, fmt
    assert json.loads((pod / "vzor-v2.json").read_text(encoding="utf-8"))["ct"] != ct
    assert (pod / "vzor-popisek.txt").read_text(encoding="utf-8") != ""
    assert not list(pod.glob("v3-*.json")) and not list(pod.glob("card-v2-*.json"))


def test_prompty_zakazuji_opisovat_vzor():
    from hlidac_reels.generator.claude import HERE
    for name in ("v2.md", "v3.md"):
        assert "nekopíruj" in (HERE / "prompts" / name).read_text(encoding="utf-8")


def test_prompt_oprava_jen_vadne_varianty(tmp_path):
    p = build_prompt(ct=311, formats=["cesta", "pn"], workdir=tmp_path, repair={3: ["chybí pole zdroje"]})
    assert "variant-3" in p and "chybí pole zdroje" in p
    assert "variant-1" not in p and "variant-2" not in p


def test_run_claude_timeout(tmp_path, monkeypatch):
    _fake(monkeypatch, "sleep")
    r = run_claude("x", workdir=tmp_path, mcp_config=tmp_path / "mcp.json", timeout_s=2)
    assert r.timed_out and not r.ok and r.log_path.exists()


def test_run_claude_ok_prompt_ze_stdin(tmp_path, monkeypatch):
    _fake(monkeypatch, "ok")
    r = run_claude("Příliš žluťoučký kůň", workdir=tmp_path, mcp_config=tmp_path / "mcp.json", timeout_s=30)
    assert r.ok and r.returncode == 0
    args = (tmp_path / "fake_args.txt").read_text(encoding="utf-8")
    assert args.endswith("Příliš žluťoučký kůň")


def test_run_claude_chyba(tmp_path, monkeypatch):
    _fake(monkeypatch, "fail")
    r = run_claude("x", workdir=tmp_path, mcp_config=tmp_path / "mcp.json", timeout_s=30)
    assert not r.ok and r.returncode == 3 and "chyba modelu" in r.log_path.read_text(encoding="utf-8")


def test_relativni_cesty_se_prevedou_na_absolutni(tmp_path, monkeypatch):
    # claude běží s cwd=workdir; relativní --mcp-config by se hledal v pracovní složce
    _fake(monkeypatch, "ok")
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "mcp.json").write_text("{}", encoding="utf-8")
    (tmp_path / "wd").mkdir()
    monkeypatch.chdir(tmp_path)
    run_claude("x", workdir=Path("wd"), mcp_config=Path("cfg/mcp.json"), timeout_s=30)
    args = (tmp_path / "wd" / "fake_args.txt").read_text(encoding="utf-8").splitlines()
    assert str((tmp_path / "cfg" / "mcp.json").resolve()) in args
    assert str((tmp_path / "wd").resolve()) in args


def test_druhy_beh_log_neprepise(tmp_path, monkeypatch):
    _fake(monkeypatch, "fail")
    run_claude("x", workdir=tmp_path, mcp_config=tmp_path / "mcp.json", timeout_s=30)
    _fake(monkeypatch, "ok")
    r = run_claude("y", workdir=tmp_path, mcp_config=tmp_path / "mcp.json", timeout_s=30)
    log = r.log_path.read_text(encoding="utf-8")
    assert "chyba modelu" in log and '"result": "ok"' in log


def test_run_claude_smaze_projektova_nastaveni(tmp_path, monkeypatch):
    # model smí zapisovat do workdir; .claude/ ani CLAUDE.md z minulého běhu nesmí ovlivnit další běh
    _fake(monkeypatch, "ok")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("x", encoding="utf-8")
    run_claude("x", workdir=tmp_path, mcp_config=tmp_path / "mcp.json", timeout_s=30)
    assert not (tmp_path / ".claude").exists() and not (tmp_path / "CLAUDE.md").exists()
