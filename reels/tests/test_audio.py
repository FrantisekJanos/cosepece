"""Zvuková stopa Reelu: syntéza z událostí šablony a hlasitost hotového videa."""
from __future__ import annotations

import hashlib
import json
import subprocess
import wave

import numpy as np
import pytest

from conftest import load_fixture
from hlidac_reels.audio import SR, build_soundtrack, measure_loudness
from hlidac_reels.card import Card
from hlidac_reels.render import audio_cues, render_reel

FPS = 30
DURATION = 16.0
# ručně složená osa: dvě scény, počítadlo, citace a dvě razítka
CUES = (
    [{"t": round(0.1 + 0.08 * k, 3), "type": "tick", "gain": 0.8, "pos": k / 12} for k in range(12)]
    + [{"t": 1.15, "type": "hit", "gain": 0.8}]
    + [{"t": 3.0, "type": "whoosh", "gain": 1, "pos": 1}, {"t": 3.2, "type": "slide", "dur": 0.55}]
    + [{"t": round(3.6 + 0.06 * k, 3), "type": "type", "gain": 0.8, "pos": k / 14} for k in range(15)]
    + [{"t": 4.7, "type": "highlight", "dur": 0.9}]
    + [{"t": 8.0, "type": "whoosh", "gain": 1, "pos": 0}, {"t": 8.3, "type": "line"}, {"t": 8.5, "type": "strike"}]
    + [{"t": 9.94, "type": "stamp", "gain": 1}, {"t": 10.32, "type": "stamp", "gain": 1}]
    + [{"t": 13.0, "type": "whoosh", "gain": 1, "pos": 1}, {"t": 13.0, "type": "outro"}]
    + [{"t": round(13.4 + 0.04 * k, 3), "type": "drop", "gain": 0.7, "pos": k / 10} for k in range(11)]
    + [{"t": 5.0, "type": "neznamy-typ"}]
)


def _read(path) -> tuple[np.ndarray, int, int]:
    with wave.open(str(path), "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, w.getnchannels())
        return data / 32768.0, w.getframerate(), w.getnchannels()


@pytest.fixture(scope="module")
def soundtrack(tmp_path_factory):
    return build_soundtrack(CUES, DURATION, tmp_path_factory.mktemp("audio") / "a.wav", seed=0)


def test_soundtrack_format_and_length(soundtrack):
    data, rate, channels = _read(soundtrack)
    assert rate == 48000 == SR
    assert channels == 2
    assert abs(len(data) / rate - DURATION) <= 1 / FPS


def test_soundtrack_does_not_clip(soundtrack):
    data, _, _ = _read(soundtrack)
    assert np.max(np.abs(data)) <= 10 ** (-2.5 / 20)
    # konec doznívá do ticha, začátek nezačíná lupnutím
    assert np.max(np.abs(data[-480:])) < 0.01
    assert np.max(np.abs(data[:48])) < 0.01


def test_stamp_cue_is_louder_than_before(soundtrack):
    data, rate, _ = _read(soundtrack)

    def energy(t: float) -> float:
        return float(np.mean(data[int(t * rate):int((t + 0.05) * rate)] ** 2))

    stamps = [c["t"] for c in CUES if c["type"] == "stamp"]
    assert len(stamps) == 2
    for t in stamps:
        assert energy(t) >= 3 * energy(t - 0.2), t


def test_same_seed_gives_identical_output(tmp_path):
    def digest(name: str, seed: int) -> str:
        return hashlib.sha256(build_soundtrack(CUES, DURATION, tmp_path / name, seed=seed).read_bytes()).hexdigest()

    assert digest("a.wav", 7) == digest("b.wav", 7)
    assert digest("a.wav", 7) != digest("c.wav", 8)


def test_soundtrack_without_cues_still_has_bed(tmp_path):
    data, _, _ = _read(build_soundtrack([], 5.0, tmp_path / "bed.wav"))
    assert len(data) == 5 * SR
    assert np.sqrt(np.mean(data ** 2)) > 0.01


@pytest.mark.render
def test_template_cues_follow_timeline():
    card = Card.from_json(load_fixture("card-1.json"))
    assert audio_cues(card, "template") is None   # v1 zvukové události nemá
    cues = audio_cues(card, "template_v2")
    times = [c["t"] for c in cues]
    assert times == sorted(times)
    assert times[0] >= 0
    kinds = {c["type"] for c in cues}
    assert {"whoosh", "tick", "hit", "stamp", "type", "highlight", "drop", "outro"} <= kinds
    # jedno razítko (pn_other_law) a jedna stěrka před každou scénou kromě první
    assert sum(c["type"] == "stamp" for c in cues) == 1
    assert sum(c["type"] == "whoosh" for c in cues) >= 4


@pytest.mark.render
def test_rendered_mp4_loudness(tmp_path):
    card = Card.from_json(load_fixture("card-1.json"))
    result = render_reel(card, tmp_path / "reel.mp4", template="template_v2")
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(result.path)],
        capture_output=True, text=True, check=True,
    )
    streams = json.loads(out.stdout)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    audio = next(s for s in streams if s["codec_type"] == "audio")
    assert audio["codec_name"] == "aac"
    assert int(audio["sample_rate"]) == 48000
    assert audio["channels"] == 2
    assert abs(float(audio["duration"]) - float(video["duration"])) <= 2 / FPS
    assert abs(float(video["duration"]) - result.duration_s) <= 1 / FPS
    loud = measure_loudness(result.path)
    assert -16 <= loud["lufs"] <= -12
    assert loud["true_peak"] <= -1.0
