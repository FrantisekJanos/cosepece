"""Stav Na X ve frontě reel_queue (PostgreSQL, marker pg)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from hlidac_reels import db, queue, xpost
from test_bot import conn, media_dir  # noqa: F401 – fixture

pg = pytest.mark.pg
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


def _reel(conn, media_dir, n=1, status="published", set_id=7, ct=221):  # noqa: F811
    video = media_dir / "videos" / f"x{n}.mp4"
    video.write_bytes(b"x")
    rid = queue.add(conn, video_path=str(video), video_sha256=f"xsha{n}", caption="Text.\n\nDruhý.", ct=ct,
                    format="v3", set_id=set_id, variant=2)
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_queue SET status=%s WHERE id=%s", (status, rid))
    conn.commit()
    return rid


def _x(conn, rid):  # noqa: F811
    with conn.cursor() as cur:
        cur.execute("SELECT x_status, x_requested_at, x_ready_at, x_msg_id FROM reel_queue WHERE id=%s", (rid,))
        r = cur.fetchone()
    conn.commit()
    return r


def _set(conn, rid, **f):  # noqa: F811
    with conn.cursor() as cur:
        for k, v in f.items():
            cur.execute(f"UPDATE reel_queue SET {k}=%s WHERE id=%s", (v, rid))
    conn.commit()


@pg
def test_migrace_dvakrat(conn):  # noqa: F811
    db.migrate(conn)
    db.migrate(conn)


@pg
def test_want_ze_zverejnene(conn, media_dir):  # noqa: F811
    rid = _reel(conn, media_dir)
    assert xpost.want(conn, rid, NOW) == "wanted"
    st, req, ready, msg = _x(conn, rid)
    assert (st, req, ready, msg) == ("wanted", NOW, None, None)
    # druhý klik nic nemění
    assert xpost.want(conn, rid, NOW) == "waiting"
    for prev in ("missing", "skipped"):
        _set(conn, rid, x_status=prev, x_msg_id=55)
        assert xpost.want(conn, rid, NOW) == "wanted"
        assert _x(conn, rid)[3] is None
    for prev in ("ready", "done"):
        _set(conn, rid, x_status=prev)
        assert xpost.want(conn, rid, NOW) == prev
        assert _x(conn, rid)[0] == prev


@pg
def test_want_jen_zverejnena_se_sadou(conn, media_dir):  # noqa: F811
    failed = _reel(conn, media_dir, 1, status="failed")
    solo = _reel(conn, media_dir, 2, set_id=None)
    assert xpost.want(conn, failed, NOW) == "gone"
    assert xpost.want(conn, solo, NOW) == "gone"
    assert xpost.want(conn, 9999, NOW) == "gone"
    assert _x(conn, failed)[0] is None


@pg
def test_finish(conn, media_dir):  # noqa: F811
    rid = _reel(conn, media_dir)
    assert not xpost.finish(conn, rid, "done")  # bez žádosti
    xpost.want(conn, rid, NOW)
    _set(conn, rid, x_status="ready")
    assert xpost.finish(conn, rid, "done")
    assert _x(conn, rid)[0] == "done"
    assert not xpost.finish(conn, rid, "skipped")  # pozdní Zrušit
    with pytest.raises(ValueError):
        xpost.finish(conn, rid, "ready")


@pg
def test_to_notify_a_mark(conn, media_dir):  # noqa: F811
    a = _reel(conn, media_dir, 1)
    b = _reel(conn, media_dir, 2, ct=None)
    c = _reel(conn, media_dir, 3)
    for rid in (a, b, c):
        xpost.want(conn, rid, NOW)
    _set(conn, a, x_status="ready")
    _set(conn, b, x_status="missing")
    items = xpost.to_notify(conn)
    assert [(i.id, i.ct, i.x_status) for i in items] == [(a, 221, "ready"), (b, None, "missing")]
    assert items[0].x_path is None
    assert items[0].caption == "Text.\n\nDruhý."
    assert xpost.mark_notified(conn, a, "ready", 501)
    assert not xpost.mark_notified(conn, a, "ready", 502)  # už odeslaná
    assert not xpost.mark_notified(conn, b, "ready", 503)  # stav se mezitím změnil
    assert [i.id for i in xpost.to_notify(conn)] == [b]


def test_jmeno_souboru_a_zpravy():
    assert xpost.file_name(12, 221) == "0012-tisk221.mp4"
    assert xpost.file_name(12345, None) == "12345.mp4"
    assert xpost.publish_buttons(12) == [("Na X", "x:12")]
    item = xpost.XItem(12, 221, "Krátký text.\n\nDruhý.", "ready")
    text, url = xpost.ready_message(item)
    assert text.startswith("Pro X: #0012 · tisk 221\nVideo: <code>0012-tisk221.mp4</code> (složka Pro X)\n\n")
    assert "Krátký text.\n\nCelý rozbor: https://cosepece.cz/tisk/221" in text
    assert url.startswith("https://x.com/intent/post?text=Kr%C3%A1tk%C3%BD")
    assert xpost.ready_buttons(12, url) == [("Otevřít X", url), ("Hotovo na X", "xdone:12"), ("Zrušit", "xskip:12")]
    # s cestou od workeru: celá cesta jako kód; text příspěvku escapovaný pro parse_mode HTML
    item = xpost.XItem(12, 221, "A < B & C.", "ready", r"C:\Users\uzivatel\Videos\Pro X\0012-tisk221.mp4")
    text, url = xpost.ready_message(item)
    assert text.startswith("Pro X: #0012 · tisk 221\nVideo: <code>C:\\Users\\uzivatel\\Videos\\Pro X\\0012-tisk221.mp4</code>\n"
                           "Složka se otevřela na PC, cestu zkopíruješ klepnutím.\n\nA &lt; B &amp; C.")
    assert "A%20%3C%20B%20%26%20C." in url or "A+%3C+B+%26+C." in url  # odkaz X nese text bez escapování HTML
    assert xpost.missing_text(12) == "Video #0012 na PC není (ručně nahrané video, nebo složka sady chybí)."
    assert xpost.missing_buttons(12) == [("Zkusit znovu", "x:12")]
