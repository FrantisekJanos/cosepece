"""CLI reels-threads-backfill: doplní na Threads položku, která už vyšla jen na FB a IG.

Jen pro stav published bez threads_media_id. Nejdřív dohledá, zda příspěvek na Threads už není
(pak jen uloží id), jinak zveřejní video jen na Threads přes dočasnou veřejnou kopii.
FB a IG se nedotkne a stav položky nemění. Token se bere z reel_state (bot ho prodlužuje),
jinak z THREADS_TOKEN; nikam se nevypisuje.
"""
from __future__ import annotations

import argparse
import os
import sys

from . import db, publish, queue
from .bot import threads_token
from .messages import NO_LINK
from .threads import Threads, ThreadsError

DEFAULT_PUBLIC_BASE = "https://cosepece.cz/reels-media"


class BackfillError(Exception):
    """Položku nelze doplnit (jiný stav, už je na Threads, chyba Threads)."""


def _fail(msg: str) -> int:
    print(f"chyba: {msg}", file=sys.stderr)
    return 1


def backfill(conn, threads, reel_id: int, *, media_dir: str, public_base: str) -> queue.Reel:
    """Doplní Threads k zveřejněné položce; vrací položku s threads_media_id."""
    reel = queue.get(conn, reel_id)
    if reel is None:
        raise BackfillError(f"položka #{reel_id} neexistuje")
    if reel.status != "published":
        raise BackfillError(f"#{reel_id} je ve stavu {reel.status}, doplnit jde jen stav published")
    if reel.threads_media_id:
        raise BackfillError(f"#{reel_id} už na Threads je: {reel.threads_permalink or NO_LINK}")
    since = reel.published_at or reel.sent_at or reel.created_at
    try:
        found = publish.find_threads(threads, reel, since)
        if found:
            queue.set_fields(conn, reel_id, threads_media_id=found[0], threads_permalink=found[1])
        else:
            publish.publish_threads_only(conn, threads, reel, media_dir=media_dir, public_base=public_base)
    except Exception as e:  # noqa: BLE001 – text chyby bez tajemství (ThreadsError je očištěná)
        raise BackfillError(f"Threads: {publish.error_text(e)}") from None
    return queue.get(conn, reel_id)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="reels-threads-backfill",
                                 description="Doplní na Threads položku zveřejněnou jen na FB a IG")
    ap.add_argument("--id", type=int, required=True, help="id položky ve frontě")
    args = ap.parse_args(argv)

    env = os.environ
    user_id = env.get("THREADS_USER_ID")
    if not user_id:
        return _fail("chybí THREADS_USER_ID (Threads není nastavené)")
    conn = db.connect()
    try:
        db.migrate(conn)
        token = threads_token(conn) or env.get("THREADS_TOKEN") or ""
        if not token:
            return _fail("chybí token Threads (reel_state ani THREADS_TOKEN)")
        threads = Threads(user_id, lambda: token, version=env.get("THREADS_GRAPH_VERSION") or "v1.0")
        try:
            reel = backfill(conn, threads, args.id, media_dir=env.get("REELS_MEDIA_DIR") or "/media",
                            public_base=env.get("REELS_PUBLIC_BASE") or DEFAULT_PUBLIC_BASE)
        except (BackfillError, ThreadsError) as e:
            return _fail(str(e).replace(token, "***"))
    finally:
        conn.close()
    print(f"#{reel.id} na Threads: {reel.threads_permalink or NO_LINK}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
