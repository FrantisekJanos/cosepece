"""Render karty do MP4: HTML šablona v headless Chromiu snímek po snímku, pak ffmpeg (H.264 + AAC).

Šablona s window.audioCues() dostane syntetizovanou zvukovou stopu (audio.py), ostatní tichou.
Orientace: "portrait" 1080×1920 (Reels, výchozí) nebo "landscape" 1920×1080 (X.com; šablony v2 a v3).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .audio import build_soundtrack, loudnorm_filter
from .card import Card

TEMPLATE = Path(__file__).parent / "template" / "reel.html"
WIDTH, HEIGHT = 1080, 1920
Orientation = Literal["portrait", "landscape"]
SIZES: dict[str, tuple[int, int]] = {"portrait": (WIDTH, HEIGHT), "landscape": (HEIGHT, WIDTH)}
# bezpečný okraj dokola na šířku (px); na výšku platí zóny Instagramu (150 nahoře, 340 dole, 60 po stranách)
LANDSCAPE_SAFE = 60
# razítka jen s důkazem v klíči důvodu skóre (spec: Upřesnění po recenzi)
STAMPS: dict[str, str] = {"deadline_cut": "ZKRÁTILI", "pn_other_law": "PŘÍLEPEK", "p90_passed": "§ 90/2"}


@dataclass
class RenderResult:
    path: Path
    duration_s: float
    frames: int


def stamps_for(card: Card) -> list[str]:
    out: list[str] = []
    for reason in card.reasons:
        stamp = STAMPS.get(reason.get("key", ""))
        if stamp and stamp not in out:
            out.append(stamp)
    return out


def _template_path(template: str) -> Path:
    """Cesta k reel.html šablony: název složky vedle tohoto souboru (template, template_v2)."""
    path = Path(__file__).parent / template / "reel.html"
    if not path.is_file():
        raise FileNotFoundError(f"šablona {template!r} neexistuje: {path}")
    return path


@contextmanager
def _page(card: Card, template: str = "template", orientation: Orientation = "portrait"):
    """Otevře šablonu s načtenou a vměstnanou kartou; viewport i rozvržení podle orientace."""
    from playwright.sync_api import sync_playwright

    if orientation not in SIZES:
        raise ValueError(f"neznámá orientace {orientation!r} (portrait, landscape)")
    if orientation == "landscape" and not (_template_path(template).parent / "landscape.css").is_file():
        raise ValueError(f"šablona {template!r} nemá rozvržení na šířku")
    width, height = SIZES[orientation]

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-gpu", "--font-render-hinting=none"])
        try:
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            page.goto(_template_path(template).as_uri())
            page.evaluate("document.fonts.ready.then(() => true)")
            if orientation == "landscape":
                # šířka sloupců závisí na sazbě: všechny řezy písma se načtou dřív, než fit() začne měřit
                page.evaluate("Promise.all([...document.fonts].map(f => f.load())).then(() => true)")
            page.evaluate("([c, o]) => { window.loadCard(c, o); window.fit(); }", [card.to_dict(), orientation])
            yield page
        finally:
            browser.close()


def measure_blocks(card: Card, template: str = "template", orientation: Orientation = "portrait") -> dict:
    """Obdélníky bloků titulek/perex/citace po vměstnání (pro test bezpečné zóny).

    Šablony v2 a v3 vracejí i obálku každé scény (klíč "scene:<id>"); na šířku musí ležet LANDSCAPE_SAFE px od okrajů.
    """
    with _page(card, template, orientation) as page:
        return page.evaluate("window.measureBlocks()")


def scene_ids(card: Card, template: str = "template_v2", orientation: Orientation = "portrait") -> list[str]:
    """Pořadí scén, které šablona pro kartu skutečně zařadí (volitelné scény jen s daty)."""
    with _page(card, template, orientation) as page:
        return page.evaluate("window.sceneIds()")


def audio_cues(card: Card, template: str = "template", orientation: Orientation = "portrait") -> list[dict] | None:
    """Zvukové události šablony pro kartu; None, když je šablona neumí (v1)."""
    with _page(card, template, orientation) as page:
        return page.evaluate(CUES_JS)


CUES_JS = "typeof window.audioCues === 'function' ? window.audioCues() : null"


def render_reel(card: Card, out_path: Path, *, fps: int = 30, template: str = "template", seed: int = 0,
                orientation: Orientation = "portrait") -> RenderResult:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frames_dir = Path(tempfile.mkdtemp(prefix="reel-"))
    try:
        with _page(card, template, orientation) as page:
            duration = float(page.evaluate("window.duration()"))
            cues = page.evaluate(CUES_JS)
            frames = int(round(duration * fps))
            for i in range(frames):
                page.evaluate("t => window.seek(t)", i / fps)
                page.screenshot(path=str(frames_dir / f"{i:05d}.png"), type="png")
        video_in = ["-framerate", str(fps), "-i", str(frames_dir / "%05d.png")]
        if cues is None:
            # šablona bez zvukových událostí: tichá stopa
            audio_in = ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
            audio_out = ["-c:a", "aac", "-b:a", "96k"]
        else:
            wav = build_soundtrack(cues, frames / fps, frames_dir / "soundtrack.wav", seed=seed)
            audio_in = ["-i", str(wav)]
            audio_out = ["-af", loudnorm_filter(wav), "-ar", "48000", "-c:a", "aac", "-b:a", "192k"]
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", *video_in, *audio_in,
                "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "20",
                *audio_out, "-movflags", "+faststart", str(out_path),
            ],
            check=True,
        )
        return RenderResult(path=out_path, duration_s=duration, frames=frames)
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Vyrenderuje karty (JSON) do MP4.")
    ap.add_argument("cards", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--template", default="template", help="složka šablony (template, template_v2, template_v3)")
    ap.add_argument("--orientation", choices=sorted(SIZES), default="portrait",
                    help="portrait 1080×1920 (výchozí), landscape 1920×1080 pro X.com (soubor s příponou -sirka)")
    args = ap.parse_args()
    suffix = "-sirka" if args.orientation == "landscape" else ""
    for path in args.cards:
        card = Card.from_json(json.loads(path.read_text(encoding="utf-8")))
        # šablona v3: víc formátů k jednomu tisku, formát je součástí názvu souboru
        fmt = card.extras.get("format")
        name = f"reel-{card.ct}-{fmt}{suffix}.mp4" if fmt else f"reel-{card.ct}{suffix}.mp4"
        res = render_reel(card, args.out / name, template=args.template, orientation=args.orientation)
        print(f"{res.path} {res.duration_s:.0f} s, {res.frames} snímků")


if __name__ == "__main__":
    main()
