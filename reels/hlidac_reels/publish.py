"""Publikace jedné schválené položky fronty na IG a FB.

Zásady: nic se nezveřejní bez schválení (run začíná jen ze stavu approved), publikace se sama
neopakuje, výsledek každé sítě se uloží hned, jak je známý (pozdější chyba ho neztratí),
a nejistý výsledek (timeout, chyba bez HTTP odpovědi) končí stavem unknown, který se před
dalším pokusem dohledá přes *_find_recent.
"""
from __future__ import annotations

import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import queue
from .caption import for_network
from .meta import MetaError, MetaTimeout
from .queue import Reel

PREFIX_LEN = 80


class _CopyError(Exception):
    """Veřejnou kopii videa nešlo vytvořit (na síť se nic neposlalo)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _classify(err: MetaError) -> str:
    """unknown = výsledek nejistý (timeout, bez HTTP odpovědi, kontejner už PUBLISHED); jinak failed."""
    if isinstance(err, MetaTimeout) or err.status is None:
        return "unknown"
    return "failed"


def _publish_ig(conn, meta, reel: Reel, media_dir: str, public_base: str) -> None:
    """IG: veřejná kopie videa pod novým tokenem → kontejner → čekání → zveřejnění; kopie se vždy smaže."""
    token = secrets.token_urlsafe(24)
    public_dir = Path(media_dir) / "public"
    public_file = public_dir / f"{token}.mp4"
    try:
        try:
            public_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(reel.video_path, public_file)
        except OSError as e:
            raise _CopyError(f"kopie videa pro IG selhala: {type(e).__name__}") from e
        queue.set_fields(conn, reel.id, public_token=token)
        container_id = meta.ig_create(f"{public_base.rstrip('/')}/{token}.mp4",
                                      for_network(reel.caption, reel.ct, "ig"))
        queue.set_fields(conn, reel.id, ig_container_id=container_id)
        meta.ig_wait(container_id)
        media_id, permalink = meta.ig_publish(container_id)
        queue.set_fields(conn, reel.id, ig_media_id=media_id, ig_permalink=permalink)
    finally:
        public_file.unlink(missing_ok=True)


def _publish_fb(conn, meta, reel: Reel) -> None:
    video_id, permalink = meta.fb_upload_reel(reel.video_path, for_network(reel.caption, reel.ct, "fb"))
    queue.set_fields(conn, reel.id, fb_video_id=video_id, fb_permalink=permalink)


def _finish(conn, reel_id: int, to_status: str, attempts: int, error: str) -> Reel:
    queue.transition(conn, reel_id, "publishing", to_status, error=error, attempts=attempts + 1)
    return queue.get(conn, reel_id)


def run(conn, meta, reel_id: int, *, media_dir: str, public_base: str) -> Reel:
    """Zveřejní schválenou položku; síť, která už id má, přeskočí. Jiný stav než approved = beze změny."""
    if not queue.transition(conn, reel_id, "approved", "publishing"):
        return queue.get(conn, reel_id)
    reel = queue.get(conn, reel_id)
    try:
        if not reel.ig_media_id:
            _publish_ig(conn, meta, reel, media_dir, public_base)
        if not reel.fb_video_id:
            _publish_fb(conn, meta, reel)
    except MetaError as e:
        return _finish(conn, reel_id, _classify(e), reel.attempts, e.message)
    except _CopyError as e:
        return _finish(conn, reel_id, "failed", reel.attempts, str(e))
    except Exception as e:  # noqa: BLE001 – neznámá chyba po volání API: výsledek nejistý
        # text výjimky se neukládá (mohl by obsahovat tajemství), jen její typ
        return _finish(conn, reel_id, "unknown", reel.attempts, f"neočekávaná chyba: {type(e).__name__}")
    queue.transition(conn, reel_id, "publishing", "published", published_at=_now(), error=None)
    return queue.get(conn, reel_id)


def reconcile(conn, meta, reel_id: int) -> Reel:
    """Položka unknown: chybějící síť dohledá; obě nalezené → published, jinak failed.

    Když dohledání samo selže, položka zůstane unknown (s chybou) a dá se zkusit znovu.
    """
    reel = queue.get(conn, reel_id)
    if reel is None or reel.status != "unknown":
        return reel
    since = reel.sent_at or reel.created_at
    try:
        if not reel.ig_media_id:
            prefix = for_network(reel.caption, reel.ct, "ig")[:PREFIX_LEN]
            found = meta.ig_find_recent(prefix, since)
            if found:
                queue.set_fields(conn, reel_id, ig_media_id=found[0], ig_permalink=found[1])
        if not reel.fb_video_id:
            prefix = for_network(reel.caption, reel.ct, "fb")[:PREFIX_LEN]
            found = meta.fb_find_recent(prefix, since)
            if found:
                queue.set_fields(conn, reel_id, fb_video_id=found[0], fb_permalink=found[1])
    except MetaError as e:
        queue.set_fields(conn, reel_id, error=f"dohledání selhalo: {e.message}")
        return queue.get(conn, reel_id)
    reel = queue.get(conn, reel_id)
    if reel.ig_media_id and reel.fb_video_id:
        queue.transition(conn, reel_id, "unknown", "published", published_at=_now(), error=None)
    else:
        queue.transition(conn, reel_id, "unknown", "failed")
    return queue.get(conn, reel_id)


def retry(conn, meta, reel_id: int, **kw) -> Reel:
    """Zkusit znovu (failed nebo unknown): nejdřív dohledání, pak znovu run jen pro chybějící síť."""
    reel = reconcile(conn, meta, reel_id)
    if reel is None or reel.status != "failed":
        return reel
    if not queue.transition(conn, reel_id, ("failed",), "approved"):
        return queue.get(conn, reel_id)
    return run(conn, meta, reel_id, **kw)
