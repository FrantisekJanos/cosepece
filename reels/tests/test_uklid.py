"""Úklid stavu (hlidac_reels.uklid): klíče reel_state, staré reel_events, složky zrušených úloh v mezisložce.

Testy nad PostgreSQL (marker pg) mažou tabulky reel_queue, reel_state, reel_jobs a reel_events.
"""
from __future__ import annotations

import inspect
import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from test_bot import CHAT, PUBLIC_BASE, TOKEN, FakeMeta, FakeTelegramApi

pg = pytest.mark.pg
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
# klíče, na které úklid nikdy nesahá
FOREIGN = ("tg_offset", "threads_token", "worker_state", "worker_seen_at", "bot_seen_at", "hlidani_aktivni",
           "autopilot", "autopilot_skipped:5")


@pytest.fixture
def conn():
    if os.environ.get("REELS_TEST_DB") != "1":
        pytest.skip("chybí REELS_TEST_DB=1 (testy mažou tabulky reel_queue a reel_state)")
    from hlidac_reels import db
    c = db.connect()
    db.migrate(c)
    with c.cursor() as cur:
        cur.execute("TRUNCATE reel_queue RESTART IDENTITY; TRUNCATE reel_state; "
                    "TRUNCATE reel_jobs RESTART IDENTITY; TRUNCATE reel_events RESTART IDENTITY")
    c.commit()
    yield c
    c.close()


def _ago(**delta) -> datetime:
    return NOW - timedelta(**delta)


def _job(conn, status: str, created: datetime, finished: datetime | None = None, jid: int | None = None) -> int:
    """Úloha s daným stavem a časy; jid = pevné id (jinak z sekvence)."""
    with conn.cursor() as cur:
        if jid is None:
            cur.execute("INSERT INTO reel_jobs (source, status, created_at, finished_at) "
                        "VALUES ('manual', %s, %s, %s) RETURNING id", (status, created, finished))
        else:
            cur.execute("INSERT INTO reel_jobs (id, source, status, created_at, finished_at) "
                        "VALUES (%s, 'manual', %s, %s, %s) RETURNING id", (jid, status, created, finished))
        jid = cur.fetchone()[0]
    conn.commit()
    return jid


_sha = iter(range(1, 10_000))


def _item(conn, status: str, at: datetime, set_id: int | None = None) -> int:
    """Položka fronty: založená, odeslaná a (podle stavu) rozhodnutá nebo zveřejněná v čase at."""
    decided = at if status in ("approved", "rejected", "expired", "withdrawn", "published") else None
    published = at if status == "published" else None
    with conn.cursor() as cur:
        cur.execute("INSERT INTO reel_queue (video_path, video_sha256, caption, status, set_id, created_at, sent_at, "
                    "decided_at, published_at) VALUES ('v.mp4', %s, 'titulek', %s, %s, %s, %s, %s, %s) RETURNING id",
                    (f"sha{next(_sha)}", status, set_id, at, at, decided, published))
        rid = cur.fetchone()[0]
    conn.commit()
    return rid


def _set(conn, keys: dict[str, str]) -> None:
    from hlidac_reels import queue
    for k, v in keys.items():
        queue.set_state(conn, k, v)


def _all_keys(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT key FROM reel_state")
        out = {r[0] for r in cur.fetchall()}
    conn.commit()
    return out


def _item_keys(rid: int, msg_id: int) -> dict[str, str]:
    return {f"edit_msg:{msg_id}": str(rid), f"landscape_failed:{rid}": "sha", f"held_msg:{rid}": "77",
            f"preview_sent:{rid}": '{"sha": "x", "sent": []}'}


def _set_keys(set_id: int) -> dict[str, str]:
    return {f"set_picked:{set_id}": "1", f"reminded:{set_id}": "1", f"set_warnings:{set_id}": "{}"}


def _job_keys(job_id: int) -> dict[str, str]:
    return {f"job_status:{job_id}": "3", f"job_retried:{job_id}": "99"}


# --- testy -------------------------------------------------------------------

def test_prefixy_shodne_s_botem():
    from hlidac_reels import bot, uklid
    assert uklid.JOB_STATUS_PREFIX == bot.JOB_STATUS_KEY
    assert uklid.LANDSCAPE_FAILED_PREFIX == bot.LANDSCAPE_FAILED_KEY.format("")
    # edit_msg: nemá v botu konstantu, bot ho skládá v textu
    assert f'f"{uklid.EDIT_MSG_PREFIX}{{' in inspect.getsource(bot)


@pg
def test_uklid_klice_starych_polozek(conn):
    from hlidac_reels import uklid
    old = _ago(days=31)
    rid = _item(conn, "published", old)
    set_id = _job(conn, "done", old, old)
    _item(conn, "rejected", old, set_id)
    _item(conn, "published", old, set_id)
    job_id = _job(conn, "failed", old, old)
    gone = {**_item_keys(rid, 900), **_set_keys(set_id), **_job_keys(job_id),
            # neexistující položka, sada a úloha
            "edit_msg:901": "9999", "held_msg:9999": "5", "reminded:8888": "1", "job_status:7777": "1",
            # vadné klíče: nečíselná přípona nebo hodnota u edit_msg:
            "held_msg:abc": "1", "edit_msg:902": "x", "edit_msg:x": str(rid), "job_retried:": "1",
            "set_warnings:²": "{}"}
    _set(conn, gone)
    _set(conn, {k: "1" for k in FOREIGN})
    deleted = uklid.prune_state(conn, NOW)
    assert sorted(deleted) == sorted(gone)
    assert _all_keys(conn) == set(FOREIGN)
    # podruhé nic
    assert uklid.prune_state(conn, NOW) == []


@pg
def test_uklid_nesmaze_zive(conn):
    from hlidac_reels import uklid
    keep: dict[str, str] = {}
    # čekající varianta odeslaná před 40 dny (vypršení selhalo)
    keep |= _item_keys(_item(conn, "pending", _ago(days=40)), 900)
    # sada s jednou čekající variantou, ostatní zamítnuté před 40 dny, úloha hotová před 40 dny
    live_set = _job(conn, "done", _ago(days=41), _ago(days=40))
    _item(conn, "pending", _ago(days=40), live_set)
    _item(conn, "rejected", _ago(days=40), live_set)
    _item(conn, "rejected", _ago(days=40), live_set)
    keep |= _set_keys(live_set)
    # sada, jejíž poslední varianta je zamítnutá před 10 dny
    recent_set = _job(conn, "done", _ago(days=41), _ago(days=40))
    _item(conn, "rejected", _ago(days=40), recent_set)
    _item(conn, "rejected", _ago(days=10), recent_set)
    keep |= _set_keys(recent_set)
    # sada bez položek, jejíž úloha ještě čeká ve frontě
    queued_set = _job(conn, "queued", _ago(days=40))
    keep |= _set_keys(queued_set)
    # úloha queued vytvořená před 40 dny, úloha failed před 10 dny
    keep |= _job_keys(_job(conn, "queued", _ago(days=40)))
    keep |= _job_keys(_job(conn, "failed", _ago(days=11), _ago(days=10)))
    # zamítnutá před 10 dny, selhaná a neznámá před 40 dny (čekají na Zkusit znovu), held před 40 dny
    keep |= _item_keys(_item(conn, "rejected", _ago(days=10)), 901)
    keep |= _item_keys(_item(conn, "failed", _ago(days=40)), 902)
    keep |= _item_keys(_item(conn, "unknown", _ago(days=40)), 903)
    keep |= _item_keys(_item(conn, "held", _ago(days=40)), 904)
    keep |= {k: "1" for k in FOREIGN}
    _set(conn, keep)
    assert uklid.prune_state(conn, NOW) == []
    assert _all_keys(conn) == set(keep)


@pg
def test_uklid_udalosti_90_dni(conn):
    from hlidac_reels import uklid
    rows = {
        "odeslana_91": (_ago(days=91), 1, _ago(days=91)),
        "historie_91": (_ago(days=91), 0, None),
        "neodeslana_91": (_ago(days=91), 1, None),
        "souhrn_neodeslany_91": (_ago(days=91), 2, None),
        "odeslana_89": (_ago(days=89), 1, _ago(days=89)),
        "historie_89": (_ago(days=89), 0, None),
    }
    with conn.cursor() as cur:
        for kind, (at, urgency, notified) in rows.items():
            cur.execute("INSERT INTO reel_events (at, kind, urgency, notified_at) VALUES (%s, %s, %s, %s)",
                        (at, kind, urgency, notified))
    conn.commit()
    assert uklid.prune_events(conn, NOW) == 2
    with conn.cursor() as cur:
        cur.execute("SELECT kind FROM reel_events")
        left = {r[0] for r in cur.fetchall()}
    conn.commit()
    assert left == {"neodeslana_91", "souhrn_neodeslany_91", "odeslana_89", "historie_89"}
    assert uklid.prune_events(conn, NOW) == 0


@pg
def test_uklid_slozka_zrusene_ulohy(conn, tmp_path):
    from hlidac_reels import uklid
    inbox = tmp_path / "inbox"
    _job(conn, "cancelled", _ago(days=3), _ago(days=2), jid=12)
    _job(conn, "cancelled", _ago(days=3), _ago(hours=1), jid=13)
    _job(conn, "queued", _ago(days=3), jid=14)
    _job(conn, "cancelled", _ago(days=3), _ago(days=2), jid=15)
    for name in ("12", "13", "14", "15"):
        d = inbox / name / "v1"
        d.mkdir(parents=True)
        (d / "video.mp4").write_bytes(b"x")
    (inbox / "15" / "manifest.json").write_text("{}", encoding="utf-8")
    # nečíselná složka a soubor se nečtou
    (inbox / "tmp").mkdir()
    (inbox / "16").write_text("x", encoding="utf-8")
    assert uklid.prune_inbox(conn, inbox, NOW) == [12]
    assert sorted(p.name for p in inbox.iterdir()) == ["13", "14", "15", "16", "tmp"]
    assert uklid.prune_inbox(conn, inbox, NOW) == []
    # chybějící mezisložka nevadí
    assert uklid.prune_inbox(conn, tmp_path / "neni", NOW) == []


@pg
def test_uklid_run_pocty(conn, tmp_path):
    from hlidac_reels import uklid
    _set(conn, {"held_msg:9999": "1", "job_status:7777": "1", "tg_offset": "5"})
    with conn.cursor() as cur:
        cur.execute("INSERT INTO reel_events (at, kind, urgency, notified_at) VALUES (%s, 'x', 1, %s)",
                    (_ago(days=91), _ago(days=91)))
    conn.commit()
    assert uklid.run(conn, NOW, None) == {"state": 2, "events": 1, "inbox": 0}
    assert uklid.run(conn, NOW, tmp_path) == {"state": 0, "events": 0, "inbox": 0}
    assert _all_keys(conn) == {"tg_offset"}


@pg
def test_uklid_pad_kroku_nezastavi_ostatni(conn, tmp_path, monkeypatch, caplog):
    import psycopg
    from hlidac_reels import uklid

    def broken(what: str):
        def fail(*a):
            raise RuntimeError(f"{what} spadl")
        return fail
    with conn.cursor() as cur:
        cur.execute("INSERT INTO reel_events (at, kind, urgency, notified_at) VALUES (%s, 'x', 1, %s)",
                    (_ago(days=91), _ago(days=91)))
    conn.commit()
    _job(conn, "cancelled", _ago(days=3), _ago(days=2), jid=12)
    (tmp_path / "12").mkdir()

    def bad_sql(c, now):
        with c.cursor() as cur:
            cur.execute("SELECT * FROM tabulka_neni")
    # klíče stavu spadnou chybou SQL: transakce se vrátí, události a složky zrušených úloh se uklidí přesto
    monkeypatch.setattr(uklid, "prune_state", bad_sql)
    with caplog.at_level("WARNING", logger="hlidac_reels.uklid"):
        assert uklid.run(conn, NOW, tmp_path) == {"state": 0, "events": 1, "inbox": 1}
    assert "tabulka_neni" in caplog.text and not (tmp_path / "12").exists()
    # spadnou klíče i události: složky běží dál
    _job(conn, "cancelled", _ago(days=3), _ago(days=2), jid=13)
    (tmp_path / "13").mkdir()
    monkeypatch.setattr(uklid, "prune_state", broken("úklid klíčů"))
    monkeypatch.setattr(uklid, "prune_events", broken("úklid událostí"))
    assert uklid.run(conn, NOW, tmp_path) == {"state": 0, "events": 0, "inbox": 1}
    assert not (tmp_path / "13").exists()
    # ztráta spojení jde dál: zachytí ji Bot._guard a smyčka (konec procesu, Docker ho spustí znovu)
    monkeypatch.setattr(uklid, "prune_state", lambda c, now: (_ for _ in ()).throw(
        psycopg.OperationalError("server closed the connection unexpectedly")))
    with pytest.raises(psycopg.OperationalError):
        uklid.run(conn, NOW, tmp_path)


# --- napojení na bota ----------------------------------------------------------

@pytest.fixture
def bot(conn, tmp_path):
    from hlidac_reels.bot import Bot
    from hlidac_reels.telegram import Telegram
    api = FakeTelegramApi()
    tg = Telegram(TOKEN, client=httpx.Client(transport=httpx.MockTransport(api)))
    (tmp_path / "media" / "videos").mkdir(parents=True)
    b = Bot(conn, tg, FakeMeta(), CHAT, str(tmp_path / "media"), PUBLIC_BASE, inbox_dir=str(tmp_path / "inbox"))
    b.now = lambda: NOW
    return b


@pg
def test_bot_uklid_v_hodinove_smycce(bot, conn, tmp_path, monkeypatch, caplog):
    from hlidac_reels import bot as bot_mod, fresh, uklid
    calls = []
    result = {"state": 3, "events": 0, "inbox": 1}

    def fake_run(c, now, inbox_dir):
        calls.append((now, inbox_dir))
        return dict(result)
    monkeypatch.setattr(uklid, "run", fake_run)
    monkeypatch.setattr(fresh, "detect", lambda c, now: [])
    monkeypatch.setattr(fresh, "retire", lambda c, now: [])
    clock = [1000.0]
    bot.clock = lambda: clock[0]
    with caplog.at_level("INFO", logger="hlidac_reels.bot"):
        bot.maybe_fresh()
        bot.maybe_fresh()
        clock[0] += bot_mod.FRESH_INTERVAL - 1
        bot.maybe_fresh()
    assert calls == [(NOW, tmp_path / "inbox")]
    assert "úklid: klíče stavu 3, události 0, složky zrušených úloh 1" in caplog.text
    clock[0] += 1
    bot.maybe_fresh()
    assert len(calls) == 2
    # bez mezisložky se úklid spustí s inbox_dir None; nulové počty se nelogují
    bot.inbox_dir = None
    result.update(state=0, inbox=0)
    caplog.clear()
    clock[0] += bot_mod.FRESH_INTERVAL
    with caplog.at_level("INFO", logger="hlidac_reels.bot"):
        bot.maybe_fresh()
    assert calls[-1] == (NOW, None)
    assert "úklid:" not in caplog.text


@pg
def test_bot_uklid_chyba_nezastavi_smycku(bot, conn, monkeypatch, caplog):
    from hlidac_reels import fresh, uklid

    def boom(c, now, inbox_dir):
        raise RuntimeError("úklid spadl")
    monkeypatch.setattr(uklid, "run", boom)
    monkeypatch.setattr(fresh, "detect", lambda c, now: [])
    monkeypatch.setattr(fresh, "retire", lambda c, now: [])
    with caplog.at_level("WARNING", logger="hlidac_reels.bot"):
        bot.maybe_fresh()
    assert "úklid stavu selhalo" in caplog.text
