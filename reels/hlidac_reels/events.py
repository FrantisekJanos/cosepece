"""Události úloh a workeru: tabulka reel_events (historie úlohy a fronta zpráv do Telegramu).

Zapisuje server (app/ReelJobs.php při přechodu úlohy) i bot. Tady je jen přístup k datům; text zprávy skládá
bot až při odeslání (messages.py). notified_at se zapíše až po úspěšném odeslání, takže výpadek Telegramu
zprávu neztratí. Klíč dedupe brání dvojí události (např. failed:<job_id> od serveru i z adopt_failed).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from .queue import _tx

# urgency: 0 = jen historie, 1 = poslat hned, 2 = do denního souhrnu
HISTORY, URGENT, SUMMARY = 0, 1, 2
# po výpadku delším než tohle se neodeslané události sloučí do jedné zprávy
OUTAGE = timedelta(hours=6)

# hm = čas události v pražském čase (H:MM) počítaný v SQL: obraz bota nespoléhá na tzdata v Pythonu
_COLS = ("id, at, kind, job_id, set_id, reel_id, detail, dedupe, urgency, notified_at, "
         "to_char(at AT TIME ZONE 'Europe/Prague', 'FMHH24:MI')")


@dataclass
class Event:
    id: int
    at: datetime
    kind: str
    job_id: int | None
    set_id: int | None
    reel_id: int | None
    detail: dict
    dedupe: str | None
    urgency: int
    notified_at: datetime | None
    hm: str


def _detail(raw: str | None) -> dict:
    # nečitelný detail (nebo JSON, který není objekt) = {}
    try:
        value = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _event(row) -> Event:
    r = list(row)
    r[6] = _detail(r[6])
    return Event(*r)


def _select(conn, where: str, params: tuple) -> list[Event]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM reel_events WHERE {where} ORDER BY id", params)
        out = [_event(r) for r in cur.fetchall()]
    conn.commit()
    return out


def add(conn, kind: str, *, job_id: int | None = None, set_id: int | None = None, reel_id: int | None = None,
        detail: dict | None = None, urgency: int = HISTORY, dedupe: str | None = None) -> int | None:
    """Zapíše událost a vrátí její id; None = událost se stejným klíčem dedupe už existuje."""
    with _tx(conn), conn.cursor() as cur:
        cur.execute(
            "INSERT INTO reel_events (kind, job_id, set_id, reel_id, detail, urgency, dedupe) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (dedupe) DO NOTHING RETURNING id",
            (kind, job_id, set_id, reel_id, json.dumps(detail or {}, ensure_ascii=False), urgency, dedupe),
        )
        r = cur.fetchone()
    return r[0] if r else None


def unsent(conn, urgency: int) -> list[Event]:
    """Neodeslané události dané naléhavosti v pořadí id."""
    return _select(conn, "notified_at IS NULL AND urgency = %s", (urgency,))


def mark_sent(conn, ids: list[int]) -> None:
    """Označí události jako odeslané; čas prvního odeslání se nepřepisuje."""
    if not ids:
        return
    with _tx(conn), conn.cursor() as cur:
        cur.execute("UPDATE reel_events SET notified_at = now() WHERE id = ANY(%s) AND notified_at IS NULL",
                    (list(ids),))


def for_job(conn, job_id: int) -> list[Event]:
    """Historie úlohy v pořadí id."""
    return _select(conn, "job_id = %s", (job_id,))


def count_since(conn, kind: str, since: datetime) -> int:
    """Počet událostí daného druhu od času since (včetně)."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM reel_events WHERE kind = %s AND at >= %s", (kind, since))
        n = cur.fetchone()[0]
    conn.commit()
    return int(n)


def adopt_failed(conn) -> int:
    """Selhání bez události (např. inbox._fail_job) převezme jako událost failed; vrací počet nových.

    Klíč failed:<id> je stejný, jaký zapisuje server (ReelJobs::failedEvent), takže se selhání ohlásí jen jednou.
    Vyžádaná úloha (manual, auto_pick) urgency 1 = zpráva hned, automatická (fresh) 2 = denní souhrn.
    Detail jako na serveru: error prázdný řetězec místo NULL, kind NULL místo prázdného řetězce.
    """
    with _tx(conn), conn.cursor() as cur:
        cur.execute(
            "INSERT INTO reel_events (kind, job_id, detail, urgency, dedupe) "
            "SELECT 'failed', id, "
            "jsonb_build_object('error', COALESCE(error, ''), 'kind', NULLIF(fail_kind, ''))::text, "
            "CASE WHEN source = 'fresh' THEN %s ELSE %s END, 'failed:' || id "
            "FROM reel_jobs WHERE status = 'failed' AND notified_at IS NULL ORDER BY id "
            "ON CONFLICT (dedupe) DO NOTHING",
            (SUMMARY, URGENT),
        )
        n = cur.rowcount
    return max(n, 0)
