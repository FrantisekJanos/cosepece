"""Fronta reelů: stavový automat nad tabulkou reel_queue."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, fields as dc_fields
from datetime import datetime

import psycopg

STATUSES = ("queued", "pending", "approved", "publishing", "published", "rejected", "failed", "unknown")


class DuplicateVideo(Exception):
    """Video se stejným video_sha256 už ve frontě je."""


@dataclass
class Reel:
    id: int
    source: str
    ct: int | None
    format: str | None
    video_path: str
    video_sha256: str
    caption: str
    status: str
    telegram_video_msg_id: int | None
    telegram_msg_id: int | None
    ig_container_id: str | None
    ig_media_id: str | None
    ig_permalink: str | None
    fb_video_id: str | None
    fb_permalink: str | None
    public_token: str | None
    error: str | None
    attempts: int
    created_at: datetime
    sent_at: datetime | None
    decided_at: datetime | None
    published_at: datetime | None
    # Threads: sloupce přidané ALTER TABLE na konec tabulky (SELECT je stejně vybírá jménem)
    threads_container_id: str | None
    threads_media_id: str | None
    threads_permalink: str | None


_COLS = ", ".join(f.name for f in dc_fields(Reel))
_ALLOWED = {f.name for f in dc_fields(Reel)} - {"id", "status"}


@contextmanager
def _tx(conn):
    """Zápis s commitem; při chybě DB rollback, aby spojení nezůstalo v přerušené transakci."""
    try:
        yield
        conn.commit()
    except psycopg.Error:
        conn.rollback()
        raise


def _check(fields: dict) -> None:
    # názvy sloupců se vkládají do SQL, proto jen ze seznamu polí Reel;
    # status se mění jen přes transition (podmíněný přechod)
    bad = set(fields) - _ALLOWED
    if bad:
        raise ValueError(f"neznámá pole: {sorted(bad)}")


def _row(cur) -> Reel | None:
    r = cur.fetchone()
    return Reel(*r) if r else None


def add(conn, *, video_path: str, video_sha256: str, caption: str,
        ct: int | None = None, format: str | None = None, source: str = "ukazka") -> int:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO reel_queue (source, ct, format, video_path, video_sha256, caption) "
                "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (source, ct, format, video_path, video_sha256, caption),
            )
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    except psycopg.errors.UniqueViolation as e:
        conn.rollback()
        raise DuplicateVideo(video_sha256) from e


def get(conn, id: int) -> Reel | None:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM reel_queue WHERE id=%s", (id,))
        r = _row(cur)
    conn.commit()
    return r


def next_queued(conn) -> Reel | None:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM reel_queue WHERE status='queued' ORDER BY id LIMIT 1")
        r = _row(cur)
    conn.commit()
    return r


def by_telegram_msg(conn, msg_id: int) -> Reel | None:
    """Položka, k níž patří zpráva v Telegramu (video nebo náhled s tlačítky)."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_COLS} FROM reel_queue WHERE telegram_video_msg_id=%s OR telegram_msg_id=%s "
            "ORDER BY id DESC LIMIT 1",
            (msg_id, msg_id),
        )
        r = _row(cur)
    conn.commit()
    return r


def by_status(conn, statuses: tuple[str, ...]) -> list[Reel]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM reel_queue WHERE status = ANY(%s) ORDER BY id", (list(statuses),))
        out = [Reel(*r) for r in cur.fetchall()]
    conn.commit()
    return out


def pending_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM reel_queue WHERE status='pending'")
        n = cur.fetchone()[0]
    conn.commit()
    return n


def counts(conn) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT status, count(*) FROM reel_queue GROUP BY status")
        out = {s: n for s, n in cur.fetchall()}
    conn.commit()
    return out


def transition(conn, id: int, from_status: str | tuple[str, ...], to_status: str, **fields) -> bool:
    """Atomický přechod: proběhne jen z očekávaného stavu, vrací, zda se změnil řádek."""
    _check(fields)
    if to_status not in STATUSES:
        raise ValueError(f"neznámý stav: {to_status}")
    froms = [from_status] if isinstance(from_status, str) else list(from_status)
    sets = ["status=%s", *(f"{k}=%s" for k in fields)]
    with _tx(conn), conn.cursor() as cur:
        cur.execute(
            f"UPDATE reel_queue SET {', '.join(sets)} WHERE id=%s AND status = ANY(%s) RETURNING id",
            (to_status, *fields.values(), id, froms),
        )
        done = cur.fetchone() is not None
    return done


def set_fields(conn, id: int, **fields) -> None:
    if not fields:
        return
    _check(fields)
    sets = ", ".join(f"{k}=%s" for k in fields)
    with _tx(conn), conn.cursor() as cur:
        cur.execute(f"UPDATE reel_queue SET {sets} WHERE id=%s", (*fields.values(), id))


def recover_after_restart(conn) -> list[int]:
    """Publikace přerušená restartem má nejistý výsledek: publishing -> unknown."""
    with _tx(conn), conn.cursor() as cur:
        cur.execute("UPDATE reel_queue SET status='unknown' WHERE status='publishing' RETURNING id")
        ids = sorted(r[0] for r in cur.fetchall())
    return ids


def get_state(conn, key: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM reel_state WHERE key=%s", (key,))
        r = cur.fetchone()
    conn.commit()
    return r[0] if r else None


def set_state(conn, key: str, value: str) -> None:
    with _tx(conn), conn.cursor() as cur:
        cur.execute(
            "INSERT INTO reel_state (key, value) VALUES (%s,%s) "
            "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
            (key, value),
        )
