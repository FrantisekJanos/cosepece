"""Testy reels-enqueue: kontrola vstupu a vložení do fronty."""
from __future__ import annotations

import os

import pytest

from hlidac_reels import enqueue, media
from hlidac_reels.media import VideoInfo

OK = VideoInfo("h264", "aac", 1080, 1920, 30.0)


def _files(tmp_path, caption):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"fake-video-bytes")
    c = tmp_path / "c.txt"
    c.write_text(caption, encoding="utf-8")
    return v, c


def test_enqueue_odmitne_dlouhy_popisek(tmp_path, monkeypatch, capsys):
    v, c = _files(tmp_path, "x" * 2150)
    monkeypatch.setattr(media, "probe", lambda p: OK)
    monkeypatch.setenv("REELS_MEDIA_DIR", str(tmp_path / "media"))

    def boom(*a, **k):
        raise AssertionError("do DB se nesmí sahat")
    monkeypatch.setattr(enqueue.db, "connect", boom)
    rc = enqueue.main(["--video", str(v), "--caption-file", str(c)])
    assert rc == 1
    assert "popisek" in capsys.readouterr().err
    assert not (tmp_path / "media").exists()


def test_enqueue_odmitne_spatne_video(tmp_path, monkeypatch, capsys):
    v, c = _files(tmp_path, "ok")
    monkeypatch.setattr(media, "probe", lambda p: VideoInfo("h264", "aac", 1920, 1080, 30.0))
    monkeypatch.setenv("REELS_MEDIA_DIR", str(tmp_path / "media"))
    assert enqueue.main(["--video", str(v), "--caption-file", str(c)]) == 1
    assert "9:16" in capsys.readouterr().err


@pytest.mark.pg
def test_enqueue_zkopiruje_a_vlozi(tmp_path, monkeypatch, capsys):
    if os.environ.get("REELS_TEST_DB") != "1":
        pytest.skip("chybí REELS_TEST_DB=1 (test maže tabulky reel_queue a reel_state)")
    from hlidac_reels import db, queue
    c0 = db.connect()
    db.migrate(c0)
    with c0.cursor() as cur:
        cur.execute("TRUNCATE reel_queue RESTART IDENTITY; TRUNCATE reel_state")
    c0.commit()

    v, c = _files(tmp_path, "Popisek")
    monkeypatch.setattr(media, "probe", lambda p: OK)
    monkeypatch.setenv("REELS_MEDIA_DIR", str(tmp_path / "media"))
    rc = enqueue.main(["--video", str(v), "--caption-file", str(c), "--ct", "311", "--format", "pn"])
    assert rc == 0
    new_id = int(capsys.readouterr().out.strip())
    r = queue.get(c0, new_id)
    assert (r.ct, r.format, r.caption, r.status) == (311, "pn", "Popisek", "queued")
    target = tmp_path / "media" / "videos" / f"{r.video_sha256}.mp4"
    assert target.read_bytes() == b"fake-video-bytes"
    assert r.video_path == str(target)
    # duplicita: kód 1, ve frontě zůstává jeden řádek
    assert enqueue.main(["--video", str(v), "--caption-file", str(c)]) == 1
    assert "už" in capsys.readouterr().err
    assert sum(queue.counts(c0).values()) == 1
    c0.close()
