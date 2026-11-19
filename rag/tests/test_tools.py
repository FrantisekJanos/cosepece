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
    r = tools.stav_dat(FakeConn([{"projevu": 1}], [{"v": raw}], [{"v": "2026-11-19"}], [{"ok": False}]))
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
        tools._tsquery("na se")
    assert tools._tsquery("DPH daň") == "dph & dan"
    assert tools._tsquery("rafinérské marže") == "rafiners:* & marz:*"
    assert tools._tsquery("zákon 586/1992 na") == "zako:* & 586/1992"
    assert tools._tsquery("4.36") == "4.36"
    assert tools._tsquery("4:36") == "4.36"
    assert tools._tsquery("4,36") == "4.36"


def test_hledej_uryvek_kolem_shody():
    text = "Úvod bez shody. " * 40 + "Rafinérských marží se to týká. " + "Konec textu. " * 40
    conn = FakeConn([dict(n=1)], [_speech(text=text)])
    r = tools.hledej_v_projevech(conn, "rafinérské marže")
    assert "MaxFragments" not in conn.sql[1] and "p.text" in conn.sql[1]
    u = r["projevy"][0]["uryvek_shoda"]
    assert "«Rafinérských»" in u and "«marží»" in u       # diakritika a velikost písmen zachovány
    assert not u.startswith("Úvod") and len(u) < 400
    assert "text" not in r["projevy"][0]


def test_hledej_ma_celkem_a_offset():
    rows = [_speech(id=i, text="dotace") for i in (1, 2)]
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
    conn = FakeConn([dict(ok=False)], [dict(n=1)], [row])
    r = tools.tisky_menici_zakon(conn, "586/1992")
    assert "ORDER BY score DESC NULLS LAST, ct" in conn.sql[2]
    assert "texty.cel" not in conn.sql[2]
    assert r["celkem"] == 1 and r["dalsi_offset"] is None and r["tisky"][0]["url_psp"]


def test_tisky_menici_zakon_z_celku_textu():
    """E10: zákon měněný jen částí textu návrhu (256/2000 v tisku 221), ne názvem ani PN."""
    row = dict(ct=221, nazev="n", typ_navrhu="x", faze="f", sbirka=None, score=47, zdroj="text")
    conn = FakeConn([dict(ok=True)], [dict(n=1)], [row])
    r = tools.tisky_menici_zakon(conn, "256/2000")
    assert "texty.cel" in conn.sql[1] and "texty.cel" in conn.sql[2]
    assert conn.params[1] == ("%,256/2000,%", "%,256/2000,%", "256/2000")
    assert "WHEN bool_or(x.zdroj = 'text') THEN 'text'" in conn.sql[2]
    assert r["tisky"][0]["ct"] == 221 and r["tisky"][0]["zdroj"] == "text"


_TISK_ROW = dict(ct=231, druh=1, typ_navrhu="vládní", navrhovatel="vláda", nazev="N", uplny_nazev="N", predlozeno=None,
                 faze="1. čtení", posledni=None, p90=False, p90ok=False, zl=False, pn_count=0, pn_last=None,
                 zakony="", sbirka=None, score=10, reasons="[]")


_STATS = {"projevu": 260, "projevu_vse": 526, "recniku": 40, "prvni": None, "posledni": None}


def test_get_tisk_body_vcetne_opakovanych():
    """Tisk 231: na pořadu schůze 30 (neprojednán, bez projevů) i 34; jedna položka na schůzi."""
    s30 = {"schuze": 30, "body": [34], "nazev": "Bod", "konec": "/sněmovní tisk 231/", "datum": None,
           "projevu": 0, "projevu_vse": 0}
    s34 = dict(s30, schuze=34, datum=date(2026, 10, 23), projevu=260, projevu_vse=526)
    conn = FakeConn([_TISK_ROW], [s30, s34], [_STATS], [{"ok": False}])
    r = tools.get_tisk(conn, 231, ["body"])
    assert r["body_poradu"] == [s30, dict(s34, datum="2026-10-23")]
    # body i počty z projev_tisk (projevy bez bodu z rec se počítají), ne podle id_bod bodu
    sql = conn.sql[1]
    assert "steno.projev_tisk" in sql and "steno.bod" in sql and "zruseno IS NULL" in sql
    assert "p.id_bod = b.id_bod" not in sql
    assert "DISTINCT ON" not in sql.upper()


def test_get_tisk_mimo_tisky_nazev_z_bodu():
    """Tisk mimo tabulku tisky: název z prvního bodu, který ho má; schůze jen s projevy bod nemá."""
    bez_bodu = {"schuze": 29, "body": [], "nazev": None, "konec": None, "datum": date(2026, 9, 1), "projevu": 1,
                "projevu_vse": 1}
    s34 = {"schuze": 34, "body": [34], "nazev": "Bod", "konec": "k", "datum": None, "projevu": 0, "projevu_vse": 0}
    r = tools.get_tisk(FakeConn([], [bez_bodu, s34], [_STATS], [{"ok": False}]), 231, ["body"])
    assert r["nazev"] == "Bod" and r["poznamka"]


def test_get_tisk_projevy_stejne_jako_projevy_k_tisku():
    conn = FakeConn([_TISK_ROW], [], [{"projevu": 10, "projevu_vse": 12, "recniku": 4, "prvni": None, "posledni": None}],
                    [{"ok": False}])
    r = tools.get_tisk(conn, 231, ["body"])
    assert r["projevy"]["projevu"] == 10 and r["projevy"]["projevu_vse"] == 12
    sql = conn.sql[2]
    assert "NOT predsedajici" in sql and "znaku >= 200" in sql and "zruseno IS NULL" in sql
    assert "steno.projev_tisk" in sql


def test_call_prilis_velka_odpoved(fake_pool):
    fake_pool(FakeConn())
    with pytest.raises(ToolError, match="Zužte"):
        mcp_server._call("t", lambda c: {"text": "x" * 70000})
    assert mcp_server._call("t", lambda c: {"text": "x" * 100}) == {"text": "x" * 100}


# ---------------------------------------------------------------- texty návrhů a PN

_OK = [{"ok": True}]


def _dok(**kw) -> dict:
    row = dict(id=7, druh="tisk", ct=311, cislo_pn=0, verze="puvodni", docx_url="https://www.psp.cz/x.docx",
               znaku=50000, chyba=None, ma_text=True, dostupny=True)
    row.update(kw)
    return row


def _cel(poradi: int, oznaceni: str, **kw) -> dict:
    row = dict(poradi=poradi, typ="clanek", oznaceni=oznaceni, cesta=f"ČÁST PRVNÍ / {oznaceni}",
               meni_zakon="586/1992", od_znaku=poradi * 100, text=f"Text {oznaceni}")
    row.update(kw)
    return row


def test_osnova_bez_duvodove():
    celky = [dict(poradi=0, typ="clanek", oznaceni="Čl. I", cesta="Čl. I", znaku=100, meni_zakon="586/1992",
                  zacatek="Zákon č. 586/1992 Sb.")]
    conn = FakeConn(_OK, [_dok()], [dict(n=1)], celky)
    r = tools.osnova_textu(conn, ct=311)
    assert "to_regclass" in conn.sql[0]
    assert "typ <> 'duvodova_zprava'" in conn.sql[2] and "typ <> 'duvodova_zprava'" in conn.sql[3]
    assert "left(text, 150)" in conn.sql[3]
    d = r["dokument"]
    assert d["druh"] == "tisk" and d["ct"] == 311 and d["pn"] is None and d["verze"] == "puvodni"
    assert d["url_docx"] and "t=311" in d["url_psp"] and d["dostupne_verze"] == ["puvodni"] and d["znaku"] == 50000
    assert "id" not in d
    assert r["celky"][0]["oznaceni"] == "Čl. I" and "text" not in r["celky"][0]
    assert r["celkem"] == 1 and r["dalsi_offset"] is None
    conn = FakeConn(_OK, [_dok()], [dict(n=0)], [])
    tools.osnova_textu(conn, ct=311, vcetne_duvodove=True)
    assert "duvodova_zprava" not in conn.sql[3]


def test_get_cel_podle_oznaceni_bez_mezer():
    conn = FakeConn(_OK, [_dok()], [dict(poradi=4, cesta="Čl. I / § 17c")], [_cel(4, "§ 17c")],
                    [dict(poradi=3, oznaceni="§ 17b"), dict(poradi=5, oznaceni="§ 18")])
    r = tools.get_cel(conn, ct=311, oznaceni="§17C")
    assert "regexp_replace" in conn.sql[2] and "lower" in conn.sql[2]
    assert r["cel"]["oznaceni"] == "§ 17c" and r["cel"]["text"] == "Text § 17c"
    assert "casti" not in r


def test_get_cel_kusy_jednoho_celku_vraci_prvni_a_casti():
    conn = FakeConn(_OK, [_dok()], [dict(poradi=2, cesta="Čl. I (1/2)"), dict(poradi=3, cesta="Čl. I (2/2)")],
                    [_cel(2, "Čl. I")], [dict(poradi=1, oznaceni="ČÁST PRVNÍ"), dict(poradi=4, oznaceni="Čl. II")])
    r = tools.get_cel(conn, ct=311, oznaceni="Čl. I")
    assert r["cel"]["poradi"] == 2 and r["casti"] == [2, 3]
    assert r["dalsi"] == "Čl. II" and r["dalsi_poradi"] == 4      # další celek za posledním kusem


def test_get_cel_clanek_pokracuje_za_citovanym_znenim():
    shody = [dict(poradi=1, cesta="Čl. I (1/2)"), dict(poradi=2, cesta="Čl. I (2/2)"), dict(poradi=4, cesta="Čl. I"),
             dict(poradi=6, cesta="Čl. I")]
    conn = FakeConn(_OK, [_dok()], shody, [dict(cesta="Čl. I / § 17c"), dict(cesta="Čl. I / § 20ba")],
                    [_cel(1, "Čl. I")], [dict(poradi=0, oznaceni="odst. 1"), dict(poradi=7, oznaceni="Čl. II")])
    r = tools.get_cel(conn, ct=311, oznaceni="čl.i")
    assert r["cel"]["poradi"] == 1 and r["casti"] == [1, 2, 4, 6] and r["dalsi"] == "Čl. II"
    # mezi shodami leží cizí celek (ne vnořený) = dva různé celky
    conn = FakeConn(_OK, [_dok()], [dict(poradi=1, cesta="Čl. I"), dict(poradi=5, cesta="Čl. I")],
                    [dict(cesta="Čl. II")])
    with pytest.raises(ValueError, match="poradi 5"):
        tools.get_cel(conn, ct=311, oznaceni="Čl. I")


def test_get_cel_opakovane_oznaceni_je_chyba_s_poradi():
    conn = FakeConn(_OK, [_dok()], [dict(poradi=5, cesta="Čl. I / § 10a"), dict(poradi=9, cesta="Čl. II / § 10a")])
    with pytest.raises(ValueError) as e:
        tools.get_cel(conn, ct=231, oznaceni="§ 10a")
    assert "poradi 5" in str(e.value) and "poradi 9" in str(e.value) and "Čl. II / § 10a" in str(e.value)


def test_get_cel_strankuje_8000():
    text = "slovo " * 3000  # 18 000 znaků

    def conn():
        return FakeConn(_OK, [_dok()], [_cel(0, "odst. 1", text=text)], [])
    r = tools.get_cel(conn(), ct=311, poradi=0, max_znaku=50000)
    assert len(r["cel"]["text"]) == 8000 and r["cel"]["znaku_celkem"] == 18000
    assert r["cel"]["pokracuje"] and r["cel"]["dalsi_offset_znaku"] == 8000
    parts, off = [], 0
    while off is not None:
        p = tools.get_cel(conn(), ct=311, poradi=0, offset_znaku=off)
        parts.append(p["cel"]["text"])
        off = p["cel"]["dalsi_offset_znaku"]
    assert "".join(parts) == text and len(parts) == 3


def test_get_cel_ma_predchozi_a_dalsi():
    conn = FakeConn(_OK, [_dok()], [_cel(4, "Čl. II")],
                    [dict(poradi=3, oznaceni="Čl. I"), dict(poradi=5, oznaceni="Čl. III")])
    r = tools.get_cel(conn, ct=311, poradi=4)
    assert r["predchozi"] == "Čl. I" and r["dalsi"] == "Čl. III"
    assert r["predchozi_poradi"] == 3 and r["dalsi_poradi"] == 5
    assert r["dokument"]["url_psp"] and r["cel"]["od_znaku"] == 400
    conn = FakeConn(_OK, [_dok()], [_cel(0, "Čl. I")], [])
    r = tools.get_cel(conn, ct=311, poradi=0)
    assert r["predchozi"] is None and r["dalsi"] is None


def test_get_cel_nenalezen_je_chyba():
    with pytest.raises(ValueError, match="osnova_textu"):
        tools.get_cel(FakeConn(_OK, [_dok()], []), ct=311, oznaceni="§ 999")
    with pytest.raises(ValueError, match="osnova_textu"):
        tools.get_cel(FakeConn(_OK, [_dok()], []), ct=311, poradi=999)
    with pytest.raises(ValueError, match="oznaceni nebo poradi"):
        tools.get_cel(FakeConn(), ct=311)


def test_verze_schvalene_neni_chyba():
    conn = FakeConn(_OK, [_dok(), _dok(id=8, verze="schvalene", ma_text=False, dostupny=False, chyba="x")])
    with pytest.raises(ValueError) as e:
        tools.osnova_textu(conn, ct=311, verze="schvalene")
    assert str(e.value) == 'Schválené znění tisku 311 zatím není, použijte verze="puvodni".'
    with pytest.raises(ValueError, match='verze="puvodni"'):
        tools.get_cel(FakeConn(_OK, [_dok()]), ct=311, poradi=0, verze="schvalene")


def test_verze_vychozi_je_schvalene_existuje_li():
    rows = [_dok(), _dok(id=8, verze="schvalene", docx_url="https://www.psp.cz/s.docx")]
    r = tools.osnova_textu(FakeConn(_OK, rows, [dict(n=0)], []), ct=311)
    assert r["dokument"]["verze"] == "schvalene" and r["dokument"]["dostupne_verze"] == ["puvodni", "schvalene"]
    assert r["dokument"]["url_docx"].endswith("s.docx")
    r = tools.osnova_textu(FakeConn(_OK, rows, [dict(n=0)], []), ct=311, verze="puvodni")
    assert r["dokument"]["verze"] == "puvodni" and r["dokument"]["url_docx"].endswith("x.docx")
    with pytest.raises(ValueError, match="verze"):
        tools.osnova_textu(FakeConn(), ct=311, verze="nova")


def test_ct_i_pn_zaroven_chyba():
    for kw in (dict(ct=311, pn=894), {}):
        with pytest.raises(ValueError, match="právě jedno"):
            tools.osnova_textu(FakeConn(), **kw)
        with pytest.raises(ValueError, match="právě jedno"):
            tools.get_cel(FakeConn(), poradi=0, **kw)


def test_pn_text_a_jen_pdf():
    r = tools.osnova_textu(FakeConn(_OK, [_dok(druh="pn", ct=76, cislo_pn=894)], [dict(n=0)], []), pn=894)
    assert r["dokument"]["pn"] == 894 and r["dokument"]["ct"] == 76 and "cd=894" in r["dokument"]["url_psp"]
    with pytest.raises(ValueError, match="jen PDF"):
        tools.osnova_textu(FakeConn(_OK, [_dok(druh="pn", cislo_pn=5, ma_text=False, dostupny=False,
                                               chyba="jen PDF")]), pn=5)
    with pytest.raises(ValueError, match="zatím nemám"):
        tools.osnova_textu(FakeConn(_OK, []), pn=5)
    with pytest.raises(ValueError, match="jednu verzi"):
        tools.osnova_textu(FakeConn(), pn=5, verze="schvalene")


def test_texty_jeste_nejsou():
    with pytest.raises(ValueError, match="Texty zatím nejsou k dispozici"):
        tools.osnova_textu(FakeConn([{"ok": False}]), ct=311)
    with pytest.raises(ValueError, match="Texty zatím nejsou k dispozici"):
        tools.hledej_v_textech(FakeConn([{"ok": False}]), "daně z příjmů")


def test_hledej_v_textech_kratky_dotaz_chyba():
    conn = FakeConn()
    with pytest.raises(ValueError, match="příliš krátký"):
        tools.hledej_v_textech(conn, "a v")
    assert conn.sql == []          # chyba dřív, než se sáhne do DB


def test_hledej_v_textech_uryvek_a_filtry():
    text = "Úvod. " * 60 + "Sazba daně z příjmů činí 15 %. " + "Konec. " * 60
    row = dict(druh="tisk", ct=311, cislo_pn=0, verze="puvodni", poradi=4, oznaceni="§ 16", cesta="Čl. I / § 16",
               docx_url="u", text=text, rank=0.1)
    conn = FakeConn(_OK, [dict(n=3)], [row])
    r = tools.hledej_v_textech(conn, "sazba daně", ct=311, meni_zakon="586/1992 Sb.", limit=1)
    sql = conn.sql[1]
    assert "c.tsv @@" in sql and "typ <> 'duvodova_zprava'" in sql and "c.meni_zakon = %s" in sql
    assert "schvalene" in sql          # výchozí verze: schválené znění, je-li
    v = r["vysledky"][0]
    assert "«Sazba»" in v["uryvek"] and "«daně»" in v["uryvek"] and len(v["uryvek"]) < 400
    assert v["pn"] is None and v["url_docx"] == "u" and "text" not in v and "rank" not in v
    assert r["celkem"] == 3 and r["dalsi_offset"] == 1 and r["dotaz"] == "sazb:* & dane"
    with pytest.raises(ValueError, match="druh"):
        tools.hledej_v_textech(FakeConn(_OK), "daně", druh="zakon")


def test_get_pn_nema_s_textem():
    import inspect
    assert "s_textem" not in inspect.signature(tools.get_pn).parameters
    assert "s_textem" not in inspect.signature(mcp_server.get_pn).parameters
    pn = dict(cislo=894, ct=76, id_osoba=1, osoba="A", cas=None, ai_score=1, ai_model="m", ai_json=None,
              signals=None, laws="", chars=10, docx_url=None)
    r = tools.get_pn(FakeConn([pn], _OK, [{"ok": True}], [{"ok": False}]), 894)
    assert r["osnova_dostupna"] is True and "text" not in r
    r = tools.get_pn(FakeConn([dict(pn)], [{"ok": False}], [{"ok": False}]), 894)
    assert r["osnova_dostupna"] is False


def _pn_row() -> dict:
    return dict(cislo=894, ct=76, id_osoba=1, osoba="A", cas=None, ai_score=1, ai_model="m", ai_json=None,
                signals=None, laws="", chars=10, docx_url=None)


def test_get_pn_projevy_z_projev_pn():
    radky = [dict(_speech(), druh="recnik")]
    conn = FakeConn([_pn_row()], [{"ok": False}], [{"ok": True}], [dict(n=25)], radky)
    r = tools.get_pn(conn, 894)
    assert r["projevy_celkem"] == 25
    assert len(r["projevy"]) == 1
    p = r["projevy"][0]
    assert p["klic"] == "10/35/10/6" and p["recnik"] == "Poslanec Radek Vondráček" and p["druh"] == "recnik"
    assert {"datum", "klub", "url"} <= set(p) and "text" not in p and "uryvek" not in p
    sql = " ".join(conn.sql[3:])
    assert "steno.projev_pn" in sql and "NOT p.predsedajici" in sql and "p.zruseno IS NULL" in sql
    assert "LIMIT 20" in sql and "ORDER BY p.datum, p.schuze, p.poradi" in sql
    assert conn.params[-1] == (894,) and conn.params[-2] == (894,)
    json.dumps(r, default=str)


def test_get_pn_bez_tabulky_projev_pn_nespadne():
    r = tools.get_pn(FakeConn([_pn_row()], [{"ok": False}], [{"ok": False}]), 894)
    assert r["projevy"] == [] and r["projevy_celkem"] == 0


def test_projevy_k_tisku_hleda_pres_projev_tisk():
    conn = FakeConn([dict(n=0)], [])
    tools.projevy_k_tisku(conn, 77)
    for q in conn.sql:
        assert "JOIN steno.projev_tisk pt ON pt.projev_id = p.id" in q and "pt.ct = %s" in q
        assert "p.ct = %s" not in q
    assert conn.params[0][0] == 77
    assert "p.druh" in conn.sql[1]


def test_projevy_k_tisku_filtr_druh():
    conn = FakeConn([dict(n=0)], [])
    tools.projevy_k_tisku(conn, 76, druh=["Fakticka", "ministr"])
    assert "p.druh = ANY(%s)" in conn.sql[0] and "p.druh = ANY(%s)" in conn.sql[1]
    assert ["fakticka", "ministr"] in list(conn.params[0])


@pytest.mark.parametrize("druh", [["blabla"], ["predsedajici"]])
def test_projevy_k_tisku_neznamy_druh(druh):
    with pytest.raises(ValueError, match="Platné:.*recnik"):
        tools.projevy_k_tisku(FakeConn(), 76, druh=druh)


def test_projevy_k_tisku_pozicni_argumenty_beze_zmeny():
    import inspect
    names = list(inspect.signature(tools.projevy_k_tisku).parameters)
    assert names[:10] == ["conn", "ct", "id_osoba", "klub", "od", "do", "min_znaku", "vcetne_predsedajicich",
                          "limit", "offset"]
    assert names[-1] == "druh"
    assert "druh" in inspect.signature(mcp_server.projevy_k_tisku).parameters
    d = mcp_server.projevy_k_tisku.__doc__
    assert "rozprava" in d and "druh" in d


def test_get_tisk_ma_texty():
    conn = FakeConn([_TISK_ROW], [], [{"projevu": 0, "projevu_vse": 0, "recniku": 0, "prvni": None, "posledni": None}],
                    _OK, [dict(verze="puvodni")])
    r = tools.get_tisk(conn, 231, ["body"])
    assert r["texty"] == {"puvodni": True, "schvalene": False}


def test_stav_dat_ma_texty():
    conn = FakeConn([{"projevu": 1}], [], [], _OK, [dict(dokumentu=3, celku=40, jen_pdf=1, schvalenych=1)])
    r = tools.stav_dat(conn)
    assert r["texty"] == {"dokumentu": 3, "celku": 40, "jen_pdf": 1, "schvalenych": 1}
    r = tools.stav_dat(FakeConn([{"projevu": 1}], [], [], [{"ok": False}]))
    assert r["texty"] is None


def test_get_cel_sousedni_shody_z_ruznych_clanku_jsou_chyba():
    conn = FakeConn(_OK, [_dok()], [dict(poradi=3, cesta="Čl. I / bod 1"), dict(poradi=4, cesta="Čl. II / bod 1")])
    with pytest.raises(ValueError, match="poradi 4"):
        tools.get_cel(conn, ct=311, oznaceni="bod 1")


def test_hledej_v_textech_jen_filtr():
    text = "Zákon č. 256/2000 Sb., o Státním zemědělském intervenčním fondu, se mění takto: " + "x " * 300
    row = dict(druh="tisk", ct=221, cislo_pn=0, verze="puvodni", poradi=1, oznaceni="Čl. I", cesta="Čl. I",
               docx_url="u", text=text)
    conn = FakeConn(_OK, [dict(n=1)], [row])
    r = tools.hledej_v_textech(conn, "", meni_zakon="256/2000")
    assert "tsv" not in conn.sql[1] and "ts_rank" not in conn.sql[2] and "c.meni_zakon = %s" in conn.sql[2]
    assert "ORDER BY d.ct DESC, d.cislo_pn, c.poradi" in conn.sql[2]
    v = r["vysledky"][0]
    assert v["ct"] == 221 and v["uryvek"].startswith("Zákon č. 256/2000 Sb., o Státním") and len(v["uryvek"]) <= 302
    assert r["dotaz"] is None and r["celkem"] == 1
    with pytest.raises(ValueError, match="filtr"):
        tools.hledej_v_textech(FakeConn(), "  ")
    with pytest.raises(ValueError, match="příliš krátký"):
        tools.hledej_v_textech(FakeConn(), "a v", ct=311)


def test_texty_limit_nejvys_100():
    conn = FakeConn(_OK, [dict(n=0)], [])
    tools.hledej_v_textech(conn, "daně", limit=200)
    assert conn.params[-1][-2] == 100
    conn = FakeConn(_OK, [_dok()], [dict(n=0)], [])
    tools.osnova_textu(conn, ct=311, limit=200)
    assert conn.params[-1][-2] == 100


def test_dokument_s_chybou_deleni_neni_dostupny():
    assert "d.chyba IS NULL" in tools._DOSTUPNY
    dok = _dok(dostupny=False, chyba="dělení selhalo: chyba zpracování: ValueError: x")
    with pytest.raises(ValueError, match=r"nepodařilo zpracovat \(dělení selhalo"):
        tools.osnova_textu(FakeConn(_OK, [dok]), ct=311)
    with pytest.raises(ValueError, match="dělení selhalo"):
        tools.get_cel(FakeConn(_OK, [dict(dok)]), ct=311, poradi=0)


def test_hledej_v_textech_jen_dostupne_dokumenty():
    conn = FakeConn(_OK, [dict(n=0)], [])
    tools.hledej_v_textech(conn, "daně", ct=311)
    for sql in conn.sql[1:]:
        assert tools._DOSTUPNY in sql and "s.chyba IS NULL" in sql


_KLUBY_POSL = [dict(klub="ANO2011", klub_nazev="Poslanecký klub ANO 2011"),
               dict(klub="ODS", klub_nazev="Poslanecký klub Občanské demokratické strany"),
               dict(klub="SPD", klub_nazev="Poslanecký klub Svoboda a přímá demokracie")]


def test_klub_bez_uvodu_poslanecky_klub():
    assert tools._resolve_klub(FakeConn(_KLUBY_POSL), "Svoboda") == ["SPD"]
    assert tools._resolve_klub(FakeConn(_KLUBY_POSL), "obcanske") == ["ODS"]
    with pytest.raises(ValueError, match="Neznámý klub"):
        tools._resolve_klub(FakeConn(_KLUBY_POSL), "Posl")


def test_stav_dat_ma_vysledek_ingestu_textu():
    raw = json.dumps({"seconds": 1, "texty": {"ok": True, "prevzato": 2, "stazeno": 3, "chyby": 1}})
    r = tools.stav_dat(FakeConn([{"projevu": 1}], [{"v": raw}], [], [{"ok": False}]))
    assert r["steno_last_result"]["texty_ok"] is True
    assert r["steno_last_result"]["texty"] == {"prevzato": 2, "stazeno": 3, "chyby": 1}
    r = tools.stav_dat(FakeConn([{"projevu": 1}], [{"v": json.dumps({"texty": {"ok": False}})}], [], [{"ok": False}]))
    assert r["steno_last_result"]["texty_ok"] is False and r["steno_last_result"]["texty"] is None
