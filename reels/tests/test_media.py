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
