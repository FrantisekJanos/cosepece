"""Fronta zadání pro generátor reelů: tabulka reel_jobs."""
from __future__ import annotations

import json
from dataclasses import dataclass, fields as dc_fields
from datetime import datetime, timedelta

import psycopg

from .queue import _tx

# výdejní pořadí: ruční, pak automatický výběr, nakonec čerstvé nálezy; uvnitř podle stáří
_ORDER = "ORDER BY CASE source WHEN 'manual' THEN 0 WHEN 'auto_pick' THEN 1 ELSE 2 END, created_at, id"
# vydání bez lease_until (starý worker) propadá po claimed_at + 60 min (ReelJobs::CLAIM_TIMEOUT_MIN)
CLAIM_TIMEOUT = timedelta(minutes=60)
# PC bez heartbeatu (reel_state worker_seen_at) déle než tohle neodpovídá (hlídání, stav PC v /fronta)
WORKER_STALE = timedelta(minutes=30)
ACTIVE = ("queued", "claimed")  # úloha ve frontě nebo u workeru; ostatní stavy jsou konečné
RETRYABLE = ("failed", "cancelled", "expired")  # konečné stavy, ze kterých jde Zkusit znovu


# reel_state job_retried:<id> = id úlohy, která úlohu <id> opakuje (Zkusit znovu jde u úlohy jen jednou)
RETRIED_KEY = "job_retried:"


class ActiveJobExists(Exception):
    """Pro tento tisk (a stejnou množinu PN) už existuje aktivní úloha (queued nebo claimed)."""


class AlreadyRetried(Exception):
    """Úloha už jednou opakovaná (Zkusit znovu); new_id = úloha, která ji opakuje (None = nečitelná hodnota)."""

    def __init__(self, new_id: int | None):
        super().__init__(new_id)
        self.new_id = new_id


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
    try:
        with conn.cursor() as cur:
            new_id = _insert(cur, source=source, ct=ct, format_hint=format_hint, reason_keys=reason_keys,
                             focus_pn=focus_pn, autopilot=autopilot)
        conn.commit()
        return new_id
    except psycopg.errors.UniqueViolation as e:
        conn.rollback()
        raise ActiveJobExists(ct) from e


def _insert(cur, *, source: str, ct: int | None, format_hint: str | None, reason_keys: list[str] | None,
            focus_pn: list[int] | None, autopilot: bool) -> int:
    """INSERT úlohy bez commitu (add, retry); vrací její id."""
    keys = json.dumps(reason_keys, ensure_ascii=False) if reason_keys is not None else None
    # seřazená množina, aby unikátní index poznal stejné zadání v jiném pořadí; prázdná = celý tisk
    pns = sorted({int(n) for n in focus_pn}) if focus_pn else None
    cur.execute(
        "INSERT INTO reel_jobs (source, ct, format_hint, reason_keys, focus_pn, autopilot) "
        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (source, ct, format_hint, keys, pns, bool(autopilot)),
    )
    return cur.fetchone()[0]


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


def cancel(conn, job_id: int) -> bool:
    """Zruší aktivní úlohu (queued, claimed); False = úloha neexistuje nebo už je v konečném stavu.

    Jediné místo, kde bot mění stav úlohy: jeden podmíněný UPDATE. Vydání a token se smažou, takže server
    workeru vrátí na progress, video i result 409 a výsledek zrušené úlohy nepřevezme.
    """
    with _tx(conn), conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status = 'cancelled', finished_at = now(), claim_token = NULL, "
                    "lease_until = NULL WHERE id = %s AND status IN ('queued','claimed')", (job_id,))
        changed = cur.rowcount > 0
    return changed


def retry(conn, job_id: int) -> int:
    """Zkusit znovu: nová úloha (nové id) se stejným tiskem, formátem a PN; vrací její id.

    Jen z konečného stavu failed, cancelled, expired, jinak ValueError (i když úloha neexistuje). Nová úloha je
    ruční (auto_pick bez tisku zůstane auto_pick) a vždy bez autopilota: opakované zadání jde ke schválení.
    Každá úloha jde opakovat jen jednou: klíč reel_state job_retried:<id> (= id nové úlohy) se vloží v téže
    transakci jako nová úloha; když už je, AlreadyRetried a nic se nezaloží (dvojí stisk, tlačítko, které se
    nepodařilo odebrat, auto_pick bez tisku, který unikátní index nehlídá). Stejné zadání, které už je aktivní,
    = ActiveJobExists; transakce se vrátí i s klíčem, takže po skončení aktivního zadání opakovat jde.
    """
    job = get(conn, job_id)
    if job is None or job.status not in RETRYABLE:
        raise ValueError(f"úlohu #{job_id} nejde opakovat ({job.status if job else 'neexistuje'})")
    source = "auto_pick" if job.source == "auto_pick" and job.ct is None else "manual"
    key = f"{RETRIED_KEY}{job_id}"
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO reel_state (key, value) VALUES (%s, '') ON CONFLICT (key) DO NOTHING "
                        "RETURNING key", (key,))
            if cur.fetchone() is None:
                cur.execute("SELECT value FROM reel_state WHERE key = %s", (key,))
                row = cur.fetchone()
                conn.rollback()
                value = row[0] if row else None
                raise AlreadyRetried(int(value) if value and value.isdigit() else None)
            new_id = _insert(cur, source=source, ct=job.ct, format_hint=job.format_hint, reason_keys=None,
                             focus_pn=job.focus_pn, autopilot=False)
            cur.execute("UPDATE reel_state SET value = %s WHERE key = %s", (str(new_id), key))
        conn.commit()
        return new_id
    except psycopg.errors.UniqueViolation as e:
        conn.rollback()
        raise ActiveJobExists(job.ct) from e
    except psycopg.Error:
        conn.rollback()
        raise


def set_status_msg(conn, job_id: int, msg_id: int | None) -> None:
    """Id stavové zprávy úlohy v Telegramu (bot ji přepisuje podle událostí)."""
    with _tx(conn), conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status_msg_id = %s WHERE id = %s", (msg_id, job_id))


def with_status_msg(conn) -> list[Job]:
    """Úlohy se stavovou zprávou k přepisu: bez autopilota, aktivní nebo skončené před méně než dnem
    (konečná událost se vykreslí i po výpadku bota)."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM reel_jobs WHERE status_msg_id IS NOT NULL AND NOT autopilot "
                    "AND (status IN ('queued','claimed') OR finished_at > now() - interval '1 day') ORDER BY id")
        out = [Job(*r) for r in cur.fetchall()]
    conn.commit()
    return out


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


def get(conn, job_id: int) -> Job | None:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM reel_jobs WHERE id=%s", (job_id,))
        r = cur.fetchone()
    conn.commit()
    return Job(*r) if r else None


def silent(conn, now: datetime) -> list[Job]:
    """Vydané úlohy bez známky života: lease_until v minulosti, bez lease_until vydané před víc než CLAIM_TIMEOUT.

    Úklid propadlých vydání dělá server až při dalším claim; když je PC vypnuté, úloha zůstane claimed.
    """
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM reel_jobs WHERE status='claimed' AND (lease_until < %(now)s "
                    "OR (lease_until IS NULL AND claimed_at < %(old)s)) ORDER BY id",
                    {"now": now, "old": now - CLAIM_TIMEOUT})
        out = [Job(*r) for r in cur.fetchall()]
    conn.commit()
    return out


def lease_expired(job: Job, now: datetime) -> bool:
    """Vydání propadlo (stejné pravidlo jako silent): lease_until v minulosti, bez lease_until vydané před víc
    než CLAIM_TIMEOUT. Úloha, která není claimed, nepropadá."""
    if job.status != "claimed":
        return False
    if job.lease_until is not None:
        return job.lease_until < now
    return job.claimed_at is not None and job.claimed_at < now - CLAIM_TIMEOUT


def skipped(conn, set_id: int) -> list[dict]:
    """Nevzniklé varianty sady [{n, format, errors}] z reel_jobs.detail úlohy (id úlohy = set_id).

    Bez úlohy, bez detail, s vadným JSON nebo bez pole skipped = []; položky, které nejsou objekt, se vynechají.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT detail FROM reel_jobs WHERE id=%s", (set_id,))
        r = cur.fetchone()
    conn.commit()
    try:
        data = json.loads(r[0]) if r and r[0] else None
    except ValueError:
        return []
    items = data.get("skipped") if isinstance(data, dict) else None
    return [s for s in items if isinstance(s, dict)] if isinstance(items, list) else []


def hm(conn, ts: datetime) -> str:
    """Čas H:MM v Europe/Prague přes PostgreSQL (obraz bota nepotřebuje tzdata)."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_char(%s::timestamptz AT TIME ZONE 'Europe/Prague', 'FMHH24:MI')", (ts,))
        text = cur.fetchone()[0]
    conn.commit()
    return text


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
