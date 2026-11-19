"""Hybridní hledání (embeddingy + fulltext, RRF): čisté funkce, nástroje nad FakeConn a PostgreSQL."""
from __future__ import annotations

import os
import time
from datetime import date

import httpx2 as httpx
import pytest

from hlidac_rag import tools
from hlidac_rag.embed import hledani
from hlidac_rag.embed.klient import Klient

KLIC = "sk-test-123"


class FakeConn:
    """Vrací připravené výsledky v pořadí dotazů, které něco čtou; SET se jen zaznamená."""

    def __init__(self, *results):
        self.results = list(results)
        self.sql: list[str] = []
        self.params: list = []

    def cursor(self, row_factory=None):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.sql.append(sql)
        self.params.append(params)
        return self

    def fetchall(self):
        return self.results.pop(0)


def _klient_s(handler) -> tuple[Klient, list]:
    volani = []

    def h(req):
        volani.append(req)
        return handler(req)
    return Klient(KLIC, transport=httpx.MockTransport(h), pokusu=1), volani


def _ok(req):
    return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.5] * 4}], "usage": {"total_tokens": 3}})


# ---------------------------------------------------------------- čisté funkce

def test_rrf_poradi():
    r = hledani.rrf([["a", "b", "c"], ["c", "d"]])
    klice = [k for k, _ in r]
    assert klice[0] == "c"                       # v obou seznamech
    assert set(klice) == {"a", "b", "c", "d"}
    skore = dict(r)
    assert skore["c"] == pytest.approx(1 / 63 + 1 / 61)
    assert skore["a"] == pytest.approx(1 / 61)
    assert klice.index("a") < klice.index("b")
    assert hledani.rrf([]) == []


def test_vektor_dotazu_bez_klice_none():
    assert hledani.vektor_dotazu("zdražování energií") is None
    assert "OPENAI_API_KEY" in hledani.posledni_chyba()


def test_vektor_dotazu_vraci_vektor(monkeypatch):
    k, _ = _klient_s(_ok)
    monkeypatch.setattr(hledani, "_klient", lambda: k)
    assert hledani.vektor_dotazu("ceny energií") == [0.5] * 4
    assert hledani.posledni_chyba() is None


def test_vektor_dotazu_timeout_none(monkeypatch):
    def spi(req):
        time.sleep(1.5)
        return _ok(req)
    k, _ = _klient_s(spi)
    monkeypatch.setattr(hledani, "_klient", lambda: k)
    monkeypatch.setattr(hledani, "TIMEOUT", 0.3)
    start = time.monotonic()
    assert hledani.vektor_dotazu("ceny energií") is None
    assert time.monotonic() - start < 1.0
    assert "0.3 s" in hledani.posledni_chyba() or "s" in hledani.posledni_chyba()


def test_vektor_dotazu_chyba_api_none(monkeypatch):
    k, _ = _klient_s(lambda req: httpx.Response(429))
    monkeypatch.setattr(hledani, "_klient", lambda: k)
    assert hledani.vektor_dotazu("ceny energií") is None
    assert "429" in hledani.posledni_chyba() and KLIC not in hledani.posledni_chyba()


def test_cache(monkeypatch):
    k, volani = _klient_s(_ok)
    monkeypatch.setattr(hledani, "_klient", lambda: k)
    assert hledani.vektor_dotazu("ceny energií") == hledani.vektor_dotazu("ceny energií")
    assert len(volani) == 1
    hledani.vektor_dotazu("jiný dotaz")
    assert len(volani) == 2


def test_husty_sql_iterative_scan():
    conn = FakeConn([dict(projev_id=1, cel_id=None, od_znaku=0, do_znaku=10, vzdalenost=0.1)])
    rows = hledani.husty(conn, [0.1, 0.2], "projev", tools._PATRI_K_TISKU, [311], model="m")
    assert rows[0]["projev_id"] == 1
    sql = " ".join(conn.sql)
    assert "hnsw.iterative_scan = relaxed_order" in sql
    assert "steno.projev_tisk" in sql and "<=>" in sql and "row_number()" in sql
    assert "u.model = %s" in conn.sql[-1]
    assert 311 in conn.params[-1] and "[0.1,0.2]" in conn.params[-1]
    with pytest.raises(ValueError):
        hledani.husty(conn, [0.1], "nic", "", [], model="m")


# ---------------------------------------------------------------- nástroje nad FakeConn

def _speech(**kw) -> dict:
    row = dict(id=1, schuze=35, turn=10, aname=6, datum=date(2026, 10, 14), cas="10.30", id_osoba=6165,
               recnik="Poslanec Radek Vondráček", role="Poslanec", klub="ANO2011", predsedajici=False, ct=76,
               znaku=0, autorizovano=True, verze=1, url="https://www.psp.cz/x.htm#r6", bod="Bod", druh="recnik",
               text="Ceny elektřiny pro domácnosti porostou.")
    row.update(kw)
    return row


def test_rezim_fulltext_beze_zmeny(monkeypatch):
    monkeypatch.setattr(hledani, "vektor_dotazu", lambda d: pytest.fail("ve fulltextu se embedding nevolá"))
    conn = FakeConn([dict(n=1)], [dict(_speech(), rank=0.5)])
    r = tools.hledej_v_projevech(conn, "elektřiny", rezim="fulltext")
    assert r["rezim_pouzity"] == "fulltext" and r["projevy"][0]["shoda"] == "slova"
    assert len(conn.sql) == 2 and "ts_rank" in conn.sql[1] and "rag.usek" not in " ".join(conn.sql)


def test_auto_bez_vektoru_fallback():
    conn = FakeConn([dict(n=1)], [dict(_speech(), rank=0.5)])
    r = tools.hledej_v_projevech(conn, "elektřiny")
    assert r["rezim_pouzity"] == "fulltext"
    assert "OPENAI_API_KEY" in r["poznamka"] and "rag.usek" not in " ".join(conn.sql)
    conn = FakeConn([dict(ok=True)], [dict(n=0)], [])
    r = tools.hledej_v_textech(conn, "elektřiny")
    assert r["rezim_pouzity"] == "fulltext" and "OPENAI_API_KEY" in r["poznamka"]


def test_auto_bez_indexu_fallback(monkeypatch):
    monkeypatch.setattr(hledani, "vektor_dotazu", lambda d: [0.1])
    conn = FakeConn([dict(ok=False)], [dict(n=0)], [])
    r = tools.hledej_v_projevech(conn, "elektřiny")
    assert r["rezim_pouzity"] == "fulltext" and "index" in r["poznamka"]


def test_vyznam_bez_vektoru_fallback():
    conn = FakeConn([dict(n=0)], [])
    r = tools.hledej_v_projevech(conn, "elektřiny", rezim="vyznam")
    assert r["rezim_pouzity"] == "fulltext" and "OPENAI_API_KEY" in r["poznamka"]


def test_neplatny_rezim_valueerror():
    with pytest.raises(ValueError, match="rezim"):
        tools.hledej_v_projevech(FakeConn(), "elektřiny", rezim="semanticky")
    with pytest.raises(ValueError, match="rezim"):
        tools.hledej_v_textech(FakeConn(), "elektřiny", rezim="x")


def test_hybrid_slouci_a_oznaci_shodu(monkeypatch):
    monkeypatch.setattr(hledani, "vektor_dotazu", lambda d: [0.1])
    text_vyznam = "Zdražování proudu dopadá na domácnosti. " * 20
    conn = FakeConn(
        [dict(ok=True)], [dict(ok=True)],                               # tabulka a index modelu
        [dict(id=1), dict(id=2)],                                        # lexikální top-50
        [dict(projev_id=3, cel_id=None, od_znaku=0, do_znaku=400, vzdalenost=0.1),
         dict(projev_id=1, cel_id=None, od_znaku=0, do_znaku=40, vzdalenost=0.2)],   # husté
        [_speech(id=1), _speech(id=2, aname=7), _speech(id=3, aname=8, text=text_vyznam)])
    r = tools.hledej_v_projevech(conn, "elektřiny", limit=10)
    assert r["rezim_pouzity"] == "hybrid" and r["celkem"] == 3
    shody = {p["klic"]: p["shoda"] for p in r["projevy"]}
    assert shody == {"10/35/10/6": "obe", "10/35/10/7": "slova", "10/35/10/8": "vyznam"}
    assert r["projevy"][0]["klic"] == "10/35/10/6"       # v obou seznamech nahoře
    vyz = next(p for p in r["projevy"] if p["shoda"] == "vyznam")
    assert len(vyz["uryvek_shoda"]) <= 302 and vyz["uryvek_shoda"].startswith("Zdražování")
    assert "«" in next(p for p in r["projevy"] if p["shoda"] == "obe")["uryvek_shoda"]
    for p in r["projevy"]:
        assert "text" not in p and "id" not in p and "rank" not in p


def test_hybrid_strankovani(monkeypatch):
    monkeypatch.setattr(hledani, "vektor_dotazu", lambda d: [0.1])
    conn = FakeConn([dict(ok=True)], [dict(ok=True)], [dict(id=i) for i in range(1, 6)], [],
                    [_speech(id=3, aname=3), _speech(id=4, aname=4)])
    r = tools.hledej_v_projevech(conn, "elektřiny", limit=2, offset=2)
    assert r["celkem"] == 5 and [p["klic"] for p in r["projevy"]] == ["10/35/10/3", "10/35/10/4"]
    assert r["dalsi_offset"] == 4
    assert conn.params[-1][0] == [3, 4]


def test_texty_prazdny_dotaz_lexikalne(monkeypatch):
    monkeypatch.setattr(hledani, "vektor_dotazu", lambda d: pytest.fail("prázdný dotaz se neembeduje"))
    conn = FakeConn([dict(ok=True)], [dict(n=0)], [])
    r = tools.hledej_v_textech(conn, "", ct=311)
    assert r["rezim_pouzity"] == "fulltext"


# ---------------------------------------------------------------- PostgreSQL

bez_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)")


@pytest.fixture
def pgconn():
    from hlidac_rag import db
    from test_embed_indexer import SCHEMA_WEB, _vycisti

    c = db.connect()
    jmeno = c.execute("SELECT current_database()").fetchone()[0]
    if not jmeno.endswith("_test"):
        c.close()
        pytest.skip(f"databáze {jmeno} nekončí na _test")
    db.migrate(c)
    _vycisti(c)
    with c.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {SCHEMA_WEB}")
        cur.execute(f"CREATE TABLE {SCHEMA_WEB}.tisky (id_tisk int, ct int, nazev text)")
        cur.execute(f"SET search_path TO {SCHEMA_WEB}, public")
        cur.executemany("INSERT INTO steno.osoba (id_osoba, jmeno, prijmeni) VALUES (%s, %s, %s)",
                        [(2001, "Jan", "Novák"), (2002, "Eva", "Malá")])
        cur.execute("INSERT INTO steno.klub_clenstvi (id_osoba, klub, klub_nazev) VALUES (2001, 'ODS', 'Klub ODS')")
    c.commit()
    yield c
    c.rollback()
    _vycisti(c)
    c.commit()
    c.close()


ELEKTRINA = "Ceny elektřiny pro domácnosti a malé firmy letos výrazně vzrostly a vláda musí pomoci. " * 4
CHMELAR = "Chmelařství na Žatecku potřebuje podporu, sucho ničí úrodu chmele a pěstitelé krachují. " * 4


def _projevy(conn) -> dict[str, int]:
    from test_embed_indexer import _sql

    ids = {}
    for aname, (klic, text, ct) in enumerate((("elektrina", ELEKTRINA, 311), ("chmel", CHMELAR, 400)), start=1):
        ids[klic] = _sql(conn, """INSERT INTO steno.projev (obdobi, schuze, turn, aname, datum, poradi, id_osoba, recnik,
                                         klub, text, znaku, hash, url, autorizovano, druh)
                                  VALUES (10, 36, 100, %s, '2026-11-13', %s, 2001, 'Poslanec Jan Novák', 'ODS', %s, %s,
                                          'h', 'https://x', true, 'recnik') RETURNING id""",
                         (aname, aname, text, len(text)))[0][0]
        _sql(conn, "INSERT INTO steno.projev_tisk (projev_id, ct, zdroj) VALUES (%s, %s, 'bod')", (ids[klic], ct))
    return ids


def _vektor_useku(conn, projev_id: int) -> list[float]:
    """Vektor uloženého úseku: falešný dotaz „blízký“ projevu."""
    from test_embed_indexer import _sql

    v = _sql(conn, "SELECT embedding::text FROM rag.usek WHERE projev_id = %s ORDER BY poradi LIMIT 1", (projev_id,))
    return [float(x) for x in v[0][0].strip("[]").split(",")]


@pytest.mark.pg
@bez_db
def test_hybrid_najde_parafrazi(pgconn, monkeypatch):
    from hlidac_rag.embed.indexer import indexuj
    from test_embed_indexer import FalesnyKlient

    ids = _projevy(pgconn)
    indexuj(pgconn, FalesnyKlient())
    vektor = _vektor_useku(pgconn, ids["elektrina"])
    monkeypatch.setattr(hledani, "vektor_dotazu", lambda d: vektor)
    r = tools.hledej_v_projevech(pgconn, "zdražování energií")
    assert r["rezim_pouzity"] == "hybrid"
    prvni = r["projevy"][0]
    assert prvni["klic"] == "10/36/100/1" and prvni["shoda"] == "vyznam"
    assert prvni["uryvek_shoda"].startswith("Ceny elektřiny")
    # filtr ct jiného tisku ho vyřadí
    r = tools.hledej_v_projevech(pgconn, "zdražování energií", ct=400)
    assert "10/36/100/1" not in [p["klic"] for p in r["projevy"]]
    # jen význam
    r = tools.hledej_v_projevech(pgconn, "zdražování energií", rezim="vyznam")
    assert r["rezim_pouzity"] == "vyznam" and r["projevy"][0]["klic"] == "10/36/100/1"


@pytest.mark.pg
@bez_db
def test_hybrid_jmeno_slova(pgconn, monkeypatch):
    from hlidac_rag.embed.indexer import indexuj
    from test_embed_indexer import FalesnyKlient

    ids = _projevy(pgconn)
    indexuj(pgconn, FalesnyKlient())
    vektor = _vektor_useku(pgconn, ids["elektrina"])
    monkeypatch.setattr(hledani, "vektor_dotazu", lambda d: vektor)
    r = tools.hledej_v_projevech(pgconn, "Žatecku")
    chmel = next(p for p in r["projevy"] if p["klic"] == "10/36/100/2")
    assert chmel["shoda"] in ("slova", "obe") and "«Žatecku»" in chmel["uryvek_shoda"]


@pytest.mark.pg
@bez_db
def test_hybrid_v_textech(pgconn, monkeypatch):
    from hlidac_rag.embed.indexer import indexuj
    from test_embed_indexer import FalesnyKlient, _dokument, _sql

    cid = _dokument(pgconn, [("clanek", "Řízení o vrácení dotace lze zahájit nejpozději do 4 let."),
                             ("clanek", "Zákon nabývá účinnosti dnem vyhlášení.")])[0]
    indexuj(pgconn, FalesnyKlient())
    v = _sql(pgconn, "SELECT embedding::text FROM rag.usek WHERE cel_id = %s", (cid,))[0][0]
    monkeypatch.setattr(hledani, "vektor_dotazu", lambda d: [float(x) for x in v.strip("[]").split(",")])
    r = tools.hledej_v_textech(pgconn, "zpětné vymáhání podpor")
    assert r["rezim_pouzity"] == "hybrid"
    assert r["vysledky"][0]["oznaceni"] == "Čl. 1" and r["vysledky"][0]["shoda"] == "vyznam"
    assert r["vysledky"][0]["ct"] == 221
    r = tools.hledej_v_textech(pgconn, "zpětné vymáhání podpor", ct=999)
    assert r["vysledky"] == []


def test_mcp_nastroje_maji_rezim():
    from hlidac_rag import mcp_server

    nastroje = {t.name: t for t in mcp_server.server._tool_manager.list_tools()}
    for jmeno in ("hledej_v_projevech", "hledej_v_textech"):
        t = nastroje[jmeno]
        assert t.parameters["properties"]["rezim"]["default"] == "auto"
        assert "rezim_pouzity" in t.description and "vyznam" in t.description


def test_denni_strop_dotazu(monkeypatch):
    k, volani = _klient_s(_ok)
    monkeypatch.setattr(hledani, "_klient", lambda: k)
    monkeypatch.setenv("EMBED_DOTAZY_DEN", "2")
    assert hledani.vektor_dotazu("první dotaz") is not None
    assert hledani.vektor_dotazu("druhý dotaz") is not None
    assert hledani.vektor_dotazu("první dotaz") is not None      # z cache, do stropu se nepočítá
    assert hledani.vektor_dotazu("třetí dotaz") is None
    assert "denní limit sémantického hledání vyčerpán" in hledani.posledni_chyba()
    assert len(volani) == 2
    # nový den (UTC) = nové počítadlo
    monkeypatch.setattr(hledani, "_dnes", lambda: "2099-01-01")
    assert hledani.vektor_dotazu("třetí dotaz") is not None and len(volani) == 3


def test_denni_strop_fallback_v_nastroji(monkeypatch):
    k, _ = _klient_s(_ok)
    monkeypatch.setattr(hledani, "_klient", lambda: k)
    monkeypatch.setenv("EMBED_DOTAZY_DEN", "0")
    conn = FakeConn([dict(n=0)], [])
    r = tools.hledej_v_projevech(conn, "elektřiny")
    assert r["rezim_pouzity"] == "fulltext" and "denní limit sémantického hledání vyčerpán" in r["poznamka"]


def test_denni_strop_vychozi(monkeypatch):
    monkeypatch.delenv("EMBED_DOTAZY_DEN", raising=False)
    assert hledani._denni_strop() == 20000
    monkeypatch.setenv("EMBED_DOTAZY_DEN", "nesmysl")
    assert hledani._denni_strop() == 20000
