"""Testy Telegram bota (PostgreSQL, marker pg; Telegram přes httpx.MockTransport, Meta falešná, bez sítě)."""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from pathlib import Path

import httpx
import pytest

from hlidac_reels.meta import MetaError

PUBLIC_BASE = "https://cosepece.cz/reels-media"
CHAT = 4242
OTHER_CHAT = 999
TOKEN = "123456:SECRET-bot-token"
pg = pytest.mark.pg


# --- falešný Telegram ----------------------------------------------------

class FakeTelegramApi:
    """Zaznamená volání Bot API a vrací ok s rostoucím message_id."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.next_id = 100
        self.updates: list[dict] = []
        self.fail: dict[str, Exception] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[1]
        if method in self.fail:
            raise self.fail[method]
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("application/json"):
            body = json.loads(request.content)
        else:
            # multipart (sendVideo): stačí poznat pole caption a chat_id
            raw = request.read().decode("utf-8", "replace")
            body = {"_multipart": raw}
        self.calls.append((method, body))
        if method == "getUpdates":
            ups, self.updates = self.updates, []
            return httpx.Response(200, json={"ok": True, "result": ups})
        if method in ("sendMessage", "sendVideo"):
            self.next_id += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self.next_id}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]

    def sent(self, method: str = "sendMessage") -> list[dict]:
        return [b for m, b in self.calls if m == method]

    def texts(self) -> list[str]:
        return [b["text"] for b in self.sent()]


def _buttons(body: dict) -> list[str]:
    markup = body.get("reply_markup") or {}
    return [b["callback_data"] for row in markup.get("inline_keyboard", []) for b in row]


class FakeMeta:
    def __init__(self):
        self.calls: Counter[str] = Counter()
        self.fail: dict[str, Exception] = {}
        self.fb_captions: list[str] = []
        self.ig_captions: list[str] = []
        self.ig_link = "https://www.instagram.com/reel/M1/"

    def _call(self, name):
        self.calls[name] += 1
        if name in self.fail:
            raise self.fail[name]

    def ig_create(self, video_url, caption):
        self.ig_captions.append(caption)
        self._call("ig_create")
        return "C1"

    def ig_wait(self, container_id, timeout=600, interval=10):
        self._call("ig_wait")

    def ig_publish(self, container_id):
        self._call("ig_publish")
        return "M1", self.ig_link

    def fb_upload_reel(self, path, description):
        self.fb_captions.append(description)
        self._call("fb_upload_reel")
        return "V1", "https://www.facebook.com/reel/V1"

    def ig_find_recent(self, caption_prefix, since):
        self._call("ig_find_recent")
        return None

    def fb_find_recent(self, caption_prefix, since):
        self._call("fb_find_recent")
        return None


# --- fixture -------------------------------------------------------------

@pytest.fixture
def conn():
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


@pytest.fixture
def media_dir(tmp_path):
    d = tmp_path / "media"
    (d / "videos").mkdir(parents=True)
    return d


@pytest.fixture
def api():
    return FakeTelegramApi()


@pytest.fixture
def meta():
    return FakeMeta()


@pytest.fixture
def bot(conn, api, meta, media_dir):
    from hlidac_reels.bot import Bot
    from hlidac_reels.telegram import Telegram
    tg = Telegram(TOKEN, client=httpx.Client(transport=httpx.MockTransport(api)))
    return Bot(conn, tg, meta, CHAT, str(media_dir), PUBLIC_BASE)


def _add(conn, media_dir: Path, n: int = 1, caption: str = "Sněmovna zkrátila lhůtu na 4 roky",
         ct: int | None = 221) -> int:
    from hlidac_reels import queue
    video = media_dir / "videos" / f"v{n}.mp4"
    video.write_bytes(b"fake mp4 " + str(n).encode())
    return queue.add(conn, video_path=str(video), video_sha256=f"sha{n}", caption=caption,
                     ct=ct, format="v3")


_uid = iter(range(1, 10_000))


def _cb(data: str, chat: int = CHAT, msg_id: int = 1) -> dict:
    return {"update_id": next(_uid), "callback_query": {
        "id": f"cb{next(_uid)}", "from": {"id": chat}, "data": data,
        "message": {"message_id": msg_id, "chat": {"id": chat}}}}


def _msg(text: str, chat: int = CHAT, reply_to: int | None = None) -> dict:
    m = {"message_id": next(_uid) + 5000, "chat": {"id": chat}, "text": text}
    if reply_to is not None:
        m["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": next(_uid), "message": m}


def _get(conn, rid):
    from hlidac_reels import queue
    return queue.get(conn, rid)


# --- testy ---------------------------------------------------------------

@pg
def test_cizi_chat_ignorovan(bot, conn, api, meta, media_dir):
    rid = _add(conn, media_dir)
    bot.send_next()
    assert _get(conn, rid).status == "pending"
    api.calls.clear()
    bot.handle(_cb(f"ok:{rid}", chat=OTHER_CHAT))
    bot.handle(_cb(f"no:{rid}", chat=OTHER_CHAT))
    bot.handle(_msg("/dalsi", chat=OTHER_CHAT))
    bot.handle(_msg("hack", chat=OTHER_CHAT, reply_to=_get(conn, rid).telegram_msg_id))
    assert api.calls == []
    assert sum(meta.calls.values()) == 0
    r = _get(conn, rid)
    assert r.status == "pending" and r.caption == "Sněmovna zkrátila lhůtu na 4 roky"


@pg
def test_schvalit_publikuje_a_posle_odkazy(bot, conn, api, meta, media_dir):
    rid = _add(conn, media_dir)
    bot.send_next()
    preview_id = _get(conn, rid).telegram_msg_id
    api.calls.clear()
    bot.handle(_cb(f"ok:{rid}", msg_id=preview_id))
    r = _get(conn, rid)
    assert r.status == "published" and r.decided_at is not None
    assert "answerCallbackQuery" in api.methods()
    texts = api.texts()
    assert texts[0] == "Zveřejňuji…"
    assert "https://www.facebook.com/reel/V1" in texts[1]
    assert "https://www.instagram.com/reel/M1/" in texts[1]
    # tlačítka u náhledu zmizela
    assert any(m == "editMessageReplyMarkup" and b["message_id"] == preview_id for m, b in api.calls)
    # druhé ťuknutí na Schválit nic nezveřejní, jen zastaví točení tlačítka
    api.calls.clear()
    bot.handle(_cb(f"ok:{rid}", msg_id=preview_id))
    assert meta.calls["fb_upload_reel"] == 1
    assert api.methods() == ["answerCallbackQuery"]


@pg
def test_schvalit_bez_ig_odkazu(bot, conn, api, meta, media_dir):
    rid = _add(conn, media_dir)
    bot.send_next()
    meta.ig_link = ""
    bot.handle(_cb(f"ok:{rid}"))
    assert _get(conn, rid).status == "published"
    assert "odkaz se nepodařilo načíst" in api.texts()[-1]


@pg
def test_chyba_publikace_posle_chybu_a_retry(bot, conn, api, meta, media_dir):
    rid = _add(conn, media_dir)
    bot.send_next()
    meta.fail["ig_create"] = MetaError("(#100) chybný parametr", status=400)
    api.calls.clear()
    bot.handle(_cb(f"ok:{rid}"))
    assert _get(conn, rid).status == "failed"
    last = api.sent()[-1]
    assert "chybný parametr" in last["text"]
    assert _buttons(last) == [f"retry:{rid}"]
    # Zkusit znovu: dohledá, pak zveřejní
    del meta.fail["ig_create"]
    api.calls.clear()
    bot.handle(_cb(f"retry:{rid}"))
    assert _get(conn, rid).status == "published"
    assert api.texts()[0] == "Zveřejňuji…"
    assert "https://www.facebook.com/reel/V1" in api.texts()[-1]
    # retry na zveřejněné položce nic nedělá
    api.calls.clear()
    bot.handle(_cb(f"retry:{rid}"))
    assert api.methods() == ["answerCallbackQuery"]
    assert meta.calls["fb_upload_reel"] == 1


@pg
def test_zamitnout_posle_dalsi(bot, conn, api, meta, media_dir):
    r1 = _add(conn, media_dir, 1)
    r2 = _add(conn, media_dir, 2)
    bot.send_next()
    preview_id = _get(conn, r1).telegram_msg_id
    api.calls.clear()
    bot.handle(_cb(f"no:{r1}", msg_id=preview_id))
    a = _get(conn, r1)
    assert a.status == "rejected" and a.decided_at is not None
    assert _get(conn, r2).status == "pending"
    assert "answerCallbackQuery" in api.methods()
    assert any(m == "editMessageReplyMarkup" and b["message_id"] == preview_id for m, b in api.calls)
    assert len(api.sent("sendVideo")) == 1
    assert sum(meta.calls.values()) == 0
    # zamítnout zamítnuté nic nezmění
    bot.handle(_cb(f"no:{r1}"))
    assert _get(conn, r1).status == "rejected" and _get(conn, r2).status == "pending"


@pg
def test_reply_zmeni_popisek_a_posle_nahled(bot, conn, api, media_dir):
    rid = _add(conn, media_dir)
    bot.send_next()
    r = _get(conn, rid)
    old_preview = r.telegram_msg_id
    api.calls.clear()
    bot.handle(_msg("Nový popisek", reply_to=r.telegram_video_msg_id))
    r2 = _get(conn, rid)
    assert r2.caption == "Nový popisek" and r2.status == "pending"
    last = api.sent()[-1]
    assert "Nový popisek" in last["text"]
    assert _buttons(last) == [f"ok:{rid}", f"edit:{rid}", f"no:{rid}"]
    assert r2.telegram_msg_id != old_preview
    # starý náhled přišel o tlačítka
    assert any(m == "editMessageReplyMarkup" and b["message_id"] == old_preview for m, b in api.calls)
    # odpověď na nový náhled taky funguje
    bot.handle(_msg("Třetí verze", reply_to=r2.telegram_msg_id))
    assert _get(conn, rid).caption == "Třetí verze"


@pg
def test_reply_prilis_dlouhy_odmitnut(bot, conn, api, media_dir):
    rid = _add(conn, media_dir)
    bot.send_next()
    r = _get(conn, rid)
    api.calls.clear()
    bot.handle(_msg("x" * 2121, reply_to=r.telegram_msg_id))
    assert _get(conn, rid).caption == "Sněmovna zkrátila lhůtu na 4 roky"
    assert "2 121" in api.texts()[-1] and "2 120" in api.texts()[-1]
    assert _get(conn, rid).telegram_msg_id == r.telegram_msg_id
    # přesně na limitu projde
    bot.handle(_msg("y" * 2120, reply_to=r.telegram_msg_id))
    assert _get(conn, rid).caption == "y" * 2120


@pg
def test_reply_na_nepending_nemeni(bot, conn, api, media_dir):
    rid = _add(conn, media_dir)
    bot.send_next()
    r = _get(conn, rid)
    bot.handle(_cb(f"no:{rid}"))
    bot.handle(_msg("Pozdní úprava", reply_to=r.telegram_msg_id))
    assert _get(conn, rid).caption == "Sněmovna zkrátila lhůtu na 4 roky"


@pg
def test_upravit_posle_pre_a_reply_na_ni_zmeni_popisek(bot, conn, api, media_dir):
    rid = _add(conn, media_dir, caption="A <b> & C")
    bot.send_next()
    api.calls.clear()
    bot.handle(_cb(f"edit:{rid}"))
    assert "answerCallbackQuery" in api.methods()
    body = api.sent()[-1]
    assert body["parse_mode"] == "HTML"
    assert "<pre>A &lt;b&gt; &amp; C</pre>" in body["text"]
    assert "Uprav a pošli jako odpověď na tuto zprávu." in body["text"]
    edit_msg_id = api.next_id
    bot.handle(_msg("Upravený popisek", reply_to=edit_msg_id))
    assert _get(conn, rid).caption == "Upravený popisek"
    assert _buttons(api.sent()[-1]) == [f"ok:{rid}", f"edit:{rid}", f"no:{rid}"]


@pg
def test_nahled_obsahuje_konec_fb_i_ig(bot, conn, api, media_dir):
    from hlidac_reels import bot as botmod
    from hlidac_reels import queue
    from hlidac_reels.caption import IG_ENDING, for_network
    rid = _add(conn, media_dir)
    text = botmod.preview_text(queue.get(conn, rid))
    fb = for_network("Sněmovna zkrátila lhůtu na 4 roky", 221, "fb")
    assert "Facebook:\n" + fb in text
    assert "Instagram" in text and IG_ENDING.strip() in text
    assert text.index("Facebook:") < text.index("Instagram")
    # FB odkaz se v IG části neopakuje
    assert text.count("tisk.php?ct=221") == 1

    # dlouhý náhled: dvě zprávy, tlačítka u druhé
    long_id = _add(conn, media_dir, 2, caption="z" * 4000)
    assert len(botmod.preview_text(queue.get(conn, long_id))) > 4096
    bot.send_next()  # pošle první (krátký)
    bot.handle(_cb(f"no:{rid}"))
    long_msgs = api.sent()[-2:]
    assert all(len(b["text"]) <= 4096 for b in long_msgs)
    assert "reply_markup" not in long_msgs[0]
    assert _buttons(long_msgs[1]) == [f"ok:{long_id}", f"edit:{long_id}", f"no:{long_id}"]
    # odpověď na první část náhledu taky mění popisek
    first_part_id = api.next_id - 1
    bot.handle(_msg("Kratší", reply_to=first_part_id))
    assert _get(conn, long_id).caption == "Kratší"


@pg
def test_video_popisek(bot, conn, api, media_dir):
    rid = _add(conn, media_dir)
    bot.send_next()
    raw = api.sent("sendVideo")[0]["_multipart"]
    assert f"#{rid} · tisk 221 · v3" in raw
    assert str(CHAT) in raw


@pg
def test_schvaleni_po_uprave_publikuje_novy_popisek(bot, conn, api, meta, media_dir):
    from hlidac_reels.caption import for_network
    rid = _add(conn, media_dir)
    bot.send_next()
    bot.handle(_msg("Opravený text", reply_to=_get(conn, rid).telegram_msg_id))
    bot.handle(_cb(f"ok:{rid}"))
    assert _get(conn, rid).status == "published"
    assert meta.fb_captions == [for_network("Opravený text", 221, "fb")]
    assert meta.ig_captions == [for_network("Opravený text", 221, "ig")]


@pg
def test_jen_jedno_pending(bot, conn, api, media_dir):
    from hlidac_reels import queue
    _add(conn, media_dir, 1)
    _add(conn, media_dir, 2)
    bot.send_next()
    bot.send_next()
    assert queue.counts(conn) == {"pending": 1, "queued": 1}
    assert len(api.sent("sendVideo")) == 1


@pg
def test_dalsi_posle_i_kdyz_ceka(bot, conn, api, media_dir):
    from hlidac_reels import queue
    _add(conn, media_dir, 1)
    _add(conn, media_dir, 2)
    bot.send_next()
    bot.handle(_msg("/dalsi"))
    assert queue.counts(conn) == {"pending": 2}
    bot.handle(_msg("/dalsi"))
    assert "Fronta je prázdná." in api.texts()[-1]
    bot.handle(_msg("/fronta"))
    assert "pending: 2" in api.texts()[-1]


@pg
def test_po_restartu_unknown_s_tlacitkem(bot, conn, api, meta, media_dir):
    from hlidac_reels import queue
    rid = _add(conn, media_dir)
    assert queue.transition(conn, rid, "queued", "pending")
    assert queue.transition(conn, rid, "pending", "approved")
    assert queue.transition(conn, rid, "approved", "publishing")
    nxt = _add(conn, media_dir, 2)
    orphan = media_dir / "public" / "stary-token.mp4"
    orphan.parent.mkdir()
    orphan.write_bytes(b"x")
    bot.start()
    assert _get(conn, rid).status == "unknown"
    assert not orphan.exists()
    announce = [b for b in api.sent() if f"retry:{rid}" in _buttons(b)]
    assert len(announce) == 1 and f"#{rid}" in announce[0]["text"]
    # pak pošle další položku ke schválení
    assert _get(conn, nxt).status == "pending"
    assert sum(meta.calls.values()) == 0


@pg
def test_offset_ulozen_a_update_se_neopakuje(bot, conn, api, meta, media_dir):
    from hlidac_reels import queue
    rid = _add(conn, media_dir)
    bot.send_next()
    upd = _cb(f"ok:{rid}")
    api.updates = [upd]
    bot.poll_once()
    assert queue.get_state(conn, "tg_offset") == str(upd["update_id"] + 1)
    gu = api.sent("getUpdates")[-1]
    assert gu["offset"] == 0 and gu["allowed_updates"] == ["message", "callback_query"]
    bot.poll_once()
    assert api.sent("getUpdates")[-1]["offset"] == upd["update_id"] + 1


@pg
def test_chyba_v_handleru_posune_offset(bot, conn, api, media_dir, monkeypatch):
    from hlidac_reels import queue

    def boom(update):
        raise RuntimeError("rozbité")
    monkeypatch.setattr(bot, "handle", boom)
    upd = _msg("/fronta")
    api.updates = [upd]
    bot.poll_once()
    assert queue.get_state(conn, "tg_offset") == str(upd["update_id"] + 1)


@pg
def test_chyba_site_neprozradi_token(bot, api, caplog):
    from hlidac_reels.telegram import TelegramError

    class Stop(Exception):
        pass

    api.fail["getUpdates"] = httpx.ConnectError(f"spojení selhalo https://api.telegram.org/bot{TOKEN}/getUpdates")
    with pytest.raises(TelegramError) as ei:
        bot.tg.get_updates(0)
    assert TOKEN not in str(ei.value) and "SECRET" not in str(ei.value)
    assert ei.value.__cause__ is None

    slept = []

    def sleep(s):
        slept.append(s)
        raise Stop
    caplog.set_level(logging.DEBUG)
    with pytest.raises(Stop):
        bot.loop(sleep=sleep)
    assert slept == [10]
    assert "SECRET" not in caplog.text
    assert "getUpdates" in caplog.text or "ConnectError" in caplog.text


def test_telegram_api_chyba_je_telegram_error(api):
    from hlidac_reels.telegram import Telegram, TelegramError

    def handler(request):
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})
    tg = Telegram(TOKEN, client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(TelegramError) as ei:
        tg.send_text(CHAT, "x")
    assert "chat not found" in str(ei.value) and "SECRET" not in str(ei.value)


@pytest.mark.parametrize("missing", ["META_SYSTEM_TOKEN", "REELS_TELEGRAM_TOKEN", "REELS_TELEGRAM_CHAT_ID",
                                     "REELS_PUBLIC_BASE", "META_PAGE_ID", "META_IG_USER_ID"])
def test_main_chybi_promenna(monkeypatch, capsys, missing):
    from hlidac_reels import bot as botmod
    env = {
        "META_SYSTEM_TOKEN": "meta-secret", "META_PAGE_ID": "1", "META_IG_USER_ID": "2",
        "REELS_TELEGRAM_TOKEN": TOKEN, "REELS_TELEGRAM_CHAT_ID": "4242",
        "REELS_PUBLIC_BASE": PUBLIC_BASE,
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv(missing)
    monkeypatch.setattr(botmod.db, "connect", lambda: pytest.fail("nemá se připojovat"))
    with pytest.raises(SystemExit) as ei:
        botmod.main()
    assert ei.value.code not in (0, None)
    err = capsys.readouterr().err
    assert missing in err and "Chybí" in err
    assert "meta-secret" not in err and "SECRET" not in err


def test_main_spatne_chat_id(monkeypatch, capsys):
    from hlidac_reels import bot as botmod
    for k, v in {"META_SYSTEM_TOKEN": "m", "META_PAGE_ID": "1", "META_IG_USER_ID": "2",
                 "REELS_TELEGRAM_TOKEN": TOKEN, "REELS_TELEGRAM_CHAT_ID": "abc",
                 "REELS_PUBLIC_BASE": PUBLIC_BASE}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(botmod.db, "connect", lambda: pytest.fail("nemá se připojovat"))
    with pytest.raises(SystemExit) as ei:
        botmod.main()
    assert ei.value.code not in (0, None)
    assert "REELS_TELEGRAM_CHAT_ID" in capsys.readouterr().err
