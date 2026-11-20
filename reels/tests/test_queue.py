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


def test_add_se_sirkou(conn):
    from hlidac_reels import queue
    i = _add(conn, 1, video_landscape_path="/m/l.mp4", video_landscape_sha256="L")
    r = queue.get(conn, i)
    assert r.video_landscape_path == "/m/l.mp4"
    assert r.video_landscape_sha256 == "L"
    assert r.telegram_landscape_msg_id is None


def test_by_telegram_msg_najde_video_na_sirku(conn):
    from hlidac_reels import queue
    i = _add(conn, 1)
    queue.set_fields(conn, i, telegram_landscape_msg_id=77)
    assert queue.by_telegram_msg(conn, 77).id == i


def test_set_landscape_jen_queued_pending(conn):
    from hlidac_reels import queue
    i = _add(conn, 1)
    queue.set_fields(conn, i, telegram_landscape_msg_id=5)
    assert queue.set_landscape(conn, i, "/m/l.mp4", "L1") is True
    r = queue.get(conn, i)
    assert (r.video_landscape_path, r.video_landscape_sha256) == ("/m/l.mp4", "L1")
    assert r.telegram_landscape_msg_id is None
    assert queue.transition(conn, i, "queued", "pending")
    queue.set_fields(conn, i, telegram_landscape_msg_id=6)
    assert queue.set_landscape(conn, i, "/m/l2.mp4", "L2") is True
    assert queue.get(conn, i).telegram_landscape_msg_id is None
    assert queue.transition(conn, i, "pending", "published")
    assert queue.set_landscape(conn, i, "/m/l3.mp4", "L3") is False
    assert queue.get(conn, i).video_landscape_sha256 == "L2"


def test_sha_in_use(conn):
    from hlidac_reels import queue
    _add(conn, 1, video_landscape_path="/m/l.mp4", video_landscape_sha256="L")
    assert queue.sha_in_use(conn, "sha1") is True   # jako video na výšku
    assert queue.sha_in_use(conn, "L") is True      # jako video na šířku
    assert queue.sha_in_use(conn, "jine") is False


def test_approve_jen_kdyz_vlastnik_videl_sirku(conn):
    from datetime import datetime, timezone
    from hlidac_reels import queue
    now = datetime.now(timezone.utc)
    # bez šířky: běžné schválení z pending
    a = _add(conn, 1)
    assert queue.approve(conn, a, now) is False  # queued
    assert queue.transition(conn, a, "queued", "pending", telegram_msg_id=10)
    assert queue.approve(conn, a, now) is True
    assert queue.get(conn, a).status == "approved" and queue.get(conn, a).decided_at is not None
    # šířka doplněná, ještě neodeslaná
    b = _add(conn, 2)
    assert queue.transition(conn, b, "queued", "pending", telegram_msg_id=20)
    assert queue.set_landscape(conn, b, "/m/w.mp4", "W")
    assert queue.approve(conn, b, now) is False
    # odeslaná, ale náhled je starší než video
    queue.set_fields(conn, b, telegram_landscape_msg_id=21, telegram_landscape_sha256="W")
    assert queue.approve(conn, b, now) is False
    assert queue.get(conn, b).status == "pending"
    # nový náhled po videu, ale do Telegramu odešel jiný soubor (vyměněný během odesílání)
    queue.set_fields(conn, b, telegram_msg_id=22, telegram_landscape_sha256="X")
    assert queue.approve(conn, b, now) is False
    queue.set_fields(conn, b, telegram_landscape_sha256=None)
    assert queue.approve(conn, b, now) is False
    # odeslaná právě ta šířka, která je v řádku
    queue.set_fields(conn, b, telegram_landscape_sha256="W")
    assert queue.approve(conn, b, now) is True


def test_set_landscape_nuluje_odeslany_otisk(conn):
    from hlidac_reels import queue
    i = _add(conn, 1)
    queue.set_fields(conn, i, telegram_landscape_msg_id=5, telegram_landscape_sha256="L0")
    assert queue.set_landscape(conn, i, "/m/l.mp4", "L1")
    r = queue.get(conn, i)
    assert r.telegram_landscape_msg_id is None and r.telegram_landscape_sha256 is None


def test_set_landscape_sent_jen_pro_stejny_soubor(conn):
    from hlidac_reels import queue
    i = _add(conn, 1)
    assert queue.set_landscape(conn, i, "/m/l.mp4", "L1")
    assert queue.set_landscape_sent(conn, i, 7, "L0") is False
    assert queue.get(conn, i).telegram_landscape_msg_id is None
    assert queue.set_landscape_sent(conn, i, 7, "L1") is True
    r = queue.get(conn, i)
    assert (r.telegram_landscape_msg_id, r.telegram_landscape_sha256) == (7, "L1")
    # šířka bez otisku (NULL) se páruje s NULL
    j = _add(conn, 2)
    assert queue.set_landscape(conn, j, "/m/l2.mp4", None)
    assert queue.set_landscape_sent(conn, j, 8, None) is True


def test_delete_state(conn):
    from hlidac_reels import queue
    queue.set_state(conn, "k", "v")
    queue.delete_state(conn, "k")
    assert queue.get_state(conn, "k") is None
    queue.delete_state(conn, "k")  # neexistující klíč nevadí
