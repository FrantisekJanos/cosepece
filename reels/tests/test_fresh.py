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
        cur.execute("DELETE FROM reel_state WHERE key IN ('top_findings', 'fresh_baseline') "
                    "OR key LIKE 'set\\_picked:%'")
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


def _set(conn, *, source: str, ct: int, age: timedelta, status: str = "pending", variants: int = 2,
         job_age: timedelta | None = None) -> int:
    from hlidac_reels import jobs
    set_id = jobs.add(conn, source=source, ct=ct)
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status='done', set_id=id, created_at=%s WHERE id=%s",
                    (NOW - (age if job_age is None else job_age), set_id))
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
    # stará sada s tiskem v nálezech: retire nic nemění (lhůty řeší queue.expire)
    old = _set(conn, source="fresh", ct=221, age=timedelta(days=8))
    gone = _set(conn, source="fresh", ct=500, age=timedelta(days=1), status="queued")
    manual = _set(conn, source="manual", ct=600, age=timedelta(days=30))
    ok = _set(conn, source="auto_pick", ct=90, age=timedelta(days=2))
    assert fresh.retire(conn, NOW) == [(gone, "withdrawn")]
    assert _statuses(conn, old) == ["pending", "pending"]
    assert _statuses(conn, gone) == ["withdrawn", "withdrawn"]
    assert _statuses(conn, manual) == ["pending", "pending"]
    assert _statuses(conn, ok) == ["pending", "pending"]
    # podruhé už nic
    assert fresh.retire(conn, NOW) == []


def test_retire_bez_cerstveho_snimku_nestahuje(conn):
    from hlidac_reels import fresh
    gone = _set(conn, source="fresh", ct=500, age=timedelta(days=1))
    old = _set(conn, source="fresh", ct=501, age=timedelta(days=8))
    assert fresh.retire(conn, NOW) == []
    # snímek starý 4 h: stažení se přeskočí
    _snapshot(conn, BASE_ITEMS, NOW - timedelta(hours=4))
    assert fresh.retire(conn, NOW) == []
    assert _statuses(conn, gone) == ["pending", "pending"]
    assert _statuses(conn, old) == ["pending", "pending"]


def test_retire_cast_sady_uz_vybrana(conn):
    from hlidac_reels import fresh
    _snapshot(conn, BASE_ITEMS, NOW)
    s = _set(conn, source="fresh", ct=500, age=timedelta(days=1))
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_queue SET status='rejected' WHERE set_id=%s AND variant=2", (s,))
    conn.commit()
    assert fresh.retire(conn, NOW) == [(s, "withdrawn")]
    assert _statuses(conn, s) == ["withdrawn", "rejected"]


def test_retire_prazdny_snimek_nestahuje(conn):
    from hlidac_reels import fresh
    _snapshot(conn, {}, NOW)
    gone = _set(conn, source="fresh", ct=500, age=timedelta(days=1))
    assert fresh.retire(conn, NOW) == []
    assert _statuses(conn, gone) == ["pending", "pending"]


def test_poskozena_baseline_jako_prvni_beh(conn, caplog):
    from hlidac_reels import fresh, queue
    _snapshot(conn, BASE_ITEMS, NOW)
    queue.set_state(conn, "fresh_baseline", "{nejde to")
    with caplog.at_level("WARNING", logger="hlidac_reels.fresh"):
        assert fresh.detect(conn, NOW) == []
    assert "fresh_baseline" in caplog.text
    assert json.loads(queue.get_state(conn, "fresh_baseline")) == BASE_ITEMS
    assert _fresh_jobs(conn) == []
    # JSON jiného tvaru se bere stejně
    queue.set_state(conn, "fresh_baseline", "[1, 2]")
    assert fresh.detect(conn, NOW) == []
    assert json.loads(queue.get_state(conn, "fresh_baseline")) == BASE_ITEMS


def test_baseline_po_kazdem_tisku(conn, monkeypatch):
    from hlidac_reels import fresh, jobs, queue
    _snapshot(conn, BASE_ITEMS, NOW)
    fresh.detect(conn, NOW)
    items = dict(BASE_ITEMS, **{"400": ["sensitive"], "401": ["late_pn"]})
    _snapshot(conn, items, NOW + timedelta(hours=1))
    real_add = jobs.add
    calls = []

    def add(conn_, **kw):
        calls.append(kw["ct"])
        if len(calls) == 2:
            raise RuntimeError("DB spadla")
        return real_add(conn_, **kw)

    monkeypatch.setattr(jobs, "add", add)
    with pytest.raises(RuntimeError):
        fresh.detect(conn, NOW + timedelta(hours=1))
    base = json.loads(queue.get_state(conn, "fresh_baseline"))
    assert base["400"] == ["sensitive"]
    assert "401" not in base
    assert [ct for ct, _ in _fresh_jobs(conn)] == [400]
    # po opravě se založí jen zbylý tisk
    monkeypatch.setattr(jobs, "add", real_add)
    assert len(fresh.detect(conn, NOW + timedelta(hours=1))) == 1
    assert sorted(ct for ct, _ in _fresh_jobs(conn)) == [400, 401]


def test_retire_vybrana_sada_nevyprsi_ani_nestahne(conn):
    from hlidac_reels import fresh, queue
    _snapshot(conn, BASE_ITEMS, NOW)
    # odeslaná před 8 dny a tisk mimo nálezy, ale varianta 1 vybraná včera a varianta 2 rozhodnutá před hodinou
    s = _set(conn, source="fresh", ct=500, age=timedelta(days=8))
    first, second = (r.id for r in queue.set_items(conn, s))
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_queue SET sent_at=%s WHERE set_id=%s", (NOW - timedelta(days=8), s))
        cur.execute("UPDATE reel_queue SET status='rejected', decided_at=%s WHERE id=%s",
                    (NOW - timedelta(hours=1), second))
        cur.execute("UPDATE reel_queue SET picked_at=%s WHERE id=%s", (NOW - timedelta(days=1), first))
    conn.commit()
    queue.mark_picked(conn, s, first)
    # nestáhne se (výběr) a nevyprší: vybraná má 14 dní od výběru, sada je dotčená
    assert fresh.retire(conn, NOW) == []
    assert queue.expire(conn, NOW) == []
    assert _statuses(conn, s) == ["pending", "rejected"]


def test_retire_po_vyberu_nestahuje(conn):
    from hlidac_reels import fresh, queue
    _snapshot(conn, BASE_ITEMS, NOW)
    # tisk mimo nálezy, ale jedna varianta vybraná: nestáhne se nic
    s = _set(conn, source="fresh", ct=500, age=timedelta(days=1), variants=3)
    queue.mark_picked(conn, s, queue.set_items(conn, s)[1].id)
    assert fresh.retire(conn, NOW) == []
    assert _statuses(conn, s) == ["pending"] * 3


def _age_all_fresh(conn, at: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET created_at=%s WHERE source='fresh'", (at,))
    conn.commit()


def test_denni_limit_fresh(conn):
    from hlidac_reels import fresh, queue
    _snapshot(conn, BASE_ITEMS, NOW)
    fresh.detect(conn, NOW)
    # změna skóre: čtyři nové tisky najednou (pořadí snímku = pořadí podle skóre)
    items = dict(BASE_ITEMS, **{"401": ["a"], "402": ["b"], "403": ["c"], "404": ["d"]})
    t1 = NOW + timedelta(hours=1)
    _snapshot(conn, items, t1)
    assert len(fresh.detect(conn, t1)) == 2
    _age_all_fresh(conn, t1)
    assert [ct for ct, _ in _fresh_jobs(conn)] == [401, 402]
    base = json.loads(queue.get_state(conn, "fresh_baseline"))
    assert "401" in base and "402" in base
    assert "403" not in base and "404" not in base
    # týž den už nic
    t2 = NOW + timedelta(hours=2)
    _snapshot(conn, items, t2)
    assert fresh.detect(conn, t2) == []
    # další den (Europe/Prague) zbylí kandidáti
    t3 = NOW + timedelta(days=1)
    _snapshot(conn, items, t3)
    assert len(fresh.detect(conn, t3)) == 2
    assert sorted(ct for ct, _ in _fresh_jobs(conn)) == [401, 402, 403, 404]


def test_denni_limit_podle_prazskeho_dne(conn):
    from hlidac_reels import fresh
    _snapshot(conn, BASE_ITEMS, NOW)
    fresh.detect(conn, NOW)
    # dvě úlohy fresh 20. 11. ve 23:30 UTC = 21. 11. 0:30 v Praze
    late = datetime(2026, 11, 20, 23, 30, tzinfo=timezone.utc)
    items = dict(BASE_ITEMS, **{"401": ["a"], "402": ["b"], "403": ["c"]})
    _snapshot(conn, items, late)
    assert len(fresh.detect(conn, late)) == 2
    _age_all_fresh(conn, late)
    # 21. 11. 10:00 UTC je v Praze týž den → limit vyčerpaný
    t = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)
    _snapshot(conn, items, t)
    assert fresh.detect(conn, t) == []
    # 20. 11. 22:30 UTC (= 23:30 v Praze 20. 11.) je jiný den → jeden zbylý kandidát
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET created_at=%s WHERE source='fresh'",
                    (datetime(2026, 11, 20, 22, 30, tzinfo=timezone.utc),))
    conn.commit()
    assert len(fresh.detect(conn, t)) == 1


def test_prazdny_snimek_jako_chybejici(conn):
    from hlidac_reels import fresh, queue
    # bez baseline: prázdný snímek baseline neuloží
    _snapshot(conn, {}, NOW)
    assert fresh.detect(conn, NOW) == []
    assert queue.get_state(conn, "fresh_baseline") is None
    # s baseline: prázdný snímek ji nepřepíše
    _snapshot(conn, BASE_ITEMS, NOW)
    fresh.detect(conn, NOW)
    _snapshot(conn, {}, NOW + timedelta(hours=1))
    assert fresh.detect(conn, NOW + timedelta(hours=1)) == []
    assert json.loads(queue.get_state(conn, "fresh_baseline")) == BASE_ITEMS


def test_prazdna_baseline_jako_prvni_beh(conn):
    from hlidac_reels import fresh, queue
    queue.set_state(conn, "fresh_baseline", "{}")
    _snapshot(conn, BASE_ITEMS, NOW)
    assert fresh.detect(conn, NOW) == []
    assert _fresh_jobs(conn) == []
    assert json.loads(queue.get_state(conn, "fresh_baseline")) == BASE_ITEMS


def test_klic_bez_udalosti_nezalozi(conn):
    from hlidac_reels import fresh, queue
    _snapshot(conn, BASE_ITEMS, NOW)
    fresh.detect(conn, NOW)
    # hotový AI rozbor nebo sledovaný zákon není nová událost: jen baseline
    items = dict(BASE_ITEMS, **{"221": ["ai_bill", "deadline_cut", "sensitive", "ai", "watched"]})
    t = NOW + timedelta(hours=1)
    _snapshot(conn, items, t)
    assert fresh.detect(conn, t) == []
    assert set(json.loads(queue.get_state(conn, "fresh_baseline"))["221"]) == set(items["221"])
    assert _fresh_jobs(conn) == []


def test_cooldown_po_rucni_uloze(conn):
    from hlidac_reels import fresh, jobs, queue
    _snapshot(conn, BASE_ITEMS, NOW)
    fresh.detect(conn, NOW)
    # ruční video k tisku 221 před 3 dny (úloha doběhla)
    job = jobs.add(conn, source="manual", ct=221)
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status='done', created_at=%s WHERE id=%s", (NOW - timedelta(days=3), job))
    conn.commit()
    items = dict(BASE_ITEMS, **{"221": ["ai_bill", "deadline_cut", "sensitive", "pn_many", "pn_other_law"]})
    t = NOW + timedelta(hours=1)
    _snapshot(conn, items, t)
    assert fresh.detect(conn, t) == []
    assert "pn_many" in json.loads(queue.get_state(conn, "fresh_baseline"))["221"]
    # po cooldownu spustí úlohu jen další nová událost
    t2 = NOW + timedelta(days=15)
    _snapshot(conn, items, t2)
    assert fresh.detect(conn, t2) == []
    items2 = dict(items, **{"221": items["221"] + ["late_pn"]})
    _snapshot(conn, items2, t2)
    assert len(fresh.detect(conn, t2)) == 1


def test_cooldown_po_publikovanem_videu(conn):
    from hlidac_reels import fresh
    _snapshot(conn, BASE_ITEMS, NOW)
    fresh.detect(conn, NOW)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO reel_queue (source, ct, video_path, video_sha256, caption, status, published_at) "
                    "VALUES ('ukazka', 400, 'x.mp4', 'sha400', 'popisek', 'published', %s)",
                    (NOW - timedelta(days=5),))
    conn.commit()
    items = dict(BASE_ITEMS, **{"400": ["deadline_cut"]})
    t = NOW + timedelta(hours=1)
    _snapshot(conn, items, t)
    assert fresh.detect(conn, t) == []
    # video starší než cooldown nebrání novému tisku
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_queue SET published_at=%s WHERE ct=400", (NOW - timedelta(days=20),))
    conn.commit()
    items = dict(BASE_ITEMS, **{"401": ["deadline_cut"]})
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_queue SET ct=401 WHERE ct=400")
    conn.commit()
    _snapshot(conn, items, t)
    assert len(fresh.detect(conn, t)) == 1
