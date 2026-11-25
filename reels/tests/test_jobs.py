"""Testy fronty zadání reel_jobs (PostgreSQL, marker pg)."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.pg


@pytest.fixture
def conn():
    if os.environ.get("REELS_TEST_DB") != "1":
        pytest.skip("chybí REELS_TEST_DB=1 (testy mažou tabulky reel_jobs, reel_queue a reel_events)")
    from hlidac_reels import db
    c = db.connect()
    db.migrate(c)
    with c.cursor() as cur:
        cur.execute("TRUNCATE reel_jobs RESTART IDENTITY; TRUNCATE reel_queue RESTART IDENTITY; "
                    "TRUNCATE reel_events RESTART IDENTITY")
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


def test_schema_f3(conn):
    """F3: nové sloupce úloh (odklad, vydání, fáze, druh chyby) a tabulka událostí reel_events."""
    import psycopg
    from hlidac_reels import db, jobs
    db.migrate(conn)
    db.migrate(conn)  # idempotentní i s novými sloupci a tabulkou
    new_cols = {"not_before", "wait_reason", "lease_until", "stage", "stage_at", "fail_kind",
                "env_retries", "detail", "status_msg_id"}
    with conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'reel_jobs'")
        cols = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'reel_events' "
                    "AND indexname IN ('reel_events_unsent_idx', 'reel_events_job_idx')")
        idx = sorted(r[0] for r in cur.fetchall())
        cur.execute("TRUNCATE reel_events RESTART IDENTITY")
    conn.commit()
    assert new_cols - cols == set()
    assert idx == ["reel_events_job_idx", "reel_events_unsent_idx"]

    # dedupe je unikátní, NULL se neomezuje
    with conn.cursor() as cur:
        cur.execute("INSERT INTO reel_events (kind, job_id, dedupe) VALUES ('failed', 1, 'job:1:failed')")
    conn.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO reel_events (kind, job_id, dedupe) VALUES ('failed', 1, 'job:1:failed')")
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO reel_events (kind, job_id) VALUES ('stage', 1)")
        cur.execute("INSERT INTO reel_events (kind, job_id) VALUES ('stage', 1)")
        cur.execute("SELECT count(*), count(dedupe), min(urgency), count(notified_at), count(at) FROM reel_events")
        assert cur.fetchone() == (3, 1, 0, 0, 3)
    conn.commit()

    # nová úloha: env_retries 0, ostatní nová pole prázdná
    i = jobs.add(conn, source="manual", ct=7)
    (job,) = jobs.pending(conn)
    assert job.id == i and job.env_retries == 0
    assert (job.not_before, job.wait_reason, job.lease_until, job.stage, job.stage_at,
            job.fail_kind, job.detail, job.status_msg_id) == (None,) * 8


def test_get_silent_hm(conn):
    from datetime import datetime, timedelta, timezone
    from hlidac_reels import jobs
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    ids = [jobs.add(conn, source="manual", ct=ct) for ct in (1, 2, 3, 4, 5)]
    # (lease_until, claimed_at): propadlé vydání, platné, bez lease_until starší 60 min, mladší 60 min
    rows = [(now - timedelta(minutes=1), now - timedelta(hours=2)),
            (now + timedelta(minutes=1), now - timedelta(hours=2)),
            (None, now - timedelta(minutes=61)), (None, now - timedelta(minutes=59))]
    with conn.cursor() as cur:
        for job_id, (lease, claimed) in zip(ids, rows):
            cur.execute("UPDATE reel_jobs SET status='claimed', lease_until=%s, claimed_at=%s WHERE id=%s",
                        (lease, claimed, job_id))
        # úloha ve frontě (queued) se nepočítá ani s propadlým lease_until
        cur.execute("UPDATE reel_jobs SET lease_until=%s WHERE id=%s", (now - timedelta(hours=1), ids[4]))
    conn.commit()
    assert [j.id for j in jobs.silent(conn, now)] == [ids[0], ids[2]]
    got = jobs.get(conn, ids[0])
    assert got.ct == 1 and got.status == "claimed" and jobs.get(conn, 999) is None
    assert jobs.hm(conn, datetime(2026, 11, 25, 8, 5, tzinfo=timezone.utc)) == "9:05"
    assert jobs.hm(conn, datetime(2026, 7, 1, 20, 30, tzinfo=timezone.utc)) == "22:30"


def _set(conn, job_id: int, sql: str, *params) -> None:
    with conn.cursor() as cur:
        cur.execute(f"UPDATE reel_jobs SET {sql} WHERE id=%s", (*params, job_id))
    conn.commit()


def test_retry_a_cancel(conn):
    from hlidac_reels import jobs
    # zrušit jde úloha ve frontě i vydaná (vydání a token se smažou), jednou
    a = jobs.add(conn, source="manual", ct=10)
    assert jobs.cancel(conn, a) is True
    job = jobs.get(conn, a)
    assert job.status == "cancelled" and job.finished_at is not None
    assert jobs.cancel(conn, a) is False
    b = jobs.add(conn, source="manual", ct=11)
    _set(conn, b, "status='claimed', claim_token='t', claimed_at=now(), lease_until=now() + interval '1 hour'")
    assert jobs.cancel(conn, b) is True
    job = jobs.get(conn, b)
    assert (job.status, job.claim_token, job.lease_until) == ("cancelled", None, None)
    # hotová a neexistující ne
    c = jobs.add(conn, source="manual", ct=12)
    _set(conn, c, "status='done', finished_at=now()")
    assert jobs.cancel(conn, c) is False and jobs.get(conn, c).status == "done"
    assert jobs.cancel(conn, 999) is False

    # opakovat jde jen konečný stav failed, cancelled, expired
    with pytest.raises(ValueError):
        jobs.retry(conn, c)
    with pytest.raises(ValueError):
        jobs.retry(conn, 999)
    with pytest.raises(ValueError):
        jobs.retry(conn, jobs.add(conn, source="manual", ct=13))
    # nová úloha (nové id): ruční, stejný tisk, formát a PN, vždy bez autopilota
    f = jobs.add(conn, source="manual", ct=14, format_hint="pn", focus_pn=[2377, 2376], autopilot=True)
    _set(conn, f, "status='failed', error='x', finished_at=now()")
    n = jobs.retry(conn, f)
    new = jobs.get(conn, n)
    assert n != f
    assert (new.source, new.ct, new.format_hint, new.focus_pn, new.autopilot, new.status) == (
        "manual", 14, "pn", [2376, 2377], False, "queued")
    # stejné zadání už ve frontě je
    with pytest.raises(jobs.ActiveJobExists):
        jobs.retry(conn, f)
    # zrušená i vypršelá; auto_pick bez tisku zůstane auto_pick, s tiskem (vybral server) a fresh = manual
    assert jobs.get(conn, jobs.retry(conn, a)).source == "manual"
    p = jobs.add(conn, source="auto_pick")
    _set(conn, p, "status='expired', finished_at=now()")
    assert (jobs.get(conn, jobs.retry(conn, p)).source, jobs.get(conn, p).status) == ("auto_pick", "expired")
    q = jobs.add(conn, source="auto_pick")
    _set(conn, q, "status='cancelled', ct=15, finished_at=now()")
    assert (lambda j: (j.source, j.ct))(jobs.get(conn, jobs.retry(conn, q))) == ("manual", 15)
    r = jobs.add(conn, source="fresh", ct=16, reason_keys=["k"])
    _set(conn, r, "status='failed', finished_at=now()")
    assert (lambda j: (j.source, j.ct))(jobs.get(conn, jobs.retry(conn, r))) == ("manual", 16)


def test_status_msg(conn):
    from hlidac_reels import jobs
    a = jobs.add(conn, source="manual", ct=20)
    jobs.set_status_msg(conn, a, 555)
    assert jobs.get(conn, a).status_msg_id == 555
    # autopilot průběh nehlásí, úloha bez zprávy se nepřepisuje
    b = jobs.add(conn, source="manual", ct=21, autopilot=True)
    jobs.set_status_msg(conn, b, 556)
    jobs.add(conn, source="manual", ct=22)
    # hotová před víc než dnem ne, před hodinou ano, vydaná ano
    d = jobs.add(conn, source="manual", ct=23)
    jobs.set_status_msg(conn, d, 557)
    _set(conn, d, "status='done', finished_at=now() - interval '2 days'")
    e = jobs.add(conn, source="manual", ct=24)
    jobs.set_status_msg(conn, e, 558)
    _set(conn, e, "status='failed', finished_at=now() - interval '1 hour'")
    f = jobs.add(conn, source="manual", ct=25)
    jobs.set_status_msg(conn, f, 559)
    _set(conn, f, "status='claimed', claimed_at=now()")
    assert [j.id for j in jobs.with_status_msg(conn)] == [a, e, f]
