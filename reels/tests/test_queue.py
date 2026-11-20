"""Testy fronty reel_queue (PostgreSQL, marker pg)."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.pg


@pytest.fixture
def conn():
    # TRUNCATE nesmí zasáhnout produkční frontu: jen s výslovným souhlasem
    if os.environ.get("REELS_TEST_DB") != "1":
        pytest.skip("chybí REELS_TEST_DB=1 (testy mažou tabulky reel_queue a reel_state)")
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


def test_status_a_neznama_pole_vyhodi_ValueError(conn):
    from hlidac_reels import queue
    i = _add(conn, 1)
    with pytest.raises(ValueError):
        queue.set_fields(conn, i, status="published")
    with pytest.raises(ValueError):
        queue.transition(conn, i, "queued", "pending", status="published")
    with pytest.raises(ValueError):
        queue.set_fields(conn, i, nesmysl="x")
    assert queue.get(conn, i).status == "queued"


def test_po_chybe_db_spojeni_dal_funguje(conn):
    import psycopg
    from hlidac_reels import queue
    a, b = _add(conn, 1), _add(conn, 2)
    queue.set_fields(conn, a, public_token="t")
    with pytest.raises(psycopg.errors.UniqueViolation):
        queue.set_fields(conn, b, public_token="t")
    assert queue.get(conn, b).status == "queued"
    with pytest.raises(psycopg.errors.UniqueViolation):
        queue.transition(conn, b, "queued", "pending", public_token="t")
    assert queue.transition(conn, b, "queued", "pending")


def test_add_se_sadou(conn):
    from hlidac_reels import queue
    i = _add(conn, 1, set_id=7, variant=2, template="template_v3")
    r = queue.get(conn, i)
    assert (r.set_id, r.variant, r.template) == (7, 2, "template_v3")


def test_set_items_podle_varianty(conn):
    from hlidac_reels import queue
    _add(conn, 1, set_id=9, variant=3)
    _add(conn, 2, set_id=9, variant=1)
    _add(conn, 3, set_id=10, variant=1)
    assert [r.variant for r in queue.set_items(conn, 9)] == [1, 3]


def test_migrace_dvakrat(conn):
    from hlidac_reels import db, queue
    db.migrate(conn)
    db.migrate(conn)
    i = _add(conn, 1)
    assert queue.transition(conn, i, "queued", "withdrawn")
    assert queue.transition(conn, i, "withdrawn", "expired")
