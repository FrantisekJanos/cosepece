"""Úklid stavu služby: klíče reel_state, staré reel_events a složky zrušených úloh v mezisložce.

Klíče reel_state nemají čas zápisu. Podle prefixu patří k položce fronty, sadě nebo úloze a smažou se, až jejich
vlastník neexistuje nebo je STATE_MAX_AGE v konečném stavu. Co ještě čeká (pending, held, failed, unknown, úloha
ve frontě), se nemaže nikdy. Klíče jiných prefixů (tg_offset, threads_token*, worker_*, hlidani_*, autopilot…) se
nečtou ani nemažou. Bot volá run() jednou za hodinu (Bot.maybe_fresh → Bot._cleanup).
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import psycopg

from . import events, jobs, queue
from .queue import _tx

log = logging.getLogger("hlidac_reels.uklid")

# klíč reel_state žije tak dlouho po konečném stavu vlastníka; stejná hodnota jako jobs.RETRY_MAX_AGE (Zkusit znovu
# starší úlohy už nejde, takže smazaný job_retried:<id> nepovolí druhé opakování)
STATE_MAX_AGE = timedelta(days=30)
EVENTS_MAX_AGE = timedelta(days=90)  # stáří odeslané nebo jen historické události reel_events
CANCELLED_DIR_AGE = timedelta(days=1)  # složka zrušené úlohy bez manifestu v mezisložce
# konečné stavy položky; failed a unknown čekají na Zkusit znovu, held na Zkusit znovu / Zahodit
FINAL_REEL = ("published", "rejected", "expired", "withdrawn")

# Prefixy z bot.py: bot importuje uklid, import bota by byl cyklus. Shodu hlídá test_prefixy_shodne_s_botem.
EDIT_MSG_PREFIX = "edit_msg:"  # edit_msg:<id zprávy> = id položky (hodnota)
JOB_STATUS_PREFIX = "job_status:"  # bot.JOB_STATUS_KEY
LANDSCAPE_FAILED_PREFIX = "landscape_failed:"  # bot.LANDSCAPE_FAILED_KEY bez id položky

# klíče položky (id položky = přípona), sady (přípona = set_id = reel_jobs.id) a úlohy (přípona = reel_jobs.id)
ITEM_PREFIXES = (LANDSCAPE_FAILED_PREFIX, queue.HELD_MSG_KEY, queue.PREVIEW_SENT_KEY)
SET_PREFIXES = (queue.PICKED_KEY, queue.REMINDED_KEY, queue.SET_WARNINGS_KEY)
JOB_PREFIXES = (JOB_STATUS_PREFIX, jobs.RETRIED_KEY)

_MAX_ID = 2**63 - 1  # bigint
# poslední aktivita položky
_REEL_AT = "coalesce({t}published_at, {t}decided_at, {t}sent_at, {t}created_at)"


def _as_id(text: str | None) -> int | None:
    """Id z textu klíče nebo hodnoty; None = vadné (prázdné, nečíselné, mimo bigint)."""
    if not text or not text.isascii() or not text.isdigit():
        return None
    n = int(text)
    return n if n <= _MAX_ID else None


def _state(conn, prefixes: tuple[str, ...]) -> list[tuple[str, str, str | None]]:
    """(klíč, prefix, hodnota) klíčů s danými prefixy; prefix se bere doslova (jako queue.state_keys)."""
    out = []
    with conn.cursor() as cur:
        for p in prefixes:
            cur.execute("SELECT key, value FROM reel_state WHERE left(key, %s) = %s ORDER BY key", (len(p), p))
            out += [(k, p, v) for k, v in cur.fetchall()]
    conn.commit()
    return out


def _live_items(cur, ids: set[int], cutoff: datetime) -> set[int]:
    """Položky, které existují a nejsou od cutoff v konečném stavu."""
    if not ids:
        return set()
    cur.execute(f"SELECT id FROM reel_queue WHERE id = ANY(%s::bigint[]) "
                f"AND (status <> ALL(%s::text[]) OR {_REEL_AT.format(t='')} >= %s)",
                (sorted(ids), list(FINAL_REEL), cutoff))
    return {r[0] for r in cur.fetchall()}


def _live_jobs(cur, ids: set[int], cutoff: datetime) -> set[int]:
    """Úlohy, které existují a jsou aktivní nebo skončily (založily se) po cutoff."""
    if not ids:
        return set()
    cur.execute("SELECT id FROM reel_jobs WHERE id = ANY(%s::bigint[]) "
                "AND (status = ANY(%s::text[]) OR coalesce(finished_at, created_at) >= %s)",
                (sorted(ids), list(jobs.ACTIVE), cutoff))
    return {r[0] for r in cur.fetchall()}


def _live_sets(cur, ids: set[int], cutoff: datetime) -> set[int]:
    """Sady s živou položkou (mimo konečný stav nebo s aktivitou po cutoff) nebo s živou úlohou."""
    if not ids:
        return set()
    cur.execute(f"SELECT s.id FROM unnest(%s::bigint[]) AS s(id) WHERE "
                f"EXISTS (SELECT 1 FROM reel_queue q WHERE q.set_id = s.id "
                f"AND (q.status <> ALL(%s::text[]) OR {_REEL_AT.format(t='q.')} >= %s)) "
                f"OR EXISTS (SELECT 1 FROM reel_jobs j WHERE j.id = s.id "
                f"AND (j.status = ANY(%s::text[]) OR coalesce(j.finished_at, j.created_at) >= %s))",
                (sorted(ids), list(FINAL_REEL), cutoff, list(jobs.ACTIVE), cutoff))
    return {r[0] for r in cur.fetchall()}


def prune_state(conn, now: datetime) -> list[str]:
    """Smaže klíče reel_state, jejichž položka, sada nebo úloha neexistuje nebo je 30 dní v konečném stavu.

    Vadný klíč (nečíselná přípona, u edit_msg: i hodnota) se smaže hned. Vrací smazané klíče.
    """
    cutoff = now - STATE_MAX_AGE
    owners: list[tuple[str, str, int | None]] = []  # (klíč, druh vlastníka, id); id None = vadný klíč
    for key, prefix, value in _state(conn, (EDIT_MSG_PREFIX, *ITEM_PREFIXES)):
        suffix = _as_id(key[len(prefix):])
        item = _as_id(value) if prefix == EDIT_MSG_PREFIX else suffix
        owners.append((key, "item", item if suffix is not None else None))
    owners += [(key, "set", _as_id(key[len(p):])) for key, p, _ in _state(conn, SET_PREFIXES)]
    owners += [(key, "job", _as_id(key[len(p):])) for key, p, _ in _state(conn, JOB_PREFIXES)]
    if not owners:
        return []

    def ids(kind: str) -> set[int]:
        return {i for _, k, i in owners if k == kind and i is not None}
    with conn.cursor() as cur:
        live = {"item": _live_items(cur, ids("item"), cutoff), "set": _live_sets(cur, ids("set"), cutoff),
                "job": _live_jobs(cur, ids("job"), cutoff)}
    conn.commit()
    gone = []
    for key, kind, oid in owners:
        if oid is None:
            log.warning("vadný klíč %s smazán", key)
            gone.append(key)
        elif oid not in live[kind]:
            gone.append(key)
    if gone:
        with _tx(conn), conn.cursor() as cur:
            cur.execute("DELETE FROM reel_state WHERE key = ANY(%s::text[])", (gone,))
    return gone


def prune_events(conn, now: datetime) -> int:
    """Smaže události starší než 90 dní, které už odešly nebo jsou jen historie; vrací počet.

    Neodeslaná naléhavá nebo souhrnná událost zůstane: bez ní by events.adopt_failed selhání ohlásil znovu.
    """
    with _tx(conn), conn.cursor() as cur:
        cur.execute("DELETE FROM reel_events WHERE at < %s AND (notified_at IS NOT NULL OR urgency = %s)",
                    (now - EVENTS_MAX_AGE, events.HISTORY))
        n = cur.rowcount
    return max(n, 0)


def prune_inbox(conn, inbox_dir: Path, now: datetime) -> list[int]:
    """Smaže složky zrušených úloh bez manifest.json (zrušeno před víc než 1 dnem); vrací id smazaných.

    Složky s manifestem převezme nebo smaže inbox.ingest_all, nečíselné složky a soubory se nečtou.
    """
    inbox_dir = Path(inbox_dir)
    if not inbox_dir.is_dir():
        return []
    candidates: dict[int, Path] = {}
    for folder in inbox_dir.iterdir():
        job_id = _as_id(folder.name)
        if job_id is None or str(job_id) != folder.name or folder.is_symlink() or not folder.is_dir():
            continue
        if (folder / "manifest.json").exists():
            continue
        candidates[job_id] = folder
    if not candidates:
        return []
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM reel_jobs WHERE id = ANY(%s::bigint[]) AND status = 'cancelled' "
                    "AND finished_at < %s ORDER BY id", (sorted(candidates), now - CANCELLED_DIR_AGE))
        stale = [r[0] for r in cur.fetchall()]
    conn.commit()
    removed = []
    for job_id in stale:
        try:
            shutil.rmtree(candidates[job_id])
        except OSError as e:
            # zkusí se znovu při dalším úklidu
            log.warning("složku zrušené úlohy %s nejde smazat: %s", job_id, e)
            continue
        removed.append(job_id)
    return removed


def run(conn, now: datetime, inbox_dir: Path | None) -> dict[str, int]:
    """Celý úklid; vrací počty smazaných klíčů, událostí a složek (inbox_dir None = bez mezisložky).

    Kroky běží každý zvlášť: pád jednoho se zaloguje (transakce se vrátí), počet zůstane 0 a další kroky běží.
    Ztráta spojení s DB jde dál, ať ji zachytí Bot._guard a smyčka (konec procesu, Docker ho spustí znovu).
    """
    steps = (
        ("state", "klíče stavu", lambda: len(prune_state(conn, now))),
        ("events", "události", lambda: prune_events(conn, now)),
        ("inbox", "složky zrušených úloh",
         lambda: len(prune_inbox(conn, inbox_dir, now)) if inbox_dir is not None else 0),
    )
    counts = {}
    for key, what, step in steps:
        try:
            counts[key] = step()
        except Exception as e:  # noqa: BLE001
            if isinstance(e, psycopg.OperationalError) or getattr(conn, "closed", False):
                raise
            log.warning("úklid (%s) selhal: %s: %s", what, type(e).__name__, e)
            counts[key] = 0
            try:
                conn.rollback()  # nepovedený příkaz nesmí nechat spojení v přerušené transakci
            except Exception:  # noqa: BLE001
                pass
    return counts
