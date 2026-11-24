"""Testy fronty reel_queue (PostgreSQL, marker pg)."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.pg


@pytest.fixture
def conn():
    # TRUNCATE nesmí zasáhnout produkční frontu: jen s výslovným souhlasem
    if os.environ.get("REELS_TEST_DB") != "1":
        pytest.skip("chybí REELS_TEST_DB=1 (testy mažou tabulky reel_queue a reel_state)")
    from hlidac_reels import db
    c = db.connect()
    db.migrate(c)
    with c.cursor() as cur:
        cur.execute("TRUNCATE reel_queue RESTART IDENTITY; TRUNCATE reel_state")
    c.commit()
    yield c
    c.close()


def _add(conn, n, **kw):
    from hlidac_reels import queue
    return queue.add(conn, video_path=f"v{n}.mp4", video_sha256=f"sha{n}", caption=f"c{n}", **kw)


def test_add_a_next_sendable_podle_id(conn):
    from datetime import datetime, timezone
    from hlidac_reels import queue
    now = datetime.now(timezone.utc)
    a, b = _add(conn, 1), _add(conn, 2)
    assert a < b
    assert queue.next_sendable(conn, now, True).id == a
    assert queue.transition(conn, a, "queued", "pending")
    # čekající položka další neblokuje
    assert queue.next_sendable(conn, now, True).id == b
    assert queue.get(conn, b).source == "ukazka"
    assert queue.get(conn, 999) is None


def test_duplicitni_video_vyhodi_DuplicateVideo(conn):
    from hlidac_reels import queue
    _add(conn, 1)
    with pytest.raises(queue.DuplicateVideo):
        _add(conn, 1)
    # spojení zůstane použitelné
    assert _add(conn, 2)


def test_transition_jen_z_ocekavaneho_stavu(conn):
    from hlidac_reels import queue
    i = _add(conn, 1)
    queue.transition(conn, i, "queued", "approved")
    assert queue.transition(conn, i, "approved", "publishing", attempts=1) is True
    assert queue.transition(conn, i, "approved", "publishing") is False
    assert queue.get(conn, i).attempts == 1
    assert queue.transition(conn, i, ("publishing", "unknown"), "published", ig_media_id="m")
    assert queue.get(conn, i).ig_media_id == "m"


def test_recover_after_restart_publishing_na_unknown(conn):
    from hlidac_reels import queue
    a, b = _add(conn, 1), _add(conn, 2)
    queue.transition(conn, a, "queued", "publishing")
    assert queue.recover_after_restart(conn) == [a]
    assert queue.get(conn, a).status == "unknown"
    assert queue.get(conn, b).status == "queued"


def test_state_offset_ulozi_a_precte(conn):
    from hlidac_reels import queue
    assert queue.get_state(conn, "tg_offset") is None
    queue.set_state(conn, "tg_offset", "5")
    queue.set_state(conn, "tg_offset", "9")
    assert queue.get_state(conn, "tg_offset") == "9"


def test_counts_podle_stavu(conn):
    from hlidac_reels import queue
    a = _add(conn, 1)
    _add(conn, 2)
    queue.transition(conn, a, "queued", "rejected")
    queue.set_fields(conn, a, error="x")
    assert queue.counts(conn) == {"queued": 1, "rejected": 1}
    assert queue.get(conn, a).error == "x"


def test_status_a_neznama_pole_vyhodi_ValueError(conn):
    from hlidac_reels import queue
    i = _add(conn, 1)
    with pytest.raises(ValueError):
        queue.set_fields(conn, i, status="published")
    with pytest.raises(ValueError):
        queue.transition(conn, i, "queued", "pending", status="published")
    with pytest.raises(ValueError):
        queue.set_fields(conn, i, nesmysl="x")
    assert queue.get(conn, i).status == "queued"


def test_po_chybe_db_spojeni_dal_funguje(conn):
    import psycopg
    from hlidac_reels import queue
    a, b = _add(conn, 1), _add(conn, 2)
    queue.set_fields(conn, a, public_token="t")
    with pytest.raises(psycopg.errors.UniqueViolation):
        queue.set_fields(conn, b, public_token="t")
    assert queue.get(conn, b).status == "queued"
    with pytest.raises(psycopg.errors.UniqueViolation):
        queue.transition(conn, b, "queued", "pending", public_token="t")
    assert queue.transition(conn, b, "queued", "pending")


def test_add_se_sadou(conn):
    from hlidac_reels import queue
    i = _add(conn, 1, set_id=7, variant=2, template="template_v3")
    r = queue.get(conn, i)
    assert (r.set_id, r.variant, r.template) == (7, 2, "template_v3")


def test_set_items_podle_varianty(conn):
    from hlidac_reels import queue
    _add(conn, 1, set_id=9, variant=3)
    _add(conn, 2, set_id=9, variant=1)
    _add(conn, 3, set_id=10, variant=1)
    assert [r.variant for r in queue.set_items(conn, 9)] == [1, 3]


def test_migrace_dvakrat(conn):
    from hlidac_reels import db, queue
    db.migrate(conn)
    db.migrate(conn)
    i = _add(conn, 1)
    assert queue.transition(conn, i, "queued", "withdrawn")
    assert queue.transition(conn, i, "withdrawn", "expired")


def test_add_se_sirkou(conn):
    from hlidac_reels import queue
    i = _add(conn, 1, video_landscape_path="/m/l.mp4", video_landscape_sha256="L")
    r = queue.get(conn, i)
    assert r.video_landscape_path == "/m/l.mp4"
    assert r.video_landscape_sha256 == "L"
    assert r.telegram_landscape_msg_id is None


def test_by_telegram_msg_najde_video_na_sirku(conn):
    from hlidac_reels import queue
    i = _add(conn, 1)
    queue.set_fields(conn, i, telegram_landscape_msg_id=77)
    assert queue.by_telegram_msg(conn, 77).id == i


def test_set_landscape_jen_queued_pending(conn):
    from hlidac_reels import queue
    i = _add(conn, 1)
    queue.set_fields(conn, i, telegram_landscape_msg_id=5)
    assert queue.set_landscape(conn, i, "/m/l.mp4", "L1") is True
    r = queue.get(conn, i)
    assert (r.video_landscape_path, r.video_landscape_sha256) == ("/m/l.mp4", "L1")
    assert r.telegram_landscape_msg_id is None
    assert queue.transition(conn, i, "queued", "pending")
    queue.set_fields(conn, i, telegram_landscape_msg_id=6)
    assert queue.set_landscape(conn, i, "/m/l2.mp4", "L2") is True
    assert queue.get(conn, i).telegram_landscape_msg_id is None
    assert queue.transition(conn, i, "pending", "published")
    assert queue.set_landscape(conn, i, "/m/l3.mp4", "L3") is False
    assert queue.get(conn, i).video_landscape_sha256 == "L2"


def test_sha_in_use(conn):
    from hlidac_reels import queue
    _add(conn, 1, video_landscape_path="/m/l.mp4", video_landscape_sha256="L")
    assert queue.sha_in_use(conn, "sha1") is True   # jako video na výšku
    assert queue.sha_in_use(conn, "L") is True      # jako video na šířku
    assert queue.sha_in_use(conn, "jine") is False


def test_approve_plati_i_pro_sirku(conn):
    from datetime import datetime, timezone
    from hlidac_reels import queue
    now = datetime.now(timezone.utc)
    a = _add(conn, 1)
    assert queue.approve(conn, a, now) is False  # queued
    assert queue.transition(conn, a, "queued", "pending", telegram_msg_id=10)
    assert queue.approve(conn, a, now) is True
    assert queue.get(conn, a).status == "approved" and queue.get(conn, a).decided_at is not None
    assert queue.approve(conn, a, now) is False  # už nečeká
    # šířka doplněná a do Telegramu zatím neodeslaná: jedno Schválit platí i pro ni (rozhodnutí 2026-11-24)
    b = _add(conn, 2)
    assert queue.transition(conn, b, "queued", "pending", telegram_msg_id=20)
    assert queue.set_landscape(conn, b, "/m/w.mp4", "W")
    assert queue.approve(conn, b, now) is True


def test_set_landscape_nuluje_odeslany_otisk(conn):
    from hlidac_reels import queue
    i = _add(conn, 1)
    queue.set_fields(conn, i, telegram_landscape_msg_id=5, telegram_landscape_sha256="L0")
    assert queue.set_landscape(conn, i, "/m/l.mp4", "L1")
    r = queue.get(conn, i)
    assert r.telegram_landscape_msg_id is None and r.telegram_landscape_sha256 is None


def test_set_landscape_sent_jen_pro_stejny_soubor(conn):
    from hlidac_reels import queue
    i = _add(conn, 1)
    assert queue.set_landscape(conn, i, "/m/l.mp4", "L1")
    assert queue.set_landscape_sent(conn, i, 7, "L0") is False
    assert queue.get(conn, i).telegram_landscape_msg_id is None
    assert queue.set_landscape_sent(conn, i, 7, "L1") is True
    r = queue.get(conn, i)
    assert (r.telegram_landscape_msg_id, r.telegram_landscape_sha256) == (7, "L1")
    # šířka bez otisku (NULL) se páruje s NULL
    j = _add(conn, 2)
    assert queue.set_landscape(conn, j, "/m/l2.mp4", None)
    assert queue.set_landscape_sent(conn, j, 8, None) is True


def test_delete_state(conn):
    from hlidac_reels import queue
    queue.set_state(conn, "k", "v")
    queue.delete_state(conn, "k")
    assert queue.get_state(conn, "k") is None
    queue.delete_state(conn, "k")  # neexistující klíč nevadí


def test_novy_stav_a_sloupce(conn):
    from hlidac_reels import db, queue
    i = _add(conn, 1)
    r = queue.get(conn, i)
    assert r.send_attempts == 0
    assert r.send_error is None
    assert r.next_send_at is None
    assert r.picked_at is None
    assert "held" in queue.STATUSES
    assert queue.transition(conn, i, "queued", "held")
    db.migrate(conn)
    db.migrate(conn)


# --- výběr jednotky k odeslání (R1, R2) a odklad (R4) ---

def _now():
    from datetime import datetime, timezone
    return datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def _set(conn, set_id, source="manual", n=3, start=None):
    """Sada n variant; čísla videí odvozená od set_id, ať se otisky neopakují."""
    base = start if start is not None else set_id * 10
    return [_add(conn, base + v, source=source, set_id=set_id, variant=v) for v in range(1, n + 1)]


def test_next_sendable_vyzadana_neceka_na_pending(conn):
    from hlidac_reels import queue
    a = _set(conn, 1)
    b = _set(conn, 2)
    for i in a:
        queue.transition(conn, i, "queued", "pending", sent_at=_now())
    r = queue.next_sendable(conn, _now(), auto_ok=False)
    assert r is not None and r.id == b[0]


def test_next_sendable_vrati_prvni_queued_variantu_sady(conn):
    from hlidac_reels import queue
    a = _set(conn, 1)
    queue.transition(conn, a[0], "queued", "pending", sent_at=_now())
    assert queue.next_sendable(conn, _now(), auto_ok=False).id == a[1]


def test_next_sendable_auto_jen_s_auto_ok(conn):
    from hlidac_reels import queue
    f = _set(conn, 1, source="fresh")
    assert queue.next_sendable(conn, _now(), auto_ok=False) is None
    assert queue.next_sendable(conn, _now(), auto_ok=True).id == f[0]
    # vyžádané zdroje projdou i bez auto_ok
    for i in f:
        queue.transition(conn, i, "queued", "rejected")
    for sid, src in ((2, "auto_pick"), (3, "ukazka")):
        ids = _set(conn, sid, source=src)
        assert queue.next_sendable(conn, _now(), auto_ok=False).id == ids[0]
        for i in ids:
            queue.transition(conn, i, "queued", "rejected")
    assert queue.REQUESTED_SOURCES == ("manual", "auto_pick", "ukazka")


def test_next_sendable_preskoci_odlozenou(conn):
    from datetime import timedelta
    from hlidac_reels import queue
    a = _add(conn, 1)
    b = _add(conn, 2)
    queue.set_fields(conn, a, next_send_at=_now() + timedelta(minutes=5))
    assert queue.next_sendable(conn, _now(), auto_ok=True).id == b
    # po uplynutí odkladu je zase na řadě starší
    assert queue.next_sendable(conn, _now() + timedelta(minutes=5), auto_ok=True).id == a


def test_auto_blocked_12h(conn):
    from datetime import timedelta
    from hlidac_reels import queue
    assert queue.auto_blocked(conn, _now()) is False
    f = _set(conn, 1, source="fresh")
    for i in f:
        queue.transition(conn, i, "queued", "pending", sent_at=_now() - timedelta(hours=11))
    assert queue.auto_blocked(conn, _now()) is True
    # po 12 h od odeslání už nebrzdí
    assert queue.auto_blocked(conn, _now() + timedelta(hours=2)) is False
    for i in f:
        queue.set_fields(conn, i, sent_at=_now() - timedelta(hours=13))
    assert queue.auto_blocked(conn, _now()) is False
    for i in f:
        queue.set_fields(conn, i, sent_at=_now() - timedelta(hours=11))
    # vybraná varianta = dotčená sada
    queue.mark_picked(conn, 1, f[1])
    assert queue.auto_blocked(conn, _now()) is False
    queue.delete_state(conn, "set_picked:1")
    assert queue.auto_blocked(conn, _now()) is True
    # rozhodnutá varianta = dotčená sada
    queue.transition(conn, f[2], "pending", "rejected", decided_at=_now())
    assert queue.auto_blocked(conn, _now()) is False


def test_auto_blocked_ignoruje_vyzadane_a_autopilot(conn):
    from datetime import timedelta
    from hlidac_reels import queue
    sent = _now() - timedelta(hours=1)
    for i in _set(conn, 1, source="manual"):
        queue.transition(conn, i, "queued", "pending", sent_at=sent)
    for i in _set(conn, 2, source="auto_pick"):
        queue.transition(conn, i, "queued", "pending", sent_at=sent)
    # autopilot: approved se sent_at, v Telegramu nikdy nebyl
    ap = _add(conn, 99, source="fresh", set_id=4, variant=1, autopilot=True)
    queue.transition(conn, ap, "queued", "approved", sent_at=sent)
    assert queue.auto_blocked(conn, _now()) is False


def test_mark_video_sent(conn):
    from hlidac_reels import queue
    i = _add(conn, 1)
    queue.mark_video_sent(conn, i, 4242)
    r = queue.get(conn, i)
    assert r.telegram_video_msg_id == 4242
    assert r.status == "queued"


def test_defer_send_backoff_a_held(conn):
    from datetime import timedelta
    from hlidac_reels import queue
    ids = _set(conn, 1)
    other = _set(conn, 2)
    for n, mins in enumerate((1, 5, 15, 60, 60), start=1):
        assert queue.defer_send(conn, ids[0], f"chyba {n}", _now()) == n
        r = queue.get(conn, ids[0])
        assert r.send_attempts == n
        assert r.send_error == f"chyba {n}"
        assert r.next_send_at == _now() + timedelta(minutes=mins)
        # celá sada se odkládá spolu, jiná varianta téže sady se mezitím neposílá
        assert queue.get(conn, ids[1]).next_send_at == _now() + timedelta(minutes=mins)
        expect = "held" if n == 5 else "queued"
        assert [queue.get(conn, i).status for i in ids] == [expect] * 3
    assert [r.id for r in queue.held_items(conn)] == ids
    # jiná sada zůstala beze změny a je na řadě
    assert queue.get(conn, other[0]).send_attempts == 0
    assert queue.next_sendable(conn, _now(), auto_ok=False).id == other[0]


def test_defer_send_held_jen_queued_varianty(conn):
    from hlidac_reels import queue
    ids = _set(conn, 1)
    queue.transition(conn, ids[0], "queued", "pending", sent_at=_now())
    for _ in range(queue.SEND_MAX_ATTEMPTS):
        queue.defer_send(conn, ids[1], "x", _now())
    assert [queue.get(conn, i).status for i in ids] == ["pending", "held", "held"]


def test_sets_sent_on_hranice_dne(conn):
    from datetime import date, datetime, timezone
    from hlidac_reels import queue
    a = _set(conn, 1, source="fresh", n=1)
    b = _set(conn, 2, source="fresh", n=1)
    queue.transition(conn, a[0], "queued", "pending", sent_at=datetime(2026, 11, 24, 22, 59, tzinfo=timezone.utc))
    queue.transition(conn, b[0], "queued", "pending", sent_at=datetime(2026, 11, 24, 23, 1, tzinfo=timezone.utc))
    assert queue.sets_sent_on(conn, date(2026, 11, 24)) == 1
    assert queue.sets_sent_on(conn, date(2026, 11, 25)) == 1


def test_sets_sent_on_jen_automaticke(conn):
    from datetime import date, datetime, timezone
    from hlidac_reels import queue
    t = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
    for sid, src in ((1, "fresh"), (2, "auto_pick"), (3, "manual")):
        for i in _set(conn, sid, source=src, n=1):
            queue.transition(conn, i, "queued", "pending", sent_at=t)
    # /vyber (auto_pick) je vyžádaná sada a do denního limitu se nepočítá
    assert queue.sets_sent_on(conn, date(2026, 11, 24)) == 1


# --- lhůty a vypršení (queue.expire, queue.auto_reminders) ---

def _sent(conn, set_id, source="manual", n=3, at=None):
    """Sada odeslaná do Telegramu (pending se sent_at)."""
    from hlidac_reels import queue
    ids = _set(conn, set_id, source=source, n=n)
    for i in ids:
        queue.transition(conn, i, "queued", "pending", sent_at=at or _now())
    return ids


def _statuses(conn, ids):
    from hlidac_reels import queue
    return [queue.get(conn, i).status for i in ids]


def test_expire_nevybrane_dotcene_sady_24h_od_rozhodnuti(conn):
    from datetime import timedelta
    from hlidac_reels import queue
    ids = _sent(conn, 1, at=_now() - timedelta(days=3), n=3)
    queue.mark_picked(conn, 1, ids[0])
    queue.set_fields(conn, ids[0], picked_at=_now() - timedelta(days=2))
    assert queue.approve(conn, ids[0], _now() - timedelta(hours=24) + timedelta(minutes=1))
    assert queue.expire(conn, _now()) == []
    assert _statuses(conn, ids) == ["approved", "pending", "pending"]
    queue.set_fields(conn, ids[1], telegram_msg_id=500)
    queue.set_fields(conn, ids[2], telegram_msg_id=500)
    out = queue.expire(conn, _now() + timedelta(minutes=2))
    assert out == [queue.Expired("unpicked", 1, ids[1:], [500])]
    assert _statuses(conn, ids) == ["approved", "expired", "expired"]
    assert queue.expire(conn, _now() + timedelta(days=30)) == []


def test_expire_nevybrane_cekaji_na_vybranou(conn):
    from datetime import timedelta
    from hlidac_reels import queue
    ids = _sent(conn, 1, at=_now() - timedelta(days=3), n=3)
    # vybrané dvě, jedna rozhodnutá před 2 dny, druhá dál čeká na schválení: zbylé nevyprší
    queue.mark_picked(conn, 1, ids[0])
    queue.mark_picked(conn, 1, ids[1])
    queue.set_fields(conn, ids[0], picked_at=_now() - timedelta(days=2))
    queue.set_fields(conn, ids[1], picked_at=_now() - timedelta(days=2))
    queue.transition(conn, ids[0], "pending", "rejected", decided_at=_now() - timedelta(days=2))
    assert queue.expire(conn, _now()) == []
    assert _statuses(conn, ids) == ["rejected", "pending", "pending"]


def test_expire_nedotcena_automaticka_7_dni(conn):
    from datetime import timedelta
    from hlidac_reels import queue
    ids = _sent(conn, 1, source="fresh", at=_now() - timedelta(days=7) + timedelta(minutes=1))
    assert queue.expire(conn, _now()) == []
    out = queue.expire(conn, _now() + timedelta(minutes=2))
    assert [(e.kind, e.set_id, e.reel_ids) for e in out] == [("auto_set", 1, ids)]
    assert _statuses(conn, ids) == ["expired"] * 3
    assert all(queue.get(conn, i).decided_at is not None for i in ids)


def test_expire_nedotcena_vyzadana_14_dni(conn):
    from datetime import timedelta
    from hlidac_reels import queue
    at = _now() - timedelta(days=14) + timedelta(minutes=1)
    ids = _sent(conn, 1, source="auto_pick", at=at)
    solo = _add(conn, 900, source="manual")
    queue.transition(conn, solo, "queued", "pending", sent_at=at, telegram_msg_id=77)
    # vyžádaná sada nevyprší po 7 dnech
    assert queue.expire(conn, _now()) == []
    out = queue.expire(conn, _now() + timedelta(minutes=2))
    assert [(e.kind, e.set_id, e.reel_ids, e.msg_ids) for e in out] == [
        ("requested_set", 1, ids, []), ("requested_set", None, [solo], [77])]
    assert _statuses(conn, ids + [solo]) == ["expired"] * 4


def test_expire_vybrana_nerozhodnuta_14_dni_od_vyberu(conn):
    from datetime import timedelta
    from hlidac_reels import queue
    ids = _sent(conn, 1, at=_now() - timedelta(days=30))
    queue.mark_picked(conn, 1, ids[0])
    queue.set_fields(conn, ids[0], picked_at=_now() - timedelta(days=14) + timedelta(minutes=1), telegram_msg_id=88)
    assert queue.expire(conn, _now()) == []
    t = _now() + timedelta(minutes=2)
    assert queue.expire(conn, t) == [queue.Expired("picked", 1, [ids[0]], [88])]
    assert _statuses(conn, ids) == ["expired", "pending", "pending"]
    # zbylé nevybrané vyprší 24 h po vypršení vybrané (to se bere jako poslední rozhodnutí)
    assert queue.expire(conn, t + timedelta(hours=23)) == []
    assert [e.kind for e in queue.expire(conn, t + timedelta(hours=24))] == ["unpicked"]
    assert _statuses(conn, ids) == ["expired"] * 3


def test_expire_held_7_dni_na_rejected(conn):
    from datetime import timedelta
    from hlidac_reels import queue
    i = _add(conn, 1)
    queue.set_fields(conn, i, next_send_at=_now() - timedelta(days=7) + timedelta(minutes=1), send_attempts=5)
    queue.transition(conn, i, "queued", "held")
    queue.set_state(conn, f"{queue.HELD_MSG_KEY}{i}", "321")
    assert queue.expire(conn, _now()) == []
    assert queue.expire(conn, _now() + timedelta(minutes=2)) == [queue.Expired("held", None, [i], [321])]
    r = queue.get(conn, i)
    # vypršení nikdy neschvaluje: held jde do rejected
    assert r.status == "rejected" and r.decided_at is not None
    assert queue.get_state(conn, f"{queue.HELD_MSG_KEY}{i}") is None


def test_nevyprsi_bez_sent_at(conn):
    from datetime import timedelta
    from hlidac_reels import queue
    # sada, která ještě nebyla v Telegramu (queued), a pending bez sent_at
    queued = _set(conn, 1, source="fresh")
    old = _add(conn, 900, source="manual")
    queue.transition(conn, old, "queued", "pending")
    # autopilot: approved se sent_at, v Telegramu nikdy nebyl
    ap = _add(conn, 901, source="fresh", set_id=4, variant=1, autopilot=True)
    queue.transition(conn, ap, "queued", "approved", sent_at=_now() - timedelta(days=60))
    assert queue.expire(conn, _now() + timedelta(days=100)) == []
    assert _statuses(conn, queued + [old, ap]) == ["queued"] * 3 + ["pending", "approved"]
    assert queue.auto_reminders(conn, _now() + timedelta(days=100)) == []


def test_pripominka_jednou(conn):
    from datetime import timedelta
    from hlidac_reels import queue
    _sent(conn, 1, source="fresh", at=_now() - timedelta(hours=12) + timedelta(minutes=1))
    _sent(conn, 2, source="manual", at=_now() - timedelta(days=2))
    touched = _sent(conn, 3, source="fresh", at=_now() - timedelta(days=2))
    queue.mark_picked(conn, 3, touched[0])
    assert queue.auto_reminders(conn, _now()) == []
    later = _now() + timedelta(minutes=2)
    assert queue.auto_reminders(conn, later) == [1]
    queue.mark_reminded(conn, 1)
    assert queue.auto_reminders(conn, later) == []


# --- řádky denního souhrnu (queue.summary_lines) ---

def test_souhrn_vyprsele_nevybrane(conn):
    from datetime import timedelta
    from hlidac_reels import queue
    ids = _sent(conn, 1, at=_now() - timedelta(days=3))
    queue.mark_picked(conn, 1, ids[0])
    queue.approve(conn, ids[0], _now() - timedelta(hours=25))
    t = _now() - timedelta(minutes=30)
    assert [e.kind for e in queue.expire(conn, t)] == ["unpicked"]
    line = f"Sada #1: vypršely nevybrané varianty #{ids[1]}, #{ids[2]}"
    assert line in queue.summary_lines(conn, _now())
    # mimo okno 24 h už ne
    assert not any(line in x for x in queue.summary_lines(conn, t + timedelta(hours=24, minutes=1)))


def test_souhrn_stazene(conn):
    from datetime import timedelta
    from hlidac_reels import queue
    ids = _set(conn, 1, source="fresh")
    for i in ids:
        queue.transition(conn, i, "queued", "withdrawn", decided_at=_now() - timedelta(hours=2))
    line = "Sada #1 stažena: nález už není mezi hlavními"
    assert line in queue.summary_lines(conn, _now())
    assert line not in queue.summary_lines(conn, _now() + timedelta(hours=23))


def test_souhrn_vybrana_zitra_vyprsi(conn):
    from datetime import timedelta
    from hlidac_reels import queue
    ids = _sent(conn, 1, at=_now() - timedelta(days=30))
    queue.mark_picked(conn, 1, ids[0])
    queue.set_fields(conn, ids[0], picked_at=_now() - timedelta(days=13, hours=1))
    line = f"Sada #1: vybraná varianta #{ids[0]} zítra vyprší (14 dní bez schválení)"
    assert line in queue.summary_lines(conn, _now())
    # víc než den před lhůtou nic
    assert line not in queue.summary_lines(conn, _now() - timedelta(hours=2))
    # autopilot se nepočítá
    ap = _add(conn, 901, source="fresh", set_id=4, variant=1, autopilot=True)
    queue.transition(conn, ap, "queued", "withdrawn", decided_at=_now())
    assert not any("#4" in x for x in queue.summary_lines(conn, _now()))
