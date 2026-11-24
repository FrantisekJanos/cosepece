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
            f = self.fail[method]
            if isinstance(f, httpx.Response):
                return f
            raise f
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
        cur.execute("TRUNCATE reel_queue RESTART IDENTITY; TRUNCATE reel_state; "
                    "TRUNCATE reel_jobs RESTART IDENTITY")
        created = []
        for name, ddl in _PHP_TABLES.items():
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
            if not cur.fetchone()[0]:
                cur.execute(ddl)
                created.append(name)
    c.commit()
    yield c
    if created:
        # minimální tabulky jen pro testy (v produkci je vlastní PHP); test mohl spojení zavřít
        if c.closed:
            c = db.connect()
        c.rollback()
        with c.cursor() as cur:
            for name in created:
                cur.execute(f"DROP TABLE IF EXISTS {name}")
        c.commit()
    c.close()


_PHP_TABLES = {
    "tisky": "CREATE TABLE tisky (ct integer)",
    "pn": "CREATE TABLE pn (id_dokument integer PRIMARY KEY, cislo integer NOT NULL, ct integer NOT NULL, "
          "id_osoba integer, osoba varchar(120), cas varchar(19))",
    "pn_text": "CREATE TABLE pn_text (id_dokument integer PRIMARY KEY, ct integer NOT NULL, cislo integer NOT NULL, "
               "ai_json text, text text)",
}


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
    api.calls.clear()
    bot.handle(_msg("/fronta"))
    assert "pending: 2" in api.texts()[0]
    # ke každé čekající položce znovu náhled s tlačítky
    assert len([b for b in api.sent() if _buttons(b)]) == 2


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
def test_main_bez_promennych_konci_chybou(monkeypatch, capsys, missing):
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


# --- oprava 1: položky uvízlé v approved, ztráta DB, /fronta s tlačítky ----

@pg
def test_po_restartu_approved_je_failed_s_tlacitkem(bot, conn, api, meta, media_dir):
    from hlidac_reels import queue
    rid = _add(conn, media_dir)
    assert queue.transition(conn, rid, "queued", "pending")
    assert queue.transition(conn, rid, "pending", "approved")
    bot.start()
    r = _get(conn, rid)
    assert r.status == "failed" and r.error == "přerušeno před zveřejněním"
    announce = [b for b in api.sent() if f"retry:{rid}" in _buttons(b)]
    assert len(announce) == 1 and f"#{rid}" in announce[0]["text"]
    assert sum(meta.calls.values()) == 0
    # Zkusit znovu nejdřív dohledá, pak zveřejní
    bot.handle(_cb(f"retry:{rid}"))
    assert _get(conn, rid).status == "published"
    assert meta.calls["fb_find_recent"] == 1 and meta.calls["ig_find_recent"] == 1


@pg
def test_ztrata_db_ukonci_bota(bot, conn, api):
    import psycopg
    conn.close()
    with pytest.raises(SystemExit) as ei:
        bot.loop(sleep=lambda s: pytest.fail("nemá čekat, má skončit"))
    assert ei.value.code not in (0, None)

    # chyba DB uvnitř zpracování zprávy taky ukončí bota (ne zalogovat a pokračovat)
    from hlidac_reels import db
    bot.conn = db.connect()
    api.updates = [_msg("/fronta")]

    def lost(update):
        raise psycopg.OperationalError("server closed the connection unexpectedly")
    bot.handle = lost
    with pytest.raises(SystemExit):
        bot.loop(sleep=lambda s: pytest.fail("nemá čekat"))
    bot.conn.close()


@pg
def test_chyba_cteni_udela_rollback(bot, conn, api, monkeypatch):
    from hlidac_reels import queue
    calls = []
    real = conn.rollback
    monkeypatch.setattr(conn, "rollback", lambda: (calls.append(1), real()))

    def boom(update):
        raise RuntimeError("rozbité")
    monkeypatch.setattr(bot, "handle", boom)
    api.updates = [_msg("/fronta")]
    bot.poll_once()
    assert calls
    assert queue.get_state(conn, "tg_offset") is not None


@pg
def test_fronta_ukaze_failed_unknown_s_tlacitkem(bot, conn, api, media_dir):
    from hlidac_reels import queue
    f = _add(conn, media_dir, 1)
    u = _add(conn, media_dir, 2)
    _add(conn, media_dir, 3)
    for rid in (f, u):
        queue.transition(conn, rid, "queued", "pending")
        queue.transition(conn, rid, "pending", "approved")
        queue.transition(conn, rid, "approved", "publishing")
    queue.transition(conn, f, "publishing", "failed", error="(#100) chybný parametr")
    queue.transition(conn, u, "publishing", "unknown", error="síťová chyba")
    api.calls.clear()
    bot.handle(_msg("/fronta"))
    msgs = api.sent()
    assert "failed: 1" in msgs[0]["text"] and "unknown: 1" in msgs[0]["text"]
    by_btn = {tuple(_buttons(b)): b["text"] for b in msgs[1:]}
    assert ("retry:%d" % f,) in by_btn and ("retry:%d" % u,) in by_btn
    assert "chybný parametr" in by_btn[("retry:%d" % f,)] and "tisk 221" in by_btn[("retry:%d" % f,)]
    assert len(msgs) == 3


@pg
def test_callback_odpovi_i_pri_chybe_db(bot, conn, api, media_dir, monkeypatch):
    import psycopg
    from hlidac_reels import queue
    rid = _add(conn, media_dir)
    bot.send_next()
    api.calls.clear()

    def broken(*a, **kw):
        raise psycopg.errors.QueryCanceled("zrušeno")
    monkeypatch.setattr(queue, "transition", broken)
    monkeypatch.setattr(queue, "approve", broken)  # Schválit jde přes queue.approve
    with pytest.raises(psycopg.Error):
        bot.handle(_cb(f"ok:{rid}"))
    assert api.methods() == ["answerCallbackQuery"]


@pg
def test_start_se_opakuje_pri_vypadku_telegramu(bot, conn, api, media_dir):
    """Výpadek Telegramu při startu neblokuje polling; video se pošle samo, až Telegram ožije."""
    from hlidac_reels import queue

    class Stop(Exception):
        pass
    rid = _add(conn, media_dir)
    api.fail["sendVideo"] = httpx.ConnectError("down")
    api.fail["getUpdates"] = httpx.ConnectError("down")
    t = iter(range(0, 10_000, 100))
    bot.clock = lambda: next(t)
    slept = []

    def sleep(s):
        slept.append(s)
        if len(slept) == 1:
            del api.fail["sendVideo"]
            return
        raise Stop
    with pytest.raises(Stop):
        bot.loop(sleep=sleep)
    assert slept == [10, 10]
    r = queue.get(conn, rid)
    # síťová chyba položku nevyřadí, další pokus ji pošle
    assert r.status == "pending" and r.error is None


# --- finální vlna oprav ------------------------------------------------------

@pg
def test_neodeslatelne_video_vyrazeno_a_pokracuje(bot, conn, api, media_dir):
    from hlidac_reels import queue
    bad = _add(conn, media_dir, 1)
    Path(_get(conn, bad).video_path).unlink()
    good = _add(conn, media_dir, 2)
    bot.send_next()
    r = _get(conn, bad)
    assert r.status == "rejected" and r.error.startswith("nelze poslat do Telegramu")
    assert any(f"#{bad} vyřazeno, video na výšku nelze poslat" in t for t in api.texts())
    assert _get(conn, good).status == "pending"
    assert queue.counts(conn) == {"rejected": 1, "pending": 1}


@pg
def test_telegram_odmitne_video_omezeny_pocet_pokusu(bot, conn, api, media_dir):
    from hlidac_reels import queue
    ids = [_add(conn, media_dir, n) for n in range(1, 5)]
    api.fail["sendVideo"] = httpx.Response(413, json={"ok": False, "description": "Request Entity Too Large"})
    bot.send_next()
    assert [_get(conn, i).status for i in ids] == ["rejected", "rejected", "rejected", "queued"]
    assert "Request Entity Too Large" in _get(conn, ids[0]).error
    assert queue.pending_count(conn) == 0


@pg
def test_vypadek_site_video_nevyradi(bot, conn, api, media_dir):
    from hlidac_reels.telegram import TelegramError
    rid = _add(conn, media_dir)
    api.fail["sendVideo"] = httpx.ConnectError("down")
    with pytest.raises(TelegramError):
        bot.send_next()
    assert _get(conn, rid).status == "queued"
    api.fail["sendVideo"] = httpx.Response(502, json={"ok": False, "description": "Bad Gateway"})
    with pytest.raises(TelegramError):
        bot.send_next()
    assert _get(conn, rid).status == "queued"
    # start() chybu jen zaloguje
    bot.start()
    assert _get(conn, rid).status == "queued"


@pg
def test_nove_video_dorazi_samo(bot, conn, api, media_dir):
    from hlidac_reels import queue
    now = [1000.0]
    bot.clock = lambda: now[0]
    bot.start()  # prázdná fronta
    rid = _add(conn, media_dir, 1)
    bot.maybe_send_next()
    assert _get(conn, rid).status == "pending"
    # nejvýš jednou za 50 s
    queue.transition(conn, rid, "pending", "rejected")
    r2 = _add(conn, media_dir, 2)
    now[0] += 10
    bot.maybe_send_next()
    assert _get(conn, r2).status == "queued"
    now[0] += 50
    bot.maybe_send_next()
    assert _get(conn, r2).status == "pending"


@pg
def test_reply_selhani_nahledu_nic_nezmeni(bot, conn, api, media_dir):
    rid = _add(conn, media_dir)
    bot.send_next()
    r = _get(conn, rid)
    api.calls.clear()
    api.fail["sendMessage"] = httpx.ConnectError("down")
    bot.handle(_msg("Nový", reply_to=r.telegram_msg_id))
    r2 = _get(conn, rid)
    assert r2.caption == r.caption and r2.telegram_msg_id == r.telegram_msg_id
    assert "editMessageReplyMarkup" not in api.methods()


@pg
def test_reply_tlacitka_stareho_nahledu_az_po_novem(bot, conn, api, media_dir):
    rid = _add(conn, media_dir)
    bot.send_next()
    old = _get(conn, rid).telegram_msg_id
    api.calls.clear()
    bot.handle(_msg("Nový", reply_to=old))
    methods = api.methods()
    assert methods.index("sendMessage") < methods.index("editMessageReplyMarkup")


@pg
def test_fronta_znovu_posle_nahled_a_vypise_ostatni(bot, conn, api, media_dir):
    from datetime import datetime, timedelta, timezone
    from hlidac_reels import queue
    p = _add(conn, media_dir, 1)
    bot.send_next()
    old_preview = _get(conn, p).telegram_msg_id
    a = _add(conn, media_dir, 2)
    queue.transition(conn, a, "queued", "approved")
    pub = _add(conn, media_dir, 3)
    queue.transition(conn, pub, "queued", "publishing",
                     decided_at=datetime.now(timezone.utc) - timedelta(minutes=20))
    fresh = _add(conn, media_dir, 4)
    queue.transition(conn, fresh, "queued", "publishing", decided_at=datetime.now(timezone.utc))
    api.calls.clear()
    bot.handle(_msg("/fronta"))
    msgs = api.sent()
    pend = [b for b in msgs if _buttons(b) == [f"ok:{p}", f"edit:{p}", f"no:{p}"]]
    assert len(pend) == 1
    assert _get(conn, p).telegram_msg_id not in (None, old_preview)
    appr = [b for b in msgs if f"#{a} " in b["text"]]
    assert len(appr) == 1 and "čeká na restart" in appr[0]["text"] and not _buttons(appr[0])
    pubm = [b for b in msgs if f"#{pub} " in b["text"]]
    assert len(pubm) == 1 and "možná přerušeno" in pubm[0]["text"] and not _buttons(pubm[0])
    assert not [b for b in msgs if f"#{fresh} " in b["text"]]


@pg
def test_upravit_selhani_odeslani_nevyhodi(bot, conn, api, media_dir):
    rid = _add(conn, media_dir)
    bot.send_next()
    api.fail["sendMessage"] = httpx.ConnectError("down")
    bot.handle(_cb(f"edit:{rid}"))  # nevyhodí
    assert "answerCallbackQuery" in api.methods()


# --- Threads (náhled, výsledek, token, doplnění) -----------------------------

TH_LINK = "https://www.threads.net/@cosepece/post/T1"


class FakeThreads:
    """Falešný klient Threads pro bota a doplnění (bez sítě)."""

    def __init__(self, media_dir: Path | None = None):
        self.calls: Counter[str] = Counter()
        self.fail: dict[str, Exception] = {}
        self.media_dir = media_dir
        self.texts: list[str] = []
        self.public_existed: list[bool] = []
        self.found: tuple[str, str] | None = None
        self.find_args: list = []
        self.link = TH_LINK
        self.refresh_result = ("novy-token-threads", 5_184_000)
        self.refreshed: list[str] = []
        self._known: set[str] = set()

    def _call(self, name):
        self.calls[name] += 1
        if name in self.fail:
            raise self.fail[name]

    def create_video(self, video_url, text):
        self.texts.append(text)
        if self.media_dir is not None:
            name = video_url.rsplit("/", 1)[1]
            self.public_existed.append((self.media_dir / "public" / name).exists())
        self._call("create_video")
        return "TC1"

    def wait(self, container_id, timeout=600, interval=10):
        self._call("wait")

    def publish(self, container_id):
        self._call("publish")
        return "T1", self.link

    def find_recent(self, text_prefix, since):
        self.find_args.append((text_prefix, since))
        self._call("find_recent")
        return self.found

    def refresh_token(self, token):
        self.refreshed.append(token)
        self._call("refresh_token")
        return self.refresh_result


def _make_bot(conn, api, meta, media_dir, threads=None, env_token=None):
    from hlidac_reels.bot import Bot
    from hlidac_reels.telegram import Telegram
    tg = Telegram(TOKEN, client=httpx.Client(transport=httpx.MockTransport(api)))
    return Bot(conn, tg, meta, CHAT, str(media_dir), PUBLIC_BASE, threads=threads,
               threads_env_token=env_token)


def _published(conn, media_dir, n=1):
    """Položka zveřejněná jen na FB a IG (jako videa 1–3 před Threads)."""
    from datetime import datetime, timezone
    from hlidac_reels import queue
    rid = _add(conn, media_dir, n)
    now = datetime.now(timezone.utc)
    assert queue.transition(conn, rid, "queued", "published", sent_at=now, published_at=now,
                            ig_media_id="M1", ig_permalink="https://www.instagram.com/reel/M1/",
                            fb_video_id="V1", fb_permalink="https://www.facebook.com/reel/V1")
    return rid


def _get_pending(conn):
    from hlidac_reels import queue
    return queue.by_status(conn, ("pending",))[0].id


@pg
def test_nahled_obsahuje_threads(conn, api, meta, media_dir):
    from hlidac_reels import queue
    from hlidac_reels.caption import for_network
    caption = "Sněmovna zkrátila lhůtu na 4 roky. Platí i zpětně.\n\nDruhý odstavec jen pro FB a IG."
    bot = _make_bot(conn, api, meta, media_dir, threads=FakeThreads())
    rid = _add(conn, media_dir, caption=caption)
    bot.send_next()
    text = api.sent()[-1]["text"]
    assert "Threads (video na výšku):\n" + for_network(caption, 221, "threads") in text
    assert text.index("Instagram") < text.index("Threads (video")
    assert _buttons(api.sent()[-1]) == [f"ok:{rid}", f"edit:{rid}", f"no:{rid}"]
    # úprava popisku změní i text pro Threads
    bot.handle(_msg("Nový text.", reply_to=queue.get(conn, rid).telegram_msg_id))
    assert "Threads (video na výšku):\n" + for_network("Nový text.", 221, "threads") in api.sent()[-1]["text"]


@pg
def test_vysledek_tri_odkazy(conn, api, meta, media_dir):
    import dataclasses
    from hlidac_reels.messages import result_text
    th = FakeThreads(media_dir)
    bot = _make_bot(conn, api, meta, media_dir, threads=th)
    rid = _add(conn, media_dir)
    bot.send_next()
    bot.handle(_cb(f"ok:{rid}"))
    r = _get(conn, rid)
    assert r.status == "published" and r.threads_media_id == "T1"
    last = api.sent()[-1]
    lines = last["text"].splitlines()
    assert "Facebook: https://www.facebook.com/reel/V1" in lines
    assert "Instagram: https://www.instagram.com/reel/M1/" in lines
    assert f"Threads: {TH_LINK}" in lines
    assert not _buttons(last)
    assert th.calls["create_video"] == 1
    # prázdný odkaz Threads
    text = result_text(dataclasses.replace(r, threads_permalink=""), threads=True)
    assert "Threads: odkaz se nepodařilo načíst" in text.splitlines()


@pg
def test_vysledek_threads_chyba_s_tlacitkem(conn, api, meta, media_dir):
    from hlidac_reels.threads import ThreadsError
    th = FakeThreads(media_dir)
    th.fail["create_video"] = ThreadsError("Invalid parameter (video_url)", status=400)
    bot = _make_bot(conn, api, meta, media_dir, threads=th)
    rid = _add(conn, media_dir)
    bot.send_next()
    bot.handle(_cb(f"ok:{rid}"))
    r = _get(conn, rid)
    assert r.status == "failed" and r.fb_video_id == "V1" and r.ig_media_id == "M1"
    last = api.sent()[-1]
    lines = last["text"].splitlines()
    assert "Facebook: https://www.facebook.com/reel/V1" in lines
    assert "Instagram: https://www.instagram.com/reel/M1/" in lines
    assert "Threads: nepublikováno – Invalid parameter (video_url)" in lines
    assert _buttons(last) == [f"retry:{rid}"]
    # Zkusit znovu: nejdřív dohledá, pak publikuje jen Threads
    del th.fail["create_video"]
    bot.handle(_cb(f"retry:{rid}"))
    r = _get(conn, rid)
    assert r.status == "published" and r.threads_media_id == "T1"
    assert th.calls["find_recent"] == 1 and th.calls["create_video"] == 2
    assert meta.calls["fb_upload_reel"] == 1 and meta.calls["ig_create"] == 1
    assert f"Threads: {TH_LINK}" in api.sent()[-1]["text"].splitlines()


@pg
def test_threads_bez_tokenu_selze_viditelne(conn, api, meta, media_dir):
    """THREADS_USER_ID je, token chybí: FB a IG vyjdou, Threads failed s tlačítkem, API se nevolá."""
    from hlidac_reels import bot as botmod
    from hlidac_reels.threads import Threads

    def no_network(request):
        pytest.fail("bez tokenu se Threads API nesmí volat")
    th = Threads("TH_USER", lambda: botmod.threads_token(conn),
                 client=httpx.Client(transport=httpx.MockTransport(no_network)))
    bot = _make_bot(conn, api, meta, media_dir, threads=th)
    _add(conn, media_dir)
    bot.start()
    rid = _get_pending(conn)
    bot.handle(_cb(f"ok:{rid}"))
    r = _get(conn, rid)
    assert r.status == "failed" and r.fb_video_id == "V1" and r.ig_media_id == "M1"
    last = api.sent()[-1]
    assert any(line.startswith("Threads: nepublikováno – chybí token Threads")
               for line in last["text"].splitlines())
    assert _buttons(last) == [f"retry:{rid}"]


@pg
def test_threads_neni_nastavene_bot_bezi(conn, api, meta, media_dir, monkeypatch):
    from hlidac_reels import publish, queue
    seen = []
    real_run = publish.run

    def spy(*a, **kw):
        seen.append(kw.get("threads", "chybí"))
        return real_run(*a, **kw)
    monkeypatch.setattr(publish, "run", spy)
    bot = _make_bot(conn, api, meta, media_dir, threads=None, env_token="tok-bez-uzivatele")
    rid = _add(conn, media_dir)
    bot.start()
    assert "Threads" not in api.sent()[-1]["text"]
    bot.handle(_cb(f"ok:{rid}"))
    assert seen == [None]
    assert _get(conn, rid).status == "published"
    assert "Threads" not in api.sent()[-1]["text"]
    # bez THREADS_USER_ID se token neukládá ani neprodlužuje
    bot.maybe_refresh_threads_token()
    assert queue.get_state(conn, "threads_token") is None


def _main_env(monkeypatch, **extra):
    env = {"META_SYSTEM_TOKEN": "m", "META_PAGE_ID": "1", "META_IG_USER_ID": "2",
           "REELS_TELEGRAM_TOKEN": TOKEN, "REELS_TELEGRAM_CHAT_ID": "4242",
           "REELS_PUBLIC_BASE": PUBLIC_BASE}
    for k in ("THREADS_USER_ID", "THREADS_TOKEN", "THREADS_GRAPH_VERSION"):
        monkeypatch.delenv(k, raising=False)
    for k, v in {**env, **extra}.items():
        monkeypatch.setenv(k, v)


def test_main_threads_jen_s_user_id(monkeypatch):
    from hlidac_reels import bot as botmod
    from hlidac_reels.threads import Threads
    made = []
    monkeypatch.setattr(botmod.db, "connect", lambda: object())
    monkeypatch.setattr(botmod.Bot, "loop", lambda self: made.append(self))
    _main_env(monkeypatch)
    botmod.main()
    assert made[-1].threads is None
    _main_env(monkeypatch, THREADS_USER_ID="TH_USER", THREADS_TOKEN="env-token-threads")
    botmod.main()
    b = made[-1]
    assert isinstance(b.threads, Threads) and b.threads.version == "v1.0"
    assert b.threads_env_token == "env-token-threads"
    assert "env-token-threads" in b._secrets
    _main_env(monkeypatch, THREADS_USER_ID="TH_USER", THREADS_GRAPH_VERSION="v2.0")
    botmod.main()
    assert made[-1].threads.version == "v2.0" and made[-1].threads_env_token is None


@pg
def test_token_z_env_do_state(conn, api, meta, media_dir):
    from datetime import datetime
    from hlidac_reels import bot as botmod
    from hlidac_reels import queue
    bot = _make_bot(conn, api, meta, media_dir, threads=FakeThreads(), env_token="env-tok")
    assert botmod.threads_token(conn) == ""
    bot.start()
    assert queue.get_state(conn, "threads_token") == "env-tok"
    assert botmod.threads_token(conn) == "env-tok"
    stamp = datetime.fromisoformat(queue.get_state(conn, "threads_token_refreshed_at"))
    assert stamp.tzinfo is not None
    # uložený (třeba prodloužený) token env nepřepíše
    queue.set_state(conn, "threads_token", "prodlouzeny")
    bot2 = _make_bot(conn, api, meta, media_dir, threads=FakeThreads(), env_token="env-tok")
    bot2.start()
    assert botmod.threads_token(conn) == "prodlouzeny"
    assert "env-tok" not in " ".join(api.texts()) and "prodlouzeny" not in " ".join(api.texts())


def _set_token(conn, token, age_days, now):
    from datetime import timedelta
    from hlidac_reels import queue
    queue.set_state(conn, "threads_token", token)
    queue.set_state(conn, "threads_token_refreshed_at", (now - timedelta(days=age_days)).isoformat())


@pg
def test_refresh_tokenu_jednou_denne(conn, api, meta, media_dir, caplog):
    from datetime import datetime, timedelta, timezone
    from hlidac_reels import queue
    th = FakeThreads()
    bot = _make_bot(conn, api, meta, media_dir, threads=th)
    now = [datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)]
    bot.now = lambda: now[0]
    # mladší než 7 dní: nic
    _set_token(conn, "stary-token-threads", 6, now[0])
    bot.maybe_refresh_threads_token()
    assert th.calls["refresh_token"] == 0
    # starší než 7 dní: prodlouží jednou
    caplog.set_level(logging.DEBUG)
    _set_token(conn, "stary-token-threads", 8, now[0])
    bot.maybe_refresh_threads_token()
    bot.maybe_refresh_threads_token()
    assert th.refreshed == ["stary-token-threads"]
    assert queue.get_state(conn, "threads_token") == "novy-token-threads"
    assert datetime.fromisoformat(queue.get_state(conn, "threads_token_refreshed_at")) == now[0]
    # za den je token pořád mladý: nic
    now[0] += timedelta(days=1, minutes=1)
    bot.maybe_refresh_threads_token()
    assert th.calls["refresh_token"] == 1
    assert "novy-token-threads" not in caplog.text and "stary-token-threads" not in caplog.text
    assert api.texts() == []  # úspěch je tichý


@pg
def test_refresh_chyba_upozorni_jednou(conn, api, meta, media_dir, caplog):
    from datetime import datetime, timedelta, timezone
    from hlidac_reels import queue
    from hlidac_reels.threads import ThreadsError
    th = FakeThreads()
    th.fail["refresh_token"] = ThreadsError(
        "Error validating access token stary-token-threads", status=400)
    bot = _make_bot(conn, api, meta, media_dir, threads=th)
    now = [datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)]
    bot.now = lambda: now[0]
    _set_token(conn, "stary-token-threads", 10, now[0])
    caplog.set_level(logging.DEBUG)
    for _ in range(3):
        bot.maybe_refresh_threads_token()
        now[0] += timedelta(hours=2)
    assert th.calls["refresh_token"] == 1
    warn = [t for t in api.texts() if "Threads" in t]
    assert len(warn) == 1 and "prodloužení" in warn[0].lower()
    assert "stary-token-threads" not in warn[0] and "stary-token-threads" not in caplog.text
    assert queue.get_state(conn, "threads_token") == "stary-token-threads"
    # další den nový pokus a nejvýš jedna další zpráva
    now[0] += timedelta(days=1)
    bot.maybe_refresh_threads_token()
    bot.maybe_refresh_threads_token()
    assert th.calls["refresh_token"] == 2
    assert len([t for t in api.texts() if "Threads" in t]) == 2


@pg
def test_refresh_bezi_ve_smycce(conn, api, meta, media_dir):
    from datetime import datetime, timezone
    th = FakeThreads()
    bot = _make_bot(conn, api, meta, media_dir, threads=th)
    now = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)
    bot.now = lambda: now
    _set_token(conn, "stary-token-threads", 30, now)

    class Stop(Exception):
        pass
    api.fail["getUpdates"] = httpx.ConnectError("down")

    def sleep(s):
        raise Stop
    with pytest.raises(Stop):
        bot.loop(sleep=sleep)
    assert th.calls["refresh_token"] == 1


# --- reels-threads-backfill --------------------------------------------------

def _backfill(monkeypatch, media_dir, th, argv, token="tok-threads-env", user="TH_USER"):
    from hlidac_reels import backfill
    for k in ("THREADS_USER_ID", "THREADS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    if user:
        monkeypatch.setenv("THREADS_USER_ID", user)
    if token:
        monkeypatch.setenv("THREADS_TOKEN", token)
    monkeypatch.setenv("REELS_MEDIA_DIR", str(media_dir))
    monkeypatch.setenv("REELS_PUBLIC_BASE", PUBLIC_BASE)
    made = []

    def factory(user_id, token_provider, version="v1.0", **kw):
        made.append((user_id, token_provider(), version))
        return th
    monkeypatch.setattr(backfill, "Threads", factory)
    return backfill.main(argv), made


def _public_files(media_dir):
    public = media_dir / "public"
    return list(public.iterdir()) if public.is_dir() else []


@pg
def test_backfill_publikuje_jen_threads(conn, media_dir, monkeypatch, capsys):
    from hlidac_reels.caption import for_network
    rid = _published(conn, media_dir)
    th = FakeThreads(media_dir)
    code, made = _backfill(monkeypatch, media_dir, th, ["--id", str(rid)])
    assert code == 0
    assert made == [("TH_USER", "tok-threads-env", "v1.0")]
    r = _get(conn, rid)
    assert r.status == "published" and r.threads_media_id == "T1" and r.threads_permalink == TH_LINK
    assert r.fb_video_id == "V1" and r.ig_media_id == "M1"
    assert th.calls["find_recent"] == 1 and th.calls["create_video"] == 1 and th.calls["publish"] == 1
    assert th.texts == [for_network(r.caption, 221, "threads")]
    assert th.find_args[0] == (th.texts[0][:80], r.published_at)
    # veřejná kopie existovala při vytvoření kontejneru a pak zmizela
    assert th.public_existed == [True]
    assert _public_files(media_dir) == []
    out = capsys.readouterr()
    assert TH_LINK in out.out and "tok-threads-env" not in out.out + out.err
    # druhé spuštění nic nezveřejní
    code, _ = _backfill(monkeypatch, media_dir, th, ["--id", str(rid)])
    assert code == 1 and th.calls["create_video"] == 1


@pg
def test_backfill_najde_existujici(conn, media_dir, monkeypatch, capsys):
    from hlidac_reels import queue
    rid = _published(conn, media_dir)
    queue.set_state(conn, "threads_token", "tok-ze-state")
    th = FakeThreads(media_dir)
    th.found = ("T9", "https://www.threads.net/@cosepece/post/T9")
    code, made = _backfill(monkeypatch, media_dir, th, ["--id", str(rid)], token="tok-env")
    assert code == 0
    assert made[0][1] == "tok-ze-state"  # token z reel_state má přednost (může být prodloužený)
    r = _get(conn, rid)
    assert r.status == "published" and r.threads_media_id == "T9"
    assert th.calls["create_video"] == 0
    assert _public_files(media_dir) == []
    assert "post/T9" in capsys.readouterr().out


@pg
def test_backfill_odmitne_neschvalene(conn, media_dir, monkeypatch, capsys):
    from hlidac_reels import queue
    th = FakeThreads(media_dir)
    pending = _add(conn, media_dir, 1)
    queue.transition(conn, pending, "queued", "pending")
    failed = _add(conn, media_dir, 2)
    queue.transition(conn, failed, "queued", "failed", fb_video_id="V2")
    for rid in (pending, failed, 999):
        code, _ = _backfill(monkeypatch, media_dir, th, ["--id", str(rid)])
        assert code == 1
    assert sum(th.calls.values()) == 0
    assert _get(conn, pending).status == "pending" and _get(conn, failed).status == "failed"
    assert "published" in capsys.readouterr().err
    # chybí konfigurace Threads
    rid = _published(conn, media_dir, 3)
    code, _ = _backfill(monkeypatch, media_dir, th, ["--id", str(rid)], user=None)
    assert code == 1 and "THREADS_USER_ID" in capsys.readouterr().err
    code, _ = _backfill(monkeypatch, media_dir, th, ["--id", str(rid)], token=None)
    assert code == 1 and "token" in capsys.readouterr().err.lower()
    assert sum(th.calls.values()) == 0


@pg
def test_backfill_chyba_threads_kod_1_a_uklid(conn, media_dir, monkeypatch, capsys):
    from hlidac_reels.threads import ThreadsError
    rid = _published(conn, media_dir)
    th = FakeThreads(media_dir)
    th.fail["wait"] = ThreadsError("kontejner Threads TC1 skončil ve stavu ERROR", status=400)
    code, _ = _backfill(monkeypatch, media_dir, th, ["--id", str(rid)])
    assert code == 1
    r = _get(conn, rid)
    assert r.status == "published" and r.threads_media_id is None and r.threads_container_id == "TC1"
    assert _public_files(media_dir) == []
    assert "ERROR" in capsys.readouterr().err


# --- opravy po review větve ---------------------------------------------------

def _dropped(api) -> list[int]:
    return [b["message_id"] for m, b in api.calls if m == "editMessageReplyMarkup"]


@pg
def test_fronta_novy_nahled_vezme_staremu_tlacitka(bot, conn, api, media_dir):
    rid = _add(conn, media_dir)
    bot.send_next()
    old = _get(conn, rid).telegram_msg_id
    api.calls.clear()
    bot.handle(_msg("/fronta"))
    new = _get(conn, rid).telegram_msg_id
    assert new not in (None, old)
    assert _dropped(api) == [old]
    # když se nový náhled nepošle, starý si tlačítka nechá
    api.calls.clear()
    api.fail["sendMessage"] = httpx.ConnectError("down")
    bot.handle(_msg("/fronta"))
    assert _get(conn, rid).telegram_msg_id == new and _dropped(api) == []


@pg
def test_start_po_zapnuti_threads_obnovi_nahledy(conn, api, meta, media_dir):
    from hlidac_reels import queue
    from hlidac_reels.caption import for_network
    # náhled poslaný ještě bez Threads
    old_bot = _make_bot(conn, api, meta, media_dir)
    rid = _add(conn, media_dir)
    old_bot.send_next()
    old = _get(conn, rid).telegram_msg_id
    assert "Threads" not in api.sent()[-1]["text"]
    api.calls.clear()
    bot = _make_bot(conn, api, meta, media_dir, threads=FakeThreads())
    bot.start()
    r = _get(conn, rid)
    assert r.telegram_msg_id not in (None, old)
    preview = [b for b in api.sent() if _buttons(b) == [f"ok:{rid}", f"edit:{rid}", f"no:{rid}"]]
    assert len(preview) == 1
    assert "Threads (video na výšku):\n" + for_network(r.caption, 221, "threads") in preview[0]["text"]
    assert _dropped(api) == [old]
    assert queue.get_state(conn, "preview_has_threads") == "1"
    # další start už nic znovu neposílá
    api.calls.clear()
    _make_bot(conn, api, meta, media_dir, threads=FakeThreads()).start()
    assert api.sent() == [] and _dropped(api) == []


@pg
def test_start_bez_threads_nahledy_neobnovuje(conn, api, meta, media_dir):
    from hlidac_reels import queue
    bot = _make_bot(conn, api, meta, media_dir)
    rid = _add(conn, media_dir)
    bot.send_next()
    old = _get(conn, rid).telegram_msg_id
    api.calls.clear()
    _make_bot(conn, api, meta, media_dir).start()
    assert _get(conn, rid).telegram_msg_id == old and api.sent() == []
    assert queue.get_state(conn, "preview_has_threads") is None


@pg
def test_start_obnova_nahledu_selze_zkusi_znovu(conn, api, meta, media_dir):
    from hlidac_reels import queue
    _add(conn, media_dir)
    _make_bot(conn, api, meta, media_dir).send_next()
    api.fail["sendMessage"] = httpx.ConnectError("down")
    _make_bot(conn, api, meta, media_dir, threads=FakeThreads()).start()
    assert queue.get_state(conn, "preview_has_threads") is None
    del api.fail["sendMessage"]
    _make_bot(conn, api, meta, media_dir, threads=FakeThreads()).start()
    assert queue.get_state(conn, "preview_has_threads") == "1"


@pg
def test_backfill_zamek(conn, media_dir, monkeypatch, capsys):
    from hlidac_reels import backfill, db
    rid = _published(conn, media_dir)
    other = db.connect()
    try:
        with other.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s, %s)", (backfill.LOCK_CLASS, rid))
        other.commit()
        th = FakeThreads(media_dir)
        code, _ = _backfill(monkeypatch, media_dir, th, ["--id", str(rid)])
        assert code == 1 and "backfill už běží" in capsys.readouterr().err
        assert sum(th.calls.values()) == 0
    finally:
        other.close()
    # po uvolnění zámku projde a zámek po sobě uvolní
    code, _ = _backfill(monkeypatch, media_dir, th, ["--id", str(rid)])
    assert code == 0
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s, %s)", (backfill.LOCK_CLASS, rid))
        assert cur.fetchone()[0] is True
        cur.execute("SELECT pg_advisory_unlock(%s, %s)", (backfill.LOCK_CLASS, rid))
    conn.commit()


def test_duvod_threads_se_strednikem():
    import dataclasses
    from datetime import datetime, timezone
    from hlidac_reels.messages import result_text
    from hlidac_reels.queue import Reel
    base = Reel(*([None] * len(dataclasses.fields(Reel))))
    reel = dataclasses.replace(
        base, id=7, status="failed", caption="x", attempts=1, created_at=datetime.now(timezone.utc),
        fb_video_id="V1", fb_permalink="https://www.facebook.com/reel/V1",
        ig_media_id="M1", ig_permalink="https://www.instagram.com/reel/M1/",
        error="Threads: Invalid parameter; video_url nelze stáhnout")
    lines = result_text(reel, threads=True).splitlines()
    assert "Threads: nepublikováno – Invalid parameter; video_url nelze stáhnout" in lines
    assert not any(line.startswith("Chyba:") for line in lines)
    # i s jinou chybou před ní (publish.run dává Threads na konec)
    reel = dataclasses.replace(reel, fb_video_id=None,
                               error="(#100) FB chyba; detail; Threads: a; b")
    lines = result_text(reel, threads=True).splitlines()
    assert "Threads: nepublikováno – a; b" in lines
    assert "Chyba: (#100) FB chyba; detail" in lines


# --- sady variant z generátoru (část 2) ---------------------------------------

_set_seq = iter(range(1, 10_000))


def _add_set(conn, media_dir: Path, set_id: int, n: int = 3, ct: int = 221, source: str = "fresh") -> list[int]:
    """Sada n variant: 1 a 3 template_v3, 2 template_v2."""
    from hlidac_reels import queue
    ids = []
    for v in range(1, n + 1):
        k = next(_set_seq)
        video = media_dir / "videos" / f"s{set_id}-{v}-{k}.mp4"
        video.write_bytes(b"fake set mp4 " + str(k).encode())
        v3 = v != 2
        ids.append(queue.add(conn, video_path=str(video), video_sha256=f"set{set_id}-{v}-{k}",
                             caption=f"Varianta {v} řádek 1\nřádek 2 varianty {v}\nřádek 3 se nevypíše",
                             ct=ct, format="vysledek" if v3 else "v2", source=source, set_id=set_id,
                             variant=v, template="template_v3" if v3 else "template_v2"))
    return ids


def _set_msg(api) -> dict:
    """Poslední zpráva s tlačítky pick:."""
    return [b for b in api.sent() if any(d.startswith("pick:") for d in _buttons(b))][-1]


def _set_msg_id(conn, rid) -> int:
    return _get(conn, rid).telegram_msg_id


@pg
def test_sada_se_posle_najednou(bot, conn, api, media_dir):
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    videos = api.sent("sendVideo")
    assert len(videos) == 3
    assert "#7 · varianta 1 · v3 vysledek" in videos[0]["_multipart"]
    assert "#7 · varianta 2 · v2" in videos[1]["_multipart"]
    msgs = [b for b in api.sent() if _buttons(b)]
    assert len(msgs) == 1
    assert _buttons(msgs[0]) == [f"pick:{i}" for i in ids] + ["doneset:7"]
    text = msgs[0]["text"]
    assert "tisk 221" in text and "Varianta 2 řádek 1\nřádek 2 varianty 2" in text
    assert "řádek 3 se nevypíše" not in text
    reels = [_get(conn, i) for i in ids]
    assert {r.status for r in reels} == {"pending"}
    assert len({r.telegram_msg_id for r in reels}) == 1 and reels[0].telegram_msg_id
    assert len({r.telegram_video_msg_id for r in reels}) == 3
    assert all(r.sent_at is not None for r in reels)


def _edits(api) -> list[tuple[int, list[str]]]:
    """Úpravy tlačítek: (message_id, callback_data); prázdný seznam = tlačítka odebrána."""
    return [(b["message_id"], _buttons(b)) for m, b in api.calls if m == "editMessageReplyMarkup"]


def _previews(api) -> list[dict]:
    return [b for b in api.sent() if any(d.startswith("ok:") for d in _buttons(b))]


@pg
def test_vybrat_vice_variant(bot, conn, api, media_dir):
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    set_msg = _set_msg_id(conn, ids[0])
    api.calls.clear()
    bot.handle(_cb(f"pick:{ids[0]}", msg_id=set_msg))
    # ostatní varianty dál čekají, zpráva sady přijde jen o tlačítko vybrané
    assert [_get(conn, i).status for i in ids] == ["pending"] * 3
    assert _edits(api) == [(set_msg, [f"pick:{ids[1]}", f"pick:{ids[2]}", "doneset:7"])]
    assert [_buttons(b)[0] for b in _previews(api)] == [f"ok:{ids[0]}"]
    assert _get(conn, ids[0]).telegram_msg_id != set_msg
    assert {_get(conn, i).telegram_msg_id for i in ids[1:]} == {set_msg}
    api.calls.clear()
    bot.handle(_cb(f"pick:{ids[2]}", msg_id=set_msg))
    assert _edits(api) == [(set_msg, [f"pick:{ids[1]}", "doneset:7"])]
    assert [_buttons(b)[0] for b in _previews(api)] == [f"ok:{ids[2]}"]
    # znovu Vybrat u už vybrané varianty nic nepošle
    api.calls.clear()
    bot.handle(_cb(f"pick:{ids[0]}", msg_id=set_msg))
    assert _previews(api) == [] and _edits(api) == []
    # Hotovo zamítne zbylou nevybranou, vybrané čekají na schválení
    bot.handle(_cb("doneset:7", msg_id=set_msg))
    assert [_get(conn, i).status for i in ids] == ["pending", "rejected", "pending"]
    assert (set_msg, []) in _edits(api)
    assert "Sada #7: zbylé varianty zamítnuty." in api.texts()


@pg
def test_vybrat_posledni_variantu_odebere_tlacitka(bot, conn, api, media_dir):
    ids = _add_set(conn, media_dir, 7, n=2)
    bot.send_next()
    set_msg = _set_msg_id(conn, ids[0])
    bot.handle(_cb(f"pick:{ids[1]}", msg_id=set_msg))
    api.calls.clear()
    bot.handle(_cb(f"pick:{ids[0]}", msg_id=set_msg))
    assert _edits(api) == [(set_msg, [])]
    assert [_get(conn, i).status for i in ids] == ["pending", "pending"]
    # Hotovo už nemá co zamítnout
    bot.handle(_cb("doneset:7", msg_id=set_msg))
    assert [_get(conn, i).status for i in ids] == ["pending", "pending"]


@pg
def test_vybrane_jde_schvalit_nevybrane_ne(bot, conn, api, meta, media_dir):
    from hlidac_reels import queue
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    set_msg = _set_msg_id(conn, ids[0])
    bot.handle(_cb(f"pick:{ids[0]}", msg_id=set_msg))
    bot.handle(_cb(f"pick:{ids[2]}", msg_id=set_msg))
    bot.handle(_cb(f"ok:{ids[1]}"))
    assert _get(conn, ids[1]).status == "pending"
    assert sum(meta.calls.values()) == 0
    bot.handle(_cb(f"ok:{ids[0]}"))
    assert _get(conn, ids[0]).status == "published"
    # sada má dál nevyřízené varianty: počítá se jako jedna jednotka
    assert queue.pending_count(conn) == 1
    bot.handle(_cb(f"ok:{ids[2]}"))
    assert _get(conn, ids[2]).status == "published"
    assert _get(conn, ids[1]).status == "pending"


@pg
def test_odpoved_po_vyberu(bot, conn, api, media_dir):
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    set_msg = _set_msg_id(conn, ids[0])
    bot.handle(_cb(f"pick:{ids[1]}", msg_id=set_msg))
    # odpověď na zprávu sady needituje žádnou variantu, odpověď na náhled vybrané ano
    bot.handle(_msg("Nový popisek", reply_to=set_msg))
    assert all(_get(conn, i).caption.startswith("Varianta") for i in ids)
    assert "Nejdřív vyber variantu" in api.texts()[-1]
    # odpověď na video nevybrané varianty taky ne
    bot.handle(_msg("Nový popisek", reply_to=_get(conn, ids[0]).telegram_video_msg_id))
    assert _get(conn, ids[0]).caption.startswith("Varianta")
    bot.handle(_msg("Nový popisek", reply_to=_get(conn, ids[1]).telegram_msg_id))
    assert _get(conn, ids[1]).caption == "Nový popisek"
    assert _get(conn, ids[0]).caption.startswith("Varianta")


@pg
def test_stare_noset_funguje_i_po_vyberu(bot, conn, api, media_dir):
    """Zpráva sady odeslaná před nasazením má noset:<set_id>; zamítne zbylé nevybrané varianty."""
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    set_msg = _set_msg_id(conn, ids[0])
    bot.handle(_cb(f"pick:{ids[1]}", msg_id=set_msg))
    bot.handle(_cb("noset:7", msg_id=set_msg))
    assert [_get(conn, i).status for i in ids] == ["rejected", "pending", "rejected"]


@pg
def test_stary_vyber_jednim_cislem(bot, conn, api, meta, media_dir):
    """Hodnota set_picked:<set_id> z dřívějška je jedno id; čte se stejně jako seznam."""
    from hlidac_reels import queue
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    queue.set_state(conn, "set_picked:7", str(ids[1]))
    assert queue.picked(conn, 7) == {ids[1]}
    bot.handle(_cb(f"ok:{ids[0]}"))
    assert _get(conn, ids[0]).status == "pending"
    bot.handle(_cb(f"ok:{ids[1]}"))
    assert _get(conn, ids[1]).status == "published"
    # další výběr se ke starému přidá
    queue.mark_picked(conn, 7, ids[2])
    assert queue.picked(conn, 7) == {ids[1], ids[2]}
    assert queue.get_state(conn, "set_picked:7") == f"{ids[1]},{ids[2]}"


@pg
def test_fronta_po_vyberu_nabidne_zbyle_varianty(bot, conn, api, media_dir):
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    set_msg = _set_msg_id(conn, ids[0])
    bot.handle(_cb(f"pick:{ids[1]}", msg_id=set_msg))
    api.calls.clear()
    bot.handle(_msg("/fronta"))
    msgs = [_buttons(b) for b in api.sent() if _buttons(b)]
    assert [f"pick:{ids[0]}", f"pick:{ids[2]}", "doneset:7"] in msgs
    assert [f"ok:{ids[1]}", f"edit:{ids[1]}", f"no:{ids[1]}"] in msgs
    assert len(msgs) == 2
    new_set = _set_msg_id(conn, ids[0])
    assert new_set != set_msg and _set_msg_id(conn, ids[2]) == new_set
    assert set_msg in _dropped(api)
    # nová zpráva sady: text jen se zbylými variantami, výběr platí jen z ní
    text = [b["text"] for b in api.sent() if "doneset:7" in _buttons(b)][0]
    assert "Varianta 1 řádek 1" in text and "Varianta 2 řádek 1" not in text
    bot.handle(_cb(f"pick:{ids[2]}", msg_id=set_msg))
    assert _get(conn, ids[2]).telegram_msg_id == new_set
    bot.handle(_cb(f"pick:{ids[2]}", msg_id=new_set))
    assert _get(conn, ids[2]).telegram_msg_id != new_set


@pg
def test_vybrana_varianta_jde_schvalit(bot, conn, api, meta, media_dir):
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    bot.handle(_cb(f"pick:{ids[2]}", msg_id=_set_msg_id(conn, ids[0])))
    bot.handle(_cb(f"ok:{ids[2]}"))
    assert _get(conn, ids[2]).status == "published"


@pg
def test_odpoved_pred_vyberem_needituje(bot, conn, api, media_dir):
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    bot.handle(_msg("Nový popisek", reply_to=_set_msg_id(conn, ids[0])))
    bot.handle(_msg("Nový popisek", reply_to=_get(conn, ids[1]).telegram_video_msg_id))
    assert all(_get(conn, i).caption.startswith("Varianta") for i in ids)
    assert "Nejdřív vyber variantu" in api.texts()[-1]


@pg
@pytest.mark.parametrize("action", ["doneset", "noset"])
def test_zamitnout_vse(bot, conn, api, media_dir, action):
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    set_msg = _set_msg_id(conn, ids[0])
    bot.handle(_cb(f"{action}:7", msg_id=set_msg))
    assert [_get(conn, i).status for i in ids] == ["rejected"] * 3
    assert set_msg in _dropped(api)
    assert "Sada #7 zamítnuta." in api.texts()


@pg
def test_denni_limit(bot, conn, api, media_dir):
    from hlidac_reels import queue
    a = _add_set(conn, media_dir, 1)
    b = _add_set(conn, media_dir, 2)
    c = _add_set(conn, media_dir, 3)
    bot.send_next()
    bot.handle(_cb("noset:1", msg_id=_set_msg_id(conn, a[0])))
    # po zamítnutí šla sama další sada
    assert _get(conn, b[0]).status == "pending"
    bot.handle(_cb("noset:2", msg_id=_set_msg_id(conn, b[0])))
    assert queue.sets_sent_on(conn, queue.local_date(conn, bot.now())) == 2
    bot.send_next()
    assert _get(conn, c[0]).status == "queued"
    # samostatné video limit nemá
    solo = _add(conn, media_dir, 1)
    bot.send_next()
    assert _get(conn, solo).status == "pending"
    bot.handle(_cb(f"no:{solo}"))
    assert _get(conn, c[0]).status == "queued"
    bot.handle(_msg("/dalsi"))
    assert [_get(conn, i).status for i in c] == ["pending"] * 3


@pg
def test_rucni_sada_limit_neomezuje_a_nepocita_se(bot, conn, api, media_dir):
    """Sada ruční úlohy (/video) jde i po vyčerpání limitu a do limitu se nepočítá (dnešní chyba)."""
    from hlidac_reels import queue
    a = _add_set(conn, media_dir, 1)
    b = _add_set(conn, media_dir, 2)
    bot.send_next()
    bot.handle(_cb("noset:1", msg_id=_set_msg_id(conn, a[0])))
    bot.handle(_cb("noset:2", msg_id=_set_msg_id(conn, b[0])))
    m = _add_set(conn, media_dir, 3, source="manual")
    auto = _add_set(conn, media_dir, 4)
    bot.send_next()
    assert [_get(conn, i).status for i in m] == ["pending"] * 3
    # pořád jen jedna nevyřízená jednotka najednou
    bot.send_next()
    assert _get(conn, auto[0]).status == "queued"
    bot.handle(_cb("noset:3", msg_id=_set_msg_id(conn, m[0])))
    assert queue.sets_sent_on(conn, queue.local_date(conn, bot.now())) == 2
    assert _get(conn, auto[0]).status == "queued"


@pg
def test_rucni_sada_v_limitu_nespotrebuje_misto(bot, conn, api, media_dir):
    m = _add_set(conn, media_dir, 1, source="manual")
    bot.send_next()
    bot.handle(_cb("noset:1", msg_id=_set_msg_id(conn, m[0])))
    a = _add_set(conn, media_dir, 2)
    b = _add_set(conn, media_dir, 3)
    bot.send_next()
    bot.handle(_cb("noset:2", msg_id=_set_msg_id(conn, a[0])))
    assert _get(conn, b[0]).status == "pending"


@pg
def test_cekani_na_limit_ohlasi_jednou(bot, conn, api, media_dir):
    a = _add_set(conn, media_dir, 1)
    b = _add_set(conn, media_dir, 2)
    c = _add_set(conn, media_dir, 3)
    bot.send_next()
    bot.handle(_cb("noset:1", msg_id=_set_msg_id(conn, a[0])))
    bot.handle(_cb("noset:2", msg_id=_set_msg_id(conn, b[0])))
    bot.send_next()
    bot.send_next()
    notes = [t for t in api.texts() if "denní limit" in t]
    assert len(notes) == 1 and "/dalsi" in notes[0] and "#3" in notes[0]
    assert _get(conn, c[0]).status == "queued"
    # jiná sada, která čeká kvůli limitu, se ohlásí znovu (jednou za sadu)
    _add_set(conn, media_dir, 4)
    bot.handle(_cb("noset:3", msg_id=_set_msg_id(conn, c[0])))  # sada 3 ještě neodešla: nic se nezmění
    from hlidac_reels import queue
    for i in c:
        queue.transition(conn, i, "queued", "rejected")
    bot.send_next()
    notes = [t for t in api.texts() if "denní limit" in t]
    assert len(notes) == 2 and "#4" in notes[1]


@pg
def test_denni_limit_podle_prazskeho_dne(conn, media_dir):
    from datetime import date, datetime, timezone
    from hlidac_reels import queue
    ids = _add_set(conn, media_dir, 1)
    _add_set(conn, media_dir, 2)  # neodeslaná sada se nepočítá
    # 23:30 UTC 20. 11. je v Praze už 21. 11.
    t = datetime(2026, 11, 20, 23, 30, tzinfo=timezone.utc)
    for i in ids:
        queue.transition(conn, i, "queued", "rejected", sent_at=t)
    assert queue.local_date(conn, t) == date(2026, 11, 21)
    assert queue.sets_sent_on(conn, date(2026, 11, 21)) == 1
    assert queue.sets_sent_on(conn, date(2026, 11, 20)) == 0


@pg
def test_jedna_nevyrizena_sada(bot, conn, api, media_dir):
    from hlidac_reels import queue
    _add_set(conn, media_dir, 1)
    b = _add_set(conn, media_dir, 2)
    bot.send_next()
    bot.send_next()
    assert _get(conn, b[0]).status == "queued"
    assert queue.pending_count(conn) == 1
    assert len(api.sent("sendVideo")) == 3


@pg
def test_fronta_obnovi_tlacitka_sady(bot, conn, api, media_dir):
    """Review Focus 5: videa sady odešla, zpráva s tlačítky ne (výpadek, restart)."""
    ids = _add_set(conn, media_dir, 7)
    api.fail["sendMessage"] = httpx.ConnectError("down")
    bot.send_next()
    assert [_get(conn, i).status for i in ids] == ["pending"] * 3
    del api.fail["sendMessage"]
    bot.send_next()  # čeká sada, nic dalšího se nepošle
    assert len(api.sent("sendVideo")) == 3
    api.calls.clear()
    bot.handle(_msg("/fronta"))
    msgs = [b for b in api.sent() if _buttons(b)]
    # jen zpráva sady, žádné náhledy se Schválit
    assert len(msgs) == 1
    assert _buttons(msgs[0]) == [f"pick:{i}" for i in ids] + ["doneset:7"]
    new_id = _get(conn, ids[0]).telegram_msg_id
    assert new_id and {_get(conn, i).telegram_msg_id for i in ids} == {new_id}
    bot.handle(_cb(f"pick:{ids[0]}", msg_id=new_id))
    bot.handle(_cb("doneset:7", msg_id=new_id))
    assert [_get(conn, i).status for i in ids] == ["pending", "rejected", "rejected"]
    # po výběru a Hotovo /fronta ukáže jen náhled vybrané varianty
    api.calls.clear()
    bot.handle(_msg("/fronta"))
    assert [_buttons(b) for b in api.sent() if _buttons(b)] == [
        [f"ok:{ids[0]}", f"edit:{ids[0]}", f"no:{ids[0]}"]]


@pg
def test_fronta_znovu_nabidne_sadu_a_stara_zprava_neplati(bot, conn, api, media_dir):
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    old = _set_msg_id(conn, ids[0])
    bot.handle(_msg("/fronta"))
    new = _set_msg_id(conn, ids[0])
    assert new != old and old in _dropped(api)
    bot.handle(_cb(f"pick:{ids[0]}", msg_id=old))
    assert {_get(conn, i).status for i in ids} == {"pending"}


@pg
def test_samostatne_video_beze_zmeny(bot, conn, api, media_dir):
    rid = _add(conn, media_dir)
    bot.send_next()
    r = _get(conn, rid)
    assert r.status == "pending"
    assert len(api.sent("sendVideo")) == 1
    assert _buttons(api.sent()[-1]) == [f"ok:{rid}", f"edit:{rid}", f"no:{rid}"]
    assert r.telegram_msg_id != r.telegram_video_msg_id


@pg
def test_sada_odmitnute_video_vyradi_jen_variantu(bot, conn, api, media_dir):
    ids = _add_set(conn, media_dir, 7)
    Path(_get(conn, ids[1]).video_path).unlink()
    bot.send_next()
    assert [_get(conn, i).status for i in ids] == ["pending", "rejected", "pending"]
    assert _buttons(_set_msg(api)) == [f"pick:{ids[0]}", f"pick:{ids[2]}", "doneset:7"]


@pg
def test_sada_bez_poslatelneho_videa_vyrazena(bot, conn, api, media_dir):
    ids = _add_set(conn, media_dir, 7, n=2)
    api.fail["sendVideo"] = httpx.Response(413, json={"ok": False, "description": "Request Entity Too Large"})
    bot.send_next()
    assert [_get(conn, i).status for i in ids] == ["rejected", "rejected"]
    assert any(t.startswith("Sada #7 vyřazena") for t in api.texts())


@pg
def test_sada_vypadek_site_necha_queued(bot, conn, api, media_dir):
    from hlidac_reels.telegram import TelegramError
    ids = _add_set(conn, media_dir, 7)
    api.fail["sendVideo"] = httpx.ConnectError("down")
    with pytest.raises(TelegramError):
        bot.send_next()
    assert [_get(conn, i).status for i in ids] == ["queued"] * 3


@pg
def test_smycka_inbox_a_cerstve(conn, api, meta, media_dir, tmp_path, monkeypatch):
    from hlidac_reels import fresh, inbox
    from hlidac_reels.bot import Bot
    from hlidac_reels.telegram import Telegram
    tg = Telegram(TOKEN, client=httpx.Client(transport=httpx.MockTransport(api)))
    bot = Bot(conn, tg, meta, CHAT, str(media_dir), PUBLIC_BASE, inbox_dir=str(tmp_path / "inbox"))
    calls = []
    monkeypatch.setattr(inbox, "ingest_all", lambda c, i, m, autopilot_on=False: calls.append(("ingest", i, m)) or [])
    monkeypatch.setattr(fresh, "detect", lambda c, now: calls.append(("detect",)) or [])
    monkeypatch.setattr(fresh, "retire", lambda c, now: calls.append(("retire",)) or [])
    t = [1000.0]
    bot.clock = lambda: t[0]

    class Stop(Exception):
        pass
    api.fail["getUpdates"] = httpx.ConnectError("down")
    n = [0]

    def sleep(s):
        n[0] += 1
        t[0] += 61
        if n[0] >= 3:
            raise Stop
    with pytest.raises(Stop):
        bot.loop(sleep=sleep)
    names = [c[0] for c in calls]
    assert names.count("ingest") == 3
    assert names.count("detect") == 1 and names.count("retire") == 1
    assert calls[0][1] == tmp_path / "inbox" and calls[0][2] == media_dir


def _to(conn, rid, status):
    from hlidac_reels import queue
    return queue.transition(conn, rid, ("queued", "pending"), status)


@pg
def test_cerstve_stazeni_a_vyprseni_sady(bot, conn, api, media_dir, monkeypatch):
    from hlidac_reels import fresh
    a = _add_set(conn, media_dir, 1)
    b = _add_set(conn, media_dir, 2)
    bot.send_next()
    bot.send_next(force=True)  # obě sady v Telegramu
    set_msg = _set_msg_id(conn, a[0])

    def retire(c, now):
        for i in a:
            _to(c, i, "withdrawn")
        for i in b:
            _to(c, i, "expired")
        return [(1, "withdrawn"), (2, "expired")]
    monkeypatch.setattr(fresh, "detect", lambda c, now: [])
    monkeypatch.setattr(fresh, "retire", retire)
    bot.maybe_fresh()
    texts = api.texts()
    assert "#1 staženo: nález už není mezi hlavními" in texts
    assert "#2 vypršelo (7 dní bez výběru)" in texts
    assert set_msg in _dropped(api)


@pg
def test_vyprseni_zbylych_variant_po_vyberu(bot, conn, api, media_dir, monkeypatch):
    from hlidac_reels import fresh
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    set_msg = _set_msg_id(conn, ids[0])
    bot.handle(_cb(f"pick:{ids[0]}", msg_id=set_msg))
    preview = _get(conn, ids[0]).telegram_msg_id

    def retire(c, now):
        for i in ids[1:]:
            _to(c, i, "expired")
        return [(7, "expired")]
    monkeypatch.setattr(fresh, "detect", lambda c, now: [])
    monkeypatch.setattr(fresh, "retire", retire)
    api.calls.clear()
    bot.maybe_fresh()
    assert "#7 zbylé varianty vypršely" in api.texts()
    assert _dropped(api) == [set_msg] and preview not in _dropped(api)
    assert _get(conn, ids[0]).status == "pending"


@pg
def test_chyba_inboxu_jen_zaloguje(bot, conn, api, media_dir, monkeypatch, caplog):
    from hlidac_reels import inbox

    def boom(*a, **k):
        raise RuntimeError("disk")
    monkeypatch.setattr(inbox, "ingest_all", boom)
    bot.inbox_dir = str(media_dir / "inbox")
    with caplog.at_level(logging.WARNING):
        bot.maybe_ingest()
    assert "disk" in caplog.text


@pg
def test_inbox_nova_sada_se_hned_posle(bot, conn, api, media_dir, monkeypatch):
    from hlidac_reels import inbox
    ids = []

    def ingest(c, i, m, autopilot_on=False):
        ids.extend(_add_set(c, media_dir, 9))
        return [inbox.IngestResult(9, list(ids), {}, True)]
    monkeypatch.setattr(inbox, "ingest_all", ingest)
    bot.inbox_dir = str(media_dir / "inbox")
    bot.maybe_ingest()
    assert {_get(conn, i).status for i in ids} == {"pending"}


@pg
def test_ingest_preda_stav_autopilota(bot, conn, media_dir, monkeypatch):
    from hlidac_reels import inbox, queue
    from hlidac_reels.bot import AUTOPILOT_KEY
    seen = []
    monkeypatch.setattr(inbox, "ingest_all", lambda c, i, m, autopilot_on: seen.append(autopilot_on) or [])
    bot.inbox_dir = str(media_dir / "inbox")
    bot._ingest()
    queue.set_state(conn, AUTOPILOT_KEY, "1")
    bot._ingest()
    queue.set_state(conn, AUTOPILOT_KEY, "0")
    bot._ingest()
    assert seen == [False, True, False]


@pg
def test_pending_count_po_sadach(conn, media_dir):
    from hlidac_reels import queue
    a = _add_set(conn, media_dir, 1)
    solo = _add(conn, media_dir, 1)
    for i in [*a, solo]:
        queue.transition(conn, i, "queued", "pending")
    assert queue.pending_count(conn) == 2
    # sada s vybranými i nevybranými variantami zůstává jedna jednotka
    queue.mark_picked(conn, 1, a[0])
    queue.mark_picked(conn, 1, a[1])
    assert queue.pending_count(conn) == 2
    queue.transition(conn, a[2], "pending", "rejected")
    assert queue.pending_count(conn) == 2


def test_texty_sady():
    from datetime import datetime, timezone
    from hlidac_reels.messages import set_buttons, set_text, set_video_caption
    from hlidac_reels.queue import Reel
    now = datetime.now(timezone.utc)

    def reel(i, v, tpl, fmt, caption):
        return Reel(id=i, source="fresh", ct=221, format=fmt, video_path="x", video_sha256=str(i),
                    caption=caption, status="pending", telegram_video_msg_id=None, telegram_msg_id=None,
                    ig_container_id=None, ig_media_id=None, ig_permalink=None, fb_video_id=None,
                    fb_permalink=None, public_token=None, error=None, attempts=0, created_at=now,
                    sent_at=None, decided_at=None, published_at=None, threads_container_id=None,
                    threads_media_id=None, threads_permalink=None, video_landscape_path=None,
                    video_landscape_sha256=None, telegram_landscape_msg_id=None,
                    telegram_landscape_sha256=None, set_id=5, variant=v, template=tpl)
    items = [reel(11, 1, "template_v2", "v2", "a\nb\nc"), reel(12, 2, "template_v3", "dopad", "x" * 5000)]
    assert set_video_caption(items[0]) == "#5 · varianta 1 · v2"
    assert set_video_caption(items[1]) == "#5 · varianta 2 · v3 dopad"
    assert set_buttons(items) == [("Vybrat 1", "pick:11"), ("Vybrat 2", "pick:12"),
                                  ("Hotovo, zbytek zamítnout", "doneset:5")]
    text = set_text(items)
    assert len(text) <= 4096 and "a\nb" in text and "\nc" not in text


@pg
def test_schvalit_nevybranou_variantu_nejde(bot, conn, api, meta, media_dir):
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    bot.handle(_cb(f"ok:{ids[0]}"))
    assert {_get(conn, i).status for i in ids} == {"pending"}
    assert sum(meta.calls.values()) == 0


@pg
def test_stazeni_neodeslane_sady_bez_zpravy(bot, conn, api, media_dir, monkeypatch):
    from hlidac_reels import fresh
    a = _add_set(conn, media_dir, 1)

    def retire(c, now):
        for i in a:
            _to(c, i, "withdrawn")
        return [(1, "withdrawn")]
    monkeypatch.setattr(fresh, "detect", lambda c, now: [])
    monkeypatch.setattr(fresh, "retire", retire)
    bot.maybe_fresh()
    assert api.calls == []
    assert {_get(conn, i).status for i in a} == {"withdrawn"}


@pg
def test_sada_vypadek_site_u_druheho_videa(bot, conn, api, media_dir):
    """1. video sady odešlo, u 2. výpadek: výjimka jde dál, celá sada zůstane queued (1. video půjde znovu)."""
    from hlidac_reels.telegram import TelegramError
    ids = _add_set(conn, media_dir, 7)
    orig = api.__call__

    def flaky(request):
        if request.url.path.endswith("/sendVideo") and len(api.sent("sendVideo")) >= 1:
            raise httpx.ConnectError("down")
        return orig(request)
    bot.tg._client = httpx.Client(transport=httpx.MockTransport(flaky))
    with pytest.raises(TelegramError):
        bot.send_next()
    assert len(api.sent("sendVideo")) == 1
    assert [_get(conn, i).status for i in ids] == ["queued"] * 3


# --- příkazy generátoru (Task 7) -----------------------------------------

def _tisk(conn, ct: int) -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO tisky (ct) VALUES (%s)", (ct,))
    conn.commit()


@pg
def test_video_zalozi_ulohu(bot, conn, api):
    from hlidac_reels import jobs
    _tisk(conn, 221)
    bot.handle(_msg("/video 221 pn"))
    (job,) = jobs.pending(conn)
    assert (job.source, job.ct, job.format_hint) == ("manual", 221, "pn")
    assert api.texts()[-1] == f"Zadání #{job.id} (tisk 221) je 1. ve frontě."


@pg
def test_video_neznamy_tisk(bot, conn, api):
    from hlidac_reels import jobs
    bot.handle(_msg("/video 99999"))
    assert api.texts()[-1] == "Tisk 99999 neznám."
    assert jobs.pending(conn) == []
    bot.handle(_msg("/video"))
    assert api.texts()[-1].startswith("Použití: /video")
    bot.handle(_msg("/video abc"))
    assert api.texts()[-1].startswith("Použití: /video")


@pg
def test_video_cislo_mimo_rozsah(bot, conn, api):
    from hlidac_reels import jobs
    for text in ("/video 99999999999", "/video 1000000", "/video -5", "/video 0"):
        bot.handle(_msg(text))
        assert api.texts()[-1].startswith("Použití: /video"), text
    assert jobs.pending(conn) == []
    bot.handle(_msg("/video 999999"))
    assert api.texts()[-1] == "Tisk 999999 neznám."


@pg
def test_video_spatny_format(bot, conn, api):
    from hlidac_reels import jobs
    _tisk(conn, 221)
    bot.handle(_msg("/video 221 xyz"))
    assert "cesta, pn, zmena, rozprava" in api.texts()[-1]
    assert jobs.pending(conn) == []


@pg
def test_video_duplicitni(bot, conn, api):
    from hlidac_reels import jobs
    _tisk(conn, 221)
    bot.handle(_msg("/video 221"))
    bot.handle(_msg("/video 221"))
    assert api.texts()[-1] == "Tisk 221 už ve frontě je."
    assert len(jobs.pending(conn)) == 1


@pg
def test_vyber(bot, conn, api):
    from hlidac_reels import jobs
    bot.handle(_msg("/vyber"))
    (job,) = jobs.pending(conn)
    assert job.source == "auto_pick" and job.ct is None
    assert api.texts()[-1] == f"Zadání #{job.id} (automatický výběr) je 1. ve frontě."


@pg
def test_fronta_ukaze_ulohy_a_pc(bot, conn, api):
    from hlidac_reels import jobs, queue
    _tisk(conn, 221)
    bot.handle(_msg("/fronta"))
    assert "PC zatím nebylo vidět" in api.texts()[-1]
    a = jobs.add(conn, source="manual", ct=221)
    queue.set_state(conn, "worker_seen_at", "2026-11-20T14:05:00+01:00")
    bot.handle(_msg("/fronta"))
    text = api.texts()[-1]
    assert f"#{a} manual 221 queued" in text
    assert "PC naposledy: 20. 11. 14:05" in text


@pg
def test_hlaseni_chyby_jednou(bot, conn, api):
    from hlidac_reels import jobs
    j = jobs.add(conn, source="manual", ct=221)
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status='failed', error=%s, finished_at=now() WHERE id=%s",
                    ("x" * 400, j))
    conn.commit()
    t = [1000.0]
    bot.clock = lambda: t[0]
    bot.maybe_notify_failed()
    msgs = [x for x in api.texts() if "se nepodařilo" in x]
    assert msgs == [f"Zadání #{j} (tisk 221) se nepodařilo: " + "x" * 300]
    t[0] += 61
    bot.maybe_notify_failed()
    assert len([x for x in api.texts() if "se nepodařilo" in x]) == 1


@pg
def test_cizi_chat_prikaz_ignoruje(bot, conn, api):
    from hlidac_reels import jobs
    _tisk(conn, 221)
    bot.handle(_msg("/video 221", chat=OTHER_CHAT))
    bot.handle(_msg("/vyber", chat=OTHER_CHAT))
    assert api.calls == [] and jobs.pending(conn) == []


# --- /pn: video k vybraným PN -------------------------------------------------

def _pn(conn, cislo: int, ct: int, osoba: str = "Jan Novák", cas: str = "2026-11-23 20:09:00",
        titulek: str | None = None, text: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO pn (id_dokument, cislo, ct, osoba, cas) VALUES (%s,%s,%s,%s,%s)",
                    (30000 + cislo, cislo, ct, osoba, cas))
        if titulek is not None or text is not None:
            ai = json.dumps({"titulek": titulek}, ensure_ascii=False) if titulek is not None else None
            cur.execute("INSERT INTO pn_text (id_dokument, ct, cislo, ai_json, text) VALUES (%s,%s,%s,%s,%s)",
                        (30000 + cislo, ct, cislo, ai, text))
    conn.commit()


def _pn_bot(bot):
    from datetime import datetime, timezone
    # 24. 11. 2026 10:00 UTC = 11:00 v Praze; okno 48 h sahá do 22. 11. 11:00
    bot.now = lambda: datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
    return bot


@pg
def test_pn_jedno(bot, conn, api):
    from hlidac_reels import jobs
    _pn(conn, 2376, 300)
    bot.handle(_msg("/pn 2376"))
    (job,) = jobs.pending(conn)
    assert (job.source, job.ct, job.focus_pn, job.format_hint) == ("manual", 300, [2376], None)
    assert api.texts()[-1] == f"Zadání #{job.id} (PN 2376, tisk 300) je 1. ve frontě."


@pg
def test_pn_vic_samostatnych(bot, conn, api):
    from hlidac_reels import jobs
    for n in (2376, 2377, 2378):
        _pn(conn, n, 300)
    bot.handle(_msg("/pn 2376 2377 2378"))
    assert [j.focus_pn for j in jobs.pending(conn)] == [[2376], [2377], [2378]]
    assert len([t for t in api.texts() if t.startswith("Zadání #")]) == 3


@pg
def test_pn_jedno_video_z_vic(bot, conn, api):
    from hlidac_reels import jobs
    for n in (2376, 2377, 2378):
        _pn(conn, n, 300)
    bot.handle(_msg("/pn 2378+2376"))
    (job,) = jobs.pending(conn)
    assert job.focus_pn == [2376, 2378] and job.ct == 300
    assert api.texts()[-1] == f"Zadání #{job.id} (PN 2376+2378, tisk 300) je 1. ve frontě."
    # mezery kolem + nevadí; jiná množina je nová úloha
    bot.handle(_msg("/pn 2376 + 2377"))
    assert [j.focus_pn for j in jobs.pending(conn)] == [[2376, 2378], [2376, 2377]]


@pg
def test_pn_neznamy(bot, conn, api):
    from hlidac_reels import jobs
    _pn(conn, 2376, 300)
    bot.handle(_msg("/pn 9999"))
    assert api.texts()[-1] == "PN 9999 zatím neznám (sync běží každou hodinu)."
    bot.handle(_msg("/pn 2376+9999"))
    assert api.texts()[-1] == "PN 9999 zatím neznám (sync běží každou hodinu)."
    assert jobs.pending(conn) == []


@pg
def test_pn_ruzne_tisky(bot, conn, api):
    from hlidac_reels import jobs
    _pn(conn, 2376, 300)
    _pn(conn, 2100, 250)
    bot.handle(_msg("/pn 2376+2100"))
    assert "různým tiskům" in api.texts()[-1] and "tisk 300" in api.texts()[-1] and "tisk 250" in api.texts()[-1]
    assert jobs.pending(conn) == []


@pg
def test_pn_nejvys_pet(bot, conn, api):
    from hlidac_reels import jobs
    for n in range(2370, 2376):
        _pn(conn, n, 300)
    bot.handle(_msg("/pn 2370+2371+2372+2373+2374+2375"))
    assert api.texts()[-1] == "Do jednoho videa nejvýš 5 PN."
    assert jobs.pending(conn) == []
    bot.handle(_msg("/pn 2370+2371+2372+2373+2374"))
    assert jobs.pending(conn)[0].focus_pn == [2370, 2371, 2372, 2373, 2374]


@pg
def test_pn_spatne_cislo(bot, conn, api):
    from hlidac_reels import jobs
    for text in ("/pn abc", "/pn 2376+", "/pn 0", "/pn 99999999999", "/pn " + " ".join(["1"] * 11)):
        bot.handle(_msg(text))
        assert api.texts()[-1].startswith("Použití: /pn"), text
    assert jobs.pending(conn) == []


@pg
def test_pn_duplicitni(bot, conn, api):
    from hlidac_reels import jobs
    _pn(conn, 2376, 300)
    _pn(conn, 2377, 300)
    bot.handle(_msg("/pn 2376+2377"))
    bot.handle(_msg("/pn 2377+2376"))
    assert api.texts()[-1] == "PN 2376+2377 (tisk 300) už ve frontě je."
    # úloha k celému tisku a k jinému PN jdou vedle
    with conn.cursor() as cur:
        cur.execute("INSERT INTO tisky (ct) VALUES (300)")
    conn.commit()
    bot.handle(_msg("/video 300"))
    bot.handle(_msg("/pn 2376"))
    assert len(jobs.pending(conn)) == 3


@pg
def test_pn_seznam_48h(bot, conn, api):
    _pn_bot(bot)
    _pn(conn, 2376, 300, osoba="Jana Malá", cas="2026-11-23 20:09:00", titulek="Mění daň z nemovitostí")
    _pn(conn, 2377, 300, cas="2026-11-23 21:00:00")
    _pn(conn, 2378, 300, cas="2026-11-23 22:20:00")
    _pn(conn, 2380, 310, cas="2026-11-24 09:00:00")
    _pn(conn, 2300, 300, cas="2026-11-22 10:59:00")  # mimo okno 48 h
    bot.handle(_msg("/pn"))
    body = api.sent()[-1]
    text = body["text"]
    assert text.index("PN 2380") < text.index("PN 2378") < text.index("PN 2377") < text.index("PN 2376")
    assert "PN 2300" not in text
    assert "PN 2376 · tisk 300 · Jana Malá · 23. 11. 20:09" in text
    assert "Mění daň z nemovitostí" in text
    assert _buttons(body) == ["pn:2380", "pn:2378", "pn:2377", "pn:2376", "pn:2376+2377+2378"]
    labels = [b["text"] for row in body["reply_markup"]["inline_keyboard"] for b in row]
    assert labels[0] == "Video k PN 2380" and labels[-1] == "Jedno video ze všech (2376+2377+2378)"


@pg
def test_pn_seznam_uryvek_bez_rozboru(bot, conn, api):
    # PN bez rozboru AI: místo titulku začátek textu za „se mění takto:“, bez hlavičky s předkladateli
    _pn_bot(bot)
    body = ("PARLAMENT ČESKÉ REPUBLIKY Poslanecká sněmovna Pozměňovací návrh poslance Benjamina Činčily "
            "k vládnímu návrhu zákona o státním rozpočtu (ST 324)\nVládní návrh zákona (ST 324) se mění takto:\n"
            "1. V kapitole 313 – Ministerstvo práce a sociálních věcí se zvyšuje ukazatel „Výdaje celkem“ "
            "o částku 500 000 000 Kč. " + "Další text. " * 40)
    _pn(conn, 2422, 324, cas="2026-11-24 10:00:00", text=body)
    _pn(conn, 2423, 324, cas="2026-11-24 10:01:00", text="Text bez obvyklé formulace.")
    _pn(conn, 2424, 324, cas="2026-11-24 10:02:00", titulek="Titulek AI", text=body)
    bot.handle(_msg("/pn"))
    text = api.sent()[-1]["text"]
    assert "„V kapitole 313 – Ministerstvo práce" in text
    assert "(text PN, rozbor AI zatím chybí)" in text
    assert "Činčily" not in text
    assert "PN 2423 · tisk 324 · Jan Novák · 24. 11. 10:01\n(rozbor AI zatím chybí)" in text
    assert "Titulek AI (shrnutí AI)" in text


def test_pn_uryvek_delka():
    from hlidac_reels.bot import PN_EXCERPT, _pn_excerpt
    long = _pn_excerpt("se mění takto: " + "slovo " * 100)
    assert len(long) <= PN_EXCERPT + 1 and long.endswith("slovo…")
    assert _pn_excerpt("Zákon se doplňuje takto: a) krátce.") == "krátce."
    assert _pn_excerpt(None) == "" and _pn_excerpt("bez formulace") == ""
    # bez „takto:“: za odkazem na tisk v hlavičce
    assert _pn_excerpt("Pozměňovací návrh poslance X k návrhu (Sněmovní tisk č. 324/0). V kapitole 329 se zvyšují výdaje.") \
        == "V kapitole 329 se zvyšují výdaje."
    # „(ST 324) Vládní návrh … se mění takto:“ a datum na začátku
    assert _pn_excerpt("PN (ST 324) Vládní návrh zákona o rozpočtu se mění takto: I. V příloze č. 4 x.") == "V příloze č. 4 x."
    assert _pn_excerpt("k tisku (sněmovní tisk 300) Datum: 23. 11. 2026 V části první x.") == "V části první x."


@pg
def test_pn_seznam_prazdny(bot, conn, api):
    _pn_bot(bot)
    _pn(conn, 2300, 300, cas="2026-11-20 10:00:00")
    bot.handle(_msg("/pn"))
    assert api.texts()[-1] == "Za posledních 48 h žádný nový PN."


@pg
def test_pn_seznam_nejvys_10_a_skupina_nejvys_5(bot, conn, api):
    _pn_bot(bot)
    for k in range(12):
        _pn(conn, 2400 + k, 300, cas=f"2026-11-24 0{k // 6}:{k % 6}0:00")  # 00:00 až 01:50
    bot.handle(_msg("/pn"))
    body = api.sent()[-1]
    buttons = _buttons(body)
    assert buttons[:10] == [f"pn:{2411 - k}" for k in range(10)]
    # skupina z nejnovějších 5 PN tisku (limit jednoho videa)
    assert buttons[10:] == ["pn:2407+2408+2409+2410+2411"]


@pg
def test_pn_tlacitko(bot, conn, api):
    from hlidac_reels import jobs
    _pn(conn, 2376, 300)
    _pn(conn, 2377, 300)
    bot.handle(_cb("pn:2376"))
    bot.handle(_cb("pn:2376+2377"))
    assert [j.focus_pn for j in jobs.pending(conn)] == [[2376], [2376, 2377]]
    assert api.methods().count("answerCallbackQuery") == 2
    bot.handle(_cb("pn:abc"))
    assert jobs.pending(conn)[-1].focus_pn == [2376, 2377] and len(jobs.pending(conn)) == 2


@pg
def test_pn_v_napovede_fronte_a_chybe(bot, conn, api):
    from hlidac_reels import jobs
    bot.handle(_msg("/neco"))
    assert "/pn" in api.texts()[-1]
    j = jobs.add(conn, source="manual", ct=300, focus_pn=[2377, 2376])
    bot.handle(_msg("/fronta"))
    assert f"#{j} manual 300 PN 2376+2377 queued" in api.texts()[-1]
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status='failed', error='x' WHERE id=%s", (j,))
    conn.commit()
    bot.maybe_notify_failed()
    assert api.texts()[-1] == f"Zadání #{j} (PN 2376+2377, tisk 300) se nepodařilo: x"


# --- video na šířku (16:9) ---------------------------------------------------

def _add_landscape(conn, media_dir: Path, rid: int, n: int = 1) -> Path:
    from hlidac_reels import queue
    land = media_dir / "videos" / f"w{n}.mp4"
    land.write_bytes(b"fake landscape " + str(n).encode())
    assert queue.set_landscape(conn, rid, str(land), f"wsha{n}")
    return land


def _video_captions(api) -> list[str]:
    return [b["_multipart"] for b in api.sent("sendVideo")]


@pg
def test_send_next_posle_obe_videa(conn, api, meta, media_dir):
    bot = _make_bot(conn, api, meta, media_dir, threads=FakeThreads())
    rid = _add(conn, media_dir)
    _add_landscape(conn, media_dir, rid)
    bot.send_next()
    assert api.methods() == ["sendVideo", "sendVideo", "sendMessage"]
    caps = _video_captions(api)
    assert "na šířku (Threads)" not in caps[0]
    assert f"#{rid} · tisk 221 · v3 · na šířku (Threads)" in caps[1]
    assert _buttons(api.sent()[-1]) == [f"ok:{rid}", f"edit:{rid}", f"no:{rid}"]
    r = _get(conn, rid)
    assert r.status == "pending"
    assert r.telegram_landscape_msg_id is not None
    assert r.telegram_video_msg_id < r.telegram_landscape_msg_id < r.telegram_msg_id


@pg
def test_send_next_bez_sirky_jedno_video(bot, conn, api, media_dir):
    rid = _add(conn, media_dir)
    bot.send_next()
    assert api.methods() == ["sendVideo", "sendMessage"]
    assert _get(conn, rid).telegram_landscape_msg_id is None


@pg
def test_send_next_sirka_odmitnuta_vyradi_polozku(bot, conn, api, media_dir):
    rid = _add(conn, media_dir)
    land = _add_landscape(conn, media_dir, rid)
    land.unlink()  # soubor šířky nejde přečíst → OSError
    good = _add(conn, media_dir, 2)
    bot.send_next()
    r = _get(conn, rid)
    assert r.status == "rejected" and r.error.startswith("nelze poslat do Telegramu")
    assert any(f"#{rid} vyřazeno, video na šířku nelze poslat" in t for t in api.texts())
    assert _get(conn, good).status == "pending"


@pg
def test_send_next_sirka_vypadek_site_polozka_zustane(bot, conn, api, media_dir):
    from hlidac_reels.telegram import TelegramError
    rid = _add(conn, media_dir)
    _add_landscape(conn, media_dir, rid)
    real = api.__call__
    count = [0]

    def flaky(request):
        if request.url.path.endswith("/sendVideo"):
            count[0] += 1
            if count[0] == 2:
                raise httpx.ConnectError("down")
        return real(request)

    from hlidac_reels.telegram import Telegram
    bot.tg = Telegram(TOKEN, client=httpx.Client(transport=httpx.MockTransport(flaky)))
    with pytest.raises(TelegramError):
        bot.send_next()
    assert _get(conn, rid).status == "queued"


@pg
def test_nahled_rika_format_threads(conn, api, meta, media_dir):
    from hlidac_reels import queue
    from hlidac_reels.messages import preview_text
    rid = _add(conn, media_dir)
    text = preview_text(queue.get(conn, rid), threads=True)
    assert "Threads (video na výšku):" in text and "na šířku" not in text
    _add_landscape(conn, media_dir, rid)
    text = preview_text(queue.get(conn, rid), threads=True)
    assert "Threads (video na šířku):" in text and "na výšku" not in text
    # bez Threads se o formátu nepíše
    assert "Threads" not in preview_text(queue.get(conn, rid))


@pg
def test_doplnena_sirka_u_pending_jednou(conn, api, meta, media_dir):
    # šířka jde do chatu jednou a jen pro informaci: žádný nový náhled, původní tlačítka platí dál
    bot = _make_bot(conn, api, meta, media_dir, threads=FakeThreads())
    now = [1000.0]
    bot.clock = lambda: now[0]
    rid = _add(conn, media_dir)
    bot.maybe_send_next()
    old = _get(conn, rid).telegram_msg_id
    _add_landscape(conn, media_dir, rid)
    api.calls.clear()
    bot.maybe_send_next()  # ještě v intervalu: nic
    assert api.calls == []
    for _ in range(3):
        now[0] += 60
        bot.maybe_send_next()
    caps = _video_captions(api)
    assert len(caps) == 1 and "na šířku (Threads)" in caps[0]
    r = _get(conn, rid)
    assert r.telegram_landscape_msg_id is not None and r.telegram_msg_id == old
    assert api.sent("sendMessage") == [] and _dropped(api) == []


@pg
def test_doplnena_sirka_vypadek_zkusi_znovu(conn, api, meta, media_dir):
    bot = _make_bot(conn, api, meta, media_dir, threads=FakeThreads())
    now = [1000.0]
    bot.clock = lambda: now[0]
    rid = _add(conn, media_dir)
    bot.maybe_send_next()
    _add_landscape(conn, media_dir, rid)
    api.calls.clear()
    api.fail["sendVideo"] = httpx.ConnectError("down")
    now[0] += 60
    bot.maybe_send_next()
    assert _get(conn, rid).telegram_landscape_msg_id is None
    del api.fail["sendVideo"]
    now[0] += 60
    bot.maybe_send_next()
    assert _get(conn, rid).telegram_landscape_msg_id is not None
    assert api.sent("sendMessage") == []


@pg
def test_doplnena_sirka_chyba_videa_zkusi_znovu(conn, api, meta, media_dir):
    bot = _make_bot(conn, api, meta, media_dir)
    now = [1000.0]
    bot.clock = lambda: now[0]
    rid = _add(conn, media_dir)
    bot.maybe_send_next()
    _add_landscape(conn, media_dir, rid)
    api.fail["sendVideo"] = httpx.ConnectError("down")
    now[0] += 60
    bot.maybe_send_next()
    assert _get(conn, rid).telegram_landscape_msg_id is None
    assert _get(conn, rid).status == "pending"
    del api.fail["sendVideo"]
    now[0] += 60
    bot.maybe_send_next()
    assert _get(conn, rid).telegram_landscape_msg_id is not None


@pg
def test_odpoved_na_video_na_sirku_meni_popisek(bot, conn, api, media_dir):
    rid = _add(conn, media_dir)
    _add_landscape(conn, media_dir, rid)
    bot.send_next()
    land_id = _get(conn, rid).telegram_landscape_msg_id
    bot.handle(_msg("Nový popisek", reply_to=land_id))
    assert _get(conn, rid).caption == "Nový popisek"


@pg
def test_schvalit_plati_i_pro_doplnenou_sirku(conn, api, meta, media_dir):
    # rozhodnutí 2026-11-24: šířka se znovu neschvaluje, ani když dorazí po náhledu
    bot = _make_bot(conn, api, meta, media_dir, threads=FakeThreads(media_dir))
    now = [1000.0]
    bot.clock = lambda: now[0]
    rid = _add(conn, media_dir)
    bot.maybe_send_next()
    old = _get(conn, rid).telegram_msg_id
    _add_landscape(conn, media_dir, rid)
    api.calls.clear()
    bot.handle(_cb(f"ok:{rid}", msg_id=old))
    assert _get(conn, rid).status == "published"
    assert [b.get("text") for b in api.sent("answerCallbackQuery")] == ["Schváleno"]


def _count_video_requests(bot, api, reject: list[bool]):
    """Telegram, který počítá sendVideo a dokud reject[0], odpoví 400 (trvalé odmítnutí)."""
    from hlidac_reels.telegram import Telegram
    count = [0]

    def handler(request):
        if request.url.path.endswith("/sendVideo"):
            count[0] += 1
            if reject[0]:
                return httpx.Response(400, json={"ok": False, "description": "Bad Request: wrong file"})
        return api(request)

    bot.tg = Telegram(TOKEN, client=httpx.Client(transport=httpx.MockTransport(handler)))
    return count


@pg
def test_doplnena_sirka_odmitnuta_ohlasi_jednou(conn, api, meta, media_dir):
    bot = _make_bot(conn, api, meta, media_dir, threads=FakeThreads())
    now = [1000.0]
    bot.clock = lambda: now[0]
    rid = _add(conn, media_dir)
    bot.maybe_send_next()
    _add_landscape(conn, media_dir, rid)
    reject = [True]
    count = _count_video_requests(bot, api, reject)
    api.calls.clear()
    for _ in range(3):
        now[0] += 60
        bot.maybe_send_next()
    assert count[0] == 1
    msgs = [t for t in api.texts() if "video na šířku Telegram odmítl" in t]
    assert len(msgs) == 1 and f"#{rid}" in msgs[0] and "wrong file" in msgs[0]
    assert "reels-add-landscape" in msgs[0]
    assert _get(conn, rid).status == "pending"
    # nový soubor se zkusí znovu
    reject[0] = False
    _add_landscape(conn, media_dir, rid, n=2)
    now[0] += 60
    bot.maybe_send_next()
    assert count[0] == 2
    assert _get(conn, rid).telegram_landscape_msg_id is not None


@pg
def test_doplnena_sirka_chybi_soubor_ohlasi_jednou(conn, api, meta, media_dir):
    bot = _make_bot(conn, api, meta, media_dir)
    now = [1000.0]
    bot.clock = lambda: now[0]
    rid = _add(conn, media_dir)
    bot.maybe_send_next()
    _add_landscape(conn, media_dir, rid).unlink()
    api.calls.clear()
    for _ in range(3):
        now[0] += 60
        bot.maybe_send_next()
    assert len([t for t in api.texts() if "video na šířku nejde přečíst" in t]) == 1
    assert _get(conn, rid).telegram_landscape_msg_id is None


# --- finální kontrola větve: otisk odeslané šířky ----------------------------

@pg
def test_sirka_vymenena_behem_send_next_schvaleni_projde(conn, api, meta, media_dir):
    from hlidac_reels import queue
    from hlidac_reels.telegram import Telegram
    th = FakeThreads(media_dir)
    bot = _make_bot(conn, api, meta, media_dir, threads=th)
    rid = _add(conn, media_dir)
    _add_landscape(conn, media_dir, rid, n=1)
    second = media_dir / "videos" / "w2.mp4"
    second.write_bytes(b"fake landscape 2")
    count = [0]

    def handler(request):
        if request.url.path.endswith("/sendVideo"):
            count[0] += 1
            if count[0] == 2:
                # reels-add-landscape vymění šířku, zatímco bot posílá tu původní
                assert queue.set_landscape(conn, rid, str(second), "wsha2")
        return api(request)

    bot.tg = Telegram(TOKEN, client=httpx.Client(transport=httpx.MockTransport(handler)))
    bot.send_next()
    r = _get(conn, rid)
    assert r.status == "pending" and r.video_landscape_sha256 == "wsha2"
    bot.handle(_cb(f"ok:{rid}", msg_id=r.telegram_msg_id))
    assert _get(conn, rid).status == "published"


@pg
def test_send_next_ulozi_otisk_odeslane_sirky(bot, conn, api, media_dir):
    rid = _add(conn, media_dir)
    _add_landscape(conn, media_dir, rid)
    bot.send_next()
    assert _get(conn, rid).telegram_landscape_sha256 == "wsha1"


@pg
def test_trvala_chyba_sirky_bez_otisku_ohlasi_jednou(conn, api, meta, media_dir):
    from hlidac_reels import queue
    bot = _make_bot(conn, api, meta, media_dir)
    now = [1000.0]
    bot.clock = lambda: now[0]
    rid = _add(conn, media_dir)
    bot.maybe_send_next()
    land = media_dir / "videos" / "w9.mp4"
    land.write_bytes(b"x")
    assert queue.set_landscape(conn, rid, str(land), None)
    count = _count_video_requests(bot, api, [True])
    api.calls.clear()
    for _ in range(3):
        now[0] += 60
        bot.maybe_send_next()
    assert count[0] == 1
    assert len([t for t in api.texts() if "video na šířku Telegram odmítl" in t]) == 1


@pg
def test_schvalit_po_odmitnute_sirce_projde(conn, api, meta, media_dir):
    # Telegram šířku odmítl (např. velikost); schválení na tom nezávisí
    bot = _make_bot(conn, api, meta, media_dir)
    now = [1000.0]
    bot.clock = lambda: now[0]
    rid = _add(conn, media_dir)
    bot.maybe_send_next()
    old = _get(conn, rid).telegram_msg_id
    _add_landscape(conn, media_dir, rid)
    _count_video_requests(bot, api, [True])
    now[0] += 60
    bot.maybe_send_next()
    bot.handle(_cb(f"ok:{rid}", msg_id=old))
    assert _get(conn, rid).status != "pending"


@pg
def test_sirka_a_b_a_zkusi_a_znovu(conn, api, meta, media_dir):
    from hlidac_reels import queue
    bot = _make_bot(conn, api, meta, media_dir)
    now = [1000.0]
    bot.clock = lambda: now[0]
    rid = _add(conn, media_dir)
    bot.maybe_send_next()
    a = _add_landscape(conn, media_dir, rid, n=1)
    reject = [True]
    count = _count_video_requests(bot, api, reject)
    now[0] += 60
    bot.maybe_send_next()  # A odmítnuto
    assert count[0] == 1
    reject[0] = False
    _add_landscape(conn, media_dir, rid, n=2)  # B projde
    now[0] += 60
    bot.maybe_send_next()
    assert count[0] == 2
    assert queue.get_state(conn, f"landscape_failed:{rid}") is None
    assert queue.set_landscape(conn, rid, str(a), "wsha1")  # zpět A (třeba už opravené)
    now[0] += 60
    bot.maybe_send_next()
    assert count[0] == 3
    assert _get(conn, rid).telegram_landscape_sha256 == "wsha1"


# --- sada variant a video na šířku --------------------------------------------

@pg
def test_sada_bez_sirky_posle_jen_videa_na_vysku(bot, conn, api, media_dir):
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    caps = _video_captions(api)
    assert len(caps) == 3 and not any("na šířku" in c for c in caps)
    assert all(_get(conn, i).telegram_landscape_msg_id is None for i in ids)


@pg
def test_sirka_u_varianty_sady_az_po_vyberu(conn, api, meta, media_dir):
    bot = _make_bot(conn, api, meta, media_dir, threads=FakeThreads(media_dir))
    now = [1000.0]
    bot.clock = lambda: now[0]
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    set_msg = _set_msg_id(conn, ids[0])
    _add_landscape(conn, media_dir, ids[1])
    api.calls.clear()
    now[0] += 60
    bot.maybe_send_next()
    # před výběrem se šířka neposílá a zpráva sady zůstane platná
    assert _video_captions(api) == []
    assert _set_msg_id(conn, ids[1]) == set_msg
    bot.handle(_cb(f"pick:{ids[1]}", msg_id=set_msg))
    picked_preview = _get(conn, ids[1]).telegram_msg_id
    # po výběru jde šířka jen pro informaci; Schválit z náhledu vybrané varianty platí i pro ni
    now[0] += 60
    bot.maybe_send_next()
    caps = _video_captions(api)
    assert len(caps) == 1 and "na šířku (Threads)" in caps[0]
    assert _get(conn, ids[1]).telegram_msg_id == picked_preview
    bot.handle(_cb(f"ok:{ids[1]}", msg_id=picked_preview))
    assert _get(conn, ids[1]).status == "published"


@pg
def test_autopilot_prepinac(bot, conn, api):
    from hlidac_reels import messages, queue
    from hlidac_reels.bot import AUTOPILOT_KEY
    assert bot._autopilot_on() is False
    bot.handle(_msg("/autopilot on"))
    assert queue.get_state(conn, AUTOPILOT_KEY) == "1"
    assert bot._autopilot_on() is True
    assert api.texts()[-1] == messages.autopilot_text(True)
    assert "bez schválení" in api.texts()[-1] and "/autopilot off" in api.texts()[-1]
    # bez argumentu: jen stav
    bot.handle(_msg("/autopilot"))
    assert api.texts()[-1] == messages.autopilot_text(True)
    bot.handle(_msg("/autopilot off"))
    assert queue.get_state(conn, AUTOPILOT_KEY) == "0"
    assert bot._autopilot_on() is False
    assert api.texts()[-1] == "Autopilot vypnut: sady jdou ke schválení."
    bot.handle(_msg("/autopilot"))
    assert api.texts()[-1] == messages.autopilot_text(False)
    # nápověda zmiňuje přepínač
    bot.handle(_msg("/neznamy"))
    assert "/autopilot" in api.texts()[-1]


# --- /pn a autopilot -----------------------------------------------------------

@pg
def test_pn_s_autopilotem_zalozi_priznak(bot, conn, api):
    from hlidac_reels import jobs
    _pn(conn, 2376, 300)
    bot.handle(_msg("/autopilot on"))
    bot.handle(_msg("/pn 2376"))
    (job,) = jobs.pending(conn)
    assert job.autopilot is True
    assert api.texts()[-1] == (f"Zadání #{job.id} (PN 2376, tisk 300) je 1. ve frontě. "
                               "Autopilot: po vygenerování se rovnou zveřejní.")


@pg
def test_pn_bez_autopilota_bez_priznaku(bot, conn, api):
    from hlidac_reels import jobs
    _pn(conn, 2376, 300)
    bot.handle(_msg("/pn 2376"))
    (job,) = jobs.pending(conn)
    assert job.autopilot is False
    assert "Autopilot" not in api.texts()[-1]


@pg
def test_pn_dvakrat_stejne(bot, conn, api):
    from hlidac_reels import jobs
    _pn(conn, 2376, 300)
    bot.handle(_msg("/autopilot on"))
    bot.handle(_cb("pn:2376"))
    bot.handle(_msg("/pn 2376"))
    assert len(jobs.pending(conn)) == 1
    assert api.texts()[-1] == "PN 2376 (tisk 300) už ve frontě je."


@pg
def test_video_a_vyber_nikdy_autopilot(bot, conn, api):
    from hlidac_reels import jobs
    _tisk(conn, 300)
    bot.handle(_msg("/autopilot on"))
    bot.handle(_msg("/video 300"))
    assert [j.autopilot for j in jobs.pending(conn)] == [False]
    assert "Autopilot" not in api.texts()[-1]
    bot._add_job("auto_pick", None, None)
    assert all(j.autopilot is False for j in jobs.pending(conn))


@pg
def test_pn_seznam_hlavicka_autopilota(bot, conn, api):
    _pn_bot(bot)
    _pn(conn, 2376, 300)
    bot.handle(_msg("/pn"))
    assert "Autopilot zapnut" not in api.texts()[-1]
    bot.handle(_msg("/autopilot on"))
    bot.handle(_msg("/pn"))
    assert api.texts()[-1].startswith("PN za posledních 48 h:\nAutopilot zapnut: vybrané PN se zveřejní bez schválení.")
