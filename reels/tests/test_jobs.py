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


def test_focus_pn_serazene_bez_duplicit(conn):
    from hlidac_reels import jobs
    i = jobs.add(conn, source="manual", ct=300, focus_pn=[2378, 2376, 2378])
    (job,) = jobs.pending(conn)
    assert job.id == i and job.focus_pn == [2376, 2378]
    jobs.add(conn, source="manual", ct=301)
    assert [j.focus_pn for j in jobs.pending(conn)] == [[2376, 2378], None]


def test_focus_pn_unikatnost(conn):
    from hlidac_reels import jobs
    jobs.add(conn, source="manual", ct=300, focus_pn=[2376, 2377])
    # stejná množina PN (jiné pořadí) je duplicita
    with pytest.raises(jobs.ActiveJobExists):
        jobs.add(conn, source="manual", ct=300, focus_pn=[2377, 2376])
    # jiná množina PN i celý tisk běží vedle sebe
    jobs.add(conn, source="manual", ct=300, focus_pn=[2376])
    jobs.add(conn, source="manual", ct=300)
    with pytest.raises(jobs.ActiveJobExists):
        jobs.add(conn, source="manual", ct=300)
    with pytest.raises(jobs.ActiveJobExists):
        jobs.add(conn, source="manual", ct=300, focus_pn=[])  # prázdný seznam = celý tisk
    assert len(jobs.pending(conn)) == 3


def test_migrace_nahradi_stary_index(conn):
    from hlidac_reels import db
    db.migrate(conn)  # podruhé: idempotentní
    with conn.cursor() as cur:
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'reel_jobs' "
                    "AND indexname LIKE 'reel_jobs_active%%'")
        names = sorted(r[0] for r in cur.fetchall())
    conn.commit()
    assert names == ["reel_jobs_active_ct_pn"]


def test_jobs_add_autopilot(conn):
    from hlidac_reels import jobs, queue
    a = jobs.add(conn, source="manual", ct=300, focus_pn=[2376], autopilot=True)
    b = jobs.add(conn, source="manual", ct=301)
    by_id = {j.id: j for j in jobs.pending(conn)}
    assert by_id[a].autopilot is True and by_id[b].autopilot is False
    r1 = queue.add(conn, video_path="a.mp4", video_sha256="x" * 64, caption="c", autopilot=True)
    r2 = queue.add(conn, video_path="b.mp4", video_sha256="y" * 64, caption="c")
    assert queue.get(conn, r1).autopilot is True and queue.get(conn, r2).autopilot is False


def test_jobs_autopilot_off(conn):
    from hlidac_reels import jobs, queue
    q = jobs.add(conn, source="manual", ct=300, autopilot=True)
    c = jobs.add(conn, source="manual", ct=301, autopilot=True)
    d = jobs.add(conn, source="manual", ct=302, autopilot=True)
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status='claimed' WHERE id=%s", (c,))
        cur.execute("UPDATE reel_jobs SET status='done' WHERE id=%s", (d,))
    conn.commit()
    assert jobs.autopilot_off(conn) == 2
    assert jobs.autopilot_off(conn) == 0
    with conn.cursor() as cur:
        cur.execute("SELECT id, autopilot FROM reel_jobs ORDER BY id")
        assert cur.fetchall() == [(q, False), (c, False), (d, True)]
    conn.commit()
    # set_fields příznak autopilota změnit nesmí
    r = queue.add(conn, video_path="a.mp4", video_sha256="x" * 64, caption="c")
    with pytest.raises(ValueError):
        queue.set_fields(conn, r, autopilot=True)


def test_migrace_dvakrat(conn):
    from hlidac_reels import db
    db.migrate(conn)
    db.migrate(conn)  # schéma je idempotentní
    with conn.cursor() as cur:
        cur.execute("SELECT table_name, is_nullable, column_default FROM information_schema.columns "
                    "WHERE column_name = 'autopilot' AND table_name IN ('reel_jobs','reel_queue') "
                    "ORDER BY table_name")
        rows = cur.fetchall()
    conn.commit()
    assert rows == [("reel_jobs", "NO", "false"), ("reel_queue", "NO", "false")]
