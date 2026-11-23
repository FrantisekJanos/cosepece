"""Čerstvé nálezy: úlohy fresh ze změn ve snímku top_findings a vypršení či stažení starých sad.

Snímek zapisuje PHP synchronizace (app/ReelsSnapshot.php) do reel_state pod klíčem top_findings:
{"at": ISO 8601 s posunem pásma, "items": {"<ct>": ["klíč důvodu", …]}}. Bot volá detect a retire každou hodinu.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from . import jobs, queue

log = logging.getLogger("hlidac_reels.fresh")

SNAPSHOT_KEY = "top_findings"
BASELINE_KEY = "fresh_baseline"
# snímek starší než tohle znamená, že synchronizace stojí: nic se nezakládá ani nestahuje
SNAPSHOT_MAX_AGE = timedelta(hours=3)
# nevybrané varianty sady fresh/auto_pick starší než tohle vyprší
SET_MAX_AGE = timedelta(days=7)
# nejvýš tolik nových úloh fresh za den (Europe/Prague): změna skóre nesmí spustit vlnu generování
FRESH_PER_DAY = 2
# k tisku, ke kterému za tuto dobu vznikla úloha (i ruční) nebo vyšlo video, se fresh nezakládá
COOLDOWN = timedelta(days=14)
# klíče důvodů skóre, které znamenají novou událost v projednávání (zkrácení lhůty, nové PN…). Ostatní klíče
# popisují tisk (AI rozbor, poslanecký návrh, sledovaný zákon, citlivá oblast) a samy o sobě nové video nespustí
NEWS_KEYS = frozenset({
    "p90_requested", "p90_passed", "deadline_cut", "short_gap", "late_pn", "surge",
    "pn_other_law", "pn_large", "retro_deadline", "pn_many",
})


def _snapshot(conn, now: datetime) -> dict[str, list[str]] | None:
    """Položky snímku, nebo None, když snímek chybí, je poškozený nebo starší než 3 h.

    Prázdný snímek ({} položek) vrací {}; detect ho bere jako chybějící, retire jen nestahuje.
    """
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
    """Založí úlohy fresh pro nové tisky v nálezech a tisky s novým klíčem důvodu z NEWS_KEYS; vrací id úloh.

    Baseline (reel_state fresh_baseline) drží všechny dosud viděné klíče každého tisku. První běh jen uloží
    baseline. Tisk, který ze snímku vypadl, v baseline zůstává: návrat se stejnými klíči úlohu nezaloží.
    Nový klíč mimo NEWS_KEYS a změna u tisku s úlohou nebo videem za posledních COOLDOWN se jen zapíše do
    baseline (úloha nevznikne ani později), aby k jednomu tisku nevznikala videa se stejným obsahem.
    Za den (Europe/Prague) vznikne nejvýš FRESH_PER_DAY úloh fresh, kandidáti v pořadí snímku (podle skóre);
    kandidát nad limit se do baseline nezapíše, takže úlohu dostane některý další den.
    Prázdný snímek se bere jako chybějící; prázdná baseline jako první běh.
    """
    items = _snapshot(conn, now)
    if not items:
        return []
    raw = queue.get_state(conn, BASELINE_KEY)
    baseline = _load_baseline(raw)
    if not baseline:
        if baseline is None and raw is not None:
            log.warning("poškozená %s, ukládám znovu jako první běh", BASELINE_KEY)
        queue.set_state(conn, BASELINE_KEY, json.dumps(items, ensure_ascii=False))
        return []

    left = FRESH_PER_DAY - _fresh_created_on(conn, queue.local_date(conn, now))
    created: list[int] = []
    for ct, keys in items.items():
        known = baseline.get(ct)
        new_keys = [k for k in keys if known is None or k not in known]
        if known is not None and not new_keys:
            continue
        news = known is None or any(k in NEWS_KEYS for k in new_keys)
        if not news or _recent(conn, int(ct), now):
            baseline[ct] = (known or []) + new_keys
            queue.set_state(conn, BASELINE_KEY, json.dumps(baseline, ensure_ascii=False))
            continue
        if left <= 0:
            continue  # denní limit: baseline beze změny, tisk přijde na řadu jindy
        try:
            created.append(jobs.add(conn, source="fresh", ct=int(ct), reason_keys=keys))
            left -= 1
        except jobs.ActiveJobExists:
            pass  # aktivní úloha pro tisk už je, změnu pokryje
        # baseline hned po každém tisku: když další jobs.add spadne, už založené úlohy se nezaloží znovu
        baseline[ct] = (known or []) + new_keys
        queue.set_state(conn, BASELINE_KEY, json.dumps(baseline, ensure_ascii=False))
    return created


def _recent(conn, ct: int, now: datetime) -> bool:
    """K tisku vznikla za posledních COOLDOWN úloha (kromě nepovedených) nebo vyšlo video."""
    since = now - COOLDOWN
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM reel_jobs WHERE ct=%s AND created_at > %s "
            "AND status NOT IN ('failed','expired','cancelled') "
            "UNION ALL SELECT 1 FROM reel_queue WHERE ct=%s AND status='published' AND published_at > %s LIMIT 1",
            (ct, since, ct, since),
        )
        found = cur.fetchone() is not None
    conn.commit()
    return found


def _fresh_created_on(conn, day) -> int:
    """Kolik úloh fresh vzniklo v daném dni (Europe/Prague, počítá PostgreSQL)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM reel_jobs WHERE source='fresh' "
            "AND (created_at AT TIME ZONE 'Europe/Prague')::date = %s",
            (day,),
        )
        n = cur.fetchone()[0]
    conn.commit()
    return n


def _load_baseline(raw: str | None) -> dict[str, list[str]] | None:
    """Baseline ze stavu, nebo None, když chybí nebo je poškozená (pak se bere jako první běh)."""
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or not all(isinstance(v, list) for v in data.values()):
        return None
    return {str(ct): [str(k) for k in keys] for ct, keys in data.items()}


def retire(conn, now: datetime) -> list[tuple[int, str]]:
    """Sady fresh/auto_pick: nevybrané varianty starší než 7 dní (fresh od založení úlohy) → expired,
    sada bez výběru s tiskem mimo nálezy → withdrawn.

    Mění se nevybrané položky sady ve stavu queued/pending; vybrané varianty (queue.picked) zůstávají a sada
    s výběrem se nestahuje. Bez čerstvého snímku nebo s prázdným snímkem (chyba skóre, obnovená prázdná DB)
    se nic nestahuje, aby nezmizely všechny sady najednou; vypršení platí dál. Vrací (set_id, nový stav)
    jen pro sady, které se změnily.
    """
    items = _snapshot(conn, now)
    with conn.cursor() as cur:
        # stáří sady fresh se měří od založení úlohy (reel_jobs.id = set_id), auto_pick od zařazení do fronty
        cur.execute(
            "SELECT set_id, CASE WHEN max(source) = 'fresh' THEN coalesce("
            "(SELECT j.created_at FROM reel_jobs j WHERE j.id = reel_queue.set_id), min(created_at)) "
            "ELSE min(created_at) END, max(ct) FROM reel_queue "
            "WHERE set_id IS NOT NULL AND status IN ('queued','pending') AND source <> 'manual' "
            "GROUP BY set_id ORDER BY set_id",
        )
        sets = cur.fetchall()
    conn.commit()

    out: list[tuple[int, str]] = []
    for set_id, created_at, ct in sets:
        picked = queue.picked(conn, set_id)
        if now - created_at > SET_MAX_AGE:
            status = "expired"
        elif not picked and items and ct is not None and str(ct) not in items:
            status = "withdrawn"
        else:
            continue
        changed = False
        for reel in queue.set_items(conn, set_id):
            if reel.id not in picked and queue.transition(conn, reel.id, ("queued", "pending"), status):
                changed = True
        if changed:
            out.append((set_id, status))
    return out
