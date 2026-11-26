"""Generátor a formát `pribeh` (šablona template_pn_v1): úloha s jedním PN = jedna varianta, výsledek hlasování
ze serveru, kontrola pravidel šablony (citace se zdrojem, verdikt jen s hlasováním a odkazem, AI označená)."""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

import hlidac_reels.generator.generate as gen
from conftest import load_fixture
from hlidac_reels import messages
from hlidac_reels.generator import formats
from hlidac_reels.generator.claude import ClaudeRun, build_prompt, prepare_workdir, write_focus
from hlidac_reels.generator.normalize import normalize
from hlidac_reels.generator.validate import validate_variant
from hlidac_reels.media import VideoInfo

CARD = "pribeh-pn-1813.json"
CAPTION = ("PN 1813 podala Michaela Šebelová (STAN). Ruší strop 5 % na příspěvky pacientským organizacím.\n\n"
           "Sněmovna zrušení stropu přijala, druhou část ne.\n\n"
           "Hodnotíme postup, ne politický obsah.\n\n#Sněmovna #pozměňovák #cosepece")


def _votes() -> list[dict]:
    return [{"cislo": 400, "schuze": 34, "datum": "2026-11-04", "vysledek": "prijato", "pro": 145, "proti": 0,
             "zdrzel": 0, "nehlasoval": 1, "pismeno": "B", "varianta": None,
             "url_psp": "https://www.psp.cz/sqw/hlasy.sqw?g=88989",
             "kluby": [{"klub": "ANO", "pro": 70, "proti": 0, "zdrzel": 0}]},
            {"cislo": 401, "schuze": 34, "datum": "2026-11-04", "vysledek": "zamitnuto", "pro": 49, "proti": 95,
             "zdrzel": 0, "nehlasoval": 2, "pismeno": "B", "varianta": None,
             "url_psp": "https://www.psp.cz/sqw/hlasy.sqw?g=88990", "kluby": []}]


def _info(vysledek="default", **change) -> dict:
    info = {"cislo": 1813, "osoba": "Michaela Šebelová", "cas": "2026-09-22 14:57:00",
            "titulek": "Ruší strop příspěvků pacientským organizacím",
            "url_psp": "https://www.psp.cz/sqw/sd.sqw?cd=1813&o=10", "id_osoba": 1, "klub": "STAN",
            "klub_ke_dni": "2026-09-22", "has_text": True,
            "vysledek": {"stav": "castecne", "pismena": "B", "stanovisko": None, "hlasovani": _votes()}
            if vysledek == "default" else vysledek}
    info.update(change)
    return info


def _card() -> dict:
    return copy.deepcopy(load_fixture(CARD))


KEYS = [r["key"] for r in load_fixture(CARD)["reasons"]]


def _check(card: dict, info: dict | None = None, caption: str = CAPTION) -> tuple[dict, list[str]]:
    """Normalizace a kontrola jako v generate._check."""
    info = _info() if info is None else info
    card, caption, _ = normalize(card, caption, fmt="pribeh", focus_info=[info])
    errs = validate_variant(card, caption, template="template_pn_v1", ct=235, reason_keys=KEYS, fmt="pribeh",
                            focus_pn=[1813], focus_info=[info])
    return card, errs


# --- výběr formátu ---

def test_jeden_pn_od_noveho_serveru_je_pribeh():
    assert formats.wants_pribeh([1813], [_info()])
    assert formats.wants_pribeh([1813], [_info(vysledek=None)])  # výsledek zatím není: příběh i tak


def test_ostatni_zadani_zustanou():
    old = _info()
    del old["vysledek"]
    assert not formats.wants_pribeh([1813], [old])  # starší server: template_pn_v1 by odmítl
    assert not formats.wants_pribeh([1813, 1814], [_info(), _info(cislo=1814)])  # společné video více PN
    assert not formats.wants_pribeh(None, [])  # celý tisk
    assert not formats.wants_pribeh([1813], [_info(has_text=False)])  # bez textu PN není z čeho citovat
    assert not formats.wants_pribeh([1813], [])
    assert "pribeh" not in formats.FORMATS  # /video formát pribeh nezadá


# --- kontrola a normalizace karty ---

def test_platna_karta_projde():
    card, errs = _check(_card())
    assert errs == []
    vys = card["pribeh"]["vysledek"]
    assert vys["stav"] == "castecne"
    assert [h["url"] for h in vys["hlasovani"]] == ["https://www.psp.cz/sqw/hlasy.sqw?g=88989",
                                                    "https://www.psp.cz/sqw/hlasy.sqw?g=88990"]
    # popis hlasování od modelu zůstane u téhož čísla hlasování, počty a kluby jsou ze serveru
    assert vys["hlasovani"][0]["o_cem"] == "návrh B bez bodu 2 čl. IV a bodu 2 čl. V"
    assert vys["hlasovani"][0]["kluby"] == [{"klub": "ANO", "pro": 70, "proti": 0, "zdrzel": 0}]
    assert [h["vysledek"] for h in vys["hlasovani"]] == ["přijato", "zamítnuto"]
    assert card["pn_autor"] == [{"cislo": 1813, "jmeno": "Michaela Šebelová", "klub": "STAN"}]


def test_verdikt_podle_serveru():
    card = _card()
    card["pribeh"]["vysledek"]["stav"] = "prijat"  # model napsal jiný verdikt než server
    card, errs = _check(card)
    assert errs == []
    vys = card["pribeh"]["vysledek"]
    assert vys["stav"] == "castecne" and "text" not in vys  # text k jinému verdiktu nepatří


def test_bez_vysledku_ze_serveru_verdikt_vypadne():
    card, errs = _check(_card(), _info(vysledek=None))
    assert errs == [] and "vysledek" not in card["pribeh"]
    ceka = _card()
    ceka["pribeh"]["vysledek"] = {"stav": "ceka", "text": "O návrhu se zatím nehlasovalo."}
    card, errs = _check(ceka, _info(vysledek=None))
    assert errs == [] and card["pribeh"]["vysledek"]["stav"] == "ceka"
    card, errs = _check(_card(), _info(vysledek={"stav": "neurceno", "pismena": None, "stanovisko": None,
                                                 "hlasovani": []}))
    assert errs == [] and "vysledek" not in card["pribeh"]


def test_server_doplni_vysledek_ktery_model_nenapsal():
    card = _card()
    del card["pribeh"]["vysledek"]
    card, errs = _check(card)
    assert errs == [] and card["pribeh"]["vysledek"]["stav"] == "castecne"
    assert card["pribeh"]["vysledek"]["hlasovani"][0]["o_cem"] == "návrh B"


def test_verdikt_bez_hlasovani_s_odkazem_odmitne():
    card = _card()
    card["pribeh"]["vysledek"]["hlasovani"] = []
    errs = validate_variant(card, CAPTION, template="template_pn_v1", ct=235, reason_keys=KEYS, fmt="pribeh",
                            focus_pn=[1813])
    assert any("hlasování" in e for e in errs)
    card = _card()
    card["pribeh"]["vysledek"]["hlasovani"][0]["url"] = "https://example.com/x"
    card["pribeh"]["vysledek"]["hlasovani"][1]["url"] = ""
    errs = validate_variant(card, CAPTION, template="template_pn_v1", ct=235, reason_keys=KEYS, fmt="pribeh",
                            focus_pn=[1813])
    assert any("hlasování" in e for e in errs)


def test_stav_jiny_nez_server_odmitne():
    # bez normalizace (oprava modelem po kontrole): kontrola sama porovná stav se serverem
    card = _card()
    card["pribeh"]["vysledek"]["stav"] = "prijat"
    errs = validate_variant(card, CAPTION, template="template_pn_v1", ct=235, reason_keys=KEYS, fmt="pribeh",
                            focus_pn=[1813], focus_info=[_info()])
    assert any("server" in e for e in errs)


@pytest.mark.parametrize("field", ["citace", "zdroj", "po"])
def test_zmena_bez_citace_nebo_zdroje_odmitne(field):
    card = _card()
    card["pribeh"]["co"][field] = ""
    _, errs = _check(card)
    assert any(e.startswith("pribeh: co") for e in errs)


def test_chybi_blok_nebo_autor_nebo_jine_pn():
    card = _card()
    del card["pribeh"]
    _, errs = _check(card)
    assert "chybí pole pribeh" in errs
    card = _card()
    card["pribeh"]["autor"] = {"jmeno": ""}
    _, errs = _check(card, _info(osoba=None))  # prázdné jméno doplní server; tady ho nezná ani server
    assert any("autor" in e for e in errs)
    card = _card()
    card["pribeh"]["cislo"] = 1814
    _, errs = _check(card)
    assert any("1813" in e for e in errs)


def test_znak_bez_zdroje_vypadne_a_ai_se_oznaci():
    card = _card()
    card["pribeh"]["znaky"][0]["zdroj"] = ""
    card["pribeh"]["znaky"][1]["ai"] = False  # typ ai
    card, errs = _check(card)
    assert errs == []
    znaky = card["pribeh"]["znaky"]
    assert len(znaky) == 2 and znaky[0]["typ"] == "ai" and znaky[0]["ai"] is True


def test_znak_ai_bez_stitku_odmitne_kontrola():
    card = _card()
    card["pribeh"]["znaky"][1]["ai"] = False
    errs = validate_variant(card, CAPTION, template="template_pn_v1", ct=235, reason_keys=KEYS, fmt="pribeh",
                            focus_pn=[1813])
    assert any("AI" in e for e in errs)


def test_dopad_bez_stitku_ai_jen_se_zdrojem():
    card = _card()
    card["pribeh"]["dopad"]["ai"] = False
    card["pribeh"]["dopad"]["zdroj"] = ""
    _, errs = _check(card)
    assert any("dopad" in e for e in errs)


def test_formát_a_popisek():
    card = _card()
    card["format"] = "pn"
    _, errs = _check(card)
    assert any("formát" in e for e in errs)
    _, errs = _check(_card(), caption="PN 1813 podala Michaela Šebelová (STAN).")  # bez závěrečné věty doplní
    assert errs == []
    _, errs = _check(_card(), caption="Video o pozměňovacím návrhu.\n\nHodnotíme postup, ne politický obsah.")
    assert any("popisek" in e for e in errs)


# --- podklady pro model ---

def test_podklady_a_prompt(tmp_path):
    prepare_workdir(tmp_path, 300)
    assert (tmp_path / "podklady" / "pribeh.md").is_file()
    vzor = json.loads((tmp_path / "podklady" / "vzor-pribeh.json").read_text(encoding="utf-8"))
    assert vzor["format"] == "pribeh" and vzor["ct"] != 300  # vzor z jiného tisku
    prompt = build_prompt(ct=235, formats=["pribeh"], workdir=tmp_path)
    assert "podklady/pribeh.md" in prompt and "variant-1/" in prompt and "variant-2" not in prompt
    assert "v2.md" not in prompt.split("Úkol:")[1]
    fix = build_prompt(ct=235, formats=["pribeh"], workdir=tmp_path, repair={1: ["pribeh: co nemá citaci"]})
    assert "OPRAVA" in fix and "pribeh.md" in fix


def test_zadani_s_vysledkem(tmp_path):
    write_focus(tmp_path, [1813], [_info()], outcome=True)
    text = (tmp_path / "podklady" / "zadani.md").read_text(encoding="utf-8")
    assert "PN 1813" in text and "přijat zčásti" in text
    assert "hlasování č. 400" in text and "https://www.psp.cz/sqw/hlasy.sqw?g=88989" in text
    write_focus(tmp_path, [1813], [_info(vysledek=None)], outcome=True)
    text = (tmp_path / "podklady" / "zadani.md").read_text(encoding="utf-8")
    assert "výsledek hlasování: zatím není" in text
    # video k více PN: zadání beze změny (výsledek se nepíše)
    write_focus(tmp_path, [1813], [_info()])
    assert "hlasování č." not in (tmp_path / "podklady" / "zadani.md").read_text(encoding="utf-8")


# --- generate(): jedna varianta template_pn_v1 ---

@pytest.fixture
def fake(monkeypatch):
    state = {"calls": [], "renders": [], "card": _card(), "caption": CAPTION}

    def run_claude(prompt, *, workdir, mcp_config, model, timeout_s=1200):
        state["calls"].append(prompt)
        d = Path(workdir) / "variant-1"
        d.mkdir(parents=True, exist_ok=True)
        (d / "card.json").write_text(json.dumps(state["card"], ensure_ascii=False), encoding="utf-8")
        (d / "caption.txt").write_text(state["caption"], encoding="utf-8")
        log = Path(workdir) / "claude.log"
        log.write_text("log", encoding="utf-8")
        return ClaudeRun(ok=True, returncode=0, timed_out=False, log_path=log)

    def render_reel(card, out_path, *, template="template", orientation="portrait", **kw):
        state["renders"].append((template, orientation, Path(out_path).name))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"mp4")

    def probe(p):
        if Path(p).name.endswith("-sirka.mp4"):
            return VideoInfo("h264", "aac", 1920, 1080, 30.0, 3)
        return VideoInfo("h264", "aac", 1080, 1920, 30.0, 3)

    monkeypatch.setattr(gen, "run_claude", run_claude)
    monkeypatch.setattr(gen, "render_reel", render_reel)
    monkeypatch.setattr(gen, "check_layout", lambda card, template, orientation="portrait": [])
    monkeypatch.setattr(gen, "probe", probe)
    return state


def _run(tmp_path, **kw):
    args = dict(ct=235, card_v2=load_fixture("card-1-v2.json"), reason_keys=KEYS, has_amendments=False,
                out=tmp_path / "out", mcp_config=tmp_path / "mcp.json", focus_pn=[1813], focus_info=[_info()])
    args.update(kw)
    return gen.generate(**args)


def test_generate_jedna_varianta_pribeh(tmp_path, fake):
    r = _run(tmp_path)
    assert r.ok and r.formats == ["pribeh"]
    assert [(v.n, v.template, v.format, v.ok) for v in r.variants] == [(1, "template_pn_v1", "pribeh", True)]
    assert fake["renders"] == [("template_pn_v1", "portrait", "reel-235-pribeh.mp4"),
                               ("template_pn_v1", "landscape", "reel-235-pribeh-sirka.mp4")]
    assert r.variants[0].video_landscape is not None
    task = fake["calls"][0].split("Úkol:")[1]
    assert len(fake["calls"]) == 1 and "pribeh.md" in task and "v3.md" not in task and "variant-2" not in task
    out = tmp_path / "out"
    assert json.loads((out / "tisk.json").read_text(encoding="utf-8"))["formats"] == ["pribeh"]
    assert "hlasování č. 400" in (out / "podklady" / "zadani.md").read_text(encoding="utf-8")
    saved = json.loads((out / "variant-1" / "card.json").read_text(encoding="utf-8"))
    assert saved["pribeh"]["vysledek"]["hlasovani"][0]["url"] == "https://www.psp.cz/sqw/hlasy.sqw?g=88989"


def test_generate_autopilot_tez_pribeh(tmp_path, fake):
    r = _run(tmp_path, autopilot=True)
    assert r.formats == ["pribeh"] and len(r.variants) == 1


def test_generate_vadna_karta_jde_do_opravy(tmp_path, fake):
    fake["card"]["pribeh"]["co"]["citace"] = ""
    r = _run(tmp_path, render=False)
    assert not r.ok and len(fake["calls"]) == gen.BUDGET_RUNS
    assert "OPRAVA" in fake["calls"][1] and "pribeh: co" in fake["calls"][1]


def test_generate_novy_vysledek_zacne_znovu(tmp_path, fake):
    # výsledek hlasování je v otisku vstupu: po hlasování úloha nepoužije kartu s „čeká“
    _run(tmp_path, render=False, focus_info=[_info(vysledek=None)])
    calls = len(fake["calls"])
    _run(tmp_path, render=False)
    assert len(fake["calls"]) == calls + 1 and "OPRAVA" not in fake["calls"][-1]


def test_generate_starsi_server_puvodni_sada(tmp_path, fake, monkeypatch):
    old = _info()
    del old["vysledek"]
    gen_dir = Path(__file__).parent / "fixtures" / "gen"

    def run_claude(prompt, *, workdir, mcp_config, model, timeout_s=1200):
        fake["calls"].append(prompt)
        for d in gen_dir.iterdir():
            shutil.copytree(d, Path(workdir) / d.name, dirs_exist_ok=True)
        log = Path(workdir) / "claude.log"
        log.write_text("log", encoding="utf-8")
        return ClaudeRun(ok=True, returncode=0, timed_out=False, log_path=log)
    monkeypatch.setattr(gen, "run_claude", run_claude)
    r = _run(tmp_path, render=False, focus_info=[old])
    assert r.formats == ["pn", "zmena"] and [v.template for v in r.variants][0] == "template_v2"


# --- texty v Telegramu ---

def test_popisky_v_telegramu():
    from hlidac_reels.queue import Reel
    fields = {f: None for f in Reel.__dataclass_fields__}
    reel = Reel(**dict(fields, id=5, ct=235, format="pribeh", template="template_pn_v1", set_id=9, variant=1,
                       caption="x"))
    assert messages.set_video_caption(reel) == "#9 · varianta 1 · příběh PN"
    assert "příběh PN" in messages.video_caption(reel)
    assert "varianta pribeh (příběh PN)" in messages.autopilot_done(reel, [1813])
    assert "Varianta 1 (příběh PN) nevznikla" in messages.autopilot_skipped([{"n": 1, "format": "pribeh",
                                                                               "errors": ["x"]}])


@pytest.mark.parametrize("name", ["pribeh-235.json", "pribeh-300.json"])
def test_vzory_pro_model_projdou_kontrolou(name):
    # vzor karty, ze kterého model opisuje tvar, musí sám projít pravidly šablony
    src = Path(gen.__file__).parent / "examples" / name
    card = json.loads(src.read_text(encoding="utf-8"))
    cislo = card["pribeh"]["cislo"]
    caption = f"PN {cislo} podal autor.\n\nHodnotíme postup, ne politický obsah.\n\n#a #b #c"
    errs = validate_variant(card, caption, template="template_pn_v1", ct=card["ct"],
                            reason_keys=[r["key"] for r in card["reasons"]], fmt="pribeh", focus_pn=[cislo])
    assert errs == []


# --- autor a klub ze serveru (klub ke dni podání, MCP vrací poslední klub) ---

def test_autor_a_klub_pribehu_ze_serveru():
    card = _card()
    card["pribeh"]["autor"] = {**card["pribeh"]["autor"], "jmeno": "M. Šebelová", "klub": "ANO"}
    card, _ = _check(card)
    assert card["pribeh"]["autor"]["jmeno"] == "Michaela Šebelová"
    assert card["pribeh"]["autor"]["klub"] == "STAN"


def test_klub_pribehu_zmizi_kdyz_ho_server_neuvadi():
    card = _card()
    card["pribeh"]["autor"] = {**card["pribeh"]["autor"], "klub": "ANO"}
    card, _ = _check(card, _info(klub=None))
    assert "klub" not in card["pribeh"]["autor"]
    assert card["pribeh"]["autor"]["jmeno"] == "Michaela Šebelová"
