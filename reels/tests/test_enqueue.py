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


class _FakeConn:
    def close(self):
        pass


def _fake_db(monkeypatch):
    monkeypatch.setattr(enqueue.db, "connect", lambda: _FakeConn())
    monkeypatch.setattr(enqueue.db, "migrate", lambda c: None)


def test_soubor_existuje_pri_volani_queue_add(tmp_path, monkeypatch):
    v, c = _files(tmp_path, "ok")
    monkeypatch.setattr(media, "probe", lambda p: OK)
    monkeypatch.setenv("REELS_MEDIA_DIR", str(tmp_path / "media"))
    _fake_db(monkeypatch)
    seen = {}

    def fake_add(conn, *, video_path, **kw):
        seen["exists"] = os.path.exists(video_path)
        seen["tmp"] = os.path.exists(video_path[:-4] + ".tmp")
        return 7
    monkeypatch.setattr(enqueue.queue, "add", fake_add)
    assert enqueue.main(["--video", str(v), "--caption-file", str(c)]) == 0
    assert seen == {"exists": True, "tmp": False}


def test_selhani_kopie_nezanecha_radek_ani_soubor(tmp_path, monkeypatch, capsys):
    v, c = _files(tmp_path, "ok")
    monkeypatch.setattr(media, "probe", lambda p: OK)
    media_dir = tmp_path / "media"
    monkeypatch.setenv("REELS_MEDIA_DIR", str(media_dir))
    _fake_db(monkeypatch)
    calls = []
    monkeypatch.setattr(enqueue.queue, "add", lambda *a, **k: calls.append(1) or 1)

    def bad_copy(src, dst):
        open(dst, "wb").write(b"pul")  # poloviční soubor
        raise OSError("disk plný")
    monkeypatch.setattr(enqueue.shutil, "copyfile", bad_copy)
    assert enqueue.main(["--video", str(v), "--caption-file", str(c)]) == 1
    assert "kopie" in capsys.readouterr().err
    assert calls == []
    assert list((media_dir / "videos").iterdir()) == []


def test_duplicita_nesmaze_existujici_soubor(tmp_path, monkeypatch, capsys):
    v, c = _files(tmp_path, "ok")
    monkeypatch.setattr(media, "probe", lambda p: OK)
    media_dir = tmp_path / "media"
    monkeypatch.setenv("REELS_MEDIA_DIR", str(media_dir))
    _fake_db(monkeypatch)
    digest = media.sha256(v)
    target = media_dir / "videos" / f"{digest}.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fake-video-bytes")

    def dup(*a, **k):
        raise enqueue.queue.DuplicateVideo(digest)
    monkeypatch.setattr(enqueue.queue, "add", dup)
    assert enqueue.main(["--video", str(v), "--caption-file", str(c)]) == 1
    assert target.read_bytes() == b"fake-video-bytes"


def test_selhani_vlozeni_smaze_nove_vytvoreny_soubor(tmp_path, monkeypatch):
    v, c = _files(tmp_path, "ok")
    monkeypatch.setattr(media, "probe", lambda p: OK)
    media_dir = tmp_path / "media"
    monkeypatch.setenv("REELS_MEDIA_DIR", str(media_dir))
    _fake_db(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("DB spadla")
    monkeypatch.setattr(enqueue.queue, "add", boom)
    with pytest.raises(RuntimeError):
        enqueue.main(["--video", str(v), "--caption-file", str(c)])
    assert list((media_dir / "videos").iterdir()) == []


# --- video na šířku ---

WIDE = VideoInfo("h264", "aac", 1920, 1080, 30.0)


def _wide_file(tmp_path, data=b"fake-wide-bytes"):
    w = tmp_path / "w.mp4"
    w.write_bytes(data)
    return w


def _probe_by_size(monkeypatch):
    # výška: 1080x1920, šířka: 1920x1080 (podle obsahu souboru)
    monkeypatch.setattr(media, "probe", lambda p: WIDE if b"wide" in p.read_bytes() else OK)


def test_enqueue_se_sirkou_vlozi_obe(tmp_path, monkeypatch):
    v, c = _files(tmp_path, "ok")
    w = _wide_file(tmp_path)
    _probe_by_size(monkeypatch)
    monkeypatch.setenv("REELS_MEDIA_DIR", str(tmp_path / "media"))
    _fake_db(monkeypatch)
    monkeypatch.setattr(enqueue.queue, "sha_in_use", lambda conn, sha: False)
    seen = {}

    def fake_add(conn, *, video_path, video_landscape_path=None, video_landscape_sha256=None, **kw):
        seen["path"] = video_landscape_path
        seen["sha"] = video_landscape_sha256
        return 3
    monkeypatch.setattr(enqueue.queue, "add", fake_add)
    assert enqueue.main(["--video", str(v), "--caption-file", str(c), "--video-landscape", str(w)]) == 0
    assert seen["sha"] == media.sha256(w)
    assert seen["path"].endswith(f"{seen['sha']}.mp4")
    assert os.path.exists(seen["path"])


def test_enqueue_sirka_spatny_pomer(tmp_path, monkeypatch, capsys):
    v, c = _files(tmp_path, "ok")
    w = _wide_file(tmp_path)
    monkeypatch.setattr(media, "probe", lambda p: OK)  # obě 1080x1920
    media_dir = tmp_path / "media"
    monkeypatch.setenv("REELS_MEDIA_DIR", str(media_dir))
    _fake_db(monkeypatch)
    calls = []
    monkeypatch.setattr(enqueue.queue, "add", lambda *a, **k: calls.append(1) or 1)
    assert enqueue.main(["--video", str(v), "--caption-file", str(c), "--video-landscape", str(w)]) == 1
    assert "16:9" in capsys.readouterr().err
    assert calls == []
    assert not media_dir.exists()


def test_enqueue_sirka_stejna_jako_vyska(tmp_path, monkeypatch, capsys):
    v, c = _files(tmp_path, "ok")
    monkeypatch.setattr(media, "probe", lambda p: OK)
    monkeypatch.setattr(media, "validate", lambda info, orientation="portrait": [])
    media_dir = tmp_path / "media"
    monkeypatch.setenv("REELS_MEDIA_DIR", str(media_dir))
    _fake_db(monkeypatch)
    calls = []
    monkeypatch.setattr(enqueue.queue, "add", lambda *a, **k: calls.append(1) or 1)
    assert enqueue.main(["--video", str(v), "--caption-file", str(c), "--video-landscape", str(v)]) == 1
    assert "video na šířku je stejný soubor jako na výšku" in capsys.readouterr().err
    assert calls == []
    assert not media_dir.exists()


def test_enqueue_sirka_uz_ve_fronte(tmp_path, monkeypatch, capsys):
    v, c = _files(tmp_path, "ok")
    w = _wide_file(tmp_path)
    _probe_by_size(monkeypatch)
    media_dir = tmp_path / "media"
    monkeypatch.setenv("REELS_MEDIA_DIR", str(media_dir))
    _fake_db(monkeypatch)
    monkeypatch.setattr(enqueue.queue, "sha_in_use", lambda conn, sha: True)
    calls = []
    monkeypatch.setattr(enqueue.queue, "add", lambda *a, **k: calls.append(1) or 1)
    assert enqueue.main(["--video", str(v), "--caption-file", str(c), "--video-landscape", str(w)]) == 1
    assert "toto video na šířku už ve frontě je" in capsys.readouterr().err
    assert calls == []
    assert list((media_dir / "videos").iterdir()) == []


def test_add_landscape_jiny_stav(tmp_path, monkeypatch, capsys):
    w = _wide_file(tmp_path)
    monkeypatch.setattr(media, "probe", lambda p: WIDE)
    media_dir = tmp_path / "media"
    monkeypatch.setenv("REELS_MEDIA_DIR", str(media_dir))
    _fake_db(monkeypatch)
    monkeypatch.setattr(enqueue.queue, "sha_in_use", lambda conn, sha: False)
    monkeypatch.setattr(enqueue.queue, "set_landscape", lambda *a, **k: False)
    assert enqueue.add_landscape_main(["--id", "5", "--video", str(w)]) == 1
    assert "#5 není ve stavu queued ani pending" in capsys.readouterr().err
    assert list((media_dir / "videos").iterdir()) == []


def test_add_landscape_uz_ve_fronte(tmp_path, monkeypatch, capsys):
    w = _wide_file(tmp_path)
    monkeypatch.setattr(media, "probe", lambda p: WIDE)
    media_dir = tmp_path / "media"
    monkeypatch.setenv("REELS_MEDIA_DIR", str(media_dir))
    _fake_db(monkeypatch)
    monkeypatch.setattr(enqueue.queue, "sha_in_use", lambda conn, sha: True)
    monkeypatch.setattr(enqueue.queue, "set_landscape", lambda *a, **k: pytest.fail("nesmí se volat"))
    assert enqueue.add_landscape_main(["--id", "5", "--video", str(w)]) == 1
    assert "toto video na šířku už ve frontě je" in capsys.readouterr().err
    assert list((media_dir / "videos").iterdir()) == []


def test_add_landscape_spatny_pomer(tmp_path, monkeypatch, capsys):
    w = _wide_file(tmp_path)
    monkeypatch.setattr(media, "probe", lambda p: OK)
    monkeypatch.setenv("REELS_MEDIA_DIR", str(tmp_path / "media"))
    assert enqueue.add_landscape_main(["--id", "5", "--video", str(w)]) == 1
    assert "16:9" in capsys.readouterr().err


def test_add_landscape_ok(tmp_path, monkeypatch, capsys):
    w = _wide_file(tmp_path)
    monkeypatch.setattr(media, "probe", lambda p: WIDE)
    monkeypatch.setenv("REELS_MEDIA_DIR", str(tmp_path / "media"))
    _fake_db(monkeypatch)
    monkeypatch.setattr(enqueue.queue, "sha_in_use", lambda conn, sha: False)
    monkeypatch.setattr(enqueue.queue, "set_landscape", lambda *a, **k: True)
    assert enqueue.add_landscape_main(["--id", "5", "--video", str(w)]) == 0
    assert capsys.readouterr().out.strip() == "#5: video na šířku uloženo"
