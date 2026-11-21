"""Testy fronty zadání reel_jobs (PostgreSQL, marker pg)."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.pg


@pytest.fixture
def conn():
    if os.environ.get("REELS_TEST_DB") != "1":
        pytest.skip("chybí REELS_TEST_DB=1 (testy mažou tabulky reel_jobs a reel_queue)")
    from hlidac_reels import db
    c = db.connect()
    db.migrate(c)
    with c.cursor() as cur:
        cur.execute("TRUNCATE reel_jobs RESTART IDENTITY; TRUNCATE reel_queue RESTART IDENTITY")
    c.commit()
    yield c
    c.close()


def test_add_a_pending_v_poradi_vydeje(conn):
    from hlidac_reels import jobs
    id_fresh = jobs.add(conn, source="fresh", ct=1, reason_keys=["a", "b"])
    jobs.add(conn, source="manual", ct=2)
    jobs.add(conn, source="auto_pick")
    assert [j.source for j in jobs.pending(conn)] == ["manual", "auto_pick", "fresh"]
    assert jobs.position(conn, id_fresh) == 3


def test_druha_aktivni_uloha_pro_tisk(conn):
    from hlidac_reels import jobs
    first = jobs.add(conn, source="manual", ct=5)
    with pytest.raises(jobs.ActiveJobExists):
        jobs.add(conn, source="manual", ct=5)
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status='done' WHERE id=%s", (first,))
    conn.commit()
    assert jobs.add(conn, source="manual", ct=5)
    # ct NULL se neomezuje
    jobs.add(conn, source="auto_pick")
    jobs.add(conn, source="auto_pick")


def test_failed_unnotified(conn):
    from hlidac_reels import jobs
    i = jobs.add(conn, source="manual", ct=3)
    assert jobs.failed_unnotified(conn) == []
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status='failed', error='x' WHERE id=%s", (i,))
    conn.commit()
    assert [j.id for j in jobs.failed_unnotified(conn)] == [i]
    jobs.mark_notified(conn, i)
    assert jobs.failed_unnotified(conn) == []
