"""Nástroj rozprava: celá rozprava k tisku v pořadí (přehled i plné texty), rozpočet, stránkování, souhrn a chyby.

Testy bez databáze používají falešné spojení jako tests/test_tools.py (řádky v pořadí dotazů); pg testy ověří SQL
nad PostgreSQL se syntetickými řádky (bez DATABASE_URL se přeskočí)."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import date

import pytest

from hlidac_rag import mcp_server, tools
from hlidac_rag.nastroje import rozprava as modul
from hlidac_rag.nastroje.rozprava import rozprava


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


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


BEZ_TABULEK = [{"tisky": False, "kroky": False}]
S_KROKY = [{"tisky": True, "kroky": True}]
NAZEV = [{"nazev": "Návrh zákona o státních zaměstnancích"}]


def _v(i: int, **kw) -> dict:
    """Řádek dotazu na vystoupení (nezrušený nepředsedající projev tisku) s id i."""
    row = dict(id=i, schuze=34, turn=600 + i, aname=1, datum=date(2026, 10, 23), id_osoba=1000 + i % 5,
               recnik=f"Poslanec Jan Novák{i % 5}", jmeno=f"Jan Novák{i % 5}", klub="ANO2011", druh="recnik",
               znaku=500, autorizovano=True, reaguje_na_odhad=None, zacatek="Děkuji za slovo, pane předsedající.",
               pn_zminene=[], cteni=None)
    row.update(kw)
    return row


def _t(r: dict, text: str | None = None, ohlaseni: str | None = None) -> dict:
    """Řádek dotazu na texty stránky (plny) k vystoupení r."""
    return dict(id=r["id"], text=text if text is not None else "x" * r["znaku"],
                url=f"https://www.psp.cz/x/s034{r['turn']}.htm#r1", ohlaseni=ohlaseni)


def _conn(rows: list[dict], texty: list[dict] | None = None, tab=BEZ_TABULEK) -> FakeConn:
    """Spojení pro jedno volání: tabulky, vystoupení, název tisku a (u plny) texty."""
    vysledky = [tab, rows, NAZEV]
    if texty is not None:
        vysledky.append(texty)
    return FakeConn(*vysledky)


def _json_len(x) -> int:
    return len(json.dumps(x, ensure_ascii=False, separators=(",", ":"), default=str))


# ---------------------------------------------------------------- přehled

def test_prehled_hlavicka_a_kompaktni_radky():
    rows = [_v(1),
            _v(2, druh="fakticka", reaguje_na_odhad="Karel Haas", pn_zminene=[2244, 2245]),
            _v(3, autorizovano=False, klub=None, reaguje_na_odhad="nevypisuje se u recnik")]
    conn = _conn(rows)
    r = rozprava(conn, 76)
    assert r["ct"] == 76 and r["nazev"] == NAZEV[0]["nazev"]
    assert r["url_psp"].endswith("t=76") and r["url_web"].endswith("/tisk/76")
    assert r["format"] == "prehled" and r["celkem"] == 3 and r["znaku_celkem"] == 1500
    assert r["od_poradi"] == 1 and r["dalsi_od_poradi"] is None
    assert "odvozené automaticky" in r["poznamka"] and "ověřujte" in r["poznamka"]
    v1, v2, v3 = r["vystoupeni"]
    assert v1 == {"poradi": 1, "klic": "10/34/601/1", "datum": "2026-10-23", "recnik": "Poslanec Jan Novák1",
                  "klub": "ANO2011", "druh": "recnik", "znaku": 500,
                  "zacatek": "Děkuji za slovo, pane předsedající."}
    assert v2["pn_zminene"] == [2244, 2245] and v2["reaguje_na_odhad"] == "Karel Haas" and v2["poradi"] == 2
    # neautorizované jen příznakem (poznámka je až v plném formátu), prázdná pole se vynechají
    assert v3["neautorizovano"] is True and "poznamka" not in v3 and "klub" not in v3
    assert "reaguje_na_odhad" not in v3 and "cteni" not in v3
    for v in r["vystoupeni"]:
        assert not {"text", "url", "ohlaseni", "id", "schuze", "turn", "aname"} & set(v)
    # vystoupení přes projev_tisk bez předsedajících a zrušených, v pořadí datum, schůze, pořadí
    sql = conn.sql[1]
    assert "steno.projev_tisk" in sql and "zruseno IS NULL" in sql and "NOT p.predsedajici" in sql
    # úsek bez řečníka (aname 0: začátek části bez kotvy) vystoupením není, stejně jako v přepočtu
    assert "(p.id_osoba IS NOT NULL OR nullif(p.recnik, '') IS NOT NULL)" in sql
    assert "ORDER BY p.datum, p.schuze, p.poradi" in sql and "steno.projev_pn" in sql
    assert "kroky" not in sql                     # bez tabulky kroky se čtení neurčuje
    assert len(conn.sql) == 3                     # přehled nečte celé texty
    json.dumps(r, ensure_ascii=False)


def test_prehled_rozpocet_a_strankovani_bez_mezer():
    rows = [_v(i) for i in range(1, 51)]
    stran, poradi, od = 0, [], 0
    while od is not None:
        r = rozprava(_conn([dict(x) for x in rows]), 76, max_znaku=2000, od_poradi=od)
        assert _json_len(r["vystoupeni"]) <= 2001
        assert r["vystoupeni"] and r["od_poradi"] == (od or 1)
        poradi += [v["poradi"] for v in r["vystoupeni"]]
        assert all(v["klic"] == f"10/34/{600 + v['poradi']}/1" for v in r["vystoupeni"])
        od = r["dalsi_od_poradi"]
        stran += 1
    assert poradi == list(range(1, 51)) and stran > 1


def test_max_znaku_omezen_na_1000_az_40000():
    rows = [_v(i, zacatek="slovo " * 25) for i in range(1, 301)]
    r = rozprava(_conn(rows), 76, max_znaku=10)
    assert 0 < _json_len(r["vystoupeni"]) <= 1001
    r = rozprava(_conn([dict(x) for x in rows]), 76, max_znaku=10 ** 6)
    assert 30000 < _json_len(r["vystoupeni"]) <= 40001


def test_od_poradi_za_koncem_vrati_prazdnou_stranku():
    r = rozprava(_conn([_v(1), _v(2)]), 76, od_poradi=5)
    assert r["vystoupeni"] == [] and r["dalsi_od_poradi"] is None and r["celkem"] == 2


# ---------------------------------------------------------------- souhrn

def test_souhrn_uplny_i_pri_strankovani():
    rows = [_v(i, id_osoba=2000 + i % 70, jmeno=f"Jméno {i % 70}", klub=["ANO2011", "ODS", "SPD"][i % 70 % 3],
               druh="fakticka" if i % 4 == 0 else "recnik", znaku=3000) for i in range(300)]
    souhrny = []
    for od in (0, 150):
        r = rozprava(_conn([dict(x) for x in rows]), 231, max_znaku=1000, od_poradi=od)
        assert len(r["vystoupeni"]) < 10            # stránka je malá, souhrn přesto úplný
        souhrny.append(r["souhrn"])
    assert souhrny[0] == souhrny[1]
    recnici = souhrny[0]["recnici"]
    assert len(recnici) == 70 and sum(s["vystoupeni"] for s in recnici) == 300
    assert [s["id_osoba"] for s in recnici] == [2000 + i for i in range(70)]    # pořadí prvního vystoupení
    assert recnici[0] == {"id_osoba": 2000, "jmeno": "Jméno 0", "klub": "ANO2011", "vystoupeni": 5, "znaku": 15000,
                          "druhy": {"fakticka": 3, "recnik": 2}}
    assert sum(s["druhy"].get("fakticka", 0) for s in recnici) == 75
    kluby = souhrny[0]["kluby"]
    assert sum(k["vystoupeni"] for k in kluby) == 300 and sum(k["recniku"] for k in kluby) == 70
    assert sum(k["znaku"] for k in kluby) == 900000
    assert [k["vystoupeni"] for k in kluby] == sorted((k["vystoupeni"] for k in kluby), reverse=True)


def test_souhrn_recnik_bez_osoby_podle_textu_odkazu():
    rows = [_v(1, id_osoba=None, jmeno=None, recnik="Poslanec Blažej", klub=None),
            _v(2, id_osoba=None, jmeno=None, recnik="Poslanec Blažej", klub=None)]
    r = rozprava(_conn(rows), 76)
    assert r["souhrn"]["recnici"] == [{"jmeno": "Poslanec Blažej", "vystoupeni": 2, "znaku": 1000,
                                       "druhy": {"recnik": 2}}]
    assert r["souhrn"]["kluby"] == [{"klub": None, "vystoupeni": 2, "znaku": 1000, "recniku": 1}]


# ---------------------------------------------------------------- plné texty

def test_plny_texty_do_rozpoctu_a_strankovani():
    rows = [_v(i, znaku=3000) for i in range(1, 21)]
    texty = {i: (f"{i:03d} " + "slovo " * 500)[:3000] for i in range(1, 21)}
    precteno, od, volani = {}, 0, 0
    while od is not None:
        conn = _conn([dict(x) for x in rows], [_t(x, texty[x["id"]]) for x in rows])
        r = rozprava(conn, 76, format="plny", max_znaku=10000, od_poradi=od)
        volani += 1
        assert r["format"] == "plny" and _json_len(r["vystoupeni"]) <= 10001
        assert len(conn.params[3][0]) <= 4           # texty se čtou jen pro stránku (podle znaku), ne pro všechna
        assert "p.id = ANY(%s)" in conn.sql[3]
        for v in r["vystoupeni"]:
            assert v["url"].startswith("https://") and "zacatek" not in v and "zkraceno" not in v
            precteno[v["poradi"]] = v["text"]
        od = r["dalsi_od_poradi"]
    assert sorted(precteno) == list(range(1, 21)) and volani == 7
    assert all(precteno[i] == texty[i] for i in range(1, 21))


def test_plny_prvni_vystoupeni_nad_rozpocet_se_zkrati():
    text = "Děkuji za slovo.\n\n" + "dlouhý projev " * 4000                        # přes 56 000 znaků
    rows = [_v(1, znaku=len(text)), _v(2, znaku=300)]
    r = rozprava(_conn(rows, [_t(rows[0], text), _t(rows[1])]), 76, format="plny", max_znaku=5000)
    (v,) = r["vystoupeni"]
    assert v["zkraceno"] is True and v["znaku"] == len(text)
    assert text.startswith(v["text"]) and 4000 < len(v["text"]) < 5000
    assert v["pokracovani"] == f'get_projev("10/34/601/1", offset_znaku={len(v["text"])})'
    assert _json_len(r["vystoupeni"]) <= 5001
    assert r["dalsi_od_poradi"] == 2
    r = rozprava(_conn([dict(x) for x in rows], [_t(rows[1])]), 76, format="plny", max_znaku=5000, od_poradi=2)
    assert [v["poradi"] for v in r["vystoupeni"]] == [2] and "zkraceno" not in r["vystoupeni"][0]
    assert r["dalsi_od_poradi"] is None


def test_ohlaseni_fakticke_jen_v_plnem_formatu():
    ohlaseni = "Děkuji. Nyní s faktickou poznámkou pan poslanec Berki. Prosím, vaše 2 minuty."
    dlouhe = "slovo " * 40 + ohlaseni                                            # konec delšího projevu (300 znaků)
    presne = "x" * (300 - len(ohlaseni) - 1) + " " + ohlaseni                  # text předsedajícího přesně 300 znaků
    rows = [_v(1, druh="fakticka"), _v(2, druh="fakticka"), _v(3), _v(4, druh="fakticka"), _v(5, druh="recnik")]
    # dotaz vrací konec o znak delší než OHLASENI: delší text pozná podle 301. znaku
    texty = [_t(rows[0], ohlaseni=ohlaseni), _t(rows[1], ohlaseni=dlouhe[-301:]),
             _t(rows[2], ohlaseni="Děkuji. Další v rozpravě pan poslanec Haas, prosím."),
             _t(rows[3], ohlaseni=presne),
             _t(rows[4], ohlaseni="Faktické poznámky nejsou, nyní v rozpravě pan poslanec Haas.")]
    r = rozprava(_conn(rows, texty), 76, format="plny")
    v1, v2, v3, v4, v5 = r["vystoupeni"]
    assert v1["ohlaseni"] == ohlaseni
    assert v2["ohlaseni"].startswith("… slovo") and v2["ohlaseni"].endswith(ohlaseni) and len(v2["ohlaseni"]) <= 302
    assert "ohlaseni" not in v3                       # předsedající faktickou neohlašuje
    assert v4["ohlaseni"] == presne                   # přesně 300 znaků = celý text, první slovo zůstane
    assert "ohlaseni" not in v5                       # zmínka o faktických u vystoupení jiného druhu
    # konec předchozího projevu: jen předsedající téhož dne, zrušené se přeskočí
    conn = _conn([_v(1, druh="fakticka")], [_t(_v(1), ohlaseni=ohlaseni)])
    rozprava(conn, 76, format="plny")
    assert "q.predsedajici" in conn.sql[3] and "q.datum = p.datum" in conn.sql[3]
    assert "q.zruseno IS NULL" in conn.sql[3] and "right(q.text, 301)" in conn.sql[3]
    r = rozprava(_conn([_v(1, druh="fakticka")]), 76)
    assert "ohlaseni" not in r["vystoupeni"][0]


def test_plny_neautorizovany_ma_poznamku():
    rows = [_v(1, autorizovano=False), _v(2)]
    r = rozprava(_conn(rows, [_t(x) for x in rows]), 76, format="plny")
    v1, v2 = r["vystoupeni"]
    assert v1["neautorizovano"] is True and v1["poznamka"] == tools.NOTE_UNAUTH
    assert "poznamka" not in v2 and "neautorizovano" not in v2


# ---------------------------------------------------------------- filtry

def _ruzne() -> list[dict]:
    return [_v(1, id_osoba=1, druh="zpravodaj", cteni="1"), _v(2, id_osoba=2, druh="recnik", cteni="2"),
            _v(3, id_osoba=1, druh="fakticka", cteni="2"), _v(4, id_osoba=3, druh="fakticka", cteni="2"),
            _v(5, id_osoba=1, druh="recnik", cteni="senat")]


def test_filtry_cisluji_vysledek_od_jedne():
    r = rozprava(_conn(_ruzne(), tab=S_KROKY), 76, osoby=[1], druh=["fakticka", "recnik"])
    assert [(v["poradi"], v["klic"]) for v in r["vystoupeni"]] == [(1, "10/34/603/1"), (2, "10/34/605/1")]
    assert r["celkem"] == 2 and r["souhrn"]["recnici"][0]["vystoupeni"] == 2
    r = rozprava(_conn(_ruzne(), tab=S_KROKY), 76, cteni="2")
    assert [v["klic"] for v in r["vystoupeni"]] == ["10/34/602/1", "10/34/603/1", "10/34/604/1"]
    assert [v["cteni"] for v in r["vystoupeni"]] == ["2", "2", "2"]
    r = rozprava(_conn(_ruzne(), tab=S_KROKY), 76, cteni="Senát", druh=["Fakticka"])   # tvar hodnot nevadí
    assert r["celkem"] == 0 and r["vystoupeni"] == [] and r["dalsi_od_poradi"] is None
    assert r["souhrn"] == {"recnici": [], "kluby": []} and r["znaku_celkem"] == 0


def test_cteni_z_tabulky_kroky():
    conn = _conn(_ruzne(), tab=S_KROKY)
    rozprava(conn, 76)
    sql = conn.sql[1]
    assert "FROM kroky" in sql and "left(datum, 10)::date" in sql
    # R9b: 1. čtení kterýmkoli krokem (i § 90/2 bez „zahájeno“), 2. a 3. čtení krokem projednávání; čtení
    # začíná prvním krokem úseku po sobě jdoucích kroků téhož čtení
    assert r"stav ~ '^1\. čtení'" in sql and r"stav ~ '^[23]\. čtení'" in sql
    assert "akce IN ('zahájeno', 'projednávání ukončeno', 'ukončena')" in sql and "akce_id IN (238, 71, 23)" in sql
    assert "lag(cteni) OVER (ORDER BY datum, id_hist)" in sql and "predchozi IS DISTINCT FROM cteni" in sql
    assert "Sněmovna (Senát)" in sql and "Sněmovna (Prezident)" in sql
    assert "ORDER BY k.datum DESC, k.id_hist DESC" in sql
    assert conn.params[1] == (76, 76)
    assert "FROM tisky" in conn.sql[2]               # název z tabulky tisky, existuje-li


# ---------------------------------------------------------------- chyby

@pytest.mark.parametrize("kw, zprava", [
    (dict(format="cely"), "Platné: prehled"),
    (dict(druh=["predsedajici"]), "Platné: recnik, fakticka, prednostni, zpravodaj, predkladatel, ministr, proceduralni."),
    (dict(druh=["fakticka", "projev"]), "Neznámý druh: projev"),
    (dict(cteni="4"), "Platné: 1, 2, 3, senat"),
])
def test_neplatne_hodnoty_jsou_chyba_pred_dotazem(kw, zprava):
    conn = FakeConn()
    with pytest.raises(ValueError, match=zprava.replace(".", r"\.")):
        rozprava(conn, 76, **kw)
    assert conn.sql == []


def test_cteni_bez_tabulky_kroky_je_chyba():
    with pytest.raises(ValueError, match="kroky"):
        rozprava(FakeConn(BEZ_TABULEK), 76, cteni="2")


def test_tisk_bez_vystoupeni_je_chyba():
    with pytest.raises(ValueError, match="K tisku 999 nejsou") as e:
        rozprava(FakeConn(BEZ_TABULEK, []), 999)
    assert "get_tisk(999)" in str(e.value)


def test_hodnoty_bez_diakritiky_a_velikosti():
    r = rozprava(_conn([_v(1)], [_t(_v(1))]), 76, format="Plný")
    assert r["format"] == "plny" and r["vystoupeni"][0]["text"]


# ---------------------------------------------------------------- strop odpovědi a registrace

def _velka_rozprava(n: int, recniku: int, znaku: int) -> tuple[list[dict], list[dict]]:
    rows = [_v(i, id_osoba=3000 + i % recniku, jmeno=f"Místopředsedkyně Jméno Příjmení {i % recniku}",
               recnik="Místopředsedkyně vlády a ministryně financí Alena Schillerová", klub="KDU-ČSL",
               druh=modul.DRUHY_FILTRU[i % 7], znaku=znaku, autorizovano=i % 2 == 0, pn_zminene=[2240, 2241, 2242],
               reaguje_na_odhad="Karel Haas", cteni="2", zacatek="\"Děkuji\" " * 20) for i in range(1, n + 1)]
    texty = [_t(r, ("\"slovo\"\n" * (znaku // 8 + 1))[:znaku], ohlaseni="faktick " * 50) for r in rows]
    return rows, texty


@pytest.mark.parametrize("n, recniku, znaku", [(300, 70, 3000), (527, 59, 1750), (300, 300, 200), (260, 70, 150)])
@pytest.mark.parametrize("format", ["prehled", "plny"])
@pytest.mark.parametrize("max_znaku", [30000, 40000, 10 ** 6])
def test_odpoved_projde_stropem_serveru(monkeypatch, n, recniku, znaku, format, max_znaku):
    rows, texty = _velka_rozprava(n, recniku, znaku)
    monkeypatch.setattr(mcp_server, "pool", FakePool(_conn(rows, texty if format == "plny" else None)))
    tool = mcp_server.server._tool_manager.get_tool("rozprava")
    r = tool.fn(ct=231, format=format, max_znaku=max_znaku)    # mcp_server._call: strop MAX_ODPOVED
    assert _json_len(r) < mcp_server.MAX_ODPOVED and r["vystoupeni"]
    assert len(r["souhrn"]["recnici"]) == recniku


def test_strop_shodny_se_serverem():
    assert modul.STROP == mcp_server.MAX_ODPOVED


def test_nastroj_je_registrovan():
    assert "rozprava" in mcp_server.MODULY_NASTROJU
    tool = mcp_server.server._tool_manager.get_tool("rozprava")
    assert tool is not None
    assert list(tool.parameters["properties"]) == ["ct", "format", "osoby", "cteni", "druh", "od_poradi", "max_znaku"]
    assert tool.parameters["required"] == ["ct"]
    for slovo in ("projevy_k_tisku", "get_projev", "dalsi_od_poradi", "prehled", "plny", "stejnými filtry"):
        assert slovo in tool.description, slovo


# ---------------------------------------------------------------- PostgreSQL

bez_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)")
OBD, SCHUZE, SCHUZE2 = 10, 980, 981
DEN1, DEN2, DEN0 = date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 1)
SCHEMA_WEB = "test_rozprava_nastroj"
HAAS = (2001, "Poslanec Karel Haas", "ANO2011")
BERKI = (2002, "Poslanec Jan Berki", "ODS")
CHAIR = (2003, "Místopředseda PSP Jan Skopeček", None)
OHLASENI = "Děkuji. Nyní s faktickou poznámkou pan poslanec Berki. Prosím, vaše 2 minuty."
BOD76 = 70001


def _pg_projevy() -> list[tuple]:
    """(schuze, poradi, datum, řečník, předsedající, druh, reaguje, autorizováno, zrušeno, tisky, pn, text)."""
    return [
        (SCHUZE, 1, DEN1, CHAIR, True, "predsedajici", None, True, False, [76], [], "Otevírám rozpravu, pane poslanče."),
        (SCHUZE, 2, DEN1, HAAS, False, "recnik", None, True, False, [76], [2245, 2244],
         "Děkuji za slovo. Pozměňovací návrhy pod sněmovním dokumentem 2244 a 2245 načtu v podrobné rozpravě."),
        (SCHUZE, 3, DEN1, CHAIR, True, "predsedajici", None, True, False, [76], [], OHLASENI),
        (SCHUZE, 4, DEN1, HAAS, False, "recnik", None, True, True, [76], [], "Zrušený projev."),
        (SCHUZE, 5, DEN1, BERKI, False, "fakticka", "Karel Haas", False, False, [76],  [],
         "Děkuju. Vaším prostřednictvím bych chtěl reagovat na Karla Haase."),
        (SCHUZE, 6, DEN1, BERKI, False, "recnik", None, True, False, [77], [], "Jen k tisku 77."),
        (SCHUZE, 7, DEN1, HAAS, False, "recnik", None, True, False, [76, 77], [], "Ke sloučené rozpravě."),
        (SCHUZE, 8, DEN1, CHAIR, True, "predsedajici", None, True, False, [76], [], "Další faktická poznámka zítra."),
        (SCHUZE, 9, DEN2, BERKI, False, "fakticka", None, True, False, [76], [], "Dobré ráno, navážu na včerejšek."),
        (SCHUZE2, 1, DEN0, HAAS, False, "zpravodaj", None, True, False, [76], [], "Jako zpravodaj uvádím."),
    ]


@pytest.fixture
def pgconn():
    from hlidac_rag import db

    c = db.connect()
    # pojistka: test maže tabulky steno, smí běžet jen nad testovací databází
    jmeno = c.execute("SELECT current_database()").fetchone()[0]
    if not jmeno.endswith("_test"):
        c.close()
        pytest.skip(f"databáze {jmeno} nekončí na _test")
    db.migrate(c)
    _vycisti(c)
    with c.cursor() as cur:
        cur.executemany("INSERT INTO steno.osoba (id_osoba, jmeno, prijmeni) VALUES (%s,%s,%s)",
                        [(2001, "Karel", "Haas"), (2002, "Jan", "Berki"), (2003, "Jan", "Skopeček")])
        cur.execute("""INSERT INTO steno.bod (id_bod, obdobi, schuze, cislo, nazev, konec, ct)
                       VALUES (%s, %s, %s, 5, 'Vládní návrh zákona o státních zaměstnancích', '/sněmovní tisk 76/', 76)""",
                    (BOD76, OBD, SCHUZE))
        _vloz_projevy(cur, _pg_projevy())
    c.commit()
    yield c
    c.rollback()
    _vycisti(c)
    c.commit()
    c.close()


def _vloz_projevy(cur, projevy: list[tuple]) -> None:
    """Vloží projevy ve tvaru _pg_projevy() s vazbami projev_tisk a projev_pn."""
    for schuze, poradi, datum, (id_osoba, recnik, klub), chair, druh, reaguje, aut, zrus, tisky, pn, text in projevy:
        cur.execute("""INSERT INTO steno.projev (obdobi, schuze, turn, aname, datum, poradi, id_osoba, recnik, klub,
                              predsedajici, text, znaku, hash, url, autorizovano, druh, reaguje_na_odhad, zruseno)
                       VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,'h',%s,%s,%s,%s,%s) RETURNING id""",
                    (OBD, schuze, poradi, datum, poradi, id_osoba, recnik, klub, chair, text, len(text),
                     f"https://www.psp.cz/s{schuze}{poradi:03d}.htm#r1", aut, druh, reaguje,
                     "2026-10-20" if zrus else None))
        pid = cur.fetchone()[0]
        cur.executemany("INSERT INTO steno.projev_tisk (projev_id, ct, zdroj) VALUES (%s, %s, 'bod')",
                        [(pid, t) for t in tisky])
        cur.executemany("INSERT INTO steno.projev_pn (projev_id, cislo_pn) VALUES (%s, %s)", [(pid, n) for n in pn])


def _tabulky_webu(conn, kroky: list[tuple], tisky: list[tuple] = ()) -> None:
    """Tabulky webu tisky a kroky ve vlastním schématu (kroky.datum je varchar(19) jako na webu), search_path na něj."""
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {SCHEMA_WEB}")
        cur.execute(f"CREATE TABLE {SCHEMA_WEB}.tisky (ct int, nazev text)")
        cur.execute(f"""CREATE TABLE {SCHEMA_WEB}.kroky (id_hist int PRIMARY KEY, id_tisk int, ct int, datum varchar(19),
                        typ_stavu int, stav varchar(120), akce_id int, akce varchar(120), id_hlasovani int)""")
        cur.executemany(f"INSERT INTO {SCHEMA_WEB}.tisky VALUES (%s, %s)", tisky)
        # krok = (id_hist, ct, datum, stav, akce[, akce_id])
        cur.executemany(f"""INSERT INTO {SCHEMA_WEB}.kroky (id_hist, ct, datum, stav, akce, akce_id)
                            VALUES (%s, %s, %s, %s, %s, %s)""", [(*k, None)[:6] for k in kroky])
        cur.execute(f"SET search_path TO {SCHEMA_WEB}, public")
    conn.commit()


def _vycisti(c):
    with c.cursor() as cur:
        cur.execute("TRUNCATE steno.projev, steno.bod, steno.osoba CASCADE")
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA_WEB} CASCADE")


@pytest.mark.pg
@bez_db
def test_pg_rozprava_nad_tabulkami_rozpravy(pgconn):
    r = rozprava(pgconn, 76)
    # schůze 981 má dřívější datum: řazení podle data, pak schůze a pořadí; bez předsedajících, zrušených a tisku 77
    assert [v["klic"] for v in r["vystoupeni"]] == ["10/981/1/1", "10/980/2/1", "10/980/5/1", "10/980/7/1",
                                                     "10/980/9/1"]
    assert [v["poradi"] for v in r["vystoupeni"]] == [1, 2, 3, 4, 5]
    assert r["nazev"] == "Vládní návrh zákona o státních zaměstnancích" and r["celkem"] == 5
    v = {x["poradi"]: x for x in r["vystoupeni"]}
    assert v[1]["druh"] == "zpravodaj" and v[1]["datum"] == "2026-10-01"
    assert v[2]["pn_zminene"] == [2244, 2245] and v[2]["recnik"] == "Poslanec Karel Haas" and v[2]["klub"] == "ANO2011"
    assert v[3]["reaguje_na_odhad"] == "Karel Haas" and v[3]["neautorizovano"] is True
    assert v[2]["zacatek"].startswith("Děkuji za slovo. Pozměňovací")
    assert all("cteni" not in x for x in r["vystoupeni"])          # testovací DB nemá tabulku kroky
    assert r["souhrn"]["recnici"] == [
        {"id_osoba": 2001, "jmeno": "Karel Haas", "klub": "ANO2011", "vystoupeni": 3, "znaku": r["souhrn"]["recnici"][0]["znaku"],
         "druhy": {"zpravodaj": 1, "recnik": 2}},
        {"id_osoba": 2002, "jmeno": "Jan Berki", "klub": "ODS", "vystoupeni": 2, "znaku": r["souhrn"]["recnici"][1]["znaku"],
         "druhy": {"fakticka": 2}}]
    assert r["souhrn"]["kluby"] == [{"klub": "ANO2011", "vystoupeni": 3, "znaku": r["souhrn"]["recnici"][0]["znaku"],
                                     "recniku": 1},
                                    {"klub": "ODS", "vystoupeni": 2, "znaku": r["souhrn"]["recnici"][1]["znaku"],
                                     "recniku": 1}]

    p = rozprava(pgconn, 76, format="plny")
    texty = {x[1]: x[11] for x in _pg_projevy() if x[0] == SCHUZE}
    w = {x["poradi"]: x for x in p["vystoupeni"]}
    assert [x["klic"] for x in p["vystoupeni"]] == [x["klic"] for x in r["vystoupeni"]]
    assert w[2]["text"] == texty[2] and w[2]["url"] == "https://www.psp.cz/s980002.htm#r1"
    # ohlášení: předchozí nezrušený projev je předsedající téhož dne a ohlašuje faktickou (zrušený se přeskočí)
    assert w[3]["ohlaseni"] == OHLASENI and w[3]["poznamka"] == tools.NOTE_UNAUTH
    assert "ohlaseni" not in w[2]                     # předsedající neohlásil faktickou
    assert "ohlaseni" not in w[5]                     # předsedající s „faktickou“ mluvil předchozí den

    f = rozprava(pgconn, 76, druh=["fakticka"])
    assert [(x["poradi"], x["klic"]) for x in f["vystoupeni"]] == [(1, "10/980/5/1"), (2, "10/980/9/1")]
    assert [x["klic"] for x in rozprava(pgconn, 76, osoby=[2002])["vystoupeni"]] == ["10/980/5/1", "10/980/9/1"]
    assert [x["klic"] for x in rozprava(pgconn, 77)["vystoupeni"]] == ["10/980/6/1", "10/980/7/1"]
    with pytest.raises(ValueError, match="kroky"):
        rozprava(pgconn, 76, cteni="2")
    with pytest.raises(ValueError, match="K tisku 78 nejsou"):
        rozprava(pgconn, 78)


@pytest.mark.pg
@bez_db
def test_pg_cteni_z_kroku_a_nazev_z_tisku(pgconn):
    """Tabulky webu tisky a kroky vytvoří test ve vlastním schématu (kroky.datum je varchar(19) jako na webu)."""
    _tabulky_webu(pgconn, [
        (1, 76, "2026-01-29 00:00:00", "1. čtení", "zahájeno"),
        (2, 76, "2026-01-14 00:00:00", "1. čtení", "doporučen"),
        (3, 76, "2026-09-30 00:00:00", "2. čtení - obecná rozprava", "zahájeno"),
        (4, 76, "2026-10-14 00:00:00", "3. čtení", "zahájeno"),
        (5, 76, "2026-10-15 10:06:00", "Sněmovna (Senát)", "rozeslán"),
        (6, 76, "2026-10-14 12:00:00", "3. čtení", "projednávání ukončeno"),   # týž úsek 3. čtení, nic nemění
        (7, 76, "", "1. čtení", "zahájeno"),                           # nečitelné datum se přeskočí
        (8, 76, None, "1. čtení", "zahájeno"),
        (9, 77, "2026-10-15 00:00:00", "Sněmovna (Prezident)", "rozeslán"),   # jiný tisk
    ], [(76, "Návrh zákona o státních zaměstnancích")])

    r = rozprava(pgconn, 76)
    assert r["nazev"] == "Návrh zákona o státních zaměstnancích"
    assert [(v["klic"], v["cteni"]) for v in r["vystoupeni"]] == [
        ("10/981/1/1", "2"), ("10/980/2/1", "3"), ("10/980/5/1", "3"), ("10/980/7/1", "3"), ("10/980/9/1", "senat")]
    assert [v["klic"] for v in rozprava(pgconn, 76, cteni="senat")["vystoupeni"]] == ["10/980/9/1"]
    t = rozprava(pgconn, 76, cteni="3", format="plny")
    assert [(v["poradi"], v["klic"]) for v in t["vystoupeni"]] == [(1, "10/980/2/1"), (2, "10/980/5/1"),
                                                                   (3, "10/980/7/1")]
    assert t["vystoupeni"][1]["ohlaseni"] == OHLASENI
    assert rozprava(pgconn, 76, cteni="prezident")["celkem"] == 0


@pytest.mark.pg
@bez_db
def test_pg_cteni_1_u_paragrafu_90(pgconn):
    """§ 90/2 (R9b): 1. čtení má jen krok „1. čtení (§ 90, odst. 2) - ob. rozprava | doporučen“, nikdy „zahájeno“;
    1. čtení začíná kterýmkoli krokem „1. čtení…“, 2. a 3. čtení jen krokem projednávání (zahájeno, ukončeno)."""
    s90, pred, den, podrobna = 982, date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 25)
    with pgconn.cursor() as cur:
        _vloz_projevy(cur, [
            (s90, 1, pred, HAAS, False, "recnik", None, True, False, [85], [], "Den před prvním čtením."),
            (s90, 2, den, HAAS, False, "predkladatel", None, True, False, [85], [], "Uvádím návrh podle § 90 odst. 2."),
            (s90, 3, podrobna, BERKI, False, "recnik", None, True, False, [85], [], "Podrobná rozprava."),
        ])
    pgconn.commit()
    _tabulky_webu(pgconn, [
        (21, 85, "2026-02-20 00:00:00", "Přijímáno do PS", "předložen"),
        (22, 85, "2026-03-03 00:00:00", "1. čtení (§ 90, odst. 2) - ob. rozprava", "doporučen"),
        (23, 85, "2026-03-25 00:00:00", "1. čtení (§ 90, odst. 2) - podr. rozprava", "souhlas s RJ"),
        (24, 85, "2026-03-25 00:00:00", "3. čtení", "rozeslán"),                   # administrativní krok se nepočítá
    ])
    r = rozprava(pgconn, 85)
    assert [(v["klic"], v.get("cteni")) for v in r["vystoupeni"]] == [
        ("10/982/1/1", None), ("10/982/2/1", "1"), ("10/982/3/1", "1")]
    f = rozprava(pgconn, 85, cteni="1")
    assert [(v["poradi"], v["klic"]) for v in f["vystoupeni"]] == [(1, "10/982/2/1"), (2, "10/982/3/1")]


@pytest.mark.pg
@bez_db
@pytest.mark.parametrize("ukonceno", [("projednávání ukončeno", None), (None, 71)])
def test_pg_cteni_od_prvniho_kroku_useku(pgconn, ukonceno):
    """Tisk 198: „3. čtení | projednávání ukončeno“ (2. 7.) je v historii dřív než „3. čtení | zahájeno“ (8. 7.).
    Čtení začíná prvním krokem každého úseku po sobě jdoucích kroků téhož čtení (2. a 3. čtení akce zahájeno /
    projednávání ukončeno / ukončena jako Scorer.php, podle textu nebo akce_id 238 / 71 / 23): pozdější krok
    téhož úseku nic nemění, opakované 2. čtení po vrácení (tisk 48) začne nový úsek."""
    s198 = 983
    dny = [date(2026, 6, 2), date(2026, 6, 30), date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 5), date(2026, 7, 8),
           date(2026, 7, 12), date(2026, 7, 20)]
    with pgconn.cursor() as cur:
        _vloz_projevy(cur, [(s198, i, d, HAAS, False, "recnik", None, True, False, [198], [], f"Projev {i}.")
                            for i, d in enumerate(dny, 1)])
    pgconn.commit()
    _tabulky_webu(pgconn, [
        (31, 198, "2026-05-27 00:00:00", "1. čtení", "doporučen"),
        (32, 198, "2026-06-02 00:00:00", "1. čtení", "zahájeno"),
        (33, 198, "2026-06-02 00:00:00", "2. čtení - obecná rozprava", "přikázán"),       # administrativní
        (34, 198, "2026-06-30 00:00:00", "2. čtení - obecná rozprava", "zahájeno"),
        (35, 198, "2026-06-30 00:00:00", "2. čtení - podrobná rozprava", "ukončena"),
        (36, 198, "2026-07-02 00:00:00", "3. čtení", *ukonceno),
        (37, 198, "2026-07-08 00:00:00", "3. čtení", "zahájeno"),                        # týž úsek 3. čtení
        (38, 198, "2026-07-10 00:00:00", "2. čtení - obecná rozprava", "vrácen"),        # vrácení do 2. čtení
        (39, 198, "2026-07-15 00:00:00", "2. čtení - obecná rozprava", "zahájeno"),      # opakované 2. čtení
        (40, 198, "2026-07-15 00:00:00", "2. čtení - podrobná rozprava", "ukončena"),
    ])
    r = rozprava(pgconn, 198)
    assert [v["cteni"] for v in r["vystoupeni"]] == ["1", "2", "2", "3", "3", "3", "3", "2"]
    assert [v["klic"] for v in rozprava(pgconn, 198, cteni="3")["vystoupeni"]] == [
        "10/983/4/1", "10/983/5/1", "10/983/6/1", "10/983/7/1"]


@pytest.mark.pg
@bez_db
def test_pg_usek_bez_recnika_neni_vystoupeni(pgconn):
    """Úsek bez řečníka (aname 0, recnik i id_osoba NULL) se ve výpisu ani v souhrnu neobjeví (jako v přepočtu)."""
    bez_recnika = (None, None, None)
    with pgconn.cursor() as cur:
        _vloz_projevy(cur, [(984, 1, DEN2, bez_recnika, False, "recnik", None, True, False, [76], [],
                             "…a proto navrhuji zamítnutí.")])
    pgconn.commit()
    r = rozprava(pgconn, 76)
    assert "10/984/1/1" not in [v["klic"] for v in r["vystoupeni"]] and r["celkem"] == 5
    assert all(s.get("jmeno") for s in r["souhrn"]["recnici"])


# ---------------------------------------------------------------- další nástroje nad projev_tisk (tools.py)

@pytest.mark.pg
@bez_db
def test_pg_filtr_ct_pres_projev_tisk(pgconn):
    """hledej_v_projevech, projevy_recnika a zmeny_projevu s ct najdou i projev sloučené rozpravy, který má
    vlastní ct jiného tisku (nebo žádné): stejné vazby jako rozprava a projevy_k_tisku."""
    with pgconn.cursor() as cur:
        cur.execute("UPDATE steno.projev SET ct = 76, verze = 2, zmeneno = now() WHERE schuze = %s AND turn = 7",
                    (SCHUZE,))
    pgconn.commit()
    for ct in (76, 77):
        h = tools.hledej_v_projevech(pgconn, "sloučené rozpravě", ct=ct)
        assert [p["klic"] for p in h["projevy"]] == ["10/980/7/1"] and h["celkem"] == 1, ct
        assert [p["klic"] for p in tools.projevy_recnika(pgconn, 2001, ct=ct)["projevy"]
                if p["klic"] == "10/980/7/1"] == ["10/980/7/1"], ct
        assert [p["klic"] for p in tools.zmeny_projevu(pgconn, ct=ct)["projevy"]] == ["10/980/7/1"], ct
    assert tools.projevy_recnika(pgconn, 2001, ct=77)["celkem"] == 1
    assert tools.hledej_v_projevech(pgconn, "sloučené rozpravě", ct=78)["celkem"] == 0


@pytest.mark.pg
@bez_db
def test_pg_projevy_k_tisku_pres_projev_tisk_a_druh(pgconn):
    """SQL projevy_k_tisku: projev sloučené rozpravy (tisky 76 i 77) je u každého tisku právě jednou, filtr druh."""
    def klice(ct, **kw):
        r = tools.projevy_k_tisku(pgconn, ct, min_znaku=0, **kw)
        k = [p["klic"] for p in r["projevy"]]
        assert r["celkem"] == len(k) == len(set(k))
        return k

    assert klice(76) == ["10/981/1/1", "10/980/2/1", "10/980/5/1", "10/980/7/1", "10/980/9/1"]
    assert klice(77) == ["10/980/6/1", "10/980/7/1"]
    assert klice(76, druh=["fakticka"]) == ["10/980/5/1", "10/980/9/1"]
    assert klice(76, druh=["Zpravodaj", "recnik"]) == ["10/981/1/1", "10/980/2/1", "10/980/7/1"]
    assert klice(77, druh=["fakticka"]) == []
    # předsedající jen na přání, zrušený nikdy
    assert klice(76, vcetne_predsedajicich=True) == ["10/981/1/1", "10/980/1/1", "10/980/2/1", "10/980/3/1",
                                                      "10/980/5/1", "10/980/7/1", "10/980/8/1", "10/980/9/1"]
    r = tools.projevy_k_tisku(pgconn, 76, min_znaku=0)
    assert {p["druh"] for p in r["projevy"]} == {"zpravodaj", "recnik", "fakticka"}


@pytest.mark.pg
@bez_db
def test_pg_get_pn_projevy(pgconn):
    """get_pn: projevy z steno.projev_pn bez předsedajících a zrušených, s počtem; PN bez zmínek = prázdný seznam."""
    with pgconn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {SCHEMA_WEB}")
        cur.execute(f"CREATE TABLE {SCHEMA_WEB}.pn (cislo int, ct int, id_osoba int, osoba text, klub text, cas text, "
                    "id_dokument int)")
        cur.execute(f"""CREATE TABLE {SCHEMA_WEB}.pn_text (id_dokument int, ai_score int, ai_model text, ai_json text,
                        signals text, laws text, chars int, docx_url text)""")
        cur.executemany(f"INSERT INTO {SCHEMA_WEB}.pn (cislo, ct, id_osoba, osoba, klub, id_dokument) "
                        "VALUES (%s,%s,%s,%s,%s,%s)",
                        [(2244, 76, 2001, "Karel Haas", "ANO2011", 1), (2246, 76, 2001, "Karel Haas", None, 2)])
        cur.execute(f"SET search_path TO {SCHEMA_WEB}, public")
        # zmínka v projevu předsedajícího (turn 3) a ve zrušeném projevu (turn 4) se nepočítá
        cur.execute("""INSERT INTO steno.projev_pn (projev_id, cislo_pn)
                       SELECT id, 2244 FROM steno.projev WHERE schuze = %s AND turn IN (3, 4)""", (SCHUZE,))
    pgconn.commit()
    r = tools.get_pn(pgconn, 2244)
    # klub předkladatele ke dni podání (pn.klub z webu), bez údaje None
    assert r["klub"] == "ANO2011" and tools.get_pn(pgconn, 2246)["klub"] is None
    assert [p["klub"] for p in tools.pn_k_tisku(pgconn, 76)["pn"]] == ["ANO2011", None]
    assert r["projevy_celkem"] == 1
    assert r["projevy"] == [{"klic": "10/980/2/1", "datum": DEN1.isoformat(), "recnik": "Poslanec Karel Haas",
                             "klub": "ANO2011", "druh": "recnik", "url": "https://www.psp.cz/s980002.htm#r1"}]
    r = tools.get_pn(pgconn, 2246)
    assert r["projevy"] == [] and r["projevy_celkem"] == 0
