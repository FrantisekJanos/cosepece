"""Kontrola videa před vložením do fronty: ffprobe, pravidla Reels, otisk souboru."""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

MIN_DURATION = 3.0
MAX_DURATION = 90.0
ASPECT_PORTRAIT = 9 / 16
ASPECT_LANDSCAPE = 16 / 9
ASPECT = ASPECT_PORTRAIT  # zpětná kompatibilita
ASPECT_TOLERANCE = 0.01  # 1 %
MAX_SIZE = 50 * 1024 * 1024  # limit nahrání videa botem do Telegramu


@dataclass
class VideoInfo:
    codec_v: str | None
    codec_a: str | None
    width: int
    height: int
    duration: float
    size: int = 0  # bajty; 0 = neznámá


def probe(path) -> VideoInfo:
    """Přečte parametry videa přes ffprobe; při chybě vyvolá RuntimeError."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
            capture_output=True, text=True, check=True,
        )
    except FileNotFoundError as e:
        raise RuntimeError("ffprobe není nainstalovaný") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffprobe selhal: {e.stderr.strip()}") from e
    data = json.loads(r.stdout)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None:
        raise RuntimeError("soubor neobsahuje video stopu")
    try:
        width, height = int(v["width"]), int(v["height"])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("ffprobe nevrátil rozměry videa") from None
    try:
        dur = float(data.get("format", {}).get("duration") or v.get("duration"))
    except (TypeError, ValueError):
        raise RuntimeError("ffprobe nevrátil délku videa") from None
    return VideoInfo(v.get("codec_name"), a.get("codec_name") if a else None,
                     width, height, dur, Path(path).stat().st_size)


def validate(info: VideoInfo, orientation: str = "portrait") -> list[str]:
    """Vrací české chyby; prázdný seznam = video je v pořádku.

    orientation: "portrait" (9:16, výchozí) nebo "landscape" (16:9)."""
    landscape = orientation == "landscape"
    target = ASPECT_LANDSCAPE if landscape else ASPECT_PORTRAIT
    label = "16:9" if landscape else "9:16"
    errs: list[str] = []
    if info.codec_v != "h264":
        errs.append(f"video kodek musí být h264, je {info.codec_v}")
    if not info.codec_a:
        errs.append("video nemá zvuk")
    elif info.codec_a != "aac":
        errs.append(f"zvukový kodek musí být aac, je {info.codec_a}")
    if info.height <= 0 or abs(info.width / info.height - target) > target * ASPECT_TOLERANCE:
        errs.append(f"poměr stran musí být {label}, je {info.width}×{info.height}")
    if not MIN_DURATION <= info.duration <= MAX_DURATION:
        errs.append(f"délka musí být {MIN_DURATION:.0f}–{MAX_DURATION:.0f} s, je {info.duration:.1f} s")
    if info.size > MAX_SIZE:
        errs.append(f"video má {info.size / 1024 / 1024:.1f} MB, limit je {MAX_SIZE // 1024 // 1024} MB")
    return errs


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(Path(path), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
