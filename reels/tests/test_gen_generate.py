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
    state = {"calls": [], "renders": [], "landscape": [], "active": 0, "prepare": None, "timed_out": False,
             "skip": set(), "land_fail": None}

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

    def render_reel(card, out_path, *, template="template", orientation="portrait", **kw):
        state["active"] += 1
        assert state["active"] == 1, "render běží souběžně"
        if orientation == "landscape":
            assert Path(out_path).name.endswith("-sirka.mp4")
            state["landscape"].append(template)
            if state["land_fail"] == "render":
                state["active"] -= 1
                raise RuntimeError("Chromium spadl")
        else:
            state["renders"].append(template)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"mp4")
        state["active"] -= 1

    monkeypatch.setattr(gen, "run_claude", run_claude)
    monkeypatch.setattr(gen, "render_reel", render_reel)
    monkeypatch.setattr(gen, "check_layout", lambda card, template: [])
    def probe(p):
        # video na šířku má rozměry 16:9; land_fail="ratio" u varianty 2 podvrhne šířku s poměrem 9:16
        if Path(p).name.endswith("-sirka.mp4") and not (state["land_fail"] == "ratio" and "variant-2" in str(p)):
            return VideoInfo("h264", "aac", 1920, 1080, 30.0, 3)
        return VideoInfo("h264", "aac", 1080, 1920, 30.0, 3)
    monkeypatch.setattr(gen, "probe", probe)
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


def test_sirka_ke_kazde_platne_variante(tmp_path, fake):
    r = _run(tmp_path)
    assert fake["landscape"] == ["template_v2", "template_v3", "template_v3"]
    for v in r.variants:
        assert v.ok and v.video_landscape and v.video_landscape.exists() and not v.warnings
        assert v.video_landscape.name == v.video.stem + "-sirka.mp4"
    data = json.loads((tmp_path / "out" / "vysledek.json").read_text(encoding="utf-8"))
    assert all(item["video_landscape"].endswith("-sirka.mp4") for item in data["variants"])


def test_vadna_sirka_nevyradi_variantu(tmp_path, fake):
    fake["land_fail"] = "ratio"
    r = _run(tmp_path)
    v = r.variants[1]
    assert r.ok and v.ok and v.video and v.video_landscape is None
    assert any("šířku" in w and "16:9" in w for w in v.warnings) and not v.errors
    assert not list((tmp_path / "out" / "variant-2").glob("*-sirka.mp4"))
    assert r.variants[0].video_landscape and r.variants[2].video_landscape


def test_pad_renderu_sirky_nevyradi_variantu(tmp_path, fake):
    fake["land_fail"] = "render"
    r = _run(tmp_path)
    assert r.ok and all(v.ok and v.video and v.video_landscape is None for v in r.variants)
    assert all(any("Chromium spadl" in w for w in v.warnings) for v in r.variants)


def test_sablona_bez_sirky(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(gen, "has_landscape", lambda template: template != "template_v2")
    r = _run(tmp_path)
    assert fake["landscape"] == ["template_v3", "template_v3"]
    assert r.variants[0].ok and r.variants[0].video_landscape is None and not r.variants[0].warnings


def test_bez_sirky_na_prani(tmp_path, fake):
    r = _run(tmp_path, landscape=False)
    assert r.ok and fake["landscape"] == [] and all(v.video_landscape is None for v in r.variants)


def test_vadna_varianta_sirku_nerenderuje(tmp_path, fake):
    fake["prepare"] = lambda wd: shutil.rmtree(wd / "variant-3")
    _run(tmp_path)
    assert fake["landscape"] == ["template_v2", "template_v3"]


FOCUS_INFO = [{"cislo": 2315, "osoba": "Jana Malá", "cas": "2026-11-23 20:09:00", "titulek": "Mění daň",
               "url_psp": "https://www.psp.cz/sqw/sd.sqw?cd=2315&o=10"}]


def test_video_k_pn_zadani_a_formaty(tmp_path, fake):
    r = _run(tmp_path, focus_pn=[2315], focus_info=FOCUS_INFO, hint="cesta", render=False)
    assert r.formats == ["pn", "zmena"]
    out = tmp_path / "out"
    tisk = json.loads((out / "tisk.json").read_text(encoding="utf-8"))
    assert tisk["focus_pn"] == [2315] and tisk["formats"] == ["pn", "zmena"]
    zadani = (out / "podklady" / "zadani.md").read_text(encoding="utf-8")
    assert "PN 2315" in zadani and "Jana Malá" in zadani
    # další běh bez PN zadání smaže
    _run(tmp_path, render=False)
    assert not (out / "podklady" / "zadani.md").exists()
    assert json.loads((out / "tisk.json").read_text(encoding="utf-8"))["focus_pn"] is None


def test_video_k_pn_karta_bez_pn_vypadne(tmp_path, fake):
    r = _run(tmp_path, focus_pn=[9999], focus_info=[], render=False)
    assert not r.variants[0].ok
    assert any("9999" in e for e in r.variants[0].errors)
    # oprava dostala chybu PN
    assert len(fake["calls"]) == 2 and "9999" in fake["calls"][1]


def test_video_k_pn_bez_textu_dve_varianty(tmp_path, fake):
    info = [dict(FOCUS_INFO[0], has_text=False)]
    r = _run(tmp_path, focus_pn=[2315], focus_info=info, render=False)
    assert r.formats == ["pn"]
    assert [(v.n, v.template, v.format) for v in r.variants] == [(1, "template_v2", None), (2, "template_v3", "pn")]
    assert "variant-3" not in fake["calls"][0] and "formát F = pn" in fake["calls"][0]
    assert json.loads((tmp_path / "out" / "tisk.json").read_text(encoding="utf-8"))["formats"] == ["pn"]


def test_normalizace_zapise_kartu_a_zasahy(tmp_path, fake):
    def prepare(wd):
        d = wd / "variant-2"
        card = json.loads((d / "card.json").read_text(encoding="utf-8"))
        card["hook"] = dict(card["hook"], big="800 mil. Kč", unit="")
        (d / "card.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
        (d / "caption.txt").write_text("Popisek bez závěru.", encoding="utf-8")
    fake["prepare"] = prepare
    r = _run(tmp_path, render=False)
    assert r.variants[1].ok, r.variants[1].errors
    d = tmp_path / "out" / "variant-2"
    card = json.loads((d / "card.json").read_text(encoding="utf-8"))
    assert card["hook"]["big"] == "800" and card["hook"]["unit"] == "mil. Kč"
    assert r.variants[1].caption.endswith("Hodnotíme postup, ne politický obsah.")
    assert (d / "caption.txt").read_text(encoding="utf-8").strip() == r.variants[1].caption
    notes = json.loads((d / "normalize.json").read_text(encoding="utf-8"))
    assert len(notes) == 2
    # bez zásahu se normalize.json zapíše prázdný
    assert json.loads((tmp_path / "out" / "variant-1" / "normalize.json").read_text(encoding="utf-8")) == []
