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
