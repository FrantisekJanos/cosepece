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
             "skip": set(), "land_fail": None, "paths": []}

    def run_claude(prompt, *, workdir, mcp_config, model, timeout_s=1200):
        state["calls"].append(prompt)
        if "OPRAVA:" not in prompt:  # generování celé sady (ne oprava)
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
        state["paths"].append(Path(out_path))  # výstup prozradí variantu (variant-N/…)
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
    state["render_reel"] = render_reel  # test si render podvrhne jinak a pak vrátí tento
    monkeypatch.setattr(gen, "check_layout", lambda card, template, orientation="portrait": [])
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


def test_oprava_nejvys_trikrat(tmp_path, fake):
    def bad(wd):
        card = json.loads((wd / "variant-3" / "card.json").read_text(encoding="utf-8"))
        card["reasons"].append({"key": "p90_passed", "label": "x", "points": 1})
        (wd / "variant-3" / "card.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    fake["prepare"] = bad
    r = _run(tmp_path)
    assert len(fake["calls"]) == gen.BUDGET_RUNS == 4  # 1 generování + 3 cílené běhy
    for prompt in fake["calls"][1:]:
        assert "variant-3" in prompt and "p90_passed" in prompt and "variant-1" not in prompt
    assert not r.variants[2].ok and r.ok


def test_sada_s_jednou_variantou_projde(tmp_path, fake):
    # MIN_OK = 1: sada s jedinou platnou variantou se posílá (rozhodnutí vlastníka, platí pro všechny zdroje)
    fake["prepare"] = lambda wd: (shutil.rmtree(wd / "variant-2"), shutil.rmtree(wd / "variant-3"))
    r = _run(tmp_path)
    assert gen.MIN_OK == 1
    assert r.ok and [v.ok for v in r.variants] == [True, False, False]


def test_zadna_platna_je_chyba(tmp_path, fake):
    fake["skip"] = {"variant-1", "variant-2", "variant-3"}
    r = _run(tmp_path, render=False)
    assert not r.ok and not any(v.ok for v in r.variants)


def test_autopilot_dve_varianty(tmp_path, fake):
    r = _run(tmp_path, render=False, autopilot=True)
    assert r.formats == ["pn"]
    assert [(v.n, v.template, v.format) for v in r.variants] == [(1, "template_v2", None), (2, "template_v3", "pn")]
    assert "variant-3" not in fake["calls"][0] and "formát F = pn" in fake["calls"][0]
    assert json.loads((tmp_path / "out" / "tisk.json").read_text(encoding="utf-8"))["formats"] == ["pn"]


def test_autopilot_zmeni_otisk_vstupu(tmp_path, fake):
    # formáty jsou v otisku: přepnutí autopilota začne úlohu znovu (jiná sada variant)
    _run(tmp_path, render=False)
    calls = len(fake["calls"])
    r = _run(tmp_path, render=False, autopilot=True)
    # nové generování celé sady (ne pokračování s opravou), stav s jen dvěma variantami
    assert "OPRAVA:" not in fake["calls"][calls]
    assert r.formats == ["pn"] and len(r.variants) == 2
    state = json.loads((tmp_path / "out" / "state.json").read_text(encoding="utf-8"))
    assert sorted(state["variants"]) == ["1", "2"]


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
    text = "Příliš žluťoučký kůň úpěl ďábelské ódy (AI).\n\nHodnotíme postup, ne politický obsah.\n\n#cosepece\n"
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
    fake["skip"] = {"variant-1", "variant-2", "variant-3"}
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


def test_sirka_s_chybou_rozvrzeni_se_nerenderuje(tmp_path, fake, monkeypatch):
    # rozvržení na šířku se kontroluje před renderem šířky; chyba vyřadí jen šířku, varianta zůstane platná na výšku
    calls: list[str] = []

    def layout(card, template, orientation="portrait"):
        calls.append(orientation)
        return ["scene:s-hook: mimo bezpečnou zónu"] if orientation == "landscape" else []
    monkeypatch.setattr(gen, "check_layout", layout)
    r = _run(tmp_path)
    assert fake["landscape"] == []  # render_reel s orientation="landscape" se nezavolal
    assert calls.count("landscape") == 3
    assert r.ok and all(v.ok and v.video and v.video_landscape is None and not v.errors for v in r.variants)
    for v in r.variants:
        assert v.warnings == ["video na šířku vynecháno: rozvržení: scene:s-hook: mimo bezpečnou zónu"]
    assert not list((tmp_path / "out").glob("variant-*/*-sirka.mp4"))


def test_pad_kontroly_rozvrzeni_sirky_nevyradi_variantu(tmp_path, fake, monkeypatch):
    def layout(card, template, orientation="portrait"):
        if orientation == "landscape":
            raise RuntimeError("Chromium spadl")
        return []
    monkeypatch.setattr(gen, "check_layout", layout)
    r = _run(tmp_path)
    assert fake["landscape"] == []
    assert r.ok and all(v.ok and v.video and v.video_landscape is None for v in r.variants)
    assert all(any("rozvržení" in w and "kontrola rozvržení selhala" in w and "Chromium spadl" in w
                   for w in v.warnings) for v in r.variants)


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
    assert len(fake["calls"]) == 4 and "9999" in fake["calls"][1]


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
    # karta serveru v testu předkladatele nemá: pole kdo od modelu zmizí (zásah navíc, tady nás nezajímá)
    notes = [n for n in json.loads((d / "normalize.json").read_text(encoding="utf-8")) if not n.startswith("kdo")]
    assert len(notes) == 2
    # bez zásahu se normalize.json zapíše prázdný
    assert json.loads((tmp_path / "out" / "variant-1" / "normalize.json").read_text(encoding="utf-8")) == []


def test_zasah_normalizace_prezije_opravu(tmp_path, fake, monkeypatch):
    # 1. kontrola rozdělí hook.big a variantu vyřadí jiná chyba; oprava jede nad upravenou kartou
    # a 2. kontrola už nic nezmění: záznam zásahu z 1. kontroly musí v normalize.json zůstat
    def prepare(wd):
        d = wd / "variant-2"
        card = json.loads((d / "card.json").read_text(encoding="utf-8"))
        card["hook"] = dict(card["hook"], big="800 mil. Kč", unit="")
        card["ct"] = 999
        (d / "card.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    fake["prepare"] = prepare
    real = gen.run_claude

    def run_claude(prompt, **kw):
        r = real(prompt, **kw)
        if len(fake["calls"]) == 2:  # oprava: model opraví jen ct
            d = Path(kw["workdir"]) / "variant-2"
            card = json.loads((d / "card.json").read_text(encoding="utf-8"))
            card["ct"] = 311
            (d / "card.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
        return r
    monkeypatch.setattr(gen, "run_claude", run_claude)
    r = _run(tmp_path, render=False)
    assert len(fake["calls"]) == 2 and r.variants[1].ok, r.variants[1].errors
    notes = [n for n in json.loads((tmp_path / "out" / "variant-2" / "normalize.json").read_text(encoding="utf-8"))
             if not n.startswith("kdo")]
    assert len(notes) == 1 and "800 mil. Kč" in notes[0]


def test_beh_nic_nezapsal(tmp_path, fake):
    # Review Focus 2: oprava skončí rc 0, ale soubory varianty nezmění (podvržený model při opravě nic nepíše)
    def bad(wd):
        card = json.loads((wd / "variant-3" / "card.json").read_text(encoding="utf-8"))
        card["ct"] = 999
        (wd / "variant-3" / "card.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    fake["prepare"] = bad
    r = _run(tmp_path, render=False)
    assert len(fake["calls"]) == 4
    assert "běh modelu nic nezapsal" in r.variants[2].errors and not r.variants[2].ok
    assert all("běh modelu nic nezapsal" not in v.errors for v in r.variants[:2])


def _fake_bin(monkeypatch, **env):
    import sys
    monkeypatch.setenv("CLAUDE_BIN", f"{sys.executable} {Path(__file__).parent / 'fake_claude.py'}")
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    for k, val in env.items():
        monkeypatch.setenv(k, val)


def test_beh_nic_nezapsal_skutecny_proces(tmp_path, monkeypatch):
    # stejný projev jako chyba D1 (Edit nepovolený): rc 0, žádný soubor; nesmí projít potichu
    _fake_bin(monkeypatch, FAKE_CLAUDE_NOWRITE="1")
    r = _run(tmp_path, render=False)
    assert not r.ok and len(r.runs) == 4
    assert all("běh modelu nic nezapsal" in v.errors for v in r.variants)


def test_env_chyba_bez_opravy(tmp_path, monkeypatch):
    from hlidac_reels.generator.claude import EnvError
    _fake_bin(monkeypatch, FAKE_CLAUDE_ERROR="1")
    with pytest.raises(EnvError) as e:
        _run(tmp_path, render=False)
    assert "limit" in e.value.reason
    log = (tmp_path / "out" / "claude.log").read_text(encoding="utf-8")
    assert log.count("=== běh ===") == 1  # oprava se nespustila


# --- Task 5: stav úlohy (state.json), pokračování, rozpočet běhů ---

def _state(tmp_path) -> dict:
    return json.loads((tmp_path / "out" / "state.json").read_text(encoding="utf-8"))


def _copy_variant(wd, n):
    shutil.copytree(GEN / f"variant-{n}", Path(wd) / f"variant-{n}", dirs_exist_ok=True)


def _env_run(prompt, kw, fake, result="Claude AI usage limit reached"):
    fake["calls"].append(prompt)
    return ClaudeRun(ok=False, returncode=1, timed_out=False, log_path=Path(kw["workdir"]) / "claude.log",
                     kind="env", result=result)


def test_pokracovani_nerenderuje_platne(tmp_path, fake, monkeypatch):
    # 1. vydání: varianta 3 chybí a rozpočet (zúžený na 1 běh) dojde; 1 a 2 se vyrenderují
    fake["skip"] = {"variant-3"}
    monkeypatch.setattr(gen, "BUDGET_RUNS", 1)
    r1 = _run(tmp_path)
    assert [v.ok for v in r1.variants] == [True, True, False] and len(fake["calls"]) == 1
    st = _state(tmp_path)
    assert [st["variants"][k]["state"] for k in ("1", "2", "3")] == ["vyrenderovana", "vyrenderovana", "chybi"]
    assert st["model_runs"] == 1
    # 2. vydání se stejným vstupem: jen cílený běh nad variantou 3, platné se znovu nerenderují
    monkeypatch.setattr(gen, "BUDGET_RUNS", 4)
    real = gen.run_claude

    def run_claude(prompt, **kw):
        r = real(prompt, **kw)
        _copy_variant(kw["workdir"], 3)
        return r
    monkeypatch.setattr(gen, "run_claude", run_claude)
    fake["renders"].clear()
    fake["landscape"].clear()
    r2 = _run(tmp_path)
    assert len(fake["calls"]) == 2
    fix = fake["calls"][1]
    assert "variant-3" in fix and "variant-1" not in fix and "variant-2" not in fix
    assert fake["renders"] == ["template_v3"] and fake["landscape"] == ["template_v3"]
    assert [v.ok for v in r2.variants] == [True, True, True] and r2.ok
    assert all(v.video and v.video.exists() and v.video_landscape for v in r2.variants)
    assert r2.variants[0].caption  # popisek hotové varianty se načte znovu
    assert _state(tmp_path)["model_runs"] == 2


def test_pokracovani_bez_videa_renderuje_znovu(tmp_path, fake):
    _run(tmp_path)
    (tmp_path / "out" / "variant-2" / "reel-311-cesta.mp4").unlink()
    fake["renders"].clear()
    r = _run(tmp_path)
    assert len(fake["calls"]) == 1 and fake["renders"] == ["template_v3"] and r.ok


def test_jiny_vstup_zacne_znova(tmp_path, fake):
    # Review Focus 1: změněná karta webu = stará platná varianta se nesmí použít
    _run(tmp_path)
    assert _state(tmp_path)["model_runs"] == 1
    (tmp_path / "out" / "variant-1" / "normalize.json").write_text('["starý zásah"]', encoding="utf-8")
    card = load_fixture("card-1-v2.json")
    card["title"] = str(card.get("title", "")) + " (upraveno)"
    fake["skip"] = {"variant-3"}  # nové generování variantu 3 nenapíše
    fake["renders"].clear()
    r = _run(tmp_path, card_v2=card)
    assert len(fake["calls"]) >= 2 and "OPRAVA:" not in fake["calls"][1]  # znovu celá sada
    assert not r.variants[2].ok  # stará varianta 3 je pryč
    assert fake["renders"] == ["template_v2", "template_v3"]
    assert json.loads((tmp_path / "out" / "variant-1" / "normalize.json").read_text(encoding="utf-8")) == []
    st = _state(tmp_path)
    assert st["model_runs"] == len(fake["calls"]) - 1 and st["variants"]["3"]["state"] == "chybi"


def test_jiny_data_date_pokracuje(tmp_path, fake):
    # server skládá card_v2 při každém claimu a data_date = dnešek: změna data nesmí restartovat úlohu
    c1 = load_fixture("card-1-v2.json")
    c1["data_date"] = "2026-11-23"
    _run(tmp_path, card_v2=c1)
    assert len(fake["calls"]) == 1 and len(fake["renders"]) == 3
    c2 = dict(c1, data_date="2026-11-24")
    r = _run(tmp_path, card_v2=c2)
    assert len(fake["calls"]) == 1 and len(fake["renders"]) == 3  # žádný nový běh modelu ani render
    assert r.ok and _state(tmp_path)["model_runs"] == 1


def test_jina_verze_promptu_zacne_znova(tmp_path, fake, monkeypatch):
    _run(tmp_path, render=False)
    monkeypatch.setattr(gen, "_prompt_versions", lambda: {"v3.md": "jina"})
    _run(tmp_path, render=False)
    assert len(fake["calls"]) == 2 and "OPRAVA:" not in fake["calls"][1]


def test_rozpocet_4_behy(tmp_path, fake):
    def bad(wd):
        card = json.loads((wd / "variant-3" / "card.json").read_text(encoding="utf-8"))
        card["ct"] = 999
        (wd / "variant-3" / "card.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    fake["prepare"] = bad
    _run(tmp_path, render=False)
    r = _run(tmp_path, render=False)  # další vydání téže úlohy: rozpočet je vyčerpaný
    assert len(fake["calls"]) == 4
    assert r.ok and not r.variants[2].ok and r.variants[2].errors and r.runs == []
    st = _state(tmp_path)
    assert st["model_runs"] == 4 and st["variants"]["3"]["state"] == "vadna" and st["variants"]["3"]["errors"]


def test_oprava_jen_vadnych_s_chybou(tmp_path, fake, monkeypatch):
    # přesná chyba rozvržení (Task 1) jde do cíleného běhu doslova, jen u vadné varianty
    err = "scene:s-hook: přetéká o 3 px (hook.unit 7 znaků, title 41 znaků)"
    seen = {"n": 0}

    def layout(card, template, orientation="portrait"):
        if card.get("format") == "pn" and orientation == "portrait":
            seen["n"] += 1
            if seen["n"] == 1:
                return [err]
        return []
    monkeypatch.setattr(gen, "check_layout", layout)
    real = gen.run_claude

    def run_claude(prompt, **kw):
        r = real(prompt, **kw)
        if "OPRAVA:" in prompt:  # model kartu přepíše (stejný obsah s jiným odsazením)
            d = Path(kw["workdir"]) / "variant-3"
            card = json.loads((d / "card.json").read_text(encoding="utf-8"))
            (d / "card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
        return r
    monkeypatch.setattr(gen, "run_claude", run_claude)
    r = _run(tmp_path)
    assert len(fake["calls"]) == 2
    fix = fake["calls"][1]
    assert err in fix and "variant-3" in fix and "variant-2" not in fix and "variant-1" not in fix
    assert all(v.ok for v in r.variants)


def test_env_chyba_v_oprave_zachova_platne(tmp_path, fake, monkeypatch):
    # chyba prostředí v cíleném běhu: EnvError ven, hotová videa zůstanou a další vydání pokračuje
    from hlidac_reels.generator.claude import EnvError
    fake["skip"] = {"variant-3"}
    real = gen.run_claude
    mode = {"env": True}

    def run_claude(prompt, **kw):
        if "OPRAVA:" in prompt and mode["env"]:
            return _env_run(prompt, kw, fake)
        r = real(prompt, **kw)
        if "OPRAVA:" in prompt:
            _copy_variant(kw["workdir"], 3)
        return r
    monkeypatch.setattr(gen, "run_claude", run_claude)
    with pytest.raises(EnvError):
        _run(tmp_path)
    assert fake["renders"] == ["template_v2", "template_v3"]
    st = _state(tmp_path)
    assert st["model_runs"] == 1  # běh, který kvůli prostředí nic nezapsal, rozpočet nespálí
    assert [st["variants"][k]["state"] for k in ("1", "2")] == ["vyrenderovana", "vyrenderovana"]
    mode["env"] = False
    fake["renders"].clear()
    r = _run(tmp_path)
    assert fake["renders"] == ["template_v3"]
    assert len(fake["calls"]) == 3 and "OPRAVA:" in fake["calls"][2]
    assert r.ok and all(v.ok for v in r.variants)


def test_env_chyba_pri_generovani_zopakuje_generovani(tmp_path, fake, monkeypatch):
    from hlidac_reels.generator.claude import EnvError
    real = gen.run_claude
    mode = {"env": True}

    def run_claude(prompt, **kw):
        if mode["env"]:
            return _env_run(prompt, kw, fake, result="limit")
        return real(prompt, **kw)
    monkeypatch.setattr(gen, "run_claude", run_claude)
    with pytest.raises(EnvError):
        _run(tmp_path, render=False)
    assert _state(tmp_path)["model_runs"] == 0
    mode["env"] = False
    r = _run(tmp_path, render=False)
    assert "OPRAVA:" not in fake["calls"][1] and r.ok and _state(tmp_path)["model_runs"] == 1


def test_platna_bez_renderu_se_pri_renderu_zkontroluje(tmp_path, fake, monkeypatch):
    # varianta platná bez kontroly rozvržení (render=False) se před renderem zkontroluje
    _run(tmp_path, render=False)
    assert _state(tmp_path)["variants"]["2"]["state"] == "platna"
    checked = []
    monkeypatch.setattr(gen, "check_layout",
                        lambda card, template, orientation="portrait": checked.append(orientation) or [])
    r = _run(tmp_path)
    assert len(fake["calls"]) == 1 and checked.count("portrait") == 3 and r.ok and all(v.video for v in r.variants)


# --- F3: fáze práce (on_stage) a zrušení (should_stop) ---

def test_generate_hlasi_faze(tmp_path, fake):
    # varianty 2 a 3 chybí: tři cílené běhy nad oběma, varianta 1 se vyrenderuje hned po první kontrole
    fake["prepare"] = lambda wd: (shutil.rmtree(wd / "variant-2"), shutil.rmtree(wd / "variant-3"))
    stages: list = []
    _run(tmp_path, on_stage=lambda s, n: stages.append((s, n)), should_stop=lambda: False)
    assert stages == [("model", None), ("kontrola", None), ("render", "varianta 1")] + \
        [("oprava", "varianty 2, 3"), ("kontrola", None)] * 3
    # jedna vadná varianta: číslo v jednotném čísle
    fake["prepare"] = lambda wd: shutil.rmtree(wd / "variant-3")
    stages.clear()
    _run(tmp_path, out=tmp_path / "o2", render=False, on_stage=lambda s, n: stages.append((s, n)))
    assert stages == [("model", None), ("kontrola", None)] + [("oprava", "varianta 3"), ("kontrola", None)] * 3


def test_generate_cancelled_pred_renderem(tmp_path, fake):
    stages: list = []
    with pytest.raises(gen.Cancelled):
        _run(tmp_path, on_stage=lambda s, n: stages.append((s, n)), should_stop=lambda: len(fake["calls"]) >= 1)
    assert fake["renders"] == [] and fake["landscape"] == []
    st = _state(tmp_path)
    assert st["model_runs"] == 1 and [st["variants"][k]["state"] for k in ("1", "2", "3")] == ["platna"] * 3
    assert stages == [("model", None), ("kontrola", None)]
    assert not (tmp_path / "out" / "vysledek.json").exists()
    # další vydání téže úlohy pokračuje ze state.json: bez běhu modelu jen render
    r = _run(tmp_path)
    assert len(fake["calls"]) == 1 and r.ok and fake["renders"] == ["template_v2", "template_v3", "template_v3"]


def test_render_selhal_platne_cekaji_na_render(tmp_path, fake, monkeypatch):
    # platné varianty, jejichž render selhal (Chromium, ffmpeg): další vydání je jen vyrenderuje bez modelu
    def broken_render(card, out_path, **kw):
        raise RuntimeError("Chromium spadl")
    monkeypatch.setattr(gen, "render_reel", broken_render)
    r = _run(tmp_path)
    assert not r.ok and r.unrendered == [1, 2, 3]
    assert [_state(tmp_path)["variants"][k]["state"] for k in ("1", "2", "3")] == ["platna"] * 3
    # render zase jede: bez běhu modelu, nic nečeká
    monkeypatch.setattr(gen, "render_reel", fake["render_reel"])
    r = _run(tmp_path)
    assert r.ok and r.unrendered == [] and len(fake["calls"]) == 1


def test_generate_cancelled_pred_modelem(tmp_path, fake):
    with pytest.raises(gen.Cancelled):
        _run(tmp_path, should_stop=lambda: True)
    assert fake["calls"] == [] and _state(tmp_path)["model_runs"] == 0


# --- F5: otisk vyrenderované karty, strop renderu, log na vydání ---

def _rendered_variants(fake) -> list[str]:
    """Složky variant, pro které proběhl render (na výšku i na šířku), v pořadí volání."""
    return [p.parent.name for p in fake["paths"]]


def _retitle(tmp_path, n: int, title: str) -> None:
    d = tmp_path / "out" / f"variant-{n}"
    card = json.loads((d / "card.json").read_text(encoding="utf-8"))
    card["title"] = title
    (d / "card.json").write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")


def test_pokracovani_vyrenderovana_se_zmenenou_kartou_renderuje_znovu(tmp_path, fake):
    # karta vyrenderované varianty se změnila (ruční úprava, zásah modelu): staré video ke kartě nepatří
    _run(tmp_path)
    assert sorted(set(_rendered_variants(fake))) == ["variant-1", "variant-2", "variant-3"]
    _retitle(tmp_path, 2, "Daň pro rafinerie: výborům zbylo 30 dní místo 60")
    fake["paths"].clear()
    r = _run(tmp_path)
    assert _rendered_variants(fake) == ["variant-2", "variant-2"]  # na výšku a na šířku, jen varianta 2
    assert len(fake["calls"]) == 1 and r.ok and all(v.ok for v in r.variants)
    st = _state(tmp_path)["variants"]["2"]
    assert st["state"] == "vyrenderovana"
    assert st["card_sha"] == gen._sha(tmp_path / "out" / "variant-2" / "card.json")


def _render_fails_for(fake, monkeypatch, folder: str) -> list[Path]:
    """Podvrhne render, který u jedné varianty vždy spadne (prostředí); vrací seznam jejích pokusů."""
    tries: list[Path] = []

    def render_reel(card, out_path, **kw):
        if Path(out_path).parent.name == folder:
            tries.append(Path(out_path))
            raise RuntimeError("Chromium spadl")
        return fake["render_reel"](card, out_path, **kw)
    monkeypatch.setattr(gen, "render_reel", render_reel)
    return tries


def test_render_selze_dvakrat_varianta_vyrazena(tmp_path, fake, monkeypatch):
    # Review Focus 4: render padá kvůli prostředí jen u varianty 2; po RENDER_TRIES pokusech varianta skončí
    # ve stavu render_selhal bez opravného běhu modelu a další vydání na její render nečekají
    assert gen.RENDER_TRIES == 2 and gen.RENDER_FAILED == "render_selhal"
    tries = _render_fails_for(fake, monkeypatch, "variant-2")
    r1 = _run(tmp_path)
    assert len(tries) == 1 and r1.ok and r1.unrendered == [2]
    st = _state(tmp_path)["variants"]["2"]
    assert st["state"] == "platna" and st["render_fails"] == 1
    r2 = _run(tmp_path)
    assert len(tries) == 2 and r2.ok and r2.unrendered == []
    v = r2.variants[1]
    assert not v.ok and v.errors and v.errors[0].startswith("render selhal 2×") and "Chromium spadl" in v.errors[0]
    assert len(fake["calls"]) == 1 and r2.model_runs == 1 and r2.runs == []  # žádný opravný běh
    st = _state(tmp_path)
    assert st["model_runs"] == 1
    assert st["variants"]["2"]["state"] == "render_selhal" and st["variants"]["2"]["render_fails"] == 2
    assert st["variants"]["2"]["errors"] == v.errors
    r3 = _run(tmp_path)
    assert len(tries) == 2 and len(fake["calls"]) == 1 and r3.unrendered == [] and r3.ok
    assert not r3.variants[1].ok and r3.variants[1].errors[0].startswith("render selhal 2×")
    assert [v.ok for v in r3.variants] == [True, False, True]


def test_neuspesne_rendery_se_nuluji_s_novou_kartou(tmp_path, fake, monkeypatch):
    # počet neúspěšných renderů patří ke kartě: změněná karta dostane znovu RENDER_TRIES pokusů
    tries = _render_fails_for(fake, monkeypatch, "variant-2")
    _run(tmp_path)
    assert _state(tmp_path)["variants"]["2"]["render_fails"] == 1
    _retitle(tmp_path, 2, "Daň pro rafinerie: výborům zbylo 30 dní místo 60")
    r = _run(tmp_path)
    st = _state(tmp_path)["variants"]["2"]
    assert len(tries) == 2 and st["state"] == "platna" and st["render_fails"] == 1 and r.unrendered == [2]


def test_kazde_vydani_vlastni_log(tmp_path, fake, monkeypatch):
    # claude.log patří jednomu vydání: log minulého vydání se přesune do claude-1.log
    real = gen.run_claude

    def run_claude(prompt, **kw):  # jako skutečný run_claude: běhy připisuje na konec logu
        log = Path(kw["workdir"]) / "claude.log"
        before = log.read_text(encoding="utf-8") if log.is_file() else ""
        r = real(prompt, **kw)
        log.write_text(before + f"=== běh {len(fake['calls'])} ===\n", encoding="utf-8")
        return r
    monkeypatch.setattr(gen, "run_claude", run_claude)
    _run(tmp_path, render=False)
    assert len(fake["calls"]) == 1
    # jiný vstup: nové generování ve stejné složce (varianta 2 má jiný formát, vydání má i opravné běhy)
    _run(tmp_path, render=False, autopilot=True)
    assert len(fake["calls"]) > 2
    out = tmp_path / "out"
    first = (out / "claude-1.log").read_text(encoding="utf-8")
    last = (out / "claude.log").read_text(encoding="utf-8")
    assert first == "=== běh 1 ===\n"
    assert "=== běh 1 ===" not in last and all(f"=== běh {n} ===" in last for n in range(2, len(fake["calls"]) + 1))
    assert sorted(p.name for p in out.glob("claude-*.log")) == ["claude-1.log"]


def test_oprava_zmeni_kartu_vyrenderovane_varianty(tmp_path, fake, monkeypatch):
    # opravný běh nad vadnou variantou 3 přepíše i card.json už vyrenderované varianty 2: ta se znovu zkontroluje
    # a vyrenderuje, jinak by worker nahrál novou kartu se starým videem
    def bad(wd):
        card = json.loads((wd / "variant-3" / "card.json").read_text(encoding="utf-8"))
        card["reasons"].append({"key": "p90_passed", "label": "x", "points": 1})
        (wd / "variant-3" / "card.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    fake["prepare"] = bad
    title = "Daň pro rafinerie: výborům zbylo 30 dní místo 60"
    real = gen.run_claude

    def run_claude(prompt, **kw):
        r = real(prompt, **kw)
        if "OPRAVA:" in prompt:  # model opraví variantu 3 a mimochodem přepíše kartu varianty 2
            _copy_variant(kw["workdir"], 3)
            _retitle(tmp_path, 2, title)
        return r
    monkeypatch.setattr(gen, "run_claude", run_claude)
    portrait = []

    def render_reel(card, out_path, **kw):
        fake["render_reel"](card, out_path, **kw)
        Path(out_path).write_text(card.title, encoding="utf-8")  # video „nese“ titulek své karty
        if kw.get("orientation", "portrait") == "portrait":
            portrait.append(Path(out_path).parent.name)
    monkeypatch.setattr(gen, "render_reel", render_reel)
    r = _run(tmp_path)
    assert len(fake["calls"]) == 2 and "variant-2" not in fake["calls"][1]
    assert portrait.count("variant-2") == 2 and portrait.count("variant-3") == 1
    assert r.ok and all(v.ok for v in r.variants)
    v2 = r.variants[1]
    assert v2.video.read_text(encoding="utf-8") == title and v2.video_landscape.read_text(encoding="utf-8") == title
    st = _state(tmp_path)["variants"]["2"]
    assert st["state"] == "vyrenderovana" and st["render_fails"] == 0
    assert st["card_sha"] == gen._sha(tmp_path / "out" / "variant-2" / "card.json")


def test_oprava_rozbije_kartu_vyrenderovane_varianty(tmp_path, fake, monkeypatch):
    # opravný běh nad variantou 3 kartu vyrenderované varianty 2 rozbije: varianta 2 jde do dalšího opravného běhu
    def bad(wd, n):
        card = json.loads((wd / f"variant-{n}" / "card.json").read_text(encoding="utf-8"))
        card["reasons"].append({"key": "p90_passed", "label": "x", "points": 1})
        (wd / f"variant-{n}" / "card.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    fake["prepare"] = lambda wd: bad(wd, 3)
    real = gen.run_claude

    def run_claude(prompt, **kw):
        r = real(prompt, **kw)
        wd = Path(kw["workdir"])
        if "OPRAVA:" in prompt and "variant-3" in prompt:
            _copy_variant(wd, 3)
            bad(wd, 2)
        elif "OPRAVA:" in prompt:
            _copy_variant(wd, 2)
        return r
    monkeypatch.setattr(gen, "run_claude", run_claude)
    r = _run(tmp_path)
    assert len(fake["calls"]) == 3
    assert "variant-2" in fake["calls"][2] and "p90_passed" in fake["calls"][2] and "variant-3" not in fake["calls"][2]
    assert r.ok and all(v.ok for v in r.variants)
    assert _rendered_variants(fake).count("variant-2") == 4  # dvakrát na výšku a na šířku
    assert _state(tmp_path)["variants"]["2"]["state"] == "vyrenderovana"


# --- atribuce (docs/reels-citace-bezpecne.md): chyba jde do opravného běhu, autopilot bez autora PN nic nevyrobí ---

def test_chyba_atribuce_spusti_opravu(tmp_path, fake, monkeypatch):
    def prepare(wd):
        d = wd / "variant-2"
        text = (d / "caption.txt").read_text(encoding="utf-8")
        (d / "caption.txt").write_text("Ministryně řekla, že lhůta stačí.\n\n" + text, encoding="utf-8")
    fake["prepare"] = prepare
    real = gen.run_claude

    def run_claude(prompt, **kw):
        r = real(prompt, **kw)
        if "OPRAVA:" in prompt:  # oprava: model větu se slovesem mluvení odstraní
            assert "R1 popisek" in prompt
            d = Path(kw["workdir"]) / "variant-2"
            text = (d / "caption.txt").read_text(encoding="utf-8")
            (d / "caption.txt").write_text(text.replace("Ministryně řekla, že lhůta stačí.\n\n", ""),
                                           encoding="utf-8")
        return r
    monkeypatch.setattr(gen, "run_claude", run_claude)
    r = _run(tmp_path, render=False)
    assert len(fake["calls"]) == 2 and r.variants[1].ok, r.variants[1].errors


def test_autopilot_pn_bez_autora_nevyrobi(tmp_path, fake):
    info = [{"cislo": 2376, "osoba": None, "klub": None, "has_text": True}]
    r = _run(tmp_path, render=False, autopilot=True, focus_pn=[2376], focus_info=info)
    assert not r.ok and fake["calls"] == []
    assert all("v evidenci psp.cz chybí" in v.errors[0] for v in r.variants)
