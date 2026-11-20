"""Testy fronty reel_queue (PostgreSQL, marker pg)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.pg


@pytest.fixture
def conn():
    from hlidac_reels import db
    c = db.connect()
    db.migrate(c)
    with c.cursor() as cur:
        cur.execute("TRUNCATE reel_queue RESTART IDENTITY; TRUNCATE reel_state")
    c.commit()
    yield c
    c.close()


def _add(conn, n, **kw):
    from hlidac_reels import queue
    return queue.add(conn, video_path=f"v{n}.mp4", video_sha256=f"sha{n}", caption=f"c{n}", **kw)


def test_add_a_next_queued_podle_id(conn):
    from hlidac_reels import queue
    a, b = _add(conn, 1), _add(conn, 2)
    assert a < b
    assert queue.next_queued(conn).id == a
    assert queue.transition(conn, a, "queued", "pending")
    assert queue.next_queued(conn).id == b
    assert queue.get(conn, b).source == "ukazka"
    assert queue.get(conn, 999) is None
    assert queue.pending_count(conn) == 1


def test_duplicitni_video_vyhodi_DuplicateVideo(conn):
    from hlidac_reels import queue
    _add(conn, 1)
    with pytest.raises(queue.DuplicateVideo):
        _add(conn, 1)
    # spojení zůstane použitelné
    assert _add(conn, 2)


def test_transition_jen_z_ocekavaneho_stavu(conn):
    from hlidac_reels import queue
    i = _add(conn, 1)
    queue.transition(conn, i, "queued", "approved")
    assert queue.transition(conn, i, "approved", "publishing", attempts=1) is True
    assert queue.transition(conn, i, "approved", "publishing") is False
    assert queue.get(conn, i).attempts == 1
    assert queue.transition(conn, i, ("publishing", "unknown"), "published", ig_media_id="m")
    assert queue.get(conn, i).ig_media_id == "m"


def test_recover_after_restart_publishing_na_unknown(conn):
    from hlidac_reels import queue
    a, b = _add(conn, 1), _add(conn, 2)
    queue.transition(conn, a, "queued", "publishing")
    assert queue.recover_after_restart(conn) == [a]
    assert queue.get(conn, a).status == "unknown"
    assert queue.get(conn, b).status == "queued"


def test_state_offset_ulozi_a_precte(conn):
    from hlidac_reels import queue
    assert queue.get_state(conn, "tg_offset") is None
    queue.set_state(conn, "tg_offset", "5")
    queue.set_state(conn, "tg_offset", "9")
    assert queue.get_state(conn, "tg_offset") == "9"


def test_counts_podle_stavu(conn):
    from hlidac_reels import queue
    a = _add(conn, 1)
    _add(conn, 2)
    queue.transition(conn, a, "queued", "rejected")
    queue.set_fields(conn, a, error="x")
    assert queue.counts(conn) == {"queued": 1, "rejected": 1}
    assert queue.get(conn, a).error == "x"
