"""Čerstvé nálezy: úlohy fresh ze změn ve snímku top_findings a vypršení či stažení starých sad.

Snímek zapisuje PHP synchronizace (app/ReelsSnapshot.php) do reel_state pod klíčem top_findings:
{"at": ISO 8601 s posunem pásma, "items": {"<ct>": ["klíč důvodu", …]}}. Bot volá detect a retire každou hodinu.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import jobs, queue

SNAPSHOT_KEY = "top_findings"
BASELINE_KEY = "fresh_baseline"
# snímek starší než tohle znamená, že synchronizace stojí: nic se nezakládá ani nestahuje
SNAPSHOT_MAX_AGE = timedelta(hours=3)
# sada fresh/auto_pick bez výběru starší než tohle vyprší
SET_MAX_AGE = timedelta(days=7)


def _snapshot(conn, now: datetime) -> dict[str, list[str]] | None:
    """Položky snímku, nebo None, když snímek chybí, je poškozený nebo starší než 3 h."""
    raw = queue.get_state(conn, SNAPSHOT_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        at = datetime.fromisoformat(data["at"])
        items = data["items"]
    except (ValueError, KeyError, TypeError):
        return None
    if at.tzinfo is None or not isinstance(items, dict) or now - at > SNAPSHOT_MAX_AGE:
        return None
    return {str(ct): [str(k) for k in keys] for ct, keys in items.items() if isinstance(keys, list)}


def detect(conn, now: datetime) -> list[int]:
    """Založí úlohy fresh pro nové tisky v nálezech a tisky s novým klíčem důvodu; vrací id úloh.

    Baseline (reel_state fresh_baseline) drží všechny dosud viděné klíče každého tisku. První běh jen uloží
    baseline. Tisk, který ze snímku vypadl, v baseline zůstává: návrat se stejnými klíči úlohu nezaloží.
    """
    items = _snapshot(conn, now)
    if items is None:
        return []
    raw = queue.get_state(conn, BASELINE_KEY)
    if raw is None:
        queue.set_state(conn, BASELINE_KEY, json.dumps(items, ensure_ascii=False))
        return []
    baseline: dict[str, list[str]] = json.loads(raw)

    created: list[int] = []
    changed = False
    for ct, keys in items.items():
        known = baseline.get(ct)
        new_keys = [k for k in keys if known is None or k not in known]
        if known is not None and not new_keys:
            continue
        try:
            created.append(jobs.add(conn, source="fresh", ct=int(ct), reason_keys=keys))
        except jobs.ActiveJobExists:
            pass  # aktivní úloha pro tisk už je, změnu pokryje
        baseline[ct] = (known or []) + new_keys
        changed = True
    if changed:
        queue.set_state(conn, BASELINE_KEY, json.dumps(baseline, ensure_ascii=False))
    return created


def retire(conn, now: datetime) -> list[tuple[int, str]]:
    """Sady fresh/auto_pick bez výběru: starší než 7 dní → expired, tisk mimo nálezy → withdrawn.

    Mění se všechny položky sady ve stavu queued/pending. Bez čerstvého snímku se nic nestahuje (stojící
    synchronizace nesmí stáhnout všechno). Vrací (set_id, nový stav) jen pro sady, které se změnily.
    """
    items = _snapshot(conn, now)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT set_id, min(created_at), max(ct) FROM reel_queue "
            "WHERE set_id IS NOT NULL AND status IN ('queued','pending') AND source <> 'manual' "
            "GROUP BY set_id ORDER BY set_id"
        )
        sets = cur.fetchall()
    conn.commit()

    out: list[tuple[int, str]] = []
    for set_id, created_at, ct in sets:
        if now - created_at > SET_MAX_AGE:
            status = "expired"
        elif items is not None and ct is not None and str(ct) not in items:
            status = "withdrawn"
        else:
            continue
        changed = False
        for reel in queue.set_items(conn, set_id):
            if queue.transition(conn, reel.id, ("queued", "pending"), status):
                changed = True
        if changed:
            out.append((set_id, status))
    return out
