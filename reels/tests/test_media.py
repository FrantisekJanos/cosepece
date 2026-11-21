"""Testy kontroly videa (bez ffprobe, kromě volitelného testu probe)."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from hlidac_reels import media
from hlidac_reels.media import VideoInfo


def _info(**kw):
    base = dict(codec_v="h264", codec_a="aac", width=1080, height=1920, duration=30.0)
    base.update(kw)
    return VideoInfo(**base)


def test_validate_ok():
    assert media.validate(_info()) == []


def test_validate_na_sirku():
    errs = media.validate(_info(width=1920, height=1080))
    assert any("9:16" in e for e in errs)


def test_validate_kratke_a_dlouhe():
    assert any("délk" in e for e in media.validate(_info(duration=2)))
    assert any("délk" in e for e in media.validate(_info(duration=91)))


def test_validate_bez_zvuku():
    errs = media.validate(_info(codec_a=None))
    assert any("zvuk" in e for e in errs)


def test_validate_kodek():
    assert any("kodek" in e for e in media.validate(_info(codec_v="vp9")))


def test_sha256(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"abc")
    assert media.sha256(p) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


@pytest.mark.skipif(shutil.which("ffprobe") is None or shutil.which("ffmpeg") is None,
                    reason="chybí ffprobe nebo ffmpeg")
def test_probe_skutecne_video(tmp_path):
    p = tmp_path / "t.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=360x640:d=4:r=10",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "4", "-c:v", "libx264",
         "-c:a", "aac", "-shortest", str(p)],
        check=True,
    )
    info = media.probe(p)
    assert (info.codec_v, info.codec_a, info.width, info.height) == ("h264", "aac", 360, 640)
    assert 3.5 < info.duration < 4.5
    assert media.validate(info) == []


def test_validate_velikost():
    assert media.validate(_info(size=50 * 1024 * 1024)) == []
    errs = media.validate(_info(size=50 * 1024 * 1024 + 1))
    assert any("50 MB" in e for e in errs)


def _fake_ffprobe(monkeypatch, fmt, stream):
    import json as _json
    out = _json.dumps({"format": fmt, "streams": [{"codec_type": "video", "codec_name": "h264", **stream}]})
    monkeypatch.setattr(media.subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout=out, stderr=""))


@pytest.mark.parametrize("fmt,stream,needle", [
    ({"duration": "N/A"}, {"width": 1080, "height": 1920}, "délk"),
    ({}, {"width": 1080, "height": 1920}, "délk"),
    ({"duration": "30.0"}, {"height": 1920}, "rozměr"),
    ({"duration": "30.0"}, {"width": "N/A", "height": 1920}, "rozměr"),
])
def test_probe_chybejici_udaje(monkeypatch, tmp_path, fmt, stream, needle):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"x")
    _fake_ffprobe(monkeypatch, fmt, stream)
    with pytest.raises(RuntimeError) as ei:
        media.probe(p)
    assert needle in str(ei.value)


def test_probe_vrati_velikost(monkeypatch, tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"x" * 1234)
    _fake_ffprobe(monkeypatch, {"duration": "30.0"}, {"width": 1080, "height": 1920})
    assert media.probe(p).size == 1234


def test_validate_sirka_ok():
    assert media.validate(VideoInfo("h264", "aac", 1920, 1080, 30.0, 1000), orientation="landscape") == []


def test_validate_sirka_odmitne_vysku():
    errs = media.validate(VideoInfo("h264", "aac", 1080, 1920, 30.0, 1000), orientation="landscape")
    assert len(errs) == 1 and "16:9" in errs[0]


def test_validate_vychozi_je_vyska():
    errs = media.validate(VideoInfo("h264", "aac", 1920, 1080, 30.0, 1000))
    assert any("9:16" in e for e in errs)
