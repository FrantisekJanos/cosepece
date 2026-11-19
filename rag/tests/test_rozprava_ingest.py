"""Testy přepočtu rozpravy (projev_tisk, druh, předchozí řečník, adresát faktické, projev_pn) nad PostgreSQL.

Bez DATABASE_URL se přeskočí. Schůzi si test vloží sám jako řádky steno.osoba, steno.bod a steno.projev
(věty jsou ze skutečných stenoprotokolů psp.cz); test assemble a run.py složí projevy z části v steno.cast.
"""
from __future__ import annotations

import json
import os
from datetime import date

import pytest

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)"),
]

OBD = 10
SCHUZE = 990
DEN1, DEN2 = date(2026, 10, 14), date(2026, 10, 15)
SCHEMA_META = "test_rozprava_meta"

OSOBY = [(1001, "Jan", "Skopeček"), (1002, "Karel", "Haas"), (1003, "Jan", "Berki"),
         (1004, "Václav", "Pláteník"), (1005, "Zuzana", "Mrázová")]
# řečník: id_osoba, text odkazu, role, předsedající
CHAIR = (1001, "Místopředseda PSP Jan Skopeček", "Místopředseda PSP", True)
HAAS = (1002, "Poslanec Karel Haas", "Poslanec", False)
BERKI = (1003, "Poslanec Jan Berki", "Poslanec", False)
PLATENIK = (1004, "Poslanec Václav Pláteník", "Poslanec", False)
MRAZOVA = (1005, "Ministryně pro místní rozvoj ČR Zuzana Mrázová", "Ministryně pro místní rozvoj ČR", False)
BLAZEJ = (None, "Poslanec Blažej", "Poslanec", False)  # bez osoby: příjmení z posledního slova

SLOUCENA, BOD285 = 58222, 58300
BODY = [(SLOUCENA, OBD, SCHUZE, 901, "Sloučená rozprava k bodům 1 a 2", "/sněmovní tisky 76 a 77/", None),
        (BOD285, OBD, SCHUZE, 5, "Vládní návrh zákona o zdravotním pojištění", "/sněmovní tisk 285/", 285)]
SL = (SLOUCENA, 76, "nadpis")  # bod sloučené rozpravy (ct NULL), tisk z nadpisu
B285 = (BOD285, 285, "rec")
N285 = (None, 285, "nadpis")   # rec bez bodu, tisk z nadpisu

FAKTICKA_BERKI = "Děkuji. Já vám přeju hezké odpoledne, vystřídali jsme se v řízení schůze.\n\nBudeme pokračovat faktickou poznámkou, a to pan poslanec Berki. Prosím, vaše 2 minuty."
BERKI_NA_HAASE = ("Děkuju. Vaším prostřednictvím, pane místopředsedo, bych chtěl reagovat na Karla Haase. "
                  "Já vždycky si vážím té jeho analýzy, ale jednu věc – přestože nesouhlasím s více – tak jednu věc bych přece jenom lehce korigoval.")


def p(poradi, recnik, text, bod=SL, datum=DEN1, turn=None, zruseno=False):
    id_osoba, label, role, chair = recnik
    id_bod, ct, zdroj = bod
    return dict(turn=turn or poradi, aname=1, datum=datum, poradi=poradi, id_osoba=id_osoba, recnik=label, role=role,
                predsedajici=chair, id_bod=id_bod, ct=ct, ct_zdroj=zdroj, text=text, zruseno=zruseno)


# turn = poradi, jen zrušený projev má turn 100 a starší poradi 6 (vložen za předsedajícího se stejným poradi)
ROZPRAVA = [
    p(1, CHAIR, "Otevírám sloučenou rozpravu. Prosím, aby se slova ujal zpravodaj pro prvé čtení, pan poslanec Blažej."),
    p(2, BLAZEJ, "Děkuji za slovo, pane předsedající. Výbor návrh projednal."),
    p(3, CHAIR, "Děkuji. Hlasovat budeme o sněmovním dokumentu číslo 492 až 498. Nyní vystoupí paní ministryně."),
    p(4, MRAZOVA, "Děkuji za slovo. Následně pozměňovací návrhy pod sněmovním dokumentem 2244 a 2245, dám společné odůvodnění."),
    p(5, HAAS, "Děkuju mnohokrát za slovo, vážený pane místopředsedo. Kolegyně, kolegové, několik z vás se mě ptalo, jak dlouhý budu mít ten projev."),
    p(6, CHAIR, FAKTICKA_BERKI),
    p(6, PLATENIK, "Pak tady máme pozměňovací návrh 497.", turn=100, zruseno=True),
    p(7, BERKI, BERKI_NA_HAASE),
    p(8, CHAIR, "Děkuju za dodržení času. Bude reagovat pan kolega Haas s faktickou poznámkou."),
    p(9, HAAS, "Já se ještě omlouvám panu ministrovi, protože jsem špatně odhadl, že ta modrá faktická byla jeho, tak to se mu teď omlouvám."),
    p(10, CHAIR, "Děkuji. Další faktická poznámka pana poslance Pláteníka. Tak prosím."),
    p(11, PLATENIK, "Děkuju. Zareaguju na pana poslance Vlčka, protože to nebylo přesné."),
    p(12, CHAIR, "Děkuji. S faktickou poznámkou pan poslanec Berki."),
    p(13, BERKI, "Děkuji. Zareaguju na pana poslance Blažeje, protože to nebylo přesné."),
    p(14, CHAIR, "Další faktická poznámka pana poslance Pláteníka."),
    p(15, PLATENIK, "Dobré ráno, navážu na včerejší debatu.", datum=DEN2),
    p(16, CHAIR, "Zahajuji projednávání bodu 5. Prosím, pane poslanče.", bod=B285, datum=DEN2),
    p(17, HAAS, "Děkuji, k tomuto návrhu mám dvě poznámky.", bod=B285, datum=DEN2),
    p(18, BERKI, "Děkuji, navážu na kolegu.", bod=N285, datum=DEN2),
]


@pytest.fixture
def conn():
    from hlidac_rag import db
    from hlidac_rag.psp import ingest

    c = db.connect()
    # pojistka: test maže tabulky steno, smí běžet jen nad testovací databází
    jmeno = c.execute("SELECT current_database()").fetchone()[0]
    if not jmeno.endswith("_test"):
        c.close()
        pytest.skip(f"databáze {jmeno} nekončí na _test")
    db.migrate(c)
    _vycisti(c)
    with c.cursor() as cur:
        # steno.rec vytváří import otevřených dat (opendata.py), ne schema.sql
        cur.execute("""CREATE TABLE steno.rec (obdobi int, schuze int, turn int, aname int,
                       id_osoba int, id_bod int, druh int)""")
        cur.executemany("INSERT INTO steno.osoba (id_osoba, jmeno, prijmeni) VALUES (%s,%s,%s)", OSOBY)
        cur.executemany("INSERT INTO steno.bod (id_bod, obdobi, schuze, cislo, nazev, konec, ct) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        BODY)
    c.commit()
    ingest._osoba_cache.clear()
    yield c
    c.rollback()
    _vycisti(c)
    c.commit()
    c.close()
    ingest._osoba_cache.clear()


def _vycisti(c):
    with c.cursor() as cur:
        cur.execute("TRUNCATE steno.projev, steno.bod, steno.osoba, steno.cast, steno.klub_clenstvi, steno.beh CASCADE")
        cur.execute("DROP TABLE IF EXISTS steno.rec")
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA_META} CASCADE")


def sql(conn, q: str, params=()):
    with conn.cursor() as cur:
        cur.execute(q, params)
        rows = cur.fetchall() if cur.description else None
    conn.commit()
    return rows


def vloz(conn, projevy, schuze=SCHUZE):
    cols = ["turn", "aname", "datum", "poradi", "id_osoba", "recnik", "role", "predsedajici", "id_bod", "ct",
            "ct_zdroj", "text"]
    with conn.cursor() as cur:
        for pr in projevy:
            cur.execute(
                f"""INSERT INTO steno.projev (obdobi, schuze, {', '.join(cols)}, znaku, hash, url, zruseno)
                    VALUES (%s, %s, {', '.join(['%s'] * len(cols))}, %s, 'h', 'https://x', %s)""",
                (OBD, schuze, *(pr[k] for k in cols), len(pr["text"]), "2026-10-20" if pr["zruseno"] else None),
            )
    conn.commit()


def prepocitej(conn, schuze=SCHUZE):
    from hlidac_rag.psp.ingest import prepocitej_rozpravu

    with conn.cursor() as cur:
        st = prepocitej_rozpravu(cur, OBD, schuze)
    conn.commit()
    return st


def id_turnu(conn, schuze=SCHUZE) -> dict[int, int]:
    return dict(sql(conn, "SELECT turn, id FROM steno.projev WHERE schuze = %s", (schuze,)))


def sloupce(conn, schuze=SCHUZE) -> dict[int, tuple]:
    """turn -> (druh, turn předchozího řečníka, reaguje_na_odhad)."""
    r = sql(conn, """SELECT p.turn, p.druh, q.turn, p.reaguje_na_odhad FROM steno.projev p
                     LEFT JOIN steno.projev q ON q.id = p.predchozi_recnik_id WHERE p.schuze = %s""", (schuze,))
    return {x[0]: tuple(x[1:]) for x in r}


def tisky(conn) -> dict[int, list]:
    out: dict[int, list] = {}
    for turn, ct, zdroj in sql(conn, """SELECT p.turn, t.ct, t.zdroj FROM steno.projev_tisk t
                                        JOIN steno.projev p ON p.id = t.projev_id ORDER BY 1, 2"""):
        out.setdefault(turn, []).append((ct, zdroj))
    return out


def pn(conn) -> dict[int, list]:
    out: dict[int, list] = {}
    for turn, cislo in sql(conn, """SELECT p.turn, n.cislo_pn FROM steno.projev_pn n
                                    JOIN steno.projev p ON p.id = n.projev_id ORDER BY 1, 2"""):
        out.setdefault(turn, []).append(cislo)
    return out


def test_slucena_rozprava_tisky_z_bodu(conn):
    vloz(conn, ROZPRAVA)
    prepocitej(conn)
    t = tisky(conn)
    # sloučená rozprava (bod 901 bez ct, konec „/sněmovní tisky 76 a 77/“): oba tisky ze zdroje bod
    for turn in range(1, 16):
        assert t[turn] == [(76, "bod"), (77, "bod")], turn
    # stejné ct z rec i z konce bodu = jeden řádek, přednost má rec
    assert t[16] == [(285, "rec")] and t[17] == [(285, "rec")]
    # projev bez bodu: jen ct projevu s jeho zdrojem
    assert t[18] == [(285, "nadpis")]


def test_fakticka_predchozi_recnik_a_adresat(conn):
    vloz(conn, ROZPRAVA)
    prepocitej(conn)
    s = sloupce(conn)
    # Berki po ohlášení faktické reaguje na Haase: adresát = kandidát se shodným příjmením
    assert s[7] == ("fakticka", 5, "Karel Haas")
    # Haas: faktická bez osloveného jména
    assert s[9] == ("fakticka", 7, None)
    # oslovený Vlček mezi předchozími řečníky není: uloží se tvar z textu
    assert s[11] == ("fakticka", 9, "Vlčka")
    # Blažej nemá osobu: příjmení z posledního slova a jméno z textu odkazu
    assert s[13] == ("fakticka", 11, "Poslanec Blažej")


def test_zpravodaj_ministr_recnik_predsedajici(conn):
    vloz(conn, ROZPRAVA)
    prepocitej(conn)
    s = sloupce(conn)
    assert s[2] == ("zpravodaj", None, None)
    assert s[4] == ("ministr", 2, None)
    assert s[5] == ("recnik", 4, None)
    assert all(s[t][0] == "predsedajici" for t in (1, 3, 6, 8, 10, 12, 14, 16))
    # i předsedající má předchozího řečníka svého bodu
    assert s[6][1] == 5


def test_predchozi_jen_ve_stejnem_bodu_a_dni(conn):
    vloz(conn, ROZPRAVA)
    prepocitej(conn)
    s = sloupce(conn)
    # další den: ohlášení faktické z předchozího dne nerozhoduje a předchozí řečník není
    assert s[15] == ("recnik", None, None)
    # jiný bod téhož dne: předchozí řečník bodu sloučené rozpravy se nepočítá
    assert s[17] == ("recnik", None, None)
    # projev bez bodu: předchozí řečník podle stejného ct
    assert s[18] == ("recnik", 17, None)


def test_pn_zminene_v_textu(conn):
    vloz(conn, ROZPRAVA)
    prepocitej(conn)
    n = pn(conn)
    assert n[4] == [2244, 2245]
    # i předsedající (filtr dělá až nástroj), rozsah se rozbalí
    assert n[3] == list(range(492, 499))
    assert set(n) == {3, 4}


def test_zruseny_projev_bez_radku(conn):
    vloz(conn, ROZPRAVA)
    prepocitej(conn)
    zruseny = id_turnu(conn)[100]
    assert sql(conn, "SELECT count(*) FROM steno.projev_tisk WHERE projev_id = %s", (zruseny,)) == [(0,)]
    assert sql(conn, "SELECT count(*) FROM steno.projev_pn WHERE projev_id = %s", (zruseny,)) == [(0,)]
    assert sloupce(conn)[100] == (None, None, None)
    # zrušený projev nerozhoduje o druhu ani předchozím řečníkovi následujícího
    assert sloupce(conn)[7][:2] == ("fakticka", 5)


def test_prepocet_je_idempotentni(conn):
    vloz(conn, ROZPRAVA)
    st = prepocitej(conn)
    assert st == {"projevu": 18, "projev_tisk": 33, "projev_pn": 9}

    def stav():
        return (tisky(conn), pn(conn), sloupce(conn))

    prvni = stav()
    assert prepocitej(conn) == st
    assert stav() == prvni
    # po změně textu a zrušení projevu přepočet staré řádky nahradí
    sql(conn, "UPDATE steno.projev SET text = 'Děkuji za slovo.' WHERE schuze = %s AND turn = 4", (SCHUZE,))
    sql(conn, "UPDATE steno.projev SET zruseno = now() WHERE schuze = %s AND turn = 7", (SCHUZE,))
    prepocitej(conn)
    assert pn(conn) == {3: list(range(492, 499))}
    assert 7 not in tisky(conn)
    s = sloupce(conn)
    assert s[7] == (None, None, None)
    assert s[9] == ("fakticka", 5, None)


@pytest.mark.parametrize("dalsich, ocekavano", [(14, "Karel Haas"), (15, "Haase")])
def test_adresat_jen_z_poslednich_15_recniku(conn, dalsich, ocekavano):
    projevy = [p(1, HAAS, "Děkuji za slovo.")]
    projevy += [p(2 + i, PLATENIK, "Děkuji, souhlasím.") for i in range(dalsich)]
    projevy += [p(2 + dalsich, CHAIR, FAKTICKA_BERKI), p(3 + dalsich, BERKI, BERKI_NA_HAASE)]
    vloz(conn, projevy)
    prepocitej(conn)
    assert sloupce(conn)[3 + dalsich] == ("fakticka", 2 + dalsich - 1, ocekavano)


# --- volání z assemble a run.py ------------------------------------------------

SCHUZE_ASM = 991
CAST = (
    '<p><b><a href="/sqw/detail.sqw?id=1002" id="r1">Poslanec Karel Haas</a></b>: '
    "Děkuju mnohokrát za slovo, vážený pane místopředsedo.</p>"
    '<p><b><a href="/sqw/detail.sqw?id=1001" id="r2">Místopředseda PSP Jan Skopeček</a></b>: '
    f"{FAKTICKA_BERKI}</p>"
    '<p><b><a href="/sqw/detail.sqw?id=1003" id="r3">Poslanec Jan Berki</a></b>: '
    f"{BERKI_NA_HAASE} Tady je pozměňovací návrh 497.</p>"
)


def vloz_cast(conn):
    sql(conn, """INSERT INTO steno.cast (obdobi, schuze, turn, url, datum, jd, stav, html)
                 VALUES (%s, %s, 1, 'https://x', %s, 1, 'konecna', %s)""", (OBD, SCHUZE_ASM, DEN1, CAST))
    sql(conn, """INSERT INTO steno.rec (obdobi, schuze, turn, aname, id_osoba, id_bod, druh) VALUES
                 (%s, %s, 1, 1, 1002, %s, 1), (%s, %s, 1, 2, 1001, %s, 2), (%s, %s, 1, 3, 1003, %s, 1)""",
        (OBD, SCHUZE_ASM, SLOUCENA) * 3)


def over_slozenou_schuzi(conn):
    r = sql(conn, """SELECT p.aname, p.druh, q.aname, p.reaguje_na_odhad FROM steno.projev p
                     LEFT JOIN steno.projev q ON q.id = p.predchozi_recnik_id
                     WHERE p.schuze = %s ORDER BY p.aname""", (SCHUZE_ASM,))
    assert r == [(1, "recnik", None, None), (2, "predsedajici", 1, None), (3, "fakticka", 1, "Karel Haas")]
    assert sql(conn, """SELECT DISTINCT t.ct, t.zdroj FROM steno.projev_tisk t JOIN steno.projev p ON p.id = t.projev_id
                        WHERE p.schuze = %s ORDER BY 1""", (SCHUZE_ASM,)) == [(76, "bod"), (77, "bod")]
    assert sql(conn, """SELECT p.aname, n.cislo_pn FROM steno.projev_pn n JOIN steno.projev p ON p.id = n.projev_id
                        WHERE p.schuze = %s""", (SCHUZE_ASM,)) == [(3, 497)]


def test_assemble_prepocita_rozpravu(conn):
    from hlidac_rag.psp import ingest

    vloz_cast(conn)
    st = ingest.assemble(conn, OBD, SCHUZE_ASM)
    assert st["nove"] == 3
    assert st["rozprava"] == {"projevu": 3, "projev_tisk": 6, "projev_pn": 1}
    over_slozenou_schuzi(conn)
    # druhé složení nic nového nevloží a tabulky rozpravy zůstanou stejné
    assert ingest.assemble(conn, OBD, SCHUZE_ASM)["rozprava"] == st["rozprava"]
    over_slozenou_schuzi(conn)


def test_run_prepocitej_rozpravu_bez_site(conn, monkeypatch, capsys):
    from hlidac_rag import db, run

    volano = []

    def zakazano(jmeno):
        def f(*a, **k):
            volano.append(jmeno)
            raise AssertionError(f"{jmeno} se při přepočtu rozpravy nevolá")
        return f

    monkeypatch.setattr(run, "Fetcher", zakazano("Fetcher"))
    monkeypatch.setattr(run.opendata, "import_all", zakazano("opendata"))
    monkeypatch.setattr(run.ingest, "discover", zakazano("discover"))
    monkeypatch.setattr(run.ingest, "crawl", zakazano("crawl"))
    monkeypatch.setattr(run, "sync_texty", zakazano("sync_texty"))
    # tabulka meta patří PHP webu: test ji vytvoří ve vlastním schématu
    sql(conn, f"CREATE SCHEMA {SCHEMA_META}")
    sql(conn, f"CREATE TABLE {SCHEMA_META}.meta (k text PRIMARY KEY, v text)")
    sql(conn, f"INSERT INTO {SCHEMA_META}.meta VALUES ('steno_obdobi', '10')")
    sql(conn, f"SET search_path TO {SCHEMA_META}, public")
    monkeypatch.setattr(db, "connect", lambda autocommit=False: conn)
    vloz_cast(conn)

    assert run.main(["--prepocitej-rozpravu"]) == 0
    assert volano == []
    over_slozenou_schuzi(conn)
    vysledek = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert vysledek["assemble"]["schuze"] == 1 and vysledek["assemble"]["nove"] == 3
    assert vysledek["rozprava"] == {"projevu": 3, "projev_tisk": 6, "projev_pn": 1}
    assert "texty" not in vysledek and "discover" not in vysledek
    assert sql(conn, "SELECT vysledek->'rozprava'->>'projevu' FROM steno.beh") == [("3",)]
