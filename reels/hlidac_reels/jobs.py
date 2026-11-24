"""Fronta zadání pro generátor reelů: tabulka reel_jobs."""
from __future__ import annotations

import json
from dataclasses import dataclass, fields as dc_fields
from datetime import datetime

import psycopg

from .queue import _tx

# výdejní pořadí: ruční, pak automatický výběr, nakonec čerstvé nálezy; uvnitř podle stáří
_ORDER = "ORDER BY CASE source WHEN 'manual' THEN 0 WHEN 'auto_pick' THEN 1 ELSE 2 END, created_at, id"


class ActiveJobExists(Exception):
    """Pro tento tisk (a stejnou množinu PN) už existuje aktivní úloha (queued nebo claimed)."""


@dataclass
class Job:
    id: int
    source: str
    ct: int | None
    format_hint: str | None
    reason_keys: str | None
    status: str
    claimed_at: datetime | None
    claim_token: str | None
    attempts: int
    error: str | None
    set_id: int | None
    created_at: datetime
    finished_at: datetime | None
    notified_at: datetime | None
    focus_pn: list[int] | None = None  # video jen o těchto PN (seřazené); None = celý tisk


_COLS = ", ".join(f.name for f in dc_fields(Job))


def add(conn, *, source: str, ct: int | None = None, format_hint: str | None = None,
        reason_keys: list[str] | None = None, focus_pn: list[int] | None = None) -> int:
    keys = json.dumps(reason_keys, ensure_ascii=False) if reason_keys is not None else None
    # seřazená množina, aby unikátní index poznal stejné zadání v jiném pořadí; prázdná = celý tisk
    pns = sorted({int(n) for n in focus_pn}) if focus_pn else None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO reel_jobs (source, ct, format_hint, reason_keys, focus_pn) VALUES (%s,%s,%s,%s,%s) "
                "RETURNING id",
                (source, ct, format_hint, keys, pns),
            )
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    except psycopg.errors.UniqueViolation as e:
        conn.rollback()
        raise ActiveJobExists(ct) from e


def pending(conn) -> list[Job]:
    """Aktivní úlohy (queued + claimed) v pořadí výdeje."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM reel_jobs WHERE status IN ('queued','claimed') {_ORDER}")
        out = [Job(*r) for r in cur.fetchall()]
    conn.commit()
    return out


def position(conn, job_id: int) -> int:
    """Pořadí úlohy mezi aktivními (1 = další na řadě); 0, když není aktivní."""
    for n, j in enumerate(pending(conn), start=1):
        if j.id == job_id:
            return n
    return 0


def failed_unnotified(conn) -> list[Job]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM reel_jobs WHERE status='failed' AND notified_at IS NULL ORDER BY id")
        out = [Job(*r) for r in cur.fetchall()]
    conn.commit()
    return out


def mark_notified(conn, job_id: int) -> None:
    with _tx(conn), conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET notified_at=now() WHERE id=%s", (job_id,))
