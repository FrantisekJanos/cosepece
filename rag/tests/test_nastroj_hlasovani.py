"""Testy nástrojů hlasovani_k_tisku a get_hlasovani (hlidac_rag.nastroje.hlasovani).

Logika nad databází běží nad PostgreSQL (bez DATABASE_URL se přeskočí): tabulky PHP webu (tisky, kroky,
hlasovani, hlasovani_kluby) si test vytvoří ve vlastním dočasném schématu podle app/Db.php a nastaví na něj
search_path, takže tabulky v public nečte ani nemění. Velikost odpovědi a registrace se testují bez databáze.
"""
from __future__ import annotations

import json
import os

import pytest

from hlidac_rag.nastroje import hlasovani as h

SCHEMA = "test_hlasovani_php"

PHP_TABULKY = f"""
CREATE SCHEMA {SCHEMA};
CREATE TABLE {SCHEMA}.tisky (
  id_tisk int PRIMARY KEY, ct int NOT NULL, druh int NOT NULL, typ_navrhu varchar(20) NOT NULL,
  nazev varchar(255));
CREATE TABLE {SCHEMA}.kroky (
  id_hist int PRIMARY KEY, id_tisk int NOT NULL, ct int NOT NULL, datum varchar(19),
  typ_stavu int, stav varchar(120), akce_id int, akce varchar(120), id_hlasovani int);
CREATE TABLE {SCHEMA}.hlasovani (
  id_hlasovani int PRIMARY KEY, ct int NOT NULL, schuze int, cislo int, datum varchar(16),
  pro int, proti int, zdrzel int, nehlasoval int, prihlaseno int, kvorum int,
  vysledek varchar(1), nazev varchar(255), krok int NOT NULL DEFAULT 0);
CREATE TABLE {SCHEMA}.hlasovani_kluby (
  id_hlasovani int NOT NULL, klub varchar(40) NOT NULL, pro int, proti int, zdrzel int, nepritomen int);
"""

NAZEV_231 = "Novela z. o daních z příjmů"


def pg(test):
    """Test nad PostgreSQL: bez DATABASE_URL se přeskočí."""
    test = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)")(test)
    return pytest.mark.pg(test)


@pytest.fixture
def conn():
    from hlidac_rag import db

    c = db.connect()
    # pojistka: test zakládá a maže schéma, smí běžet jen nad testovací databází
    jmeno = c.execute("SELECT current_database()").fetchone()[0]
    if not jmeno.endswith("_test"):
        c.close()
        pytest.skip(f"databáze {jmeno} nekončí na _test")
    with c.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        cur.execute(PHP_TABULKY)
        cur.execute(f"SET search_path TO {SCHEMA}, public")
    c.commit()
    yield c
    c.rollback()
    with c.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    c.commit()
    c.close()


@pytest.fixture
def steno(conn):
    """Tabulka steno.projev (vytvoří ji db.migrate, když chybí); vložené řádky se na konci vrátí zpět rollbackem."""
    from hlidac_rag import db

    if conn.execute("SELECT to_regclass('steno.projev')").fetchone()[0] is None:
        db.migrate(conn)
    return conn


def projev(conn, schuze: int, turn: int, aname: int, poradi: int, hlasovani: list[int] | None,
           recnik: str = "Poslanec Test", obdobi: int = 10, zruseno: bool = False) -> None:
    conn.execute("""INSERT INTO steno.projev (obdobi, schuze, turn, aname, datum, poradi, recnik, text, znaku, hash,
                                              url, hlasovani, zruseno)
                    VALUES (%s, %s, %s, %s, '2026-10-24', %s, %s, 'text', 4, 'h', 'u', %s,
                            CASE WHEN %s THEN now() END)""",
                 (obdobi, schuze, turn, aname, poradi, recnik, hlasovani, zruseno))


def tisk(conn, ct: int, nazev: str) -> None:
    conn.execute("INSERT INTO tisky (id_tisk, ct, druh, typ_navrhu, nazev) VALUES (%s, %s, 1, 'vládní', %s)",
                 (1000 + ct, ct, nazev))


def hlas(conn, id_hlasovani: int, ct: int, schuze: int, cislo: int, datum: str, vysledek: str | None = "A",
         krok: int = 0, nazev: str = NAZEV_231, pro: int = 100, proti: int = 50, zdrzel: int = 5,
         nehlasoval: int = 2, prihlaseno: int = 157, kvorum: int = 79) -> None:
    conn.execute("""INSERT INTO hlasovani (id_hlasovani, ct, schuze, cislo, datum, pro, proti, zdrzel, nehlasoval,
                                           prihlaseno, kvorum, vysledek, nazev, krok)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                 (id_hlasovani, ct, schuze, cislo, datum, pro, proti, zdrzel, nehlasoval, prihlaseno, kvorum,
                  vysledek, nazev, krok))


def klub(conn, id_hlasovani: int, zkratka: str, pro: int, proti: int, zdrzel: int, nepritomen: int) -> None:
    conn.execute("INSERT INTO hlasovani_kluby (id_hlasovani, klub, pro, proti, zdrzel, nepritomen) "
                 "VALUES (%s, %s, %s, %s, %s, %s)", (id_hlasovani, zkratka, pro, proti, zdrzel, nepritomen))


def krok_historie(conn, id_hist: int, ct: int, stav: str, akce: str, id_hlasovani: int | None) -> None:
    conn.execute("""INSERT INTO kroky (id_hist, id_tisk, ct, datum, typ_stavu, stav, akce_id, akce, id_hlasovani)
                    VALUES (%s, %s, %s, '2026-10-24', 64, %s, 1, %s, %s)""",
                 (id_hist, 1000 + ct, ct, stav, akce, id_hlasovani))


# ---------------------------------------------------------------- nad databází

@pg
def test_radek_a_hlavicka(conn):
    tisk(conn, 231, NAZEV_231)
    hlas(conn, 88876, 231, 34, 287, "2026-10-24 04:39", vysledek="R", pro=81, proti=96, zdrzel=0, nehlasoval=0,
         prihlaseno=177, kvorum=89)
    klub(conn, 88876, "ODS", 23, 0, 0, 11)
    klub(conn, 88876, "ANO2011", 0, 69, 0, 11)
    klub(conn, 88876, "TOP09", 7, 0, 0, 2)

    out = h._hlasovani_k_tisku(conn, 231)

    assert out["ct"] == 231
    assert out["nazev"] == NAZEV_231
    assert out["url_psp"] == "https://www.psp.cz/sqw/historie.sqw?o=10&t=231"
    assert out["url_web"] == "https://cosepece.cz/tisk.php?ct=231"
    assert out["celkem"] == 1 and out["offset"] == 0 and out["dalsi_offset"] is None and out["pocet"] == 1
    assert out["kluby_sloupce"] == ["pro", "proti", "zdrzel", "nepritomen"]
    assert "poznamka" not in out
    assert out["hlasovani"] == [{
        "id_hlasovani": 88876, "schuze": 34, "cislo": 287, "datum": "2026-10-24 04:39", "vysledek": "zamítnuto",
        "pro": 81, "proti": 96, "zdrzel": 0, "nehlasoval": 0, "prihlaseno": 177, "kvorum": 89, "krok": None,
        "url_psp": "https://www.psp.cz/sqw/hlasy.sqw?g=88876",
        "kluby": {"ANO2011": [0, 69, 0, 11], "ODS": [23, 0, 0, 11], "TOP09": [7, 0, 0, 2]},
    }]
    # kluby od největšího (součet sloupců = počet členů), při shodě abecedně
    assert list(out["hlasovani"][0]["kluby"]) == ["ANO2011", "ODS", "TOP09"]


@pg
def test_vysledek_textem_ne_z_kvora(conn):
    tisk(conn, 231, NAZEV_231)
    # zmatečné hlasování: pro >= kvórum, a přesto R; výsledek se bere z dat, ne z počtů
    hlas(conn, 1, 231, 30, 1, "2026-09-01 10:00", vysledek="A")
    hlas(conn, 2, 231, 30, 2, "2026-09-01 10:01", vysledek="R", pro=51, kvorum=33)
    hlas(conn, 3, 231, 30, 3, "2026-09-01 10:02", vysledek="X")
    hlas(conn, 4, 231, 30, 4, "2026-09-01 10:03", vysledek=None)

    vysledky = [r["vysledek"] for r in h._hlasovani_k_tisku(conn, 231)["hlasovani"]]

    assert vysledky == ["přijato", "zamítnuto", "neznámý", "neznámý"]


@pg
def test_krok_jen_u_hlasovani_z_historie(conn):
    tisk(conn, 231, NAZEV_231)
    hlas(conn, 10, 231, 34, 300, "2026-10-24 05:00", krok=1)
    hlas(conn, 11, 231, 34, 301, "2026-10-24 05:01", krok=0)
    krok_historie(conn, 500, 231, "Odesíláno do Senátu", "souhlas", 10)
    # krok 0: i kdyby na hlasování odkazoval řádek kroků, krok zůstane None
    krok_historie(conn, 501, 231, "2. čtení", "vrácen", 11)

    radky = h._hlasovani_k_tisku(conn, 231)["hlasovani"]

    assert [(r["id_hlasovani"], r["krok"]) for r in radky] == [(10, "Odesíláno do Senátu"), (11, None)]


@pg
def test_razeni_datum_cislo_id(conn):
    tisk(conn, 231, NAZEV_231)
    hlas(conn, 30, 231, 35, 2, "2026-10-25 09:00")
    hlas(conn, 22, 231, 34, 5, "2026-10-24 10:00")
    hlas(conn, 21, 231, 33, 5, "2026-10-24 10:00")
    hlas(conn, 20, 231, 34, 7, "2026-10-24 10:00")
    hlas(conn, 40, 231, 34, 1, "2026-10-23 18:00")

    ids = [r["id_hlasovani"] for r in h._hlasovani_k_tisku(conn, 231)["hlasovani"]]

    assert ids == [40, 21, 22, 20, 30]


@pg
def test_strankovani(conn):
    tisk(conn, 231, NAZEV_231)
    for i in range(5):
        hlas(conn, 100 + i, 231, 34, i + 1, f"2026-10-24 10:0{i}")

    prvni = h._hlasovani_k_tisku(conn, 231, limit=2)
    posledni = h._hlasovani_k_tisku(conn, 231, limit=2, offset=4)

    assert (prvni["celkem"], prvni["offset"], prvni["pocet"], prvni["dalsi_offset"]) == (5, 0, 2, 2)
    assert [r["id_hlasovani"] for r in prvni["hlasovani"]] == [100, 101]
    assert (posledni["celkem"], posledni["offset"], posledni["pocet"], posledni["dalsi_offset"]) == (5, 4, 1, None)
    assert [r["id_hlasovani"] for r in posledni["hlasovani"]] == [104]


@pg
def test_limit_nad_strop_se_orizne(conn):
    tisk(conn, 231, NAZEV_231)
    n = h.MAX_LIMIT_HLASOVANI + 5
    for i in range(n):
        hlas(conn, 1000 + i, 231, 34, i + 1, "2026-10-24 10:00")

    out = h._hlasovani_k_tisku(conn, 231, limit=10_000)

    assert out["celkem"] == n
    assert out["pocet"] == h.MAX_LIMIT_HLASOVANI
    assert out["dalsi_offset"] == h.MAX_LIMIT_HLASOVANI


@pg
def test_vychozi_limit_50(conn):
    tisk(conn, 231, NAZEV_231)
    for i in range(55):
        hlas(conn, 1000 + i, 231, 34, i + 1, "2026-10-24 10:00")

    assert h._hlasovani_k_tisku(conn, 231)["pocet"] == 50


@pg
def test_sledovany_tisk_bez_hlasovani(conn):
    tisk(conn, 88, "Novela z. o státní sociální podpoře")

    out = h._hlasovani_k_tisku(conn, 88)

    assert out["hlasovani"] == []
    assert out["celkem"] == 0 and out["pocet"] == 0 and out["dalsi_offset"] is None
    assert out["nazev"] == "Novela z. o státní sociální podpoře"
    assert out["poznamka"]


@pg
def test_tisk_jen_v_hlasovani_ma_nazev_z_hlasovani(conn):
    # varianty názvu s „**“ na konci i uprostřed místo mezery: vybere se ta bez hvězdiček
    hlas(conn, 7, 999, 20, 1, "2026-06-01 10:00", nazev="Vl.n.z. o něčem**")
    hlas(conn, 8, 999, 20, 2, "2026-06-01 10:01", nazev="Vl.n.z. o**něčem")
    hlas(conn, 9, 999, 20, 3, "2026-06-01 10:02", nazev="Vl.n.z. o něčem")

    out = h._hlasovani_k_tisku(conn, 999)

    assert out["celkem"] == 3
    assert out["nazev"] == "Vl.n.z. o něčem"


@pg
def test_neznamy_tisk_je_valueerror(conn):
    tisk(conn, 231, NAZEV_231)

    with pytest.raises(ValueError, match="Tisk 12345 v 10. období nesleduji"):
        h._hlasovani_k_tisku(conn, 12345)


@pg
def test_chybejici_tabulka_je_citelna_chyba(conn):
    tisk(conn, 231, NAZEV_231)
    conn.execute("DROP TABLE hlasovani")
    # jen dočasné schéma, ať tabulku nenajde ani v public
    conn.execute(f"SET LOCAL search_path TO {SCHEMA}")

    with pytest.raises(ValueError, match="hlasovani"):
        h._hlasovani_k_tisku(conn, 231)


# ---------------------------------------------------------------- get_hlasovani

def _detail_s_kluby(conn):
    tisk(conn, 231, NAZEV_231)
    hlas(conn, 88876, 231, 34, 287, "2026-10-24 04:39", vysledek="R", pro=81, proti=96, zdrzel=0, nehlasoval=0,
         prihlaseno=177, kvorum=89, krok=1)
    krok_historie(conn, 500, 231, "Odesíláno do Senátu", "souhlas", 88876)
    klub(conn, 88876, "ODS", 23, 0, 0, 11)
    klub(conn, 88876, "ANO2011", 0, 69, 0, 11)
    klub(conn, 88876, "TOP09", 7, 0, 0, 2)


@pg
def test_get_hlasovani_plny_detail(steno):
    _detail_s_kluby(steno)

    out = h._get_hlasovani(steno, 88876)

    assert out == {
        "id_hlasovani": 88876, "ct": 231, "nazev": NAZEV_231, "url_web": "https://cosepece.cz/tisk.php?ct=231",
        "schuze": 34, "cislo": 287, "datum": "2026-10-24 04:39", "vysledek": "zamítnuto",
        "pro": 81, "proti": 96, "zdrzel": 0, "nehlasoval": 0, "prihlaseno": 177, "kvorum": 89,
        "krok": "Odesíláno do Senátu", "url_psp": "https://www.psp.cz/sqw/hlasy.sqw?g=88876",
        "kluby": [{"klub": "ANO2011", "pro": 0, "proti": 69, "zdrzel": 0, "nepritomen": 11},
                  {"klub": "ODS", "pro": 23, "proti": 0, "zdrzel": 0, "nepritomen": 11},
                  {"klub": "TOP09", "pro": 7, "proti": 0, "zdrzel": 0, "nepritomen": 2}],
        "projevy": [], "projevu_celkem": 0,
    }


@pg
def test_get_hlasovani_nazev_z_hlasovani_pro_nesledovany_tisk(steno):
    hlas(steno, 7, 999, 20, 1, "2026-06-01 10:00", nazev="Vl.n.z. o něčem**")
    hlas(steno, 9, 999, 20, 3, "2026-06-01 10:02", nazev="Vl.n.z. o něčem")

    out = h._get_hlasovani(steno, 7)

    assert out["ct"] == 999 and out["nazev"] == "Vl.n.z. o něčem" and out["kluby"] == []


@pg
def test_get_hlasovani_projevy(steno):
    tisk(steno, 231, NAZEV_231)
    hlas(steno, 88876, 231, 34, 287, "2026-10-24 04:39")
    hlas(steno, 88877, 231, 34, 288, "2026-10-24 04:40")
    # řazení podle schůze a pořadí, ne podle turn/aname
    projev(steno, 9991, 40, 2, 5, [88876], recnik="Poslanec Druhý")
    projev(steno, 9990, 90, 9, 7, [88876, 88877], recnik="Poslanec První")
    projev(steno, 9990, 10, 1, 3, [88877, 88876], recnik="Poslankyně Nultá")
    # nepatří: zrušený, jiné období, jiné hlasování, bez hlasování
    projev(steno, 9990, 11, 1, 4, [88876], zruseno=True)
    projev(steno, 9990, 12, 1, 5, [88876], obdobi=9)
    projev(steno, 9990, 13, 1, 6, [88877])
    projev(steno, 9990, 14, 1, 8, None)

    out = h._get_hlasovani(steno, 88876)

    assert out["projevu_celkem"] == 3
    assert [(p["klic"], p["recnik"]) for p in out["projevy"]] == [
        ("10/9990/10/1", "Poslankyně Nultá"), ("10/9990/90/9", "Poslanec První"),
        ("10/9991/40/2", "Poslanec Druhý")]
    assert out["projevy"][0]["datum"] == "2026-10-24"
    assert "poznamka" not in out


@pg
def test_get_hlasovani_projevy_se_orezou_na_50(steno):
    tisk(steno, 231, NAZEV_231)
    hlas(steno, 88876, 231, 34, 287, "2026-10-24 04:39")
    for i in range(h.MAX_PROJEVU + 3):
        projev(steno, 9990, 100 + i, 1, i, [88876])

    out = h._get_hlasovani(steno, 88876)

    assert out["projevu_celkem"] == h.MAX_PROJEVU + 3
    assert len(out["projevy"]) == h.MAX_PROJEVU
    assert out["projevy"][0]["klic"] == "10/9990/100/1"


@pg
def test_get_hlasovani_bez_tabulky_projevu(conn, monkeypatch):
    tisk(conn, 231, NAZEV_231)
    hlas(conn, 88876, 231, 34, 287, "2026-10-24 04:39")
    # skutečnou tabulku nechat být: nástroj se nasměruje na neexistující
    monkeypatch.setattr(h, "PROJEVY_TABULKA", "steno.neexistujici_tabulka")

    out = h._get_hlasovani(conn, 88876)

    assert out["projevy"] == [] and out["projevu_celkem"] == 0
    assert out["poznamka"]
    assert out["ct"] == 231


@pg
def test_get_hlasovani_neznamy_id_je_valueerror(conn):
    tisk(conn, 231, NAZEV_231)
    hlas(conn, 88876, 231, 34, 287, "2026-10-24 04:39")

    with pytest.raises(ValueError, match=r"Hlasování 1 neznám.*hlasovani_k_tisku\(ct\)"):
        h._get_hlasovani(conn, 1)


@pg
def test_get_hlasovani_chybejici_tabulka_je_citelna_chyba(conn):
    conn.execute("DROP TABLE hlasovani_kluby")
    conn.execute(f"SET LOCAL search_path TO {SCHEMA}")

    with pytest.raises(ValueError, match="hlasovani_kluby"):
        h._get_hlasovani(conn, 88876)


# ---------------------------------------------------------------- bez databáze

def _velikost(obj) -> int:
    """Délka odpovědi tak, jak ji měří mcp_server._call."""
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str))


def test_nejhorsi_pripad_se_vejde_pod_54000_znaku():
    # řádek z DB s nejdelšími hodnotami: šestimístné id, trojciferné počty, nejdelší text výsledku,
    # stav kroku v plné délce sloupce (varchar 120)
    radek_db = dict(id_hlasovani=999999, schuze=999, cislo=9999, datum="2026-12-31 23:59", vysledek="R",
                    pro=200, proti=200, zdrzel=200, nehlasoval=200, prihlaseno=200, kvorum=200, krok="Ž" * 120)
    kluby = {k: [200, 200, 200, 200]
             for k in ("ANO2011", "MS", "SPD", "STAN", "Piráti", "TOP09", "ODS", "KDU-ČSL", "Nezařaz")}
    radek = h._radek_hlasovani(radek_db, kluby)
    # nejhorší umělý řádek je aspoň tak dlouhý jako naměřená maxima (řádek 354, kompaktní kluby 224)
    assert _velikost({k: v for k, v in radek.items() if k != "kluby"}) >= 354
    assert _velikost(radek["kluby"]) >= 224

    odpoved = h._odpoved(99999, "Ř" * 255, 99999, 99999, [radek] * h.MAX_LIMIT_HLASOVANI)

    assert odpoved["pocet"] == h.MAX_LIMIT_HLASOVANI
    assert _velikost(odpoved) <= 54000


def test_nastroj_je_registrovany_jen_pro_cteni():
    from hlidac_rag import mcp_server

    nastroje = {t.name: t for t in mcp_server.server._tool_manager.list_tools()}

    assert "hlasovani_k_tisku" in nastroje
    t = nastroje["hlasovani_k_tisku"]
    assert t.annotations.read_only_hint is True and t.annotations.open_world_hint is False
    assert f"nejvýš {h.MAX_LIMIT_HLASOVANI}" in t.description


def test_get_hlasovani_je_registrovany_jen_pro_cteni():
    from hlidac_rag import mcp_server

    nastroje = {t.name: t for t in mcp_server.server._tool_manager.list_tools()}

    assert "get_hlasovani" in nastroje
    t = nastroje["get_hlasovani"]
    assert t.annotations.read_only_hint is True and t.annotations.open_world_hint is False
    assert list(t.parameters["properties"]) == ["id_hlasovani"]
