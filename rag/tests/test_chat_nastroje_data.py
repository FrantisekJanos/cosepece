"""Testy výsledku PN, upozornění a připravovaných v hlidac_rag.tools (sdílené čtecí funkce MCP a chatbota).

Nad PostgreSQL (bez DATABASE_URL se přeskočí): tabulky webu (pn, pn_text, pn_vysledek, alerts, pripravovane) si test
vytvoří ve vlastním schématu podle app/Db.php a nastaví na něj search_path, takže tabulky v public nečte ani nemění.
"""
from __future__ import annotations

import json
import os

import pytest

from hlidac_rag import tools

SCHEMA = "test_chat_data_php"

PHP_TABULKY = f"""
CREATE SCHEMA {SCHEMA};
CREATE TABLE {SCHEMA}.pn (id_dokument int PRIMARY KEY, cislo int NOT NULL, ct int NOT NULL, id_osoba int,
  osoba varchar(120), klub varchar(40), cas varchar(19));
CREATE TABLE {SCHEMA}.pn_text (id_dokument int PRIMARY KEY, ct int NOT NULL, cislo int NOT NULL, docx_url varchar(255),
  chars int NOT NULL DEFAULT 0, laws text, signals text, ai_json text, ai_score int, ai_model varchar(80),
  praxe_json text, praxe_model varchar(80));
CREATE TABLE {SCHEMA}.pn_vysledek (ct int NOT NULL, cislo int NOT NULL, pismena varchar(120), stav varchar(20) NOT NULL,
  hlasovani text, stanovisko varchar(40), updated_at varchar(19), PRIMARY KEY (ct, cislo));
CREATE TABLE {SCHEMA}.alerts (alert_key varchar(190) PRIMARY KEY, kind varchar(20) NOT NULL, ct int NOT NULL,
  event_at varchar(19) NOT NULL, created_at varchar(19) NOT NULL, title varchar(255) NOT NULL, body text NOT NULL,
  watched int NOT NULL DEFAULT 0, posted int NOT NULL DEFAULT 0, score int NOT NULL DEFAULT 0);
CREATE TABLE {SCHEMA}.pripravovane (pid varchar(20) PRIMARY KEY, nazev text, resort varchar(255), faze varchar(20) NOT NULL,
  stav varchar(80), zalozeno varchar(10), zmeneno varchar(10), pripominky_od varchar(10), pripominky_do varchar(10),
  dni int, mist int NOT NULL DEFAULT 0);
"""


def pg(test):
    """Test nad PostgreSQL: bez DATABASE_URL se přeskočí."""
    test = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)")(test)
    return pytest.mark.pg(test)


@pytest.fixture
def conn():
    from hlidac_rag import db

    c = db.connect()
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


def pn(conn, cislo: int, ct: int = 231, stav: str | None = None, pismena: str = "B1") -> None:
    conn.execute("INSERT INTO pn (id_dokument, cislo, ct, id_osoba, osoba, klub, cas) VALUES (%s, %s, %s, 1, 'Jan Test', 'ODS', "
                 "'2026-10-01 10:00:00')", (cislo, cislo, ct))
    if stav:
        conn.execute("INSERT INTO pn_vysledek (ct, cislo, pismena, stav, hlasovani, stanovisko) VALUES (%s, %s, %s, %s, %s, 'souhlas')",
                     (ct, cislo, pismena, stav, json.dumps([{"cislo": 12, "id": 777, "vysledek": "A", "varianta": None}])))


def alert(conn, key: str, kind: str, ct: int, event_at: str, title: str = "T") -> None:
    conn.execute("INSERT INTO alerts (alert_key, kind, ct, event_at, created_at, title, body, score) "
                 "VALUES (%s, %s, %s, %s, %s, %s, 'popis', 40)", (key, kind, ct, event_at, event_at, title))


def prep(conn, pid: str, nazev: str, zmeneno: str = "2026-11-01") -> None:
    conn.execute("INSERT INTO pripravovane (pid, nazev, resort, faze, stav, zmeneno) VALUES (%s, %s, 'MF', 'review', 'x', %s)",
                 (pid, nazev, zmeneno))


@pg
def test_get_pn_ma_vysledek(conn):
    pn(conn, 500, stav="castecne", pismena="B1, B2")
    r = tools.get_pn(conn, 500)
    assert r["vysledek"]["stav"] == "castecne"
    assert r["vysledek"]["popis"] == "Přijat zčásti"
    assert r["vysledek"]["pismena"] == [{"pismeno": "B1"}, {"pismeno": "B2"}]
    assert r["vysledek"]["hlasovani"][0]["id"] == 777
    assert r["vysledek"]["stanovisko"] == "souhlas"


@pg
def test_get_pn_bez_vysledku_je_none(conn):
    pn(conn, 501)
    assert tools.get_pn(conn, 501)["vysledek"] is None


@pg
def test_pn_k_tisku_ma_vysledek_u_kazde_polozky(conn):
    pn(conn, 502, stav="prijat")
    pn(conn, 503)
    r = tools.pn_k_tisku(conn, 231)
    podle = {p["cislo"]: p for p in r["pn"]}
    assert "vysledek" in podle[502] and "vysledek" in podle[503]
    assert podle[502]["vysledek"]["stav"] == "prijat"
    assert podle[503]["vysledek"] is None


@pg
def test_upozorneni_filtr_ct(conn):
    alert(conn, "a1", "pn", 231, "2026-11-01 10:00:00", "Nová PN")
    alert(conn, "a2", "pn", 232, "2026-11-02 10:00:00")
    alert(conn, "prep_review:P-1", "prep_review", 0, "2026-11-03 10:00:00")
    r = tools.upozorneni(conn, ct=231)
    assert r["pocet"] == 1
    u = r["upozorneni"][0]
    assert (u["ct"], u["typ"], u["text"], u["url"]) == (231, "pn", "Nová PN", "https://cosepece.cz/tisk/231")
    vse = tools.upozorneni(conn)
    assert vse["pocet"] == 3
    assert vse["upozorneni"][0]["url"] == "https://cosepece.cz/pripravovane#prep-P-1"
    assert tools.upozorneni(conn, limit=1)["pocet"] == 1


@pg
def test_pripravovane_podle_pid(conn):
    prep(conn, "KORNA1", "Zákon o dani")
    prep(conn, "KORNA2", "Zákon o vodě")
    r = tools.pripravovane(conn, pid="KORNA2")
    assert r["pocet"] == 1
    m = r["materialy"][0]
    assert m["pid"] == "KORNA2" and m["faze"] == "review" and m["druh"] == "zakon"
    assert m["url"].endswith("/KORNA2/")
    assert tools.pripravovane(conn, q="dani")["pocet"] == 1
    assert tools.pripravovane(conn)["pocet"] == 2


@pg
def test_pripravovane_neexistujici_pid_vraci_prazdno(conn):
    prep(conn, "KORNA1", "Zákon o dani")
    assert tools.pripravovane(conn, pid="NENI") == {"pocet": 0, "materialy": []}
