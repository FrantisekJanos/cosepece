"""Testy událostí úloh a workeru reel_events (PostgreSQL, marker pg)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.pg


@pytest.fixture
def conn():
    if os.environ.get("REELS_TEST_DB") != "1":
        pytest.skip("chybí REELS_TEST_DB=1 (testy mažou tabulky reel_events a reel_jobs)")
    from hlidac_reels import db
    c = db.connect()
    db.migrate(c)
    with c.cursor() as cur:
        cur.execute("TRUNCATE reel_events RESTART IDENTITY; TRUNCATE reel_jobs RESTART IDENTITY")
    c.commit()
    yield c
    c.close()


def _fail(conn, job_id: int, error: str = "x", kind: str | None = None, notified: bool = False) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status='failed', error=%s, fail_kind=%s, finished_at=now(), "
                    "notified_at=CASE WHEN %s THEN now() END WHERE id=%s", (error, kind, notified, job_id))
    conn.commit()


def test_add_dedupe(conn):
    from hlidac_reels import events
    first = events.add(conn, "failed", job_id=7, detail={"error": "chyba č. 1"}, urgency=events.URGENT,
                       dedupe="failed:7")
    assert isinstance(first, int)
    # stejný klíč: nic nového, None
    assert events.add(conn, "failed", job_id=7, urgency=events.URGENT, dedupe="failed:7") is None
    # bez klíče se nic neslučuje
    a = events.add(conn, "stage", job_id=7, detail={"stage": "model"})
    b = events.add(conn, "stage", job_id=7, detail={"stage": "model"})
    assert a is not None and b is not None and a != b
    got = events.for_job(conn, 7)
    assert [e.kind for e in got] == ["failed", "stage", "stage"]
    assert got[0].detail == {"error": "chyba č. 1"} and got[0].urgency == events.URGENT
    assert got[0].dedupe == "failed:7" and got[0].notified_at is None
    # bez detailu prázdný objekt
    c = events.add(conn, "worker_resumed", urgency=events.URGENT)
    with conn.cursor() as cur:
        cur.execute("SELECT detail, job_id FROM reel_events WHERE id=%s", (c,))
        assert cur.fetchone() == ("{}", None)
    conn.commit()


def test_unsent_poradi_a_mark_sent(conn):
    from hlidac_reels import events
    u1 = events.add(conn, "failed", job_id=1, urgency=events.URGENT)
    events.add(conn, "stage", job_id=1)
    s1 = events.add(conn, "failed", job_id=2, urgency=events.SUMMARY)
    u2 = events.add(conn, "worker_paused", detail={"reason": "limit"}, urgency=events.URGENT)
    assert [e.id for e in events.unsent(conn, events.URGENT)] == [u1, u2]
    assert [e.id for e in events.unsent(conn, events.SUMMARY)] == [s1]
    events.mark_sent(conn, [u1])
    assert [e.id for e in events.unsent(conn, events.URGENT)] == [u2]
    # prázdný seznam nic nedělá, opakované označení nemění čas prvního odeslání
    events.mark_sent(conn, [])
    first = next(e for e in events.for_job(conn, 1) if e.id == u1).notified_at
    assert first is not None
    events.mark_sent(conn, [u1, u2])
    assert events.unsent(conn, events.URGENT) == []
    assert next(e for e in events.for_job(conn, 1) if e.id == u1).notified_at == first
    assert [e.id for e in events.unsent(conn, events.SUMMARY)] == [s1]


def test_hm_v_prazskem_case(conn):
    from hlidac_reels import events
    with conn.cursor() as cur:
        cur.execute("INSERT INTO reel_events (at, kind, job_id, detail) "
                    "VALUES ('2026-11-24 13:02:00+00', 'claimed', 5, '{\"attempt\": 1}')")
        # nečitelný detail i JSON, který není objekt, se čte jako {}
        cur.execute("INSERT INTO reel_events (at, kind, job_id, detail) "
                    "VALUES ('2026-11-24 08:05:00+00', 'stage', 5, 'nejde o JSON')")
        cur.execute("INSERT INTO reel_events (kind, job_id, detail) VALUES ('stage', 5, '[1, 2]')")
        cur.execute("INSERT INTO reel_events (kind, job_id, detail) VALUES ('stage', 5, NULL)")
    conn.commit()
    got = events.for_job(conn, 5)
    assert got[0].hm == "14:02"
    assert got[0].at == datetime(2026, 11, 24, 13, 2, tzinfo=timezone.utc)
    assert got[0].detail == {"attempt": 1}
    assert got[1].hm == "9:05"
    assert [e.detail for e in got[1:]] == [{}, {}, {}]


def test_count_since(conn):
    from hlidac_reels import events
    with conn.cursor() as cur:
        cur.execute("INSERT INTO reel_events (at, kind) VALUES (now() - interval '2 hours', 'worker_paused')")
    conn.commit()
    events.add(conn, "worker_paused", urgency=events.URGENT)
    events.add(conn, "worker_resumed", urgency=events.URGENT)
    now = datetime.now(timezone.utc)
    assert events.count_since(conn, "worker_paused", now - timedelta(hours=1)) == 1
    assert events.count_since(conn, "worker_paused", now - timedelta(hours=3)) == 2
    assert events.count_since(conn, "done", now - timedelta(hours=3)) == 0


def test_adopt_failed(conn):
    from hlidac_reels import events, jobs
    manual = jobs.add(conn, source="manual", ct=1)
    fresh = jobs.add(conn, source="fresh", ct=2)
    told = jobs.add(conn, source="auto_pick", ct=3)
    jobs.add(conn, source="manual", ct=4)  # aktivní úloha se nepřebírá
    _fail(conn, manual, "žádná platná varianta", kind="content")
    _fail(conn, fresh, "chyba prostředí")
    _fail(conn, told, "už ohlášeno", notified=True)

    assert events.adopt_failed(conn) == 2
    assert events.adopt_failed(conn) == 0
    got = {e.job_id: e for e in events.unsent(conn, events.URGENT) + events.unsent(conn, events.SUMMARY)}
    assert set(got) == {manual, fresh}
    assert got[manual].kind == "failed" and got[manual].urgency == events.URGENT
    assert got[manual].dedupe == f"failed:{manual}"
    assert got[manual].detail == {"error": "žádná platná varianta", "kind": "content"}
    assert got[fresh].urgency == events.SUMMARY
    assert got[fresh].detail == {"error": "chyba prostředí", "kind": None}


def test_adopt_failed_neprepise_udalost_serveru(conn):
    from hlidac_reels import events, jobs
    j = jobs.add(conn, source="manual", ct=1)
    _fail(conn, j, "x", kind="timeout")
    # server (ReelJobs::failedEvent) už událost zapsal se stejným klíčem
    events.add(conn, "failed", job_id=j, detail={"error": "x", "kind": "timeout", "attempts": 3},
               urgency=events.URGENT, dedupe=f"failed:{j}")
    assert events.adopt_failed(conn) == 0
    got = events.for_job(conn, j)
    assert len(got) == 1 and got[0].detail["attempts"] == 3


def test_konstanty():
    from hlidac_reels import events
    assert (events.HISTORY, events.URGENT, events.SUMMARY) == (0, 1, 2)
    assert events.OUTAGE == timedelta(hours=6)
