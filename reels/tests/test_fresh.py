"""Testy detektoru čerstvých nálezů a vypršení/stažení sad (PostgreSQL, marker pg)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.pg

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

BASE_ITEMS = {
    "221": ["ai_bill", "deadline_cut", "sensitive"],
    "90": ["ai_bill"],
    "311": ["ai_pn", "sensitive"],
    "78": ["poslanecky"],
    "120": ["late_pn"],
}


@pytest.fixture
def conn():
    if os.environ.get("REELS_TEST_DB") != "1":
        pytest.skip("chybí REELS_TEST_DB=1 (testy mažou tabulky reel_jobs, reel_queue a reel_state)")
    from hlidac_reels import db
    c = db.connect()
    db.migrate(c)
    with c.cursor() as cur:
        cur.execute("TRUNCATE reel_jobs RESTART IDENTITY; TRUNCATE reel_queue RESTART IDENTITY")
        cur.execute("DELETE FROM reel_state WHERE key IN ('top_findings', 'fresh_baseline')")
    c.commit()
    yield c
    c.close()


def _snapshot(conn, items: dict, at: datetime) -> None:
    from hlidac_reels import queue
    # stejný tvar jako PHP ReelsSnapshot::write (date('c') s posunem pásma)
    at_local = at.astimezone(timezone(timedelta(hours=1)))
    queue.set_state(conn, "top_findings", json.dumps({"at": at_local.isoformat(), "items": items}))


def _fresh_jobs(conn) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute("SELECT ct, reason_keys FROM reel_jobs WHERE source='fresh' ORDER BY id")
        out = cur.fetchall()
    conn.commit()
    return out


def test_prvni_beh_jen_baseline(conn):
    from hlidac_reels import fresh, queue
    _snapshot(conn, BASE_ITEMS, NOW - timedelta(minutes=10))
    assert fresh.detect(conn, NOW) == []
    assert json.loads(queue.get_state(conn, "fresh_baseline")) == BASE_ITEMS
    assert _fresh_jobs(conn) == []


def test_bez_snimku(conn):
    from hlidac_reels import fresh, queue
    assert fresh.detect(conn, NOW) == []
    assert queue.get_state(conn, "fresh_baseline") is None


def test_novy_tisk_a_novy_klic(conn):
    from hlidac_reels import fresh, queue
    _snapshot(conn, BASE_ITEMS, NOW - timedelta(minutes=10))
    fresh.detect(conn, NOW)

    items = dict(BASE_ITEMS)
    items["221"] = ["ai_bill", "late_pn", "deadline_cut", "sensitive"]
    items["400"] = ["deadline_cut", "sensitive"]
    _snapshot(conn, items, NOW + timedelta(hours=1))
    created = fresh.detect(conn, NOW + timedelta(hours=1, minutes=5))
    assert len(created) == 2
    jobs_ = _fresh_jobs(conn)
    assert sorted(ct for ct, _ in jobs_) == [221, 400]
    assert dict((ct, json.loads(k)) for ct, k in jobs_)[221] == items["221"]

    # stejný snímek podruhé nic nezaloží
    _snapshot(conn, items, NOW + timedelta(hours=2))
    assert fresh.detect(conn, NOW + timedelta(hours=2)) == []
    base = json.loads(queue.get_state(conn, "fresh_baseline"))
    assert set(base["221"]) == set(items["221"])


def test_zmizely_a_vraceny_tisk(conn):
    from hlidac_reels import fresh
    _snapshot(conn, BASE_ITEMS, NOW)
    fresh.detect(conn, NOW)
    # 90 vypadne ze snímku: baseline si ho pamatuje
    items = {k: v for k, v in BASE_ITEMS.items() if k != "90"}
    _snapshot(conn, items, NOW + timedelta(hours=1))
    assert fresh.detect(conn, NOW + timedelta(hours=1)) == []
    # vrátí se se stejnými klíči → nic; s novým klíčem → úloha
    _snapshot(conn, BASE_ITEMS, NOW + timedelta(hours=2))
    assert fresh.detect(conn, NOW + timedelta(hours=2)) == []
    items = dict(BASE_ITEMS, **{"90": ["ai_bill", "deadline_cut"]})
    _snapshot(conn, items, NOW + timedelta(hours=3))
    assert len(fresh.detect(conn, NOW + timedelta(hours=3))) == 1


def test_aktivni_uloha_jen_baseline(conn):
    from hlidac_reels import fresh, jobs, queue
    _snapshot(conn, BASE_ITEMS, NOW)
    fresh.detect(conn, NOW)
    jobs.add(conn, source="manual", ct=400)
    items = dict(BASE_ITEMS, **{"400": ["sensitive"]})
    _snapshot(conn, items, NOW + timedelta(hours=1))
    assert fresh.detect(conn, NOW + timedelta(hours=1)) == []
    assert json.loads(queue.get_state(conn, "fresh_baseline"))["400"] == ["sensitive"]


def test_stary_snimek(conn):
    from hlidac_reels import fresh, queue
    _snapshot(conn, BASE_ITEMS, NOW)
    fresh.detect(conn, NOW)
    items = dict(BASE_ITEMS, **{"400": ["sensitive"]})
    _snapshot(conn, items, NOW - timedelta(hours=4))
    assert fresh.detect(conn, NOW) == []
    assert "400" not in json.loads(queue.get_state(conn, "fresh_baseline"))
    # stará data ani baseline nezaloží
    with conn.cursor() as cur:
        cur.execute("DELETE FROM reel_state WHERE key='fresh_baseline'")
    conn.commit()
    assert fresh.detect(conn, NOW) == []
    assert queue.get_state(conn, "fresh_baseline") is None


def _set(conn, *, source: str, ct: int, age: timedelta, status: str = "pending", variants: int = 2) -> int:
    from hlidac_reels import jobs
    set_id = jobs.add(conn, source=source, ct=ct)
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status='done', set_id=id WHERE id=%s", (set_id,))
        for v in range(1, variants + 1):
            cur.execute(
                "INSERT INTO reel_queue (source, ct, video_path, video_sha256, caption, status, set_id, variant, created_at) "
                "VALUES (%s,%s,%s,%s,'popisek',%s,%s,%s,%s)",
                (source, ct, f"/x/{set_id}-{v}.mp4", f"sha-{set_id}-{v}", status, set_id, v, NOW - age),
            )
    conn.commit()
    return set_id


def _statuses(conn, set_id: int) -> list[str]:
    from hlidac_reels import queue
    return [r.status for r in queue.set_items(conn, set_id)]


def test_retire(conn):
    from hlidac_reels import fresh
    _snapshot(conn, BASE_ITEMS, NOW - timedelta(minutes=30))
    old = _set(conn, source="fresh", ct=221, age=timedelta(days=8))
    gone = _set(conn, source="fresh", ct=500, age=timedelta(days=1), status="queued")
    manual = _set(conn, source="manual", ct=600, age=timedelta(days=30))
    ok = _set(conn, source="auto_pick", ct=90, age=timedelta(days=2))
    assert sorted(fresh.retire(conn, NOW)) == sorted([(old, "expired"), (gone, "withdrawn")])
    assert _statuses(conn, old) == ["expired", "expired"]
    assert _statuses(conn, gone) == ["withdrawn", "withdrawn"]
    assert _statuses(conn, manual) == ["pending", "pending"]
    assert _statuses(conn, ok) == ["pending", "pending"]
    # podruhé už nic
    assert fresh.retire(conn, NOW) == []


def test_retire_bez_cerstveho_snimku_nestahuje(conn):
    from hlidac_reels import fresh
    gone = _set(conn, source="fresh", ct=500, age=timedelta(days=1))
    old = _set(conn, source="fresh", ct=501, age=timedelta(days=8))
    # bez snímku: jen vypršení
    assert fresh.retire(conn, NOW) == [(old, "expired")]
    # snímek starý 4 h: stažení se přeskočí
    _snapshot(conn, BASE_ITEMS, NOW - timedelta(hours=4))
    assert fresh.retire(conn, NOW) == []
    assert _statuses(conn, gone) == ["pending", "pending"]


def test_retire_cast_sady_uz_vybrana(conn):
    from hlidac_reels import fresh
    _snapshot(conn, BASE_ITEMS, NOW)
    s = _set(conn, source="fresh", ct=500, age=timedelta(days=1))
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_queue SET status='rejected' WHERE set_id=%s AND variant=2", (s,))
    conn.commit()
    assert fresh.retire(conn, NOW) == [(s, "withdrawn")]
    assert _statuses(conn, s) == ["withdrawn", "rejected"]
