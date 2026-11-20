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


def _check_video(path: Path, orientation: str) -> bool:
    """Zkontroluje video (ffprobe + pravidla); chyby vypíše na stderr."""
    try:
        info = media.probe(path)
    except RuntimeError as e:
        _fail(str(e))
        return False
    errs = media.validate(info, orientation)
    for e in errs:
        print(f"chyba: {e}", file=sys.stderr)
    return not errs


def _store(video: Path, media_dir: Path) -> tuple[Path, str, bool]:
    """Zkopíruje video do <media_dir>/videos/<sha256>.mp4 (přes .tmp + os.replace).

    Vrací cílový soubor, otisk a zda soubor vznikl nově. Při OSError dočasný soubor uklidí.
    Soubor se kopíruje dřív než řádek do fronty: bot tak nikdy nevidí queued řádek bez hotového souboru."""
    digest = media.sha256(video)
    target_dir = media_dir / "videos"
    target = target_dir / f"{digest}.mp4"
    tmp = target.with_suffix(".tmp")
    created = False
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        if not target.exists():  # soubor pod konečným názvem je vždy úplný
            shutil.copyfile(video, tmp)
            os.replace(tmp, target)
            created = True
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return target, digest, created


def _cleanup(target: Path, created: bool) -> None:
    if created:
        target.unlink(missing_ok=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="reels-enqueue", description="Vloží video do fronty ke schválení")
    ap.add_argument("--video", required=True)
    ap.add_argument("--caption-file", required=True)
    ap.add_argument("--ct", type=int)
    ap.add_argument("--format")
    ap.add_argument("--video-landscape", help="volitelné video 16:9 (pro Threads)")
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

    if not _check_video(video, "portrait"):
        return 1
    landscape = None
    if args.video_landscape:
        landscape = Path(args.video_landscape)
        if not landscape.is_file():
            return _fail(f"video na šířku neexistuje: {landscape}")
        if not _check_video(landscape, "landscape"):
            return 1
        if media.sha256(landscape) == media.sha256(video):
            return _fail("video na šířku je stejný soubor jako na výšku")

    media_dir = Path(os.environ.get("REELS_MEDIA_DIR", "/media"))
    try:
        target, digest, created = _store(video, media_dir)
    except OSError as e:
        return _fail(f"kopie videa selhala: {e}")

    land_target = land_digest = None
    land_created = False
    if landscape:
        try:
            land_target, land_digest, land_created = _store(landscape, media_dir)
        except OSError as e:
            _cleanup(target, created)
            return _fail(f"kopie videa na šířku selhala: {e}")

    def cleanup():
        _cleanup(target, created)
        if land_target:
            _cleanup(land_target, land_created)

    try:
        conn = db.connect()
        try:
            db.migrate(conn)
            if land_digest and queue.sha_in_use(conn, land_digest):
                cleanup()
                return _fail("toto video na šířku už ve frontě je")
            new_id = queue.add(conn, video_path=str(target), video_sha256=digest, caption=caption,
                               ct=args.ct, format=args.format,
                               video_landscape_path=str(land_target) if land_target else None,
                               video_landscape_sha256=land_digest)
        finally:
            conn.close()
    except queue.DuplicateVideo:
        # existující soubor patří řádku, který už ve frontě je; nemazat (smaže se jen nově vzniklý)
        cleanup()
        return _fail("toto video už ve frontě je")
    except BaseException:
        cleanup()
        raise
    print(new_id)
    return 0


def add_landscape_main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="reels-add-landscape",
                                 description="Doplní nebo vymění video 16:9 u položky ve frontě")
    ap.add_argument("--id", type=int, required=True)
    ap.add_argument("--video", required=True)
    args = ap.parse_args(argv)

    video = Path(args.video)
    if not video.is_file():
        return _fail(f"video neexistuje: {video}")
    if not _check_video(video, "landscape"):
        return 1
    try:
        target, digest, created = _store(video, Path(os.environ.get("REELS_MEDIA_DIR", "/media")))
    except OSError as e:
        return _fail(f"kopie videa selhala: {e}")
    try:
        conn = db.connect()
        try:
            db.migrate(conn)
            if queue.sha_in_use(conn, digest):
                _cleanup(target, created)
                return _fail("toto video na šířku už ve frontě je")
            if not queue.set_landscape(conn, args.id, str(target), digest):
                _cleanup(target, created)
                return _fail(f"#{args.id} není ve stavu queued ani pending")
        finally:
            conn.close()
    except BaseException:
        _cleanup(target, created)
        raise
    print(f"#{args.id}: video na šířku uloženo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
