"""Přírůstkový indexer embeddingů nad PostgreSQL s falešným klientem (žádné volání API). Bez DATABASE_URL se přeskočí.

Tabulku webu tisky si test vytvoří ve vlastním schématu a nastaví na něj search_path; steno, texty a rag vyprázdní.
"""
from __future__ import annotations

import hashlib
import os
from datetime import date

import pytest

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)"),
]

SCHEMA_WEB = "test_embed_web"
OBD = 10


class FalesnyKlient:
    """Deterministické vektory z hashe textu; tokeny = počet znaků // 4. Počítá volání."""

    def __init__(self, model: str = "text-embedding-3-large", dim: int = 1024, chyba: Exception | None = None):
        self.model, self.dim, self.chyba = model, dim, chyba
        self.volani: list[list[str]] = []

    def embed(self, texty: list[str]) -> tuple[list[list[float]], int]:
        self.volani.append(list(texty))
        if self.chyba:
            raise self.chyba
        vektory = []
        for t in texty:
            h = hashlib.sha256(t.encode()).digest()
            v = [0.0] * self.dim
            for i in range(8):
                v[h[i] * 4 % self.dim] += 1.0
            vektory.append(v)
        return vektory, sum(len(t) // 4 for t in texty)


def _sql(conn, q: str, params=()):
    with conn.cursor() as cur:
        cur.execute(q, params)
        rows = cur.fetchall() if cur.description else None
    conn.commit()
    return rows


def _vycisti(c):
    with c.cursor() as cur:
        cur.execute("TRUNCATE rag.usek, steno.projev, steno.bod, steno.osoba CASCADE")
        cur.execute("TRUNCATE texty.dokument CASCADE")
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA_WEB} CASCADE")


@pytest.fixture
def conn():
    from hlidac_rag import db

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
        cur.execute(f"INSERT INTO {SCHEMA_WEB}.tisky VALUES (1, 221, 'Novela zákona o střetu zájmů')")
        cur.execute(f"SET search_path TO {SCHEMA_WEB}, public")
        cur.execute("""INSERT INTO steno.bod (id_bod, obdobi, schuze, cislo, nazev, ct)
                       VALUES (9001, 10, 36, 1, 'Vládní návrh zákona o cenách energií', 311)""")
    c.commit()
    yield c
    c.rollback()
    _vycisti(c)
    c.commit()
    c.close()


def _projev(conn, aname: int, text: str, tisky=(311,), zruseno=False, predsedajici=False, klub="ODS",
            druh="recnik") -> int:
    r = _sql(conn, """INSERT INTO steno.projev (obdobi, schuze, turn, aname, datum, poradi, id_osoba, recnik, role, klub,
                             predsedajici, id_bod, text, znaku, hash, url, zruseno, druh)
                      VALUES (10, 36, 100, %s, %s, %s, 2001, 'Poslanec Jan Novák', 'Poslanec', %s, %s, 9001, %s, %s,
                              'h', 'https://x', %s, %s) RETURNING id""",
             (aname, date(2026, 11, 13), aname, klub, predsedajici, text, len(text),
              "2026-11-14" if zruseno else None, druh))
    pid = r[0][0]
    for t in tisky:
        _sql(conn, "INSERT INTO steno.projev_tisk (projev_id, ct, zdroj) VALUES (%s, %s, 'bod')", (pid, t))
    return pid


def _dokument(conn, celky: list[tuple[str, str]], druh="tisk", ct=221, cislo_pn=0, verze="puvodni") -> list[int]:
    """Dostupný dokument s celky (typ, text); vrátí id celků."""
    text = "\n".join(t for _, t in celky)
    d = _sql(conn, """INSERT INTO texty.dokument (druh, ct, cislo_pn, verze, zdroj_textu, text, hash, znaku, rozdeleno_at)
                      VALUES (%s, %s, %s, %s, 'tisk_text', %s, 'h', %s, now()) RETURNING id""",
             (druh, ct, cislo_pn, verze, text, len(text)))[0][0]
    ids = []
    for i, (typ, t) in enumerate(celky):
        ids.append(_sql(conn, """INSERT INTO texty.cel (dokument_id, poradi, typ, oznaceni, cesta, meni_zakon, text,
                                                       znaku, od_znaku)
                                 VALUES (%s, %s, %s, %s, %s, '256/2000', %s, %s, 0) RETURNING id""",
                        (d, i, typ, f"Čl. {i + 1}", f"ČÁST TŘETÍ / Čl. {i + 1}", t, len(t)))[0][0])
    return ids


DLOUHY = " ".join(f"Věta {i} o cenách elektřiny pro domácnosti a firmy." for i in range(150))   # ~7 500 znaků
KRATKY_OK = "Děkuji za slovo. " + "Ceny energií rostou a vláda musí jednat. " * 6


def _useky(conn, sloupec: str, zdroj_id: int):
    return _sql(conn, f"SELECT poradi, od_znaku, do_znaku, text_hash, model FROM rag.usek WHERE {sloupec} = %s "
                      "ORDER BY poradi", (zdroj_id,))


def test_indexuje_projev_a_celek(conn):
    from hlidac_rag.embed.indexer import indexuj

    pid = _projev(conn, 1, DLOUHY)
    cid = _dokument(conn, [("clanek", "Lhůta pro zahájení řízení o vrácení dotace se zkracuje na 4 roky.")])[0]
    k = FalesnyKlient()
    r = indexuj(conn, k)
    assert r["zdroju"] == 2 and r["preruseno_stropem"] is False and r["zbyva_zdroju"] == 0
    up = _useky(conn, "projev_id", pid)
    assert len(up) >= 3 and r["useku"] == len(up) + 1
    assert all(u[4] == "text-embedding-3-large" for u in up)
    text = _sql(conn, "SELECT text FROM steno.projev WHERE id = %s", (pid,))[0][0]
    assert up[0][1] == 0 and up[-1][2] == len(text)
    assert len(_useky(conn, "cel_id", cid)) == 1
    assert r["tokenu"] == sum(len(t) // 4 for v in k.volani for t in v)
    assert r["odhad_usd"] == pytest.approx(r["tokenu"] / 1e6 * 0.13, abs=1e-6)
    # vstup embeddingu = hlavička + tělo; vektor je uložený
    assert any("tisk 311" in t and "cenách energií" in t for v in k.volani for t in v)
    assert any("Tisk 221" in t and "střetu zájmů" in t for v in k.volani for t in v)
    assert _sql(conn, "SELECT count(*) FROM rag.usek WHERE embedding IS NULL")[0][0] == 0


def test_kratky_projev_preskocen(conn):
    from hlidac_rag.embed.indexer import indexuj

    pid = _projev(conn, 1, "Děkuji, pane předsedající.")
    r = indexuj(conn, FalesnyKlient(), zdroje=("projev",))
    assert r["zdroju"] == 0 and _useky(conn, "projev_id", pid) == []


def test_opakovany_beh_nic_nedela(conn):
    from hlidac_rag.embed.indexer import indexuj

    _projev(conn, 1, DLOUHY)
    _dokument(conn, [("clanek", "Text článku o dotacích.")])
    indexuj(conn, FalesnyKlient())
    k = FalesnyKlient()
    r = indexuj(conn, k)
    assert r["useku"] == 0 and r["zdroju"] == 0 and k.volani == []


def test_zmena_textu_nahradi_useky(conn):
    from hlidac_rag.embed.indexer import indexuj

    pid = _projev(conn, 1, DLOUHY)
    indexuj(conn, FalesnyKlient())
    pred = _useky(conn, "projev_id", pid)
    _sql(conn, "UPDATE steno.projev SET text = %s, znaku = %s WHERE id = %s", (KRATKY_OK, len(KRATKY_OK), pid))
    r = indexuj(conn, FalesnyKlient())
    po = _useky(conn, "projev_id", pid)
    assert r["zdroju"] == 1 and len(po) == 1
    assert not {u[3] for u in pred} & {u[3] for u in po}


def test_zmena_hlavicky_nahradi_useky(conn):
    from hlidac_rag.embed.indexer import indexuj

    pid = _projev(conn, 1, KRATKY_OK)
    indexuj(conn, FalesnyKlient())
    _sql(conn, "INSERT INTO steno.projev_tisk (projev_id, ct, zdroj) VALUES (%s, 312, 'rec')", (pid,))
    k = FalesnyKlient()
    assert indexuj(conn, k)["zdroju"] == 1 and "tisky 311, 312" in k.volani[0][0]


def test_jiny_model_preindexuje(conn):
    from hlidac_rag.embed.indexer import indexuj

    pid = _projev(conn, 1, KRATKY_OK)
    indexuj(conn, FalesnyKlient())
    r = indexuj(conn, FalesnyKlient(model="text-embedding-3-small"))
    assert r["zdroju"] == 1 and r["odhad_usd"] == pytest.approx(r["tokenu"] / 1e6 * 0.02, abs=1e-6)
    assert [u[4] for u in _useky(conn, "projev_id", pid)] == ["text-embedding-3-small"]


def test_zruseny_projev_bez_useku(conn):
    from hlidac_rag.embed.indexer import indexuj

    zrus = _projev(conn, 1, DLOUHY, zruseno=True)
    pid = _projev(conn, 2, KRATKY_OK)
    indexuj(conn, FalesnyKlient())
    assert _useky(conn, "projev_id", zrus) == [] and len(_useky(conn, "projev_id", pid)) == 1
    # projev zrušený až po indexaci úseky při dalším běhu ztratí
    _sql(conn, "UPDATE steno.projev SET zruseno = now() WHERE id = %s", (pid,))
    indexuj(conn, FalesnyKlient())
    assert _useky(conn, "projev_id", pid) == []


def test_nedostupny_dokument_ztrati_useky(conn):
    from hlidac_rag.embed.indexer import indexuj

    cid = _dokument(conn, [("clanek", "Text článku o dotacích.")])[0]
    indexuj(conn, FalesnyKlient())
    _sql(conn, "UPDATE texty.dokument SET chyba = 'dělení selhalo'")
    k = FalesnyKlient()
    indexuj(conn, k)
    assert _useky(conn, "cel_id", cid) == [] and k.volani == []


def test_strop_tokenu_cele_zdroje(conn):
    from hlidac_rag.embed.indexer import indexuj

    pids = [_projev(conn, i, DLOUHY.replace("Věta", f"Věta{i}")) for i in range(1, 6)]
    k = FalesnyKlient()
    r = indexuj(conn, k, max_tokenu=6000, zdroje=("projev",))
    assert r["preruseno_stropem"] is True and r["zbyva_zdroju"] > 0
    assert r["tokenu"] <= 6000 and 0 < r["zdroju"] < 5
    plne = 0
    for pid in pids:
        n = len(_useky(conn, "projev_id", pid))
        text = _sql(conn, "SELECT text FROM steno.projev WHERE id = %s", (pid,))[0][0]
        from hlidac_rag.embed.useky import rozdel
        assert n in (0, len(rozdel(text)))
        plne += bool(n)
    assert plne == r["zdroju"] and r["zbyva_zdroju"] == 5 - plne
    # další běh dokončí zbytek
    r2 = indexuj(conn, FalesnyKlient())
    assert r2["zdroju"] == 5 - plne and r2["zbyva_zdroju"] == 0


def test_chyba_klienta_neshodi_beh(conn):
    from hlidac_rag.embed.indexer import indexuj
    from hlidac_rag.embed.klient import EmbedChyba

    pid = _projev(conn, 1, DLOUHY)
    r = indexuj(conn, FalesnyKlient(chyba=EmbedChyba("OpenAI vrátilo HTTP 500")))
    assert "HTTP 500" in r["chyba"] and r["zdroju"] == 0 and r["zbyva_zdroju"] == 1
    assert _useky(conn, "projev_id", pid) == []


def test_spatna_dimenze_klienta(conn):
    from hlidac_rag.embed.indexer import indexuj

    _projev(conn, 1, KRATKY_OK)
    k = FalesnyKlient(dim=1536)
    r = indexuj(conn, k)
    assert "1024" in r["chyba"] and k.volani == []


def test_filtry_zkopirovane(conn):
    from hlidac_rag.embed.indexer import indexuj

    pid = _projev(conn, 1, KRATKY_OK, tisky=(77, 76), klub="ANO2011", druh="fakticka")
    norm, duv = _dokument(conn, [("clanek", "Normativní text článku."), ("duvodova_zprava", "Důvodová zpráva.")],
                          verze="schvalene")
    pn = _dokument(conn, [("bod", "V bodě 1 se slovo deset nahrazuje slovem čtyři.")], druh="pn", cislo_pn=5012)[0]
    indexuj(conn, FalesnyKlient())
    p = _sql(conn, """SELECT ct, id_osoba, klub, datum, predsedajici, druh, zdroj FROM rag.usek WHERE projev_id = %s""",
             (pid,))
    assert p == [(76, 2001, "ANO2011", date(2026, 11, 13), False, "fakticka", "projev")]
    c = {r[0]: r[1:] for r in _sql(conn, """SELECT cel_id, ct, druh, verze, vcetne_duvodove, zdroj FROM rag.usek
                                             WHERE cel_id IS NOT NULL""")}
    assert c[norm] == (221, "tisk", "schvalene", True, "cel")
    assert c[duv] == (221, "tisk", "schvalene", False, "cel")
    assert c[pn] == (221, "pn", "puvodni", True, "cel")
