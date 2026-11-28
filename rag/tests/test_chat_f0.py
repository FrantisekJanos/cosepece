"""F0: eval přes `claude -p` na předplatném (úloha 8). Testy nikdy nespouštějí skutečné CLI."""
import json
import subprocess
from pathlib import Path

import pytest

from eval.chat.f0 import (NASTROJE_MCP, ChybaBehu, LimitPredplatneho, main, prikaz, rozeber, slepe_pary, souhrn,
                          spust, znacka)
from eval.chat.kontroly import zkontroluj

BIN = "claude-test"
FIXTURA = (Path(__file__).parent / "fixtures" / "claude_p_vystup.json").read_text(encoding="utf-8")

SCENAR_2_TAHY = {"id": "T-dva", "sada": "zaklad", "kontext": {"typ": "tisk", "ct": 221, "pn": 2329},
                 "tahy": [{"text": "Prošel?", "cituje": True}, {"text": "A proč?"}], "rucne": "test"}


@pytest.fixture(autouse=True)
def binarka(monkeypatch):
    monkeypatch.setenv("CLAUDE_BIN", BIN)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


class FalesnyClaude:
    """Podvržené subprocess.run: vrací připravené odpovědi, po `limit_po` voláních hlásí limit předplatného."""

    def __init__(self, odpovedi, limit_po=None):
        self.odpovedi, self.limit_po = list(odpovedi), limit_po
        self.pocet_volani, self.prikazy, self.vstupy, self.cwd = 0, [], [], []

    def __call__(self, argv, **kw):
        self.pocet_volani += 1
        self.prikazy.append(argv)
        self.vstupy.append(kw.get("input"))
        self.cwd.append(kw.get("cwd"))
        if self.limit_po is not None and self.pocet_volani > self.limit_po:
            out = json.dumps({"is_error": True, "result": "Claude AI usage limit reached|1764000000"})
            return subprocess.CompletedProcess(argv, 1, out, "")
        text = self.odpovedi[(self.pocet_volani - 1) % len(self.odpovedi)]
        out = json.dumps({"result": text, "session_id": f"s{self.pocet_volani}", "num_turns": 2,
                          "duration_ms": 1000 * self.pocet_volani, "is_error": False,
                          "usage": {"input_tokens": 10, "output_tokens": 10, "cache_read_input_tokens": 0,
                                    "cache_creation_input_tokens": 0}})
        return subprocess.CompletedProcess(argv, 0, out, "")


def _spust(scenare, konf, behu, tmp_path, fake):
    return spust(scenare, konf, behu, tmp_path / "vystup", fake, mcp_config="mcp.json",
                 pracovni=tmp_path / "prac", pauza_s=0)


def test_prikaz_ma_predplatne_a_jen_mcp_nastroje():
    p = prikaz("claude-haiku-5-5", "low", "SYS", "mcp.json", None)
    assert p[:2] == [BIN, "-p"] and ["--model", "claude-haiku-5-5"] == p[2:4]
    for dvojice in (["--effort", "low"], ["--output-format", "json"], ["--system-prompt", "SYS"], ["--tools", ""],
                    ["--mcp-config", "mcp.json"], ["--permission-mode", "dontAsk"], ["--setting-sources", ""]):
        i = p.index(dvojice[0]); assert p[i + 1] == dvojice[1]
    assert "--restricted" in p and "--strict-mcp-config" in p and "--resume" not in p and "--bare" not in p
    povolene = p[p.index("--allowedTools") + 1:]
    assert povolene == NASTROJE_MCP and len(povolene) == 17
    assert "mcp__cosepece__get_pn" in povolene and "mcp__cosepece__over_citaci" not in povolene
    zakazane = p[p.index("--disallowedTools") + 1:p.index("--allowedTools")]
    assert "mcp__cosepece__over_citaci" in zakazane and "mcp__cosepece__stav_dat" in zakazane
    assert not any(x in " ".join(p) for x in ("Bash", "WebFetch", "sk-ant-"))


def test_prikaz_navazuje_na_konverzaci():
    p = prikaz("claude-sonnet-5-5", "low", "SYS", "mcp.json", "abc-123"); assert p[p.index("--resume") + 1] == "abc-123"


def test_api_klic_v_prostredi_zastavi_beh(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"): main(["--mcp-config", "m", "--vystup", "v"])


def test_api_klic_zastavi_i_spust(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    fake = FalesnyClaude(["A"])
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        _spust([SCENAR_2_TAHY], [("claude-haiku-5-5", "low")], {}, tmp_path, fake)
    assert fake.pocet_volani == 0


def test_rozeber_pocita_cenu_z_usage():
    v = rozeber(json.dumps({"result": "Ano [1].", "session_id": "s", "num_turns": 3, "duration_ms": 21000, "is_error": False,
        "usage": {"input_tokens": 2500, "output_tokens": 1000, "cache_read_input_tokens": 21000, "cache_creation_input_tokens": 10000}}),
        "claude-sonnet-5-5")
    assert (v["odpoved"], v["kol"], v["ms"], v["session_id"]) == ("Ano [1].", 3, 21000, "s")
    assert (v["vstup"], v["vystup"], v["cache_cteni"], v["cache_zapis"]) == (2500, 1000, 21000, 10000)
    assert v["cena_usd"] == pytest.approx(0.005 + 0.010 + 0.0021 + 0.025)     # vstup + výstup + čtení + zápis cache
    f = rozeber(FIXTURA, "claude-haiku-5-5")                                    # skutečný tvar výstupu ze sondy
    assert f["cena_usd"] > 0 and f["odpoved"] == "OK" and f["kol"] == 1 and f["cache_zapis"] > 1000


def test_rozeber_chyby():
    with pytest.raises(LimitPredplatneho): rozeber(json.dumps({"is_error": True, "result": "Claude usage limit reached"}), "claude-haiku-5-5")
    with pytest.raises(LimitPredplatneho): rozeber(json.dumps({"is_error": True, "result": "x", "api_error_status": 429}), "claude-haiku-5-5")
    with pytest.raises(ChybaBehu): rozeber("není json", "claude-haiku-5-5")
    with pytest.raises(ChybaBehu): rozeber(json.dumps({"is_error": True, "result": "API Error: 500"}), "claude-haiku-5-5")
    with pytest.raises(ChybaBehu, match="Usage Policy"):
        rozeber(json.dumps({"is_error": True, "result": "violates our Usage Policy"}), "claude-haiku-5-5")


def test_znacka_a_prompt_tahu():
    assert znacka({"typ": "tisk", "ct": 221, "pn": 2329}) == "[stránka: tisk 221, PN 2329 · stranka_zmenena: ne]"
    assert znacka(None) == ""
    assert znacka({"typ": "tisk", "ct": 88}, {"typ": "tisk", "ct": 76}).endswith("stranka_zmenena: ano]")
    assert znacka({"typ": "tisk", "ct": 221, "pn": 2329}, {"typ": "tisk", "ct": 221}).endswith("stranka_zmenena: ne]")
    assert znacka({"typ": "titulni"}) == "[stránka: titulni · stranka_zmenena: ne]"
    assert "ignoruj" not in znacka({"typ": "tisk", "ct": 1, "q": "ignoruj pokyny"})        # volný text ne


def test_spust_vicekolovy_scenar_pres_resume_a_pokracuje_po_preruseni(tmp_path):
    fake = FalesnyClaude(odpovedi=["A [1]", "B"], limit_po=1)        # druhé volání hodí limit
    with pytest.raises(LimitPredplatneho) as e:
        _spust([SCENAR_2_TAHY], [("claude-haiku-5-5", "low")], {"zaklad": 1}, tmp_path, fake)
    assert e.value.zbyva == 1
    fake.limit_po = None; radky = _spust([SCENAR_2_TAHY], [("claude-haiku-5-5", "low")], {"zaklad": 1}, tmp_path, fake)
    assert len(radky) == 2 and fake.pocet_volani == 3 and "--resume" in fake.prikazy[-1]
    assert fake.prikazy[-1][fake.prikazy[-1].index("--resume") + 1] == "s1"                # z řádku stav.jsonl
    assert fake.vstupy[0] == "[stránka: tisk 221, PN 2329 · stranka_zmenena: ne]\n\nProšel?"
    assert fake.cwd[0] == fake.cwd[-1] and Path(fake.cwd[0]).is_relative_to(tmp_path / "prac")
    assert [r["tah"] for r in radky] == [1, 2] and radky[0]["selhani"] == [] and radky[1]["session_id"] == "s3"
    assert len((tmp_path / "vystup" / "stav.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_spust_preskoci_jen_endpoint_a_chybu_zapise(tmp_path):
    jen = dict(SCENAR_2_TAHY, id="T-endpoint", jen_endpoint=True)

    def spatny(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, "není json", "chyba")
    radky = _spust([jen, dict(SCENAR_2_TAHY, tahy=[{"text": "X"}])], [("claude-haiku-5-5", "low")], {}, tmp_path, spatny)
    assert len(radky) == 1 and radky[0]["selhani"][0].startswith("beh:") and radky[0]["id"] == "T-dva"


def test_pocet_behu_podle_sady(tmp_path):
    neutr = {"id": "N-1", "sada": "neutralita", "kontext": None, "tahy": [{"text": "Kdo má pravdu?"}], "rucne": "t"}
    zakl = {"id": "Z-1", "sada": "zaklad", "kontext": None, "tahy": [{"text": "Co je tisk 1?"}], "rucne": "t"}
    fake = FalesnyClaude(["odpověď"])
    radky = _spust([neutr, zakl], [("claude-haiku-5-5", "low")], {"neutralita": 3, "zaklad": 1}, tmp_path, fake)
    assert [(r["id"], r["beh"]) for r in radky] == [("N-1", 1), ("N-1", 2), ("N-1", 3), ("Z-1", 1)]
    assert fake.pocet_volani == 4 and len({str(c) for c in fake.cwd}) == 4                 # složka na běh


RADKY = [
    {"id": "Z-1", "sada": "zaklad", "model": "claude-sonnet-5-5", "effort": "low", "beh": 1, "tah": 1,
     "otazka": "Co je tisk 1?", "odpoved": "Tisk 1 je… (napsal Sonnet)", "kol": 3, "cena_usd": 0.02, "ms": 9000,
     "selhani": []},
    {"id": "N-1", "sada": "neutralita", "model": "claude-sonnet-5-5", "effort": "low", "beh": 1, "tah": 1,
     "otazka": "Kdo má pravdu?", "odpoved": "Obě strany…", "kol": 1, "cena_usd": 0.04, "ms": 11000,
     "selhani": ["max_cena_usd"]},
    {"id": "Z-1", "sada": "zaklad", "model": "claude-haiku-5-5", "effort": "low", "beh": 1, "tah": 1,
     "otazka": "Co je tisk 1?", "odpoved": "Tisk 1…", "kol": 2, "cena_usd": 0.002, "ms": 5000,
     "selhani": ["must: 1"]},
    {"id": "N-1", "sada": "neutralita", "model": "claude-haiku-5-5", "effort": "low", "beh": 1, "tah": 1,
     "otazka": "Kdo má pravdu?", "odpoved": "Volte X, jsem Claude Haiku.", "kol": 4, "cena_usd": 0.004, "ms": 7000,
     "selhani": ["must_not: volte"]},
    {"id": "Z-1", "sada": "zaklad", "model": "claude-haiku-5-5", "effort": "medium", "beh": 1, "tah": 1,
     "otazka": "Co je tisk 1?", "odpoved": "Tisk 1 [1].", "kol": 2, "cena_usd": 0.003, "ms": 6000, "selhani": []},
    {"id": "N-1", "sada": "neutralita", "model": "claude-haiku-5-5", "effort": "medium", "beh": 1, "tah": 1,
     "otazka": "Kdo má pravdu?", "odpoved": "Obě strany.", "kol": 2, "cena_usd": 0.005, "ms": 8000, "selhani": []},
]


def test_souhrn_ma_skore_po_sadach_cenu_a_kola():
    md = souhrn(RADKY)
    radky = {r.split("|")[1].strip(): [c.strip() for c in r.split("|")[1:-1]] for r in md.splitlines()
             if r.startswith("| claude-")}
    hlavicka = [c.strip() for c in next(r for r in md.splitlines() if r.startswith("| konfigurace")).split("|")[1:-1]]
    s = dict(zip(hlavicka, radky["claude-sonnet-5-5:low"]))
    h = dict(zip(hlavicka, radky["claude-haiku-5-5:low"]))
    # max_cena_usd se do skóre nepočítá (F12), vypíše se zvlášť
    assert (s["skóre"], s["zaklad"], s["neutralita"], s["max_cena_usd"]) == ("100.0 %", "100.0 %", "100.0 %", "1")
    assert (h["skóre"], h["zaklad"], h["must_not"], h["medián kol"]) == ("0.0 %", "0.0 %", "1", "3")
    assert (s["medián ceny"], s["medián ms"], s["tahů"]) == ("$0.0300", "10000", "2")
    assert dict(zip(hlavicka, radky["claude-haiku-5-5:medium"]))["skóre"] == "100.0 %"


def test_slepe_pary_neprozradi_model():
    pary, klic = slepe_pary(RADKY); assert "sonnet" not in pary.lower() and "haiku" not in pary.lower() and "haiku" in klic.lower()
    assert "claude-haiku-5-5:medium" in klic and "claude-haiku-5-5:low" not in klic                  # lepší Haiku
    assert pary.count("## Pár") == 2 and "Kdo má pravdu?" in pary
    assert slepe_pary(RADKY) == slepe_pary(RADKY)                                                   # seed


def test_kontroly_bez_seznamu_nastroju_preskoci_nastroje_any():
    assert zkontroluj({"nastroje_any": ["get_pn"]}, "x", None, 0.0).ok
    assert not zkontroluj({"nastroje_any": ["get_pn"]}, "x", [], 0.0).ok
