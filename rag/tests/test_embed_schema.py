"""Schéma rag (úseky s embeddingy) nad PostgreSQL s pgvector. Bez DATABASE_URL se přeskočí."""
import os

import pytest

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)"),
]


def test_schema_embeddingy():
    from hlidac_rag import db

    c = db.connect()
    try:
        db.migrate(c)
        db.migrate(c)   # druhá migrace nespadne (idempotentní)
        with c.cursor() as cur:
            cur.execute("SELECT to_regclass('rag.usek') IS NOT NULL")
            assert cur.fetchone()[0]
            cur.execute("""SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a
                           WHERE a.attrelid = 'rag.usek'::regclass AND a.attname = 'embedding'""")
            assert cur.fetchone()[0] == "halfvec(1024)"
            cur.execute("SELECT indexdef FROM pg_indexes WHERE schemaname = 'rag' AND tablename = 'usek'")
            defs = [r[0] for r in cur.fetchall()]
        assert any("hnsw" in d and "halfvec_cosine_ops" in d for d in defs), defs
        for sloupec in ("(projev_id)", "(cel_id)", "(ct)", "(id_osoba, datum)"):
            assert any(sloupec in d for d in defs), sloupec
    finally:
        c.rollback()
        c.close()


def test_usek_prave_jeden_zdroj():
    import psycopg

    from hlidac_rag import db

    c = db.connect()
    try:
        db.migrate(c)
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO rag.usek (zdroj, poradi, od_znaku, do_znaku, hlavicka, text_hash, model) "
                      "VALUES ('projev', 0, 0, 1, 'h', 'x', 'm')")
    finally:
        c.rollback()
        c.close()
