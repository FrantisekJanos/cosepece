"""Testy ingestu textů (schéma texty) nad PostgreSQL. Bez DATABASE_URL se přeskočí.

Tabulky PHP webu (tisky, kroky, pn, tisk_text, pn_text) si test vytvoří ve vlastním
dočasném schématu podle app/Db.php a nastaví na něj search_path, takže skutečné
tabulky v public nečte ani nemění. Schéma texty patří jen tomuto ingestu a test ho vyprázdní.
"""
import io
import os
import zipfile
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)"),
]

TEXTY = Path(__file__).parent / "fixtures" / "texty"
HTML = TEXTY / "html"
SCHEMA = "test_texty_php"

PHP_TABULKY = f"""
CREATE SCHEMA {SCHEMA};
CREATE TABLE {SCHEMA}.tisky (
  id_tisk int PRIMARY KEY, ct int NOT NULL, druh int NOT NULL, typ_navrhu varchar(20) NOT NULL,
  nazev varchar(255), faze varchar(60));
CREATE TABLE {SCHEMA}.kroky (
  id_hist int PRIMARY KEY, id_tisk int NOT NULL, ct int NOT NULL, datum varchar(19),
  typ_stavu int, stav varchar(120), akce_id int, akce varchar(120));
CREATE TABLE {SCHEMA}.pn (
  id_dokument int PRIMARY KEY, cislo int NOT NULL, ct int NOT NULL, id_osoba int,
  osoba varchar(120), cas varchar(19));
CREATE TABLE {SCHEMA}.tisk_text (
  ct int PRIMARY KEY, docx_url varchar(255), text text, chars int NOT NULL DEFAULT 0, error text);
CREATE TABLE {SCHEMA}.pn_text (
  id_dokument int PRIMARY KEY, ct int NOT NULL, cislo int NOT NULL, docx_url varchar(255),
  text text, chars int NOT NULL DEFAULT 0, error text);
"""


def vzor_docx(text: str) -> bytes:
    odstavce = "".join(f"<w:p><w:r><w:t>{r}</w:t></w:r></w:p>" for r in text.split("\n"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f"<w:document><w:body>{odstavce}</w:body></w:document>")
    return buf.getvalue()


class FakeFetcher:
    """Vrací bajty podle prvního podřetězce URL, který sedí (pořadí rozhoduje); počítá volání."""

    def __init__(self, odpovedi: list[tuple[str, bytes | None]] | None = None):
        self.odpovedi = odpovedi or []
        self.urls: list[str] = []
        self.requests = 0

    def get(self, url: str) -> bytes | None:
        self.urls.append(url)
        self.requests += 1
        for podretezec, data in self.odpovedi:
            if podretezec in url:
                return data
        return None

    def pocet(self, podretezec: str) -> int:
        return sum(podretezec in u for u in self.urls)


@pytest.fixture
def conn():
    from hlidac_rag import db

    c = db.connect()
    # pojistka: test maže tabulky texty, smí běžet jen nad testovací databází
    jmeno = c.execute("SELECT current_database()").fetchone()[0]
    if not jmeno.endswith("_test"):
        c.close()
        pytest.skip(f"databáze {jmeno} nekončí na _test")
    db.migrate(c)
    with c.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        cur.execute(PHP_TABULKY)
        cur.execute("TRUNCATE texty.dokument CASCADE")
        cur.execute(f"SET search_path TO {SCHEMA}, public")
    c.commit()
    yield c
    c.rollback()
    with c.cursor() as cur:
        cur.execute("TRUNCATE texty.dokument CASCADE")
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    c.commit()
    c.close()


def sql(conn, q: str, params=()):
    with conn.cursor() as cur:
        cur.execute(q, params)
        rows = cur.fetchall() if cur.description else None
    conn.commit()
    return rows


def celky(conn, druh: str, ct: int, verze: str = "puvodni"):
    return sql(conn, """SELECT c.poradi, c.oznaceni, c.text FROM texty.cel c JOIN texty.dokument d ON d.id = c.dokument_id
                        WHERE d.druh = %s AND d.ct = %s AND d.verze = %s ORDER BY c.poradi""", (druh, ct, verze))


def test_prevzeti_a_rozdeleni(conn):
    from hlidac_rag.texty.ingest import sync_texty

    text = (TEXTY / "tisk-311.txt").read_text(encoding="utf-8")
    sql(conn, "INSERT INTO tisk_text (ct, docx_url, text, chars) VALUES (311, 'https://x/idd=1', %s, %s)", (text, len(text)))
    f = FakeFetcher()
    st = sync_texty(conn, f, 10)
    assert st["prevzato"] == 1 and st["rozdeleno"] == 1
    assert f.requests == 0
    d = sql(conn, "SELECT zdroj_textu, cislo_pn, verze, znaku, hash, rozdeleno_at IS NOT NULL FROM texty.dokument")
    assert d == [("tisk_text", 0, "puvodni", len(text), d[0][4], True)] and len(d[0][4]) == 64
    oznaceni = [r[1] for r in celky(conn, "tisk", 311)]
    assert "Čl. I" in oznaceni
    # druhý běh nic nepřevezme ani nedělí
    st = sync_texty(conn, f, 10)
    assert st["prevzato"] == 0 and st["rozdeleno"] == 0


def test_zmena_hashe_prepise_celky(conn):
    from hlidac_rag.texty.ingest import sync_texty
    from hlidac_rag.texty.parser import rozdel

    text = (TEXTY / "tisk-311.txt").read_text(encoding="utf-8")
    sql(conn, "INSERT INTO tisk_text (ct, text) VALUES (311, %s)", (text,))
    sync_texty(conn, FakeFetcher(), 10)
    puvodni = celky(conn, "tisk", 311)
    novy = "Čl. I\nNové znění jediného článku.\n\nČl. II\nÚčinnost dnem vyhlášení."
    sql(conn, "UPDATE tisk_text SET text = %s WHERE ct = 311", (novy,))
    st = sync_texty(conn, FakeFetcher(), 10)
    assert st["prevzato"] == 1 and st["rozdeleno"] == 1
    po = celky(conn, "tisk", 311)
    assert len(po) == len(rozdel(novy)) < len(puvodni)
    assert [r[0] for r in po] == list(range(len(po)))
    assert all(r[2] in novy for r in po)
    assert sql(conn, "SELECT count(*) FROM texty.cel")[0][0] == len(po)


def test_schvalene_neni_ulozi_kontrolu(conn):
    from hlidac_rag.texty.ingest import sync_texty

    sql(conn, "INSERT INTO tisky (id_tisk, ct, druh, typ_navrhu, faze) VALUES (1, 311, 1, 'vládní', 'Vyhlášeno')")
    sql(conn, "INSERT INTO tisk_text (ct, text) VALUES (311, 'Čl. I\nText.')")
    f = FakeFetcher([("historie.sqw", (HTML / "historie-311.html").read_bytes())])
    st = sync_texty(conn, f, 10)
    assert st["schvalene"] == 0
    assert f.pocet("historie.sqw") == 1 and "t=311" in f.urls[0]
    r = sql(conn, """SELECT text, chyba, zkontrolovano_at IS NOT NULL, zdroj_textu FROM texty.dokument
                     WHERE druh = 'tisk' AND ct = 311 AND verze = 'schvalene'""")
    assert r == [(None, "schválené znění zatím není", True, "vlastni_stazeni")]
    # do 24 h se stránka znovu nestahuje
    sync_texty(conn, f, 10)
    assert f.pocet("historie.sqw") == 1
    # po 24 h ano
    sql(conn, "UPDATE texty.dokument SET zkontrolovano_at = now() - interval '25 hours' WHERE verze = 'schvalene'")
    sync_texty(conn, f, 10)
    assert f.pocet("historie.sqw") == 2


def test_schvalene_stazeno_a_tret_cteni(conn):
    from hlidac_rag.texty.ingest import sync_texty

    # tisk 76 je jen ve 3. čtení (krok typ_stavu 5), schválené znění už na psp.cz je
    sql(conn, "INSERT INTO tisky (id_tisk, ct, druh, typ_navrhu, faze) VALUES (2, 76, 1, 'vládní', '3. čtení')")
    sql(conn, "INSERT INTO kroky (id_hist, id_tisk, ct, typ_stavu) VALUES (10, 2, 76, 5)")
    sql(conn, "INSERT INTO tisk_text (ct, text) VALUES (76, 'Čl. I\nPůvodní.')")
    # zamítnutý tisk se nekontroluje, i když měl 3. čtení
    sql(conn, "INSERT INTO tisky (id_tisk, ct, druh, typ_navrhu, faze) VALUES (3, 77, 1, 'vládní', 'Zamítnuto')")
    sql(conn, "INSERT INTO kroky (id_hist, id_tisk, ct, typ_stavu) VALUES (11, 3, 77, 5)")
    sql(conn, "INSERT INTO tisk_text (ct, text) VALUES (77, 'Čl. I\nZamítnutý.')")
    f = FakeFetcher([
        ("historie.sqw", (HTML / "historie-76.html").read_bytes()),
        ("v=PZ", (HTML / "tiskt-76-pz.html").read_bytes()),
        ("orig2.sqw?idd=278472", vzor_docx("Čl. I\nSchválené znění.\nČl. II\nÚčinnost.")),
    ])
    st = sync_texty(conn, f, 10)
    assert st["schvalene"] == 1 and st["chyby"] == 0
    assert f.pocet("t=77") == 0
    r = sql(conn, """SELECT docx_url, chyba, zdroj_textu FROM texty.dokument
                     WHERE ct = 76 AND verze = 'schvalene'""")
    assert r[0][0].endswith("idd=278472") and r[0][1] is None and r[0][2] == "vlastni_stazeni"
    assert [c[1] for c in celky(conn, "tisk", 76, "schvalene")] == ["Čl. I", "Čl. II"]
    # se staženým textem se už nekontroluje
    sql(conn, "UPDATE texty.dokument SET zkontrolovano_at = now() - interval '25 hours'")
    sync_texty(conn, f, 10)
    assert f.pocet("historie.sqw") == 1


def test_pn_bez_docx_je_jen_pdf(conn):
    from hlidac_rag.texty.ingest import sync_texty

    sql(conn, "INSERT INTO pn (id_dokument, cislo, ct) VALUES (500, 894, 311)")
    f = FakeFetcher([("sd.sqw", b"<html><a href='/sqw/text/orig2.sqw?idd=1'>PDF</a></html>")])
    st = sync_texty(conn, f, 10)
    assert st["jen_pdf"] == 1 and st["stazeno"] == 0
    assert "cd=894" in f.urls[0] and "o=10" in f.urls[0]
    r = sql(conn, "SELECT druh, ct, cislo_pn, text, chyba FROM texty.dokument")
    assert r == [("pn", 311, 894, None, "jen PDF")]
    # dokument už existuje, znovu se nestahuje
    sync_texty(conn, f, 10)
    assert f.requests == 1


def test_stazeni_pn_a_tisku_a_limit(conn):
    from hlidac_rag.texty.ingest import sync_texty

    sql(conn, "INSERT INTO pn (id_dokument, cislo, ct) VALUES (500, 894, 76)")
    sql(conn, "INSERT INTO tisky (id_tisk, ct, druh, typ_navrhu, faze) VALUES (2, 76, 1, 'vládní', '1. čtení')")
    sql(conn, "INSERT INTO tisky (id_tisk, ct, druh, typ_navrhu, faze) VALUES (4, 75, 1, 'vládní', '1. čtení')")
    f = FakeFetcher([
        ("sd.sqw", (HTML / "sd-894.html").read_bytes()),
        ("tiskt.sqw", (HTML / "tiskt-76-0.html").read_bytes()),
        ("idd=269467", vzor_docx("1. V § 5 se slovo „a“ nahrazuje slovem „b“.")),
        ("idd=266516", vzor_docx("Čl. I\nNávrh.")),
    ])
    # limit 2: nejdřív tisky (76, 75; tisk 75 dostane stejnou vzorovou stránku), PN až v dalším běhu
    st = sync_texty(conn, f, 10, limit=2)
    assert st["stazeno"] == 2
    assert sql(conn, "SELECT druh FROM texty.dokument") == [("tisk",), ("tisk",)]
    st = sync_texty(conn, f, 10, limit=2)
    assert st["stazeno"] == 1
    r = sql(conn, "SELECT druh, ct, cislo_pn, zdroj_textu, docx_url FROM texty.dokument ORDER BY druh, ct")
    assert [x[:4] for x in r] == [("pn", 76, 894, "vlastni_stazeni"), ("tisk", 75, 0, "vlastni_stazeni"),
                                  ("tisk", 76, 0, "vlastni_stazeni")]
    assert r[0][4].endswith("idd=269467")
    assert all(n > 0 for (n,) in sql(conn, "SELECT count(*) FROM texty.cel GROUP BY dokument_id"))


def test_vadny_docx_neshodi_beh(conn):
    from hlidac_rag.texty.ingest import sync_texty

    sql(conn, "INSERT INTO pn (id_dokument, cislo, ct) VALUES (500, 894, 76)")
    sql(conn, "INSERT INTO pn (id_dokument, cislo, ct) VALUES (501, 895, 76)")
    f = FakeFetcher([
        ("cd=894", (HTML / "sd-894.html").read_bytes()),
        ("cd=895", b"<html></html>"),
        ("idd=269467", b"<html>chybova stranka</html>"),
    ])
    st = sync_texty(conn, f, 10)
    assert st["chyby"] == 1 and st["jen_pdf"] == 1
    r = sql(conn, "SELECT cislo_pn, text, chyba FROM texty.dokument ORDER BY cislo_pn")
    assert r == [(894, None, "DOCX nelze přečíst"), (895, None, "jen PDF")]


def test_prevzeti_prepise_vlastni_stazeni(conn):
    from hlidac_rag.texty.ingest import sync_texty

    sql(conn, "INSERT INTO pn (id_dokument, cislo, ct) VALUES (500, 894, 76)")
    sync_texty(conn, FakeFetcher([("sd.sqw", b"<html></html>")]), 10)
    text = (TEXTY / "pn-894.txt").read_text(encoding="utf-8")
    sql(conn, "INSERT INTO pn_text (id_dokument, ct, cislo, text) VALUES (500, 76, 894, %s)", (text,))
    st = sync_texty(conn, FakeFetcher(), 10)
    assert st["prevzato"] == 1 and st["rozdeleno"] == 1
    r = sql(conn, "SELECT zdroj_textu, chyba, znaku FROM texty.dokument")
    assert r == [("pn_text", None, len(text))]


def test_nedostupne_psp_prerusi_jen_stahovani(conn):
    import urllib.error

    from hlidac_rag.texty.ingest import sync_texty

    class Vypadek(FakeFetcher):
        def get(self, url):
            super().get(url)
            raise urllib.error.URLError("psp.cz nedostupné")

    sql(conn, "INSERT INTO tisky (id_tisk, ct, druh, typ_navrhu, faze) VALUES (2, 76, 1, 'vládní', '1. čtení')")
    sql(conn, "INSERT INTO pn (id_dokument, cislo, ct) VALUES (500, 894, 76)")
    sql(conn, "INSERT INTO tisk_text (ct, text) VALUES (311, 'Čl. I\nText.')")
    f = Vypadek()
    st = sync_texty(conn, f, 10)
    assert f.requests == 1 and st["chyby"] == 1
    assert st["prevzato"] == 1 and st["rozdeleno"] == 1
    # nic se neuložilo, příští běh to zkusí znovu
    assert sql(conn, "SELECT druh, ct FROM texty.dokument") == [("tisk", 311)]


def test_selhani_deleni_neblokuje_frontu(conn, monkeypatch):
    from hlidac_rag.texty import ingest

    puvodni_rozdel = ingest.rozdel

    def rozdel(text):
        if "ROZBITÝ" in text:
            raise ValueError("neočekávaná struktura")
        return puvodni_rozdel(text)

    monkeypatch.setattr(ingest, "rozdel", rozdel)
    # rozbitý dokument má nižší id, takže by ve frontě stál první
    sql(conn, "INSERT INTO tisk_text (ct, text) VALUES (1, 'ROZBITÝ text')")
    sql(conn, "INSERT INTO tisk_text (ct, text) VALUES (2, 'Čl. I\nDobrý.')")
    st = ingest.sync_texty(conn, FakeFetcher(), 10)
    assert st["rozdeleno"] == 1 and st["chyby"] == 1
    r = sql(conn, "SELECT ct, chyba, rozdeleno_at IS NOT NULL FROM texty.dokument ORDER BY ct")
    assert r[0][0] == 1 and r[0][1].startswith("dělení selhalo: chyba zpracování: ValueError") and r[0][2]
    assert r[1] == (2, None, True)
    assert [c[1] for c in celky(conn, "tisk", 2)] == ["Čl. I"]
    # druhý běh rozbitý dokument znovu nedělí
    st = ingest.sync_texty(conn, FakeFetcher(), 10)
    assert st["rozdeleno"] == 0 and st["chyby"] == 0
    # po změně textu se dělí znovu
    sql(conn, "UPDATE tisk_text SET text = 'Čl. I\nOpraveno.' WHERE ct = 1")
    st = ingest.sync_texty(conn, FakeFetcher(), 10)
    assert st["rozdeleno"] == 1
    assert sql(conn, "SELECT chyba FROM texty.dokument WHERE ct = 1") == [(None,)]


def test_neocekavana_chyba_stahovani_pokracuje(conn):
    from hlidac_rag.texty.ingest import sync_texty

    class Rozbity(FakeFetcher):
        def get(self, url):
            data = super().get(url)
            if "ct=76" in url:
                raise RuntimeError("encrypted ZIP")
            return data

    sql(conn, "INSERT INTO tisky (id_tisk, ct, druh, typ_navrhu, faze) VALUES (2, 76, 1, 'vládní', '1. čtení')")
    sql(conn, "INSERT INTO tisky (id_tisk, ct, druh, typ_navrhu, faze) VALUES (4, 75, 1, 'vládní', '1. čtení')")
    sql(conn, "INSERT INTO tisk_text (ct, text) VALUES (311, 'Čl. I\nText.')")
    f = Rozbity([
        ("tiskt.sqw", (HTML / "tiskt-76-0.html").read_bytes()),
        ("idd=266516", vzor_docx("Čl. I\nNávrh.")),
    ])
    st = sync_texty(conn, f, 10)
    assert st["chyby"] == 1 and st["stazeno"] == 1 and st["rozdeleno"] == 2
    r = sql(conn, "SELECT ct, chyba FROM texty.dokument WHERE ct IN (75, 76) ORDER BY ct")
    assert r[0] == (75, None) and r[1][1] == "chyba zpracování: RuntimeError: encrypted ZIP"


def test_chybove_radky_znovu_po_7_dnech(conn):
    from hlidac_rag.texty.ingest import sync_texty

    sql(conn, "INSERT INTO pn (id_dokument, cislo, ct) VALUES (500, 894, 76)")
    sql(conn, "INSERT INTO pn (id_dokument, cislo, ct) VALUES (501, 895, 76)")
    f = FakeFetcher([("cd=895", b"<html></html>")])  # 894: stránka 404, 895: jen PDF
    st = sync_texty(conn, f, 10)
    assert st["chyby"] == 1 and st["jen_pdf"] == 1
    r = sql(conn, "SELECT cislo_pn, chyba, zkontrolovano_at IS NOT NULL FROM texty.dokument ORDER BY cislo_pn")
    assert r == [(894, "stránka na psp.cz nenalezena", True), (895, "jen PDF", True)]
    # do 7 dnů nic
    sync_texty(conn, f, 10)
    assert f.requests == 2
    # po 7 dnech jen chybový řádek, „jen PDF“ ne
    sql(conn, "UPDATE texty.dokument SET zkontrolovano_at = now() - interval '8 days'")
    f.odpovedi = [
        ("cd=894", (HTML / "sd-894.html").read_bytes()),
        ("idd=269467", vzor_docx("1. V § 5 se slovo „a“ nahrazuje slovem „b“.")),
    ]
    st = sync_texty(conn, f, 10)
    assert st["stazeno"] == 1
    assert f.pocet("cd=895") == 1 and f.pocet("cd=894") == 2
    assert sql(conn, "SELECT chyba, text IS NOT NULL FROM texty.dokument WHERE cislo_pn = 894") == [(None, True)]


def test_faze_vice_radku_tisku(conn):
    from hlidac_rag.texty.ingest import sync_texty

    # dva řádky stejného ct: jeden vyhlášený, druhý s fází, která je abecedně „větší“
    sql(conn, "INSERT INTO tisky (id_tisk, ct, druh, typ_navrhu, faze) VALUES (1, 311, 1, 'vládní', 'Vyhlášeno')")
    sql(conn, "INSERT INTO tisky (id_tisk, ct, druh, typ_navrhu, faze) VALUES (2, 311, 1, 'vládní', 'Zařazeno')")
    sql(conn, "INSERT INTO tisk_text (ct, text) VALUES (311, 'Čl. I\nText.')")
    f = FakeFetcher([("historie.sqw", (HTML / "historie-311.html").read_bytes())])
    sync_texty(conn, f, 10)
    assert f.pocet("historie.sqw") == 1
