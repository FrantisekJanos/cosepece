"""CLI reels-enqueue: zkontroluje video, zkopíruje ho do médií a vloží do fronty."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from . import db, media, queue

CAPTION_LIMIT = 2200 - 80  # 80 znaků rezerva na konec popisku


def _fail(msg: str) -> int:
    print(f"chyba: {msg}", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="reels-enqueue", description="Vloží video do fronty ke schválení")
    ap.add_argument("--video", required=True)
    ap.add_argument("--caption-file", required=True)
    ap.add_argument("--ct", type=int)
    ap.add_argument("--format")
    args = ap.parse_args(argv)

    video = Path(args.video)
    cap_file = Path(args.caption_file)
    if not video.is_file():
        return _fail(f"video neexistuje: {video}")
    if not cap_file.is_file():
        return _fail(f"soubor s popiskem neexistuje: {cap_file}")
    caption = cap_file.read_text(encoding="utf-8").strip()
    if not caption:
        return _fail("popisek je prázdný")
    if len(caption) > CAPTION_LIMIT:
        return _fail(f"popisek má {len(caption)} znaků, limit je {CAPTION_LIMIT}")

    try:
        info = media.probe(video)
    except RuntimeError as e:
        return _fail(str(e))
    errs = media.validate(info)
    if errs:
        for e in errs:
            print(f"chyba: {e}", file=sys.stderr)
        return 1

    digest = media.sha256(video)
    target_dir = Path(os.environ.get("REELS_MEDIA_DIR", "/media")) / "videos"
    target = target_dir / f"{digest}.mp4"

    conn = db.connect()
    try:
        db.migrate(conn)
        # nejdřív vložit, aby duplicita nezanechala zbytečnou kopii; při selhání kopie řádek odstranit
        try:
            new_id = queue.add(conn, video_path=str(target), video_sha256=digest, caption=caption,
                               ct=args.ct, format=args.format)
        except queue.DuplicateVideo:
            return _fail("toto video už ve frontě je")
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copyfile(video, target)
        except OSError as e:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM reel_queue WHERE id=%s", (new_id,))
            conn.commit()
            return _fail(f"kopie videa selhala: {e}")
    finally:
        conn.close()
    print(new_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
