"""Bot a Reels na X přes složku Pro X (PostgreSQL, marker pg; Telegram falešný)."""
from __future__ import annotations

import httpx
import pytest

from hlidac_reels import publish, queue, xpost
from test_bot import _add, _cb, _get, api, bot, conn, media_dir, meta  # noqa: F401 – fixture

pg = pytest.mark.pg


def _all_buttons(body: dict) -> list[tuple[str, str]]:
    markup = body.get("reply_markup") or {}
    return [(b["text"], b.get("callback_data") or b.get("url")) for row in markup.get("inline_keyboard", [])
            for b in row]


def _published(conn, media_dir, set_id=7, n=1):  # noqa: F811
    rid = _add(conn, media_dir, n=n, caption="Sněmovna zkrátila lhůtu.\n\nDalší odstavec.")
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_queue SET set_id=%s, variant=1, status='approved' WHERE id=%s", (set_id, rid))
    conn.commit()
    return rid


def _x(conn, rid):  # noqa: F811
    with conn.cursor() as cur:
        cur.execute("SELECT x_status, x_msg_id FROM reel_queue WHERE id=%s", (rid,))
        r = cur.fetchone()
    conn.commit()
    return r


def _set_x(conn, rid, status):  # noqa: F811
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_queue SET x_status=%s WHERE id=%s", (status, rid))
    conn.commit()


@pg
def test_zverejneni_ze_sady_ma_tlacitko_na_x(bot, conn, api, media_dir):  # noqa: F811
    rid = _published(conn, media_dir)
    bot._publish(rid, publish.run)
    assert _get(conn, rid).status == "published"
    assert _all_buttons(api.sent()[-1]) == [("Na X", f"x:{rid}")]


@pg
def test_zverejneni_bez_sady_bez_tlacitka(bot, conn, api, media_dir):  # noqa: F811
    rid = _published(conn, media_dir, set_id=None)
    bot._publish(rid, publish.run)
    assert _get(conn, rid).status == "published"
    assert "reply_markup" not in api.sent()[-1]


@pg
def test_na_x_ready_hotovo(bot, conn, api, media_dir):  # noqa: F811
    rid = _published(conn, media_dir)
    bot._publish(rid, publish.run)
    api.calls.clear()
    bot.handle(_cb(f"x:{rid}", msg_id=77))
    assert _x(conn, rid) == ("wanted", None)
    assert api.texts() == [f"Na X #{rid:04d}: čekám na PC"]
    assert any(m == "editMessageReplyMarkup" and b["message_id"] == 77 for m, b in api.calls)
    # druhý klik: jen odpověď na tlačítko
    api.calls.clear()
    bot.handle(_cb(f"x:{rid}", msg_id=77))
    assert api.methods() == ["answerCallbackQuery"]
    # worker nahlásil ready
    _set_x(conn, rid, "ready")
    api.calls.clear()
    bot._x_notify()
    msg = api.sent()[-1]
    assert msg["text"].startswith(f"Pro X: #{rid:04d} · tisk 221\nVideo: <code>{rid:04d}-tisk221.mp4</code>")
    assert msg["parse_mode"] == "HTML"
    assert "Sněmovna zkrátila lhůtu.\n\nCelý rozbor: https://cosepece.cz/tisk/221" in msg["text"]
    buttons = _all_buttons(msg)
    assert buttons[0][0] == "Otevřít X" and buttons[0][1].startswith("https://x.com/intent/post?text=Sn%C4%9Bmovna")
    assert buttons[1:] == [("Hotovo na X", f"xdone:{rid}"), ("Zrušit", f"xskip:{rid}")]
    assert _x(conn, rid)[1] is not None
    api.calls.clear()
    bot._x_notify()  # nic dvakrát
    assert api.sent() == []
    bot.handle(_cb(f"xdone:{rid}", msg_id=88))
    assert _x(conn, rid)[0] == "done"
    assert any(m == "editMessageReplyMarkup" and b["message_id"] == 88 for m, b in api.calls)
    api.calls.clear()
    bot.handle(_cb(f"xskip:{rid}", msg_id=88))  # pozdní Zrušit nic nezmění
    assert _x(conn, rid)[0] == "done" and api.methods() == ["answerCallbackQuery"]


@pg
def test_na_x_missing_a_znovu(bot, conn, api, media_dir):  # noqa: F811
    rid = _published(conn, media_dir)
    bot._publish(rid, publish.run)
    bot.handle(_cb(f"x:{rid}"))
    _set_x(conn, rid, "missing")
    api.calls.clear()
    bot._x_notify()
    msg = api.sent()[-1]
    assert msg["text"].startswith(f"Video #{rid:04d} na PC není")
    assert _all_buttons(msg) == [("Zkusit znovu", f"x:{rid}")]
    bot.handle(_cb(f"x:{rid}"))
    assert _x(conn, rid) == ("wanted", None)


@pg
def test_na_x_nezverejnena(bot, conn, api, media_dir):  # noqa: F811
    rid = _add(conn, media_dir)
    api.calls.clear()
    bot.handle(_cb(f"x:{rid}"))
    assert _x(conn, rid)[0] is None
    assert api.methods() == ["answerCallbackQuery"]


@pg
def test_tlacitko_odkazu_odmitnuto(bot, conn, api, media_dir):  # noqa: F811
    rid = _published(conn, media_dir)
    bot._publish(rid, publish.run)
    bot.handle(_cb(f"x:{rid}"))
    _set_x(conn, rid, "ready")
    real = api.__call__
    state = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sendMessage") and b'"url"' in request.content and state["n"] == 0:
            state["n"] += 1
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: BUTTON_URL_INVALID"})
        return real(request)

    bot.tg._client = httpx.Client(transport=httpx.MockTransport(flaky))
    api.calls.clear()
    bot._x_notify()
    msg = api.sent()[-1]
    assert "Otevřít X: https://x.com/intent/post?text=" in msg["text"]
    assert _all_buttons(msg) == [("Hotovo na X", f"xdone:{rid}"), ("Zrušit", f"xskip:{rid}")]
    assert _x(conn, rid)[1] is not None


@pg
def test_x_notify_bezi_v_maybe_events(bot, conn, api, media_dir):  # noqa: F811
    rid = _published(conn, media_dir)
    bot._publish(rid, publish.run)
    bot.handle(_cb(f"x:{rid}"))
    _set_x(conn, rid, "ready")
    bot.maybe_events()
    assert _x(conn, rid)[1] is not None
