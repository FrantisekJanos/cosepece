"""Publikace jedné schválené položky fronty na IG, Threads a FB (v tomto pořadí).

Zásady: nic se nezveřejní bez schválení (run začíná jen ze stavu approved), publikace se sama
neopakuje, výsledek každé sítě se uloží hned, jak je známý (pozdější chyba ho neztratí),
a nejistý výsledek (timeout, chyba bez HTTP odpovědi) končí stavem unknown, který se před
dalším pokusem dohledá přes *_find_recent.

Threads je volitelné (threads=None = není nastavené): bez něj stačí k published IG a FB.
Chyba Threads nezastaví FB (každá síť je samostatný příspěvek); chyba IG zastaví vše jako dřív.
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
from .threads import ThreadsError, ThreadsNoToken, ThreadsTimeout

PREFIX_LEN = 80


class _NotSent(Exception):
    """Krok selhal dřív, než se na síť cokoli poslalo (výsledek jistý: failed)."""


class _CopyError(_NotSent):
    """Veřejnou kopii videa nešlo vytvořit (na síť se nic neposlalo)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _classify(err: MetaError | ThreadsError) -> str:
    """unknown = výsledek nejistý (timeout, bez HTTP odpovědi, kontejner už PUBLISHED); jinak failed."""
    if isinstance(err, ThreadsNoToken):
        return "failed"  # požadavek se vůbec neodeslal
    if isinstance(err, (MetaTimeout, ThreadsTimeout)) or err.status is None:
        return "unknown"
    return "failed"


def _outcome(e: Exception) -> tuple[str, str]:
    """(stav, text chyby) pro výjimku z kroku publikace."""
    if isinstance(e, (MetaError, ThreadsError)):
        return _classify(e), e.message
    if isinstance(e, _NotSent):
        return "failed", str(e)
    # neznámá chyba po volání API: výsledek nejistý; text výjimky se neukládá
    # (mohl by obsahovat tajemství), jen její typ
    return "unknown", f"neočekávaná chyba: {type(e).__name__}"


def _public_copy(conn, reel: Reel, media_dir: str, public_base: str, network: str) -> tuple[str, Path]:
    """Veřejná kopie videa pod novým tokenem; vrací (URL, soubor). Soubor maže volající ve finally."""
    token = secrets.token_urlsafe(24)
    public_dir = Path(media_dir) / "public"
    public_file = public_dir / f"{token}.mp4"
    try:
        public_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(reel.video_path, public_file)
    except OSError as e:
        public_file.unlink(missing_ok=True)
        raise _CopyError(f"kopie videa pro {network} selhala: {type(e).__name__}") from e
    try:
        queue.set_fields(conn, reel.id, public_token=token)
    except BaseException:
        public_file.unlink(missing_ok=True)  # volající soubor ještě nezná
        raise
    return f"{public_base.rstrip('/')}/{token}.mp4", public_file


def _publish_ig(conn, meta, reel: Reel, video_url: str) -> None:
    """IG: kontejner z veřejné URL → čekání → zveřejnění."""
    container_id = meta.ig_create(video_url, for_network(reel.caption, reel.ct, "ig"))
    queue.set_fields(conn, reel.id, ig_container_id=container_id)
    meta.ig_wait(container_id)
    media_id, permalink = meta.ig_publish(container_id)
    queue.set_fields(conn, reel.id, ig_media_id=media_id, ig_permalink=permalink)


def _threads_text(reel: Reel) -> str:
    text = for_network(reel.caption, reel.ct, "threads")
    if not text[:PREFIX_LEN].strip():
        raise _NotSent("prázdný text pro Threads")
    return text


def _publish_threads(conn, threads, reel: Reel, video_url: str) -> None:
    """Threads: kontejner ze stejné veřejné URL jako IG → čekání → zveřejnění."""
    container_id = threads.create_video(video_url, _threads_text(reel))
    queue.set_fields(conn, reel.id, threads_container_id=container_id)
    threads.wait(container_id)
    media_id, permalink = threads.publish(container_id)
    queue.set_fields(conn, reel.id, threads_media_id=media_id, threads_permalink=permalink)


def _publish_fb(conn, meta, reel: Reel) -> None:
    video_id, permalink = meta.fb_upload_reel(reel.video_path, for_network(reel.caption, reel.ct, "fb"))
    queue.set_fields(conn, reel.id, fb_video_id=video_id, fb_permalink=permalink)


def _finish(conn, reel_id: int, to_status: str, attempts: int, error: str) -> Reel:
    queue.transition(conn, reel_id, "publishing", to_status, error=error, attempts=attempts + 1)
    return queue.get(conn, reel_id)


def _done(reel: Reel, threads) -> bool:
    """Všechny zapnuté sítě mají id (Threads jen když je nastavené)."""
    return bool(reel.ig_media_id and reel.fb_video_id and (threads is None or reel.threads_media_id))


def run(conn, meta, reel_id: int, *, media_dir: str, public_base: str, threads=None) -> Reel:
    """Zveřejní schválenou položku; síť, která už id má, přeskočí. Jiný stav než approved = beze změny.

    Pořadí IG → Threads → FB. Veřejná kopie videa vznikne jednou pro IG i Threads a smaže se
    až po obou (i po chybě). Chyba IG zastaví další sítě; chyba Threads FB nezastaví.
    """
    if not queue.transition(conn, reel_id, "approved", "publishing"):
        return queue.get(conn, reel_id)
    reel = queue.get(conn, reel_id)
    need_ig = not reel.ig_media_id
    need_threads = threads is not None and not reel.threads_media_id
    errors: list[tuple[str, str]] = []
    try:
        public: tuple[str, Path] | None = None
        try:
            if need_ig:
                public = _public_copy(conn, reel, media_dir, public_base, "IG")
                _publish_ig(conn, meta, reel, public[0])
            if need_threads:
                try:
                    if public is None:
                        public = _public_copy(conn, reel, media_dir, public_base, "Threads")
                    _publish_threads(conn, threads, reel, public[0])
                except Exception as e:  # noqa: BLE001 – chyba Threads nezastaví FB
                    status, msg = _outcome(e)
                    errors.append((status, f"Threads: {msg}"))
        finally:
            if public is not None:
                public[1].unlink(missing_ok=True)
        if not reel.fb_video_id:
            _publish_fb(conn, meta, reel)
    except Exception as e:  # noqa: BLE001 – chyba IG nebo FB (klasifikace v _outcome)
        errors.append(_outcome(e))
    if errors:
        status = "unknown" if any(s == "unknown" for s, _ in errors) else "failed"
        return _finish(conn, reel_id, status, reel.attempts, "; ".join(m for _, m in errors))
    queue.transition(conn, reel_id, "publishing", "published", published_at=_now(), error=None)
    return queue.get(conn, reel_id)


def reconcile(conn, meta, reel_id: int, threads=None) -> Reel:
    """Dohledání chybějících sítí pro failed i unknown (viz _reconcile)."""
    return _reconcile(conn, meta, reel_id, threads)[0]


def _reconcile(conn, meta, reel_id: int, threads=None) -> tuple[Reel, bool]:
    """Před každým opakováním (failed i unknown) dohledá chybějící sítě na profilech.

    Vrací (položka, dohledání proběhlo). Všechny zapnuté sítě nalezené → published.
    Jinak unknown → failed (failed zůstane failed).
    Když dohledání samo selže, stav se nemění, uloží se jen chyba a nic se nepublikuje.
    """
    reel = queue.get(conn, reel_id)
    if reel is None or reel.status not in ("unknown", "failed"):
        return reel, False
    since = reel.sent_at or reel.created_at
    try:
        if not reel.ig_media_id:
            prefix = for_network(reel.caption, reel.ct, "ig")[:PREFIX_LEN]
            found = meta.ig_find_recent(prefix, since)
            if found:
                queue.set_fields(conn, reel_id, ig_media_id=found[0], ig_permalink=found[1])
        if threads is not None and not reel.threads_media_id:
            # prázdný prefix by sedl na jakýkoli příspěvek: _threads_text ho odmítne
            prefix = _threads_text(reel)[:PREFIX_LEN]
            found = threads.find_recent(prefix, since)
            if found:
                queue.set_fields(conn, reel_id, threads_media_id=found[0], threads_permalink=found[1])
        if not reel.fb_video_id:
            prefix = for_network(reel.caption, reel.ct, "fb")[:PREFIX_LEN]
            found = meta.fb_find_recent(prefix, since)
            if found:
                queue.set_fields(conn, reel_id, fb_video_id=found[0], fb_permalink=found[1])
    except (MetaError, ThreadsError) as e:
        queue.set_fields(conn, reel_id, error=f"dohledání selhalo: {e.message}")
        return queue.get(conn, reel_id), False
    except _NotSent as e:
        queue.set_fields(conn, reel_id, error=f"dohledání selhalo: {e}")
        return queue.get(conn, reel_id), False
    except Exception as e:  # noqa: BLE001 – text výjimky se neukládá (mohl by obsahovat tajemství)
        queue.set_fields(conn, reel_id, error=f"dohledání selhalo: {type(e).__name__}")
        return queue.get(conn, reel_id), False
    reel = queue.get(conn, reel_id)
    if _done(reel, threads):
        queue.transition(conn, reel_id, ("unknown", "failed"), "published", published_at=_now(), error=None)
    else:
        queue.transition(conn, reel_id, "unknown", "failed")
    return queue.get(conn, reel_id), True


def retry(conn, meta, reel_id: int, threads=None, **kw) -> Reel:
    """Zkusit znovu (failed nebo unknown): nejdřív dohledání, pak znovu run jen pro chybějící sítě.

    Když dohledání selže, run se nespustí (nesmí vzniknout duplicitní příspěvek).
    """
    reel, searched = _reconcile(conn, meta, reel_id, threads)
    if not searched or reel.status != "failed":
        return reel
    if not queue.transition(conn, reel_id, ("failed",), "approved"):
        return queue.get(conn, reel_id)
    return run(conn, meta, reel_id, threads=threads, **kw)
