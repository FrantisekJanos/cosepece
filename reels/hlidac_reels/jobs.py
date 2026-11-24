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
    autopilot: bool = False  # zadáno se zapnutým autopilotem: videa se zveřejní bez schválení
    # protokol úloh (F3): stejné pořadí jako sloupce v schema_reels.sql
    not_before: datetime | None = None  # úloha se nevydá dřív (odklad)
    wait_reason: str | None = None  # proč čeká (např. titulek AI)
    lease_until: datetime | None = None  # vydání platí do; None = claimed_at + 60 min
    stage: str | None = None  # fáze: model, kontrola, oprava, render, upload
    stage_at: datetime | None = None
    fail_kind: str | None = None  # content, timeout, env, infra, data, lease
    env_retries: int = 0  # návraty do fronty po chybě env/infra
    detail: str | None = None  # JSON výsledku po variantách (vzniklé varianty, vynechané s důvody); zapisuje PHP result
    status_msg_id: int | None = None  # stavová zpráva v Telegramu


_COLS = ", ".join(f.name for f in dc_fields(Job))


def add(conn, *, source: str, ct: int | None = None, format_hint: str | None = None,
        reason_keys: list[str] | None = None, focus_pn: list[int] | None = None,
        autopilot: bool = False) -> int:
    keys = json.dumps(reason_keys, ensure_ascii=False) if reason_keys is not None else None
    # seřazená množina, aby unikátní index poznal stejné zadání v jiném pořadí; prázdná = celý tisk
    pns = sorted({int(n) for n in focus_pn}) if focus_pn else None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO reel_jobs (source, ct, format_hint, reason_keys, focus_pn, autopilot) "
                "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (source, ct, format_hint, keys, pns, bool(autopilot)),
            )
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    except psycopg.errors.UniqueViolation as e:
        conn.rollback()
        raise ActiveJobExists(ct) from e


def autopilot_off(conn) -> int:
    """Vypnutý autopilot: aktivním úlohám (queued, claimed) smaže příznak, jejich sady půjdou ke schválení.

    Bez toho by úloha zadaná se zapnutým autopilotem a převzatá až po jeho opětovném zapnutí
    (PC mezitím offline) prošla bez schválení. Vrací počet dotčených úloh.
    """
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET autopilot=false WHERE autopilot AND status IN ('queued','claimed')")
        n = cur.rowcount
    conn.commit()
    return n


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


def focus_pn(conn, job_id: int) -> list[int] | None:
    """PN, o kterých je video úlohy (None = celý tisk nebo úloha neexistuje)."""
    with conn.cursor() as cur:
        cur.execute("SELECT focus_pn FROM reel_jobs WHERE id=%s", (job_id,))
        r = cur.fetchone()
    conn.commit()
    return list(r[0]) if r and r[0] else None
