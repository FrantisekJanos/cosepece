"""Testy přepočtu rozpravy (projev_tisk, druh, předchozí řečník, adresát faktické, projev_pn), ct projevu
v assemble a get_tisk nad projev_tisk, vše nad PostgreSQL.

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
         (1004, "Václav", "Pláteník"), (1005, "Zuzana", "Mrázová"),
         (1006, "Lukáš", "Vlček"),     # poslanec období (klub), v rozpravě nemluví
         (1007, "Alois", "Rašín")]     # osoba bez klubu a bez projevu v období
# řečník: id_osoba, text odkazu, role, předsedající
CHAIR = (1001, "Místopředseda PSP Jan Skopeček", "Místopředseda PSP", True)
HAAS = (1002, "Poslanec Karel Haas", "Poslanec", False)
BERKI = (1003, "Poslanec Jan Berki", "Poslanec", False)
PLATENIK = (1004, "Poslanec Václav Pláteník", "Poslanec", False)
MRAZOVA = (1005, "Ministryně pro místní rozvoj ČR Zuzana Mrázová", "Ministryně pro místní rozvoj ČR", False)
BLAZEJ = (None, "Poslanec Blažej", "Poslanec", False)  # bez osoby: příjmení z posledního slova
BEZ_RECNIKA = (None, None, None, False)  # úsek na začátku části bez kotvy řečníka (aname 0)

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
        cur.execute("INSERT INTO steno.klub_clenstvi (id_osoba, klub) VALUES (1006, 'ODS')")
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
    from hlidac_rag.psp.rozprava_prepocet import prepocitej_rozpravu

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
    # oslovený Vlček mezi předchozími řečníky není, je ale poslancem období (klub): uloží se tvar z textu
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


def test_adresat_z_textu_jen_prijmeni_osoby_obdobi(conn):
    """Tvar z textu bez shody s posledními řečníky bodu se uloží, jen když je to příjmení osoby období
    (člen klubu nebo řečník kteréhokoli projevu období), jinak NULL („Pirátů“, historický Rašín)."""
    vloz(conn, [
        p(1, HAAS, "Děkuji za slovo."),
        p(2, CHAIR, "Další faktická poznámka pana poslance Pláteníka."),
        p(3, PLATENIK, "Vaším prostřednictvím bych chtěl zareagovat na kolegyni Pirátů, která tu mluvila."),
        p(4, CHAIR, "S faktickou poznámkou pan poslanec Berki."),
        p(5, BERKI, "Zareaguju na pana poslance Rašína, protože to nebylo přesné."),
        p(6, CHAIR, "S faktickou poznámkou pan poslanec Berki."),
        p(7, BERKI, "Zareaguju na pana poslance Vlčka, protože to nebylo přesné."),
        p(8, CHAIR, "S faktickou poznámkou paní ministryně Mrázová."),
        p(9, MRAZOVA, "Ráda bych reagovala na pana místopředsedu Skopečka, který řídí schůzi."),
        p(10, CHAIR, "S faktickou poznámkou pan poslanec Berki."),
        p(11, BERKI, "Chtěl bych se vyjádřit k panu poslanci Haasovi, který tvrdil opak."),
    ])
    prepocitej(conn)
    s = sloupce(conn)
    assert s[3] == ("fakticka", 1, None)               # „Pirátů“ není příjmení žádné osoby
    assert s[5] == ("fakticka", 3, None)               # Rašín je v osobách, ale v období nemluví ani nemá klub
    assert s[7] == ("fakticka", 5, "Vlčka")            # poslanec s klubem
    assert s[9] == ("fakticka", 7, "Skopečka")         # řečník období (předsedající kandidátem adresáta není)
    assert s[11] == ("fakticka", 9, "Karel Haas")      # „se vyjádřit k“: kandidát se shodným příjmením


@pytest.mark.parametrize("dalsich, ocekavano", [(14, "Karel Haas"), (15, "Haase")])
def test_adresat_jen_z_poslednich_15_recniku(conn, dalsich, ocekavano):
    projevy = [p(1, HAAS, "Děkuji za slovo.")]
    projevy += [p(2 + i, PLATENIK, "Děkuji, souhlasím.") for i in range(dalsich)]
    projevy += [p(2 + dalsich, CHAIR, FAKTICKA_BERKI), p(3 + dalsich, BERKI, BERKI_NA_HAASE)]
    vloz(conn, projevy)
    prepocitej(conn)
    assert sloupce(conn)[3 + dalsich] == ("fakticka", 2 + dalsich - 1, ocekavano)


def test_usek_bez_recnika_neni_predchozi_ani_adresat(conn):
    # 15 úseků bez řečníka (aname 0) mezi Haasem a faktickou: neberou místo v okně kandidátů
    projevy = [p(1, HAAS, "Děkuji za slovo.")]
    projevy += [p(2 + i, BEZ_RECNIKA, "…a proto navrhuji zamítnutí.") for i in range(15)]
    projevy += [p(17, CHAIR, FAKTICKA_BERKI), p(18, BERKI, BERKI_NA_HAASE)]
    vloz(conn, projevy)
    prepocitej(conn)
    s = sloupce(conn)
    assert s[18] == ("fakticka", 1, "Karel Haas")
    # úsek sám přepočet dostane, předchozím řečníkem je mu Haas
    assert s[2] == ("recnik", 1, None) and s[16][1] == 1


def test_migrace_neceka_na_cteni_projevu(conn):
    """Hodinový běh volá db.migrate: když sloupce rozpravy už existují, nesmí brát exkluzivní zámek steno.projev
    (ALTER TABLE … IF NOT EXISTS ho bere vždy), jinak čeká za běžícím čtením MCP a další čtení blokuje."""
    from hlidac_rag import db

    # chybějící sloupec (databáze před nasazením rozpravy) migrace doplní
    sql(conn, "ALTER TABLE steno.projev DROP COLUMN reaguje_na_odhad")
    db.migrate(conn)
    ctenar = db.connect()
    try:
        ctenar.execute("SELECT count(*) FROM steno.projev").fetchone()   # otevřená transakce drží ACCESS SHARE
        conn.execute("SET lock_timeout = '500ms'")
        db.migrate(conn)                                                   # druhá migrace: sloupce už jsou
        db.migrate(conn)
        sloupce = sql(conn, """SELECT column_name FROM information_schema.columns
                               WHERE table_schema = 'steno' AND table_name = 'projev'
                                 AND column_name IN ('druh', 'predchozi_recnik_id', 'reaguje_na_odhad')
                               ORDER BY 1""")
        assert sloupce == [("druh",), ("predchozi_recnik_id",), ("reaguje_na_odhad",)]
    finally:
        conn.rollback()
        conn.execute("RESET lock_timeout")
        conn.commit()
        ctenar.rollback()
        ctenar.close()


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


# --- ct projevu v assemble: bod z rec rozhoduje, nadpis bodu bez tisku končí tisk dne -------------------

SCHUZE_CT = 992
INTERPELACE = 58500  # bod „Ústní interpelace“: ct NULL, konec prázdný (schůze 34, bod 203)
NADPIS_285 = ('<p align=center><b>5.<br>Vládní návrh zákona o zdravotním pojištění<br>/sněmovní tisk '
              '<a href="/sqw/historie.sqw?T=285&O=10">285</a>/ &ndash; druhé čtení</b></p>')
NADPIS_INTERPELACE = "<p align=center><b>203.<br>Ústní interpelace</b></p>"


def _r(id_osoba: int, aname: int, label: str, text: str) -> str:
    return f'<p><b><a href="/sqw/detail.sqw?id={id_osoba}" id="r{aname}">{label}</a></b>: {text}</p>'


CAST_CT = (
    _r(1001, 1, "Místopředseda PSP Jan Skopeček", "Zahajuji projednávání bodu.") + NADPIS_285
    + _r(1002, 2, "Poslanec Karel Haas", "Děkuji, k tomuto návrhu mám dvě poznámky.")
    + _r(1005, 3, "Ministryně pro místní rozvoj ČR Zuzana Mrázová", "Děkuji za interpelaci, odpovím písemně.")
    + _r(1001, 4, "Místopředseda PSP Jan Skopeček", "Přecházíme k dalšímu bodu.") + NADPIS_INTERPELACE
    + _r(1003, 5, "Poslanec Jan Berki", "Vážená paní ministryně, ptám se na dotace.")
    + _r(1004, 6, "Poslanec Václav Pláteník", "Děkuji, ke sloučené rozpravě.")
)


def test_assemble_ct_jen_z_bodu_rec(conn):
    """Projev 10/34/622/10: rec dává bod interpelací (bez tisku), tisk dne z dřívějšího nadpisu se nepoužije."""
    from hlidac_rag.psp import ingest

    sql(conn, "INSERT INTO steno.bod (id_bod, obdobi, schuze, cislo, nazev, konec, ct) VALUES (%s,%s,%s,203,%s,'',NULL)",
        (INTERPELACE, OBD, SCHUZE_CT, "Ústní interpelace"))
    sql(conn, """INSERT INTO steno.cast (obdobi, schuze, turn, url, datum, jd, stav, html)
                 VALUES (%s, %s, 622, 'https://x', %s, 10, 'neautorizovano', %s)""", (OBD, SCHUZE_CT, DEN1, CAST_CT))
    # aname 1, 4 a 5 bez řádku v rec (rec nemá bod u 27 % řádků)
    sql(conn, """INSERT INTO steno.rec (obdobi, schuze, turn, aname, id_osoba, id_bod, druh) VALUES
                 (%s, %s, 622, 2, 1002, %s, 1), (%s, %s, 622, 3, 1005, %s, 5), (%s, %s, 622, 6, 1004, %s, 1)""",
        (OBD, SCHUZE_CT, BOD285, OBD, SCHUZE_CT, INTERPELACE, OBD, SCHUZE_CT, SLOUCENA))
    ingest.assemble(conn, OBD, SCHUZE_CT)

    assert sql(conn, "SELECT aname, ct, ct_zdroj FROM steno.projev WHERE schuze = %s ORDER BY aname", (SCHUZE_CT,)) == [
        (1, 285, "nadpis"),  # nadpis v jeho projevu
        (2, 285, "rec"),     # bod z rec s ct
        (3, None, None),     # bod z rec bez tisku (interpelace): tisk dne se nepoužije
        (4, 285, "nadpis"),  # tisk dne měl dřív, než otevřel interpelace
        (5, None, None),     # bez rec, po nadpisu interpelací: tisk dne skončil
        (6, 76, "bod"),      # sloučená rozprava „/sněmovní tisky 76 a 77/“: první tisk z konce bodu
    ]
    tisky_projevu: dict[int, list] = {}
    for aname, ct, zdroj in sql(conn, """SELECT p.aname, t.ct, t.zdroj FROM steno.projev_tisk t
                                         JOIN steno.projev p ON p.id = t.projev_id
                                         WHERE p.schuze = %s ORDER BY 1, 2""", (SCHUZE_CT,)):
        tisky_projevu.setdefault(aname, []).append((ct, zdroj))
    assert tisky_projevu == {1: [(285, "nadpis")], 2: [(285, "rec")], 4: [(285, "nadpis")],
                             6: [(76, "bod"), (77, "bod")]}


# --- get_tisk: body pořadu a počty projevů z projev_tisk ---------------------------------------------------

NAZEV_231 = "Vládní návrh zákona, kterým se mění zákon č. 483/1991 Sb., o České televizi"
BOD231_S30, BOD231 = 59435, 60023
DLOUHY = "Děkuji za slovo, vážený pane předsedající, k návrhu mám několik poznámek. " * 3  # přes 200 znaků


def tabulka_tisky(conn):
    """get_tisk čte tabulku tisky webu: test ji vytvoří prázdnou ve vlastním schématu."""
    sql(conn, f"CREATE SCHEMA {SCHEMA_META}")
    sql(conn, f"""CREATE TABLE {SCHEMA_META}.tisky (ct int, druh int, typ_navrhu text, navrhovatel text, nazev text,
                  uplny_nazev text, predlozeno text, faze text, posledni text, p90 boolean, p90ok boolean, zl boolean,
                  pn_count int, pn_last text, zakony text, sbirka text, score int, reasons text)""")
    sql(conn, f"SET search_path TO {SCHEMA_META}, public")


def test_get_tisk_body_a_pocty_z_projev_tisk(conn):
    """Tisk 231: na pořadu schůze 30 (neprojednán) i 34; většina projevů schůze 34 má tisk jen z nadpisu, bez bodu."""
    from hlidac_rag import tools

    tabulka_tisky(conn)
    sql(conn, """INSERT INTO steno.bod (id_bod, obdobi, schuze, cislo, nazev, konec, ct) VALUES
                 (%s, %s, 30, 34, %s, '/sněmovní tisk 231/', 231), (%s, %s, %s, 34, %s, '/sněmovní tisk 231/', 231),
                 (%s, %s, %s, 203, 'Ústní interpelace', '', NULL)""",
        (BOD231_S30, OBD, NAZEV_231, BOD231, OBD, SCHUZE, NAZEV_231, INTERPELACE, OBD, SCHUZE))
    b231, n231, interp = (BOD231, 231, "rec"), (None, 231, "nadpis"), (INTERPELACE, None, None)
    vloz(conn, [
        p(1, CHAIR, "Otevírám rozpravu k bodu 34.", bod=b231),
        p(2, HAAS, DLOUHY, bod=b231),
        p(3, BERKI, DLOUHY, bod=n231),
        p(4, PLATENIK, "Děkuji.", bod=n231, datum=DEN2),
        p(5, MRAZOVA, DLOUHY, bod=n231, zruseno=True),
        p(6, MRAZOVA, DLOUHY, bod=interp),
    ])
    prepocitej(conn)

    r = tools.get_tisk(conn, 231, ["body"])
    s30 = {"schuze": 30, "body": [34], "nazev": NAZEV_231, "konec": "/sněmovní tisk 231/", "datum": None,
           "projevu": 0, "projevu_vse": 0}
    # projevu: bez předsedajících, od 200 znaků (Haas s bodem a Berki bez bodu); zrušený a interpelace ne
    assert r["body_poradu"] == [s30, dict(s30, schuze=SCHUZE, datum=DEN1.isoformat(), projevu=2, projevu_vse=4)]
    assert r["projevy"] == {"projevu": 2, "projevu_vse": 4, "recniku": 2, "prvni": DEN1.isoformat(),
                            "posledni": DEN1.isoformat()}
    assert r["nazev"] == NAZEV_231  # tisk mimo tabulku tisky: název z bodu pořadu


def test_get_tisk_body_vcetne_slucene_rozpravy(conn):
    """Tisk 77 nemá vlastní bod s ct: schůzi a bod 901 dá sloučená rozprava přes projev_tisk."""
    from hlidac_rag import tools

    tabulka_tisky(conn)
    vloz(conn, ROZPRAVA)
    prepocitej(conn)
    r = tools.get_tisk(conn, 77, ["body"])
    # 15 nezrušených projevů bodu 901, od 200 znaků bez předsedajících jen Berki (reaguje na Haase)
    assert r["body_poradu"] == [{"schuze": SCHUZE, "body": [901], "nazev": "Sloučená rozprava k bodům 1 a 2",
                                 "konec": "/sněmovní tisky 76 a 77/", "datum": DEN1.isoformat(), "projevu": 1,
                                 "projevu_vse": 15}]
    with pytest.raises(ValueError, match="nesleduji"):
        tools.get_tisk(conn, 78, ["body"])


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
    posledni = '{"crawl": {"fetched": 12}, "texty": {"ok": true}}'
    sql(conn, f"INSERT INTO {SCHEMA_META}.meta VALUES ('steno_last_result', %s)", (posledni,))
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
    # výsledek posledního běhu synchronizace (stav_dat) přepočet nepřepíše
    assert sql(conn, f"SELECT v FROM {SCHEMA_META}.meta WHERE k = 'steno_last_result'") == [(posledni,)]


class _Fetcher:
    requests = 0

    def __init__(self, delay=None):
        pass


def test_run_slozi_vse_pri_prazdne_projev_tisk(conn, monkeypatch, capsys):
    """Po nasazení rozpravy je projev_tisk prázdná: běžný běh složí všechny schůze s uloženými částmi (jako
    --reassemble), i když crawl nic nezměnil; další běh už jen schůze se změnou."""
    from hlidac_rag import db, run
    from hlidac_rag.psp.ingest import CrawlResult

    monkeypatch.setattr(run, "Fetcher", _Fetcher)
    monkeypatch.setattr(run.ingest, "discover", lambda *a, **k: 0)
    monkeypatch.setattr(run.ingest, "crawl", lambda *a, **k: CrawlResult())
    monkeypatch.setattr(run, "sync_texty", lambda *a, **k: {})
    sql(conn, f"CREATE SCHEMA {SCHEMA_META}")
    sql(conn, f"CREATE TABLE {SCHEMA_META}.meta (k text PRIMARY KEY, v text)")
    sql(conn, f"INSERT INTO {SCHEMA_META}.meta VALUES ('steno_obdobi', '10')")
    sql(conn, f"SET search_path TO {SCHEMA_META}, public")
    monkeypatch.setattr(db, "connect", lambda autocommit=False: conn)
    vloz_cast(conn)

    assert run.main(["--no-opendata"]) == 0
    vysledek = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert vysledek["assemble"]["schuze"] == 1 and vysledek["assemble"]["nove"] == 3
    over_slozenou_schuzi(conn)
    assert run.main(["--no-opendata"]) == 0
    vysledek = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert vysledek["assemble"]["schuze"] == 0
    # běžný běh výsledek pro stav_dat zapíše
    assert json.loads(sql(conn, f"SELECT v FROM {SCHEMA_META}.meta WHERE k = 'steno_last_result'")[0][0]) == vysledek
