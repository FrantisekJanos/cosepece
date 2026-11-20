"""Sestavení sady 3 variant (plán Task 4). Model, render i ffprobe jsou podvržené."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import hlidac_reels.generator.generate as gen
from conftest import load_fixture
from hlidac_reels.generator.claude import ClaudeRun
from hlidac_reels.media import VideoInfo

GEN = Path(__file__).parent / "fixtures" / "gen"
KEYS = [r["key"] for r in load_fixture("v3-cesta-311.json")["reasons"]]


@pytest.fixture
def fake(monkeypatch):
    state = {"calls": [], "renders": [], "active": 0, "prepare": None, "timed_out": False, "skip": set()}

    def run_claude(prompt, *, workdir, mcp_config, model, timeout_s=1200):
        state["calls"].append(prompt)
        if len(state["calls"]) == 1:
            for d in GEN.iterdir():
                if d.name not in state["skip"]:
                    shutil.copytree(d, Path(workdir) / d.name, dirs_exist_ok=True)
            if state["prepare"]:
                state["prepare"](Path(workdir))
        log = Path(workdir) / "claude.log"
        log.write_text("log", encoding="utf-8")
        to = state["timed_out"]
        return ClaudeRun(ok=not to, returncode=None if to else 0, timed_out=to, log_path=log)

    def render_reel(card, out_path, *, template="template", **kw):
        state["active"] += 1
        assert state["active"] == 1, "render běží souběžně"
        state["renders"].append(template)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"mp4")
        state["active"] -= 1

    monkeypatch.setattr(gen, "run_claude", run_claude)
    monkeypatch.setattr(gen, "render_reel", render_reel)
    monkeypatch.setattr(gen, "check_layout", lambda card, template: [])
    monkeypatch.setattr(gen, "probe", lambda p: VideoInfo("h264", "aac", 1080, 1920, 30.0, 3))
    return state


def _run(tmp_path, **kw):
    args = dict(ct=311, card_v2=load_fixture("card-1-v2.json"), reason_keys=KEYS, has_amendments=False,
                out=tmp_path / "out", mcp_config=tmp_path / "mcp.json")
    args.update(kw)
    return gen.generate(**args)


def test_tri_platne_varianty(tmp_path, fake):
    r = _run(tmp_path)
    assert r.ok and r.formats == ["cesta", "pn"]
    assert [v.ok for v in r.variants] == [True, True, True]
    assert all(v.video and v.video.exists() for v in r.variants)
    data = json.loads((tmp_path / "out" / "vysledek.json").read_text(encoding="utf-8"))
    assert data["ok"] and len(data["variants"]) == 3


def test_chybejici_varianta_vypadne(tmp_path, fake):
    fake["prepare"] = lambda wd: shutil.rmtree(wd / "variant-3")
    r = _run(tmp_path)
    assert r.ok and [v.ok for v in r.variants] == [True, True, False]
    assert any("chybí" in e for e in r.variants[2].errors)


def test_neplatny_json_vypadne(tmp_path, fake):
    fake["prepare"] = lambda wd: (wd / "variant-2" / "card.json").write_text("{", encoding="utf-8")
    r = _run(tmp_path)
    assert not r.variants[1].ok and r.ok


def test_oprava_jen_jednou(tmp_path, fake):
    def bad(wd):
        card = json.loads((wd / "variant-3" / "card.json").read_text(encoding="utf-8"))
        card["reasons"].append({"key": "p90_passed", "label": "x", "points": 1})
        (wd / "variant-3" / "card.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    fake["prepare"] = bad
    r = _run(tmp_path)
    assert len(fake["calls"]) == 2
    assert "variant-3" in fake["calls"][1] and "p90_passed" in fake["calls"][1] and "variant-1" not in fake["calls"][1]
    assert not r.variants[2].ok and r.ok


def test_jedna_platna_je_chyba(tmp_path, fake):
    fake["prepare"] = lambda wd: (shutil.rmtree(wd / "variant-2"), shutil.rmtree(wd / "variant-3"))
    r = _run(tmp_path)
    assert not r.ok


def test_timeout_zachova_hotove(tmp_path, fake):
    fake["timed_out"] = True
    fake["prepare"] = lambda wd: shutil.rmtree(wd / "variant-3")
    r = _run(tmp_path)
    assert r.ok and [v.ok for v in r.variants] == [True, True, False]


def test_render_postupne(tmp_path, fake):
    _run(tmp_path)
    assert fake["renders"] == ["template_v2", "template_v3", "template_v3"]


def test_bez_renderu(tmp_path, fake):
    r = _run(tmp_path, render=False)
    assert r.ok and fake["renders"] == [] and all(v.video is None for v in r.variants)


def test_diakritika_prezije(tmp_path, fake):
    text = "Příliš žluťoučký kůň úpěl ďábelské ódy.\n\nHodnotíme postup, ne politický obsah.\n\n#cosepece\n"
    fake["prepare"] = lambda wd: (wd / "variant-1" / "caption.txt").write_text(text, encoding="utf-8")
    r = _run(tmp_path)
    data = json.loads((tmp_path / "out" / "vysledek.json").read_text(encoding="utf-8"))
    assert r.variants[0].caption == text.strip() and data["variants"][0]["caption"] == text.strip()


def test_main_navratovy_kod(tmp_path, fake, monkeypatch):
    (tmp_path / "mcp.json").write_text("{}", encoding="utf-8")
    card = tmp_path / "card.json"
    card.write_text(json.dumps(load_fixture("card-1-v2.json"), ensure_ascii=False), encoding="utf-8")
    base = ["--ct", "311", "--card-v2", str(card), "--reasons", ",".join(KEYS),
            "--mcp-config", str(tmp_path / "mcp.json"), "--out", str(tmp_path / "o")]
    assert gen.main(base) == 0
    fake["calls"].clear()
    fake["prepare"] = lambda wd: (shutil.rmtree(wd / "variant-2"), shutil.rmtree(wd / "variant-3"))
    assert gen.main(base[:-1] + [str(tmp_path / "o2")]) == 2
    assert gen.main(base[:2] + ["--card-v2", str(tmp_path / "neni.json")] + base[4:]) == 1


def test_main_bez_mcp_konfigurace(tmp_path, fake):
    card = tmp_path / "card.json"
    card.write_text(json.dumps(load_fixture("card-1-v2.json"), ensure_ascii=False), encoding="utf-8")
    assert gen.main(["--ct", "311", "--card-v2", str(card), "--mcp-config", str(tmp_path / "neni.json"),
                     "--out", str(tmp_path / "o")]) == 1


def test_vadna_struktura_vyradi_jen_variantu(tmp_path, fake):
    # pád uvnitř validace (reasons není seznam) nesmí shodit celou sadu
    def bad(wd):
        card = json.loads((wd / "variant-2" / "card.json").read_text(encoding="utf-8"))
        card["reasons"] = 5
        (wd / "variant-2" / "card.json").write_text(json.dumps(card), encoding="utf-8")
    fake["prepare"] = bad
    r = _run(tmp_path)
    assert r.ok and not r.variants[1].ok and r.variants[1].errors
    assert (tmp_path / "out" / "vysledek.json").exists()


def test_stare_varianty_se_smazou(tmp_path, fake):
    # varianta z minulého běhu ve stejné složce se nesmí vydávat za novou
    shutil.copytree(GEN / "variant-3", tmp_path / "out" / "variant-3")
    fake["skip"] = {"variant-3"}
    r = _run(tmp_path)
    assert not r.variants[2].ok


def test_timeout_je_v_chybe_varianty(tmp_path, fake):
    fake["timed_out"] = True
    fake["skip"] = {"variant-3"}
    r = _run(tmp_path)
    assert any("timeout" in e for e in r.variants[2].errors)
    data = json.loads((tmp_path / "out" / "vysledek.json").read_text(encoding="utf-8"))
    assert data["runs"][0]["timed_out"] is True
