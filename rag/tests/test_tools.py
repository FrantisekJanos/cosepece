"""Testy nástrojů MCP bez databáze: falešné spojení vrací připravené řádky v pořadí dotazů."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime, timezone

import psycopg
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from hlidac_rag import mcp_server, tools


class FakeConn:
    """Vrací připravené výsledky (seznamy řádků) v pořadí dotazů a pamatuje si SQL."""

    def __init__(self, *results):
        self.results = list(results)
        self.sql: list[str] = []

    def cursor(self, row_factory=None):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.sql.append(sql)
        return self

    def fetchall(self):
        return self.results.pop(0)


def _speech(**kw) -> dict:
    row = dict(id=1, poradi=10, schuze=35, turn=10, aname=6, datum=date(2026, 10, 14), cas="10.30", id_osoba=6165,
               recnik="Poslanec Radek Vondráček", role="Poslanec", klub="ANO2011", predsedajici=False, ct=76,
               znaku=0, autorizovano=True, verze=1, url="https://www.psp.cz/x.htm#r6", bod="Bod",
               turny=[10], hlasovani=[], ct_zdroj="rec", id_bod=1)
    row.update(kw)
    return row


def test_get_projev_vraci_cely_text_a_strankuje():
    full = "slovo " * 1000  # 6000 znaků
    r = tools.get_projev(FakeConn([_speech(text=full)], []), "10/35/10/6", max_znaku=1000)
    assert r["text"] == full[:1000]
    assert "uryvek" not in r
    assert r["klic"] == "10/35/10/6" and r["znaku_celkem"] == 6000
    assert r["pokracuje"] and r["dalsi_offset_znaku"] == 1000
    json.dumps(r, ensure_ascii=False)

    parts, off = [], 0
    while off is not None:
        p = tools.get_projev(FakeConn([_speech(text=full)], []), "10/35/10/6", max_znaku=1000, offset_znaku=off)
        parts.append(p["text"])
        off = p["dalsi_offset_znaku"]
    assert "".join(parts) == full


def test_get_projev_okoli_ma_poradi_a_delsi_uryvek():
    neighbour = _speech(id=2, poradi=11, aname=7, text="a " * 2000)
    r = tools.get_projev(FakeConn([_speech(text="text projevu")], [neighbour], []), "10/35/10/6",
                         okoli=1, okoli_max_znaku=1000)
    assert r["text"] == "text projevu"
    o = r["okoli"][0]
    assert o["poradi"] == 11 and o["klic"] == "10/35/10/7"
    assert 900 < len(o["uryvek"]) <= 1002 and o["zkraceno"]


def test_get_projev_neautorizovany_ma_poznamku():
    r = tools.get_projev(FakeConn([_speech(text="x", autorizovano=False)], []), "10/35/10/6")
    assert r["poznamka"] == tools.NOTE_UNAUTH and r["text"] == "x"


def test_zmeny_projevu_s_datetime():
    row = _speech(zmeneno=datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc), znaku_predtim=10, verze=2)
    r = tools.zmeny_projevu(FakeConn([row]))
    assert r["projevy"][0]["zmeneno"].startswith("2026-11-13")
    json.dumps(r, ensure_ascii=False)


@pytest.mark.parametrize("value", ["abc", "14. 10. 2026", "2026-13-01"])
def test_spatne_datum_je_srozumitelna_chyba(value):
    with pytest.raises(ValueError, match="RRRR-MM-DD"):
        tools.projevy_k_tisku(FakeConn(), 76, od=value)


def test_datum_projde():
    assert tools._date("2026-10-14", "od") == "2026-10-14"
    assert tools._date(None, "od") is None


# ---------------------------------------------------------------- _call

class FakePool:
    def __init__(self, conn):
        self.conn = conn
        self.used = 0

    @contextmanager
    def connection(self):
        self.used += 1
        yield self.conn


@pytest.fixture
def fake_pool(monkeypatch):
    def make(conn):
        p = FakePool(conn)
        monkeypatch.setattr(mcp_server, "pool", p)
        return p
    return make


def test_call_nastavuje_jen_transakci_ne_spojeni(fake_pool):
    conn = FakeConn()
    fake_pool(conn)
    assert mcp_server._call("t", lambda c: {"ok": True}) == {"ok": True}
    assert conn.sql[0] == "SET TRANSACTION READ ONLY"
    assert conn.sql[1].startswith("SET LOCAL statement_timeout")
    assert not hasattr(conn, "read_only")       # režim čtení se nenastavuje na spojení v poolu
    assert conn.sql[-1].startswith("INSERT INTO mcp.volani")


def test_call_valueerror_je_toolerror(fake_pool):
    fake_pool(FakeConn())

    def boom(c):
        raise ValueError("datum zadejte ve tvaru RRRR-MM-DD")
    with pytest.raises(ToolError, match="RRRR-MM-DD"):
        mcp_server._call("t", boom)


def test_call_timeout_je_toolerror(fake_pool):
    fake_pool(FakeConn())

    def slow(c):
        raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
    with pytest.raises(ToolError, match="déle než"):
        mcp_server._call("t", slow)


def test_call_chyba_db_neprozradi_detail(fake_pool):
    fake_pool(FakeConn())

    def broken(c):
        raise psycopg.errors.UndefinedTable('relation "tajne.x" does not exist')
    with pytest.raises(ToolError) as e:
        mcp_server._call("t", broken)
    assert "tajne" not in str(e.value)


def test_get_tisk_neznamy_je_valueerror():
    with pytest.raises(ValueError, match="99999"):
        tools.get_tisk(FakeConn([], []), 99999)


def test_get_pn_neznamy_je_valueerror():
    with pytest.raises(ValueError, match="neznám"):
        tools.get_pn(FakeConn([]), 1)


def test_tisk_podle_sbirky_a_program_schuze_nenalezeno_je_valueerror():
    with pytest.raises(ValueError, match="1/2026"):
        tools.tisk_podle_sbirky(FakeConn([]), "1/2026")
    with pytest.raises(ValueError, match="neznám"):
        tools.program_schuze(FakeConn([]), 999)


def test_nejednoznacna_osoba_je_valueerror_s_kandidaty():
    osoby = [dict(id_osoba=i, jmeno="Jan", prijmeni="Novák", klub=f"K{i}", projevu=5, pn=0) for i in (1, 2)]
    with pytest.raises(ValueError, match="id_osoba=2"):
        tools.projevy_recnika(FakeConn(osoby), "Novák")


def test_stav_dat_nevraci_text_chyby():
    raw = '{"error": "connection to 10.0.0.5 failed", "seconds": 1}'
    r = tools.stav_dat(FakeConn([{"projevu": 1}], [{"v": raw}], [{"v": "2026-11-19"}]))
    assert "10.0.0.5" not in json.dumps(r, default=str)
    assert r["steno_last_result"]["ok"] is False and r["steno_last_result"]["seconds"] == 1
    assert "error" not in r["steno_last_result"]


def test_vsechny_nastroje_maji_readonly_anotaci():
    nastroje = mcp_server.server._tool_manager.list_tools()
    assert nastroje
    for t in nastroje:
        assert t.annotations.read_only_hint is True and t.annotations.open_world_hint is False, t.name


_KLUBY = [dict(klub="ANO2011", klub_nazev="Klub hnutí ANO 2011"), dict(klub="ODS", klub_nazev="Občanská demokratická strana"),
          dict(klub="KDU-ČSL", klub_nazev="Klub KDU-ČSL")]


def test_klub_ano_najde_ano2011():
    assert tools._resolve_klub(FakeConn(_KLUBY), "ANO") == ["ANO2011"]
    assert tools._resolve_klub(FakeConn(_KLUBY), "kdu") == ["KDU-ČSL"]


def test_klub_neznamy_vyjmenuje_platne():
    with pytest.raises(ValueError, match="Platné:") as e:
        tools._resolve_klub(FakeConn(_KLUBY), "XYZ")
    assert "ANO2011" in str(e.value) and "ODS" in str(e.value)


def test_projevy_k_tisku_filtruje_klub_podle_prefixu():
    conn = FakeConn(_KLUBY, [dict(n=0)], [])
    tools.projevy_k_tisku(conn, 76, klub="ano")
    assert "p.klub = ANY(%s)" in conn.sql[1]


def test_osoba_nejednoznacna_je_valueerror_s_kandidaty():
    osoby = [dict(id_osoba=6165, jmeno="Radek", prijmeni="Vondráček", klub="ANO2011", projevu=5, pn=0),
             dict(id_osoba=7058, jmeno="Jiří", prijmeni="Vondráček", klub="ODS", projevu=3, pn=0)]
    with pytest.raises(ValueError) as e:
        tools._resolve_person(FakeConn(osoby), "Vondráček")
    assert "6165" in str(e.value) and "7058" in str(e.value)


def test_osoba_nenalezena_je_valueerror_bez_kandidatu():
    with pytest.raises(ValueError, match="nenalezeno") as e:
        tools._resolve_person(FakeConn([]), "Neexistující")
    assert "Kandidáti" not in str(e.value)


# ---------------------------------------------------------------- hledání a seznamy

def test_tsquery_kratka_slova():
    with pytest.raises(ValueError, match="příliš krátký"):
        tools._tsquery("na se daň")
    assert tools._tsquery("rafinérské marže") == "rafinerske:* & marze:*"
    assert tools._tsquery("zákon 586/1992 na") == "zakon:* & 586 & 1992"


def test_hledej_uryvek_kolem_shody():
    row = _speech(uryvek_shoda="… rafinerske «marze» rostou …", text="x")
    conn = FakeConn([dict(n=1)], [row])
    r = tools.hledej_v_projevech(conn, "rafinérské marže")
    sql = conn.sql[1]
    assert "ts_headline" in sql and "StartSel=«, StopSel=»" in sql and "MaxFragments" in sql
    assert "unaccent_simple(p.text), to_tsquery" in sql  # úryvek nad textem bez diakritiky
    assert r["projevy"][0]["uryvek_shoda"] == "… rafinerske «marze» rostou …"


def test_hledej_ma_celkem_a_offset():
    rows = [_speech(id=i, uryvek_shoda="«a»") for i in (1, 2)]
    r = tools.hledej_v_projevech(FakeConn([dict(n=5)], rows), "dotace", limit=2, offset=2)
    assert r["celkem"] == 5 and r["offset"] == 2 and r["dalsi_offset"] == 4
    r = tools.hledej_v_projevech(FakeConn([dict(n=4)], rows), "dotace", limit=2, offset=2)
    assert r["dalsi_offset"] is None


def test_pn_k_tisku_ma_celkem_a_offset():
    autori = [dict(osoba="A", n=3), dict(osoba="B", n=1)]
    pn = [dict(cislo=1, id_osoba=1, osoba="A", cas=None, ai_score=None, ai_json=None, laws=None, chars=1)]
    r = tools.pn_k_tisku(FakeConn(autori, pn), 76, limit=1, offset=0)
    assert r["celkem"] == 4 and r["dalsi_offset"] == 1 and r["predkladatele"] == [("A", 3), ("B", 1)]
    r = tools.pn_k_tisku(FakeConn(autori, pn), 76, limit=1, offset=3)
    assert r["dalsi_offset"] is None


def test_projevy_recnika_q_bez_diakritiky():
    conn = FakeConn([dict(n=0)], [])
    r = tools.projevy_recnika(conn, 6165, q="rafinérské marže")
    assert "unaccent_simple(p.text)" in conn.sql[0]
    assert r["celkem"] == 0 and r["offset"] == 0 and r["dalsi_offset"] is None


def test_tisky_menici_zakon_razeni():
    row = dict(ct=76, nazev="n", typ_navrhu="x", faze="f", sbirka=None, score=50, zdroj="pn")
    conn = FakeConn([dict(n=1)], [row])
    r = tools.tisky_menici_zakon(conn, "586/1992")
    assert "ORDER BY score DESC NULLS LAST, ct" in conn.sql[1]
    assert r["celkem"] == 1 and r["dalsi_offset"] is None and r["tisky"][0]["url_psp"]
