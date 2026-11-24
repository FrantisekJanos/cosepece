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
    """Jedno volání = nejvýš jedna jednotka (R3); čekající položka další neblokuje (R1)."""
    from hlidac_reels import queue
    _add(conn, media_dir, 1)
    _add(conn, media_dir, 2)
    bot.send_next()
    assert queue.counts(conn) == {"pending": 1, "queued": 1}
    assert len(api.sent("sendVideo")) == 1
    bot.send_next()
    assert queue.counts(conn) == {"pending": 2}
    assert len(api.sent("sendVideo")) == 2


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
    assert "Čeká na tebe (2)" in api.texts()[0]
    # ke každé čekající položce znovu náhled s tlačítky (až po Poslat tlačítka znovu)
    api.calls.clear()
    bot.handle(_cb("resend:0"))
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
    # další položku ke schválení pošle až smyčka (R3)
    assert _get(conn, nxt).status == "queued"
    bot.maybe_send_next()
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
    overview = api.texts()[0]
    assert "Problémy (2)" in overview and "chybný parametr" in overview and "nejistý" in overview
    api.calls.clear()
    bot.handle(_cb("resend:0"))
    msgs = api.sent()
    by_btn = {tuple(_buttons(b)): b["text"] for b in msgs}
    assert ("retry:%d" % f,) in by_btn and ("retry:%d" % u,) in by_btn
    assert "chybný parametr" in by_btn[("retry:%d" % f,)] and "tisk 221" in by_btn[("retry:%d" % f,)]
    assert len(msgs) == 2


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
    from datetime import datetime, timedelta, timezone
    now = [datetime.now(timezone.utc)]
    bot.now = lambda: now[0]
    slept = []

    def sleep(s):
        slept.append(s)
        if len(slept) == 1:
            del api.fail["sendVideo"]
            now[0] += timedelta(minutes=2)  # odklad po chybě uplynul
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
    # další jednotka až v dalším cyklu (R3)
    assert _get(conn, good).status == "queued"
    bot.send_next()
    assert _get(conn, good).status == "pending"
    assert queue.counts(conn) == {"rejected": 1, "pending": 1}


@pg
def test_telegram_odmitne_video_omezeny_pocet_pokusu(bot, conn, api, media_dir):
    """Odmítnuté video spotřebuje cyklus: za volání se vyřadí nejvýš jedna jednotka (R3)."""
    ids = [_add(conn, media_dir, n) for n in range(1, 4)]
    api.fail["sendVideo"] = httpx.Response(413, json={"ok": False, "description": "Request Entity Too Large"})
    bot.send_next()
    assert [_get(conn, i).status for i in ids] == ["rejected", "queued", "queued"]
    assert "Request Entity Too Large" in _get(conn, ids[0]).error
    bot.send_next()
    assert [_get(conn, i).status for i in ids] == ["rejected", "rejected", "queued"]


@pg
def test_vypadek_site_video_nevyradi(bot, conn, api, media_dir):
    """Přechodná chyba položku odloží (R4), výjimka nejde ven a před koncem odkladu se nic nezkouší."""
    from datetime import datetime, timedelta, timezone
    now = [datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)]
    bot.now = lambda: now[0]
    rid = _add(conn, media_dir)
    api.fail["sendVideo"] = httpx.ConnectError("down")
    bot.send_next()
    r = _get(conn, rid)
    assert r.status == "queued" and r.send_attempts == 1 and r.error is None
    assert r.next_send_at == now[0] + timedelta(minutes=1) and "ConnectError" in r.send_error
    api.fail["sendVideo"] = httpx.Response(502, json={"ok": False, "description": "Bad Gateway"})
    bot.send_next()  # odklad ještě běží: žádný pokus
    assert _get(conn, rid).send_attempts == 1
    now[0] += timedelta(minutes=1)
    bot.send_next()
    r = _get(conn, rid)
    assert r.status == "queued" and r.send_attempts == 2 and "Bad Gateway" in r.send_error
    # start() nic neposílá, pokus přijde ve smyčce
    now[0] += timedelta(minutes=5)
    bot.start()
    assert _get(conn, rid).send_attempts == 2
    bot.maybe_send_next()
    assert _get(conn, rid).status == "queued"
    assert _get(conn, rid).send_attempts == 3


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
    _fronta_resend(bot, api)
    msgs = api.sent()
    pend = [b for b in msgs if _buttons(b) == [f"ok:{p}", f"edit:{p}", f"no:{p}"]]
    assert len(pend) == 1
    assert _get(conn, p).telegram_msg_id not in (None, old_preview)
    appr = [b for b in msgs if f"#{a} " in b["text"]]
    assert len(appr) == 1 and "čeká na zveřejnění" in appr[0]["text"] and not _buttons(appr[0])
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
    bot.maybe_send_next()
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
    bot.maybe_send_next()
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
    _fronta_resend(bot, api)
    new = _get(conn, rid).telegram_msg_id
    assert new not in (None, old)
    assert _dropped(api) == [old]
    # když se nový náhled nepošle, starý si tlačítka nechá
    api.calls.clear()
    api.fail["sendMessage"] = httpx.ConnectError("down")
    _fronta_resend(bot, api)
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
    # sada má dál nevyřízenou vybranou variantu
    assert [_get(conn, i).status for i in ids[1:]] == ["pending", "pending"]
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
    _fronta_resend(bot, api)
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


def _remaining(api) -> list[dict]:
    """Zprávy „Sada #N: zbývá…“."""
    return [b for b in api.sent() if "zbývá" in b.get("text", "") or "zbývají" in b.get("text", "")]


@pg
def test_po_rozhodnuti_zbyva_varianta(bot, conn, api, meta, media_dir):
    """Incident: 2 ze 3 vybrané a schválené, varianta 1 zůstala bez připomenutí."""
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    set_msg = _set_msg_id(conn, ids[0])
    bot.handle(_cb(f"pick:{ids[1]}", msg_id=set_msg))
    bot.handle(_cb(f"pick:{ids[2]}", msg_id=set_msg))
    api.calls.clear()
    bot.handle(_cb(f"ok:{ids[1]}"))
    # druhá vybraná ještě čeká: zatím žádná připomínka
    assert _remaining(api) == []
    bot.handle(_cb(f"no:{ids[2]}"))
    rem = _remaining(api)
    assert len(rem) == 1
    assert "Sada #7: zbývá varianta 1. Vybrat, nebo Hotovo." in rem[0]["text"]
    assert _buttons(rem[0]) == [f"pick:{ids[0]}", "doneset:7"]
    new_msg = _set_msg_id(conn, ids[0])
    assert new_msg != set_msg and set_msg in _dropped(api)
    # nová zpráva platí, Vybrat z ní funguje
    api.calls.clear()
    bot.handle(_cb(f"pick:{ids[0]}", msg_id=new_msg))
    assert [_buttons(b)[0] for b in _previews(api)] == [f"ok:{ids[0]}"]


@pg
def test_zbyva_i_kdyz_publikace_selze(bot, conn, api, meta, media_dir):
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    set_msg = _set_msg_id(conn, ids[0])
    bot.handle(_cb(f"pick:{ids[1]}", msg_id=set_msg))
    api.calls.clear()
    meta.fail["ig_create"] = MetaError("(#100) chybný parametr", status=400)
    bot.handle(_cb(f"ok:{ids[1]}"))
    assert _get(conn, ids[1]).status == "failed"
    assert len(_remaining(api)) == 1


@pg
def test_nezbyva_nic_bez_zpravy(bot, conn, api, meta, media_dir):
    ids = _add_set(conn, media_dir, 7, n=2)
    bot.send_next()
    set_msg = _set_msg_id(conn, ids[0])
    bot.handle(_cb(f"pick:{ids[0]}", msg_id=set_msg))
    bot.handle(_cb(f"pick:{ids[1]}", msg_id=set_msg))
    api.calls.clear()
    bot.handle(_cb(f"ok:{ids[0]}"))
    bot.handle(_cb(f"no:{ids[1]}"))
    assert _remaining(api) == []
    assert not [b for b in api.sent() if any(d.startswith("pick:") for d in _buttons(b))]


@pg
def test_pick_zapise_picked_at(bot, conn, api, media_dir):
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    assert _get(conn, ids[0]).picked_at is None
    bot.handle(_cb(f"pick:{ids[0]}", msg_id=_set_msg_id(conn, ids[0])))
    assert _get(conn, ids[0]).picked_at is not None
    assert _get(conn, ids[1]).picked_at is None


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
    # automatická sada čeká: denní limit je vyčerpaný
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
    """Automatickou sadu brzdí jen nedotčená předchozí automatická sada (R2); po výběru odejde."""
    a = _add_set(conn, media_dir, 1)
    b = _add_set(conn, media_dir, 2)
    bot.send_next()
    bot.send_next()
    bot.send_next()
    assert _get(conn, b[0]).status == "queued"
    assert len(api.sent("sendVideo")) == 3
    notes = [t for t in api.texts() if t.startswith("Sada #2 čeká, dokud nevybereš")]
    assert len(notes) == 1 and "/dalsi" in notes[0]
    bot.handle(_cb(f"pick:{a[0]}", msg_id=_set_msg_id(conn, a[0])))
    bot.send_next()  # další cyklus
    assert [_get(conn, i).status for i in b] == ["pending"] * 3


@pg
def test_vyzadana_sada_neceka_na_automatickou(bot, conn, api, media_dir):
    """Vyžádaná sada (R1) jde i vedle nedotčené automatické sady."""
    _add_set(conn, media_dir, 1)
    m = _add_set(conn, media_dir, 2, source="manual")
    bot.send_next()
    bot.send_next()
    assert [_get(conn, i).status for i in m] == ["pending"] * 3


def _clocked(bot):
    """Řízený čas bota: tick(s) posune hodiny smyčky i bot.now."""
    from datetime import datetime, timedelta, timezone
    t = {"mono": 1000.0, "now": datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)}
    bot.clock = lambda: t["mono"]
    bot.now = lambda: t["now"]

    def tick(seconds: float = 60):
        t["mono"] += seconds
        t["now"] += timedelta(seconds=seconds)
    return tick


def _set_offers(api) -> list[dict]:
    return [b for b in api.sent() if any(d.startswith("pick:") for d in _buttons(b))]


def _statuses(conn, ids) -> list[str]:
    return [_get(conn, i).status for i in ids]


@pg
def test_incident_nevybrana_varianta_neblokuje(bot, conn, api, media_dir):
    """Incident 24. 11.: nevybraná varianta sady #6 nesmí zdržet další hotové sady; za cyklus jedna (R3)."""
    from hlidac_reels import queue
    tick = _clocked(bot)
    s6 = _add_set(conn, media_dir, 6, source="manual")
    queue.transition(conn, s6[0], "queued", "pending", sent_at=bot.now(), telegram_video_msg_id=1,
                     telegram_msg_id=2)
    for i in s6[1:]:
        queue.mark_picked(conn, 6, i)
        queue.transition(conn, i, "queued", "published", sent_at=bot.now(), decided_at=bot.now())
    later = [_add_set(conn, media_dir, n, source="manual") for n in (8, 9, 10)]
    for k in range(3):
        if k:
            tick()
        bot.maybe_send_next()
        for j, ids in enumerate(later):
            assert _statuses(conn, ids) == ["pending" if j <= k else "queued"] * 3, (k, j)
        assert len(api.sent("sendVideo")) == 3 * (k + 1)
    assert _get(conn, s6[0]).status == "pending"
    assert len(_set_offers(api)) == 3


def _video_handler(api, decide):
    """Telegram, kde decide(n, caption) vrátí odpověď pro n-té sendVideo (None = normální odpověď)."""
    count = [0]

    def handler(request):
        if request.url.path.endswith("/sendVideo"):
            count[0] += 1
            resp = decide(count[0], request.read().decode("utf-8", "replace"))
            if resp is not None:
                return resp
        return api(request)
    return handler, count


@pg
def test_restart_uprostred_sady(conn, api, meta, media_dir):
    """Review Focus 1: 2 ze 3 videí odešla, pak 502 a restart: pošle se jen 3. video a jedna zpráva sady."""
    from hlidac_reels.bot import Bot
    from hlidac_reels.telegram import Telegram
    handler, count = _video_handler(
        api, lambda n, raw: httpx.Response(502, json={"ok": False, "description": "Bad Gateway"}) if n == 3 else None)
    tg = Telegram(TOKEN, client=httpx.Client(transport=httpx.MockTransport(handler)))
    bot = Bot(conn, tg, meta, CHAT, str(media_dir), PUBLIC_BASE)
    _clocked(bot)
    ids = _add_set(conn, media_dir, 7, source="manual")
    bot.maybe_send_next()
    reels = [_get(conn, i) for i in ids]
    assert [r.status for r in reels] == ["queued"] * 3
    assert reels[0].telegram_video_msg_id and reels[1].telegram_video_msg_id
    assert not reels[2].telegram_video_msg_id
    # restart: nový bot nad touž frontou
    bot = Bot(conn, tg, meta, CHAT, str(media_dir), PUBLIC_BASE)
    tick2 = _clocked(bot)
    tick2(120)
    bot.maybe_send_next()
    tick2()
    bot.maybe_send_next()
    assert count[0] == 4
    assert len(_set_offers(api)) == 1
    assert _statuses(conn, ids) == ["pending"] * 3


@pg
def test_start_a_prevzeti_neobchazi_r3(bot, conn, api, media_dir, monkeypatch):
    """Restart s plnou frontou a převzetí několika sad z mezisložky: do posunu hodin o SEND_INTERVAL
    odejde právě jedna jednotka (R3, ochrana před zahlcením chatu po výpadku PC)."""
    from hlidac_reels import inbox
    from hlidac_reels.bot import SEND_INTERVAL
    tick = _clocked(bot)
    _add(conn, media_dir, 1)
    _add_set(conn, media_dir, 2, source="manual")
    sets = []

    def ingest(c, i, m, autopilot_on=False):
        out = []
        for n in (11, 12, 13):
            ids = _add_set(c, media_dir, n, source="manual")
            sets.append(ids)
            out.append(inbox.IngestResult(n, ids, {}, True))
        return out
    monkeypatch.setattr(inbox, "ingest_all", ingest)
    bot.inbox_dir = str(media_dir / "inbox")
    api.fail["getUpdates"] = httpx.ConnectError("down")

    def units() -> int:
        return len(_previews(api)) + len(_set_offers(api))
    bot.start()
    for _ in range(3):  # obrátky smyčky během jednoho SEND_INTERVAL
        bot.maybe_send_next()
        bot.maybe_ingest()
        bot.maybe_autopublish()
        tick(SEND_INTERVAL / 4)
    assert len(sets) == 3
    assert units() == 1
    tick(SEND_INTERVAL)
    bot.maybe_send_next()
    bot.maybe_ingest()
    assert units() == 2


@pg
def test_trvala_prechodna_chyba(conn, api, meta, media_dir):
    """Review Focus 2: 429 u první sady ji odkládá, druhá odejde v dalším cyklu; po 5 pokusech held
    a právě jedna zpráva s tlačítky."""
    from hlidac_reels import queue
    from hlidac_reels.bot import SEND_INTERVAL, Bot
    from hlidac_reels.telegram import Telegram
    handler, count = _video_handler(
        api, lambda n, raw: httpx.Response(429, json={"ok": False, "description": "Too Many Requests"})
        if "#1 · varianta" in raw else None)
    tg = Telegram(TOKEN, client=httpx.Client(transport=httpx.MockTransport(handler)))
    bot = Bot(conn, tg, meta, CHAT, str(media_dir), PUBLIC_BASE)
    tick = _clocked(bot)
    first = _add_set(conn, media_dir, 1, source="manual")
    second = _add_set(conn, media_dir, 2, source="manual")
    bot.maybe_send_next()
    assert _statuses(conn, first) == ["queued"] * 3 and _get(conn, first[0]).send_attempts == 1
    assert _statuses(conn, second) == ["queued"] * 3
    tick(SEND_INTERVAL)
    bot.maybe_send_next()
    assert _statuses(conn, second) == ["pending"] * 3
    # další cykly, dokud první sada nepřejde do held (odklady 1, 5, 15, 60 min)
    for _ in range(200):
        tick(SEND_INTERVAL)
        bot.maybe_send_next()
    assert _statuses(conn, first) == ["held"] * 3
    assert _get(conn, first[0]).send_attempts == queue.SEND_MAX_ATTEMPTS
    # první video sady se zkusilo jednou za pokus, druhá sada 3 videa
    assert count[0] == queue.SEND_MAX_ATTEMPTS + 3
    held_msgs = [b for b in api.sent() if any(d.startswith("sendretry:") for d in _buttons(b))]
    assert len(held_msgs) == 1
    assert _buttons(held_msgs[0]) == [f"sendretry:{first[0]}", f"drop:{first[0]}"]
    assert "Sada #1" in held_msgs[0]["text"] and "Too Many Requests" in held_msgs[0]["text"]


@pg
def test_vsechna_videa_odmitnuta(conn, api, meta, media_dir):
    """Review Focus 5: všechna videa sady 400 → jedna zpráva o vyřazení, další jednotka v příštím cyklu."""
    from hlidac_reels.bot import Bot
    from hlidac_reels.telegram import Telegram
    handler, count = _video_handler(
        api, lambda n, raw: httpx.Response(400, json={"ok": False, "description": "Bad Request: wrong file"})
        if "varianta" in raw else None)
    tg = Telegram(TOKEN, client=httpx.Client(transport=httpx.MockTransport(handler)))
    bot = Bot(conn, tg, meta, CHAT, str(media_dir), PUBLIC_BASE)
    tick = _clocked(bot)
    ids = _add_set(conn, media_dir, 7, source="manual")
    solo = _add(conn, media_dir)
    bot.maybe_send_next()
    assert _statuses(conn, ids) == ["rejected"] * 3
    notes = [t for t in api.texts() if "vyřazen" in t]
    assert len(notes) == 1 and notes[0].startswith("Sada #7 vyřazena")
    assert _get(conn, solo).status == "queued"
    tick()
    bot.maybe_send_next()
    assert _get(conn, solo).status == "pending"
    assert count[0] == 4


@pg
def test_ztracena_zprava_sady_se_doplni(bot, conn, api, media_dir):
    """Zpráva sady neodešla: další cyklus ji pošle sám, bez /fronta; videa podruhé ne."""
    tick = _clocked(bot)
    ids = _add_set(conn, media_dir, 7, source="manual")
    api.fail["sendMessage"] = httpx.ConnectError("down")
    bot.maybe_send_next()
    assert _statuses(conn, ids) == ["pending"] * 3
    assert {_get(conn, i).telegram_msg_id for i in ids} == {None}
    del api.fail["sendMessage"]
    tick()
    bot.maybe_send_next()
    offers = _set_offers(api)
    assert len(offers) == 1
    assert _buttons(offers[0]) == [f"pick:{i}" for i in ids] + ["doneset:7"]
    msg_id = _get(conn, ids[0]).telegram_msg_id
    assert msg_id and {_get(conn, i).telegram_msg_id for i in ids} == {msg_id}
    assert len(api.sent("sendVideo")) == 3
    # další cyklus už nic neposílá
    tick()
    bot.maybe_send_next()
    assert len(_set_offers(api)) == 1


@pg
def test_ztraceny_nahled_se_doplni(bot, conn, api, media_dir):
    """Náhled samostatného videa neodešel: video zůstane odeslané jednou a náhled doplní další cyklus."""
    tick = _clocked(bot)
    rid = _add(conn, media_dir)
    api.fail["sendMessage"] = httpx.ConnectError("down")
    bot.maybe_send_next()
    r = _get(conn, rid)
    assert r.status == "pending" and r.telegram_video_msg_id and r.telegram_msg_id is None
    del api.fail["sendMessage"]
    tick()
    bot.maybe_send_next()
    assert len(_previews(api)) == 1 and _get(conn, rid).telegram_msg_id
    assert len(api.sent("sendVideo")) == 1


@pg
def test_sendretry_a_drop(bot, conn, api, media_dir):
    """Zkusit znovu vrátí celou held sadu do fronty s nulovými pokusy, Zahodit ji zamítne; jinde „už nečeká“."""
    from datetime import timedelta
    from hlidac_reels import queue
    _clocked(bot)
    ids = _add_set(conn, media_dir, 7, n=2, source="manual")
    solo = _add(conn, media_dir, 1)
    for i in [*ids, solo]:
        queue.set_fields(conn, i, send_attempts=5, send_error="HTTP 429",
                         next_send_at=bot.now() + timedelta(hours=1))
        queue.transition(conn, i, "queued", "held")
    waiting = _add(conn, media_dir, 2)
    bot.send_next()
    assert _get(conn, waiting).status == "pending"

    def answer():
        return api.sent("answerCallbackQuery")[-1].get("text", "")

    # na čekající položce tlačítka nic nemění
    for action in ("sendretry", "drop"):
        bot.handle(_cb(f"{action}:{waiting}"))
        assert "už nečeká" in answer()
        assert _get(conn, waiting).status == "pending"
    # Zahodit: samostatné video
    bot.handle(_cb(f"drop:{solo}", msg_id=55))
    r = _get(conn, solo)
    assert r.status == "rejected" and r.error == "zahozeno"
    assert f"#{solo} zahozeno." in api.texts()
    assert (55, []) in _edits(api)
    bot.handle(_cb(f"sendretry:{solo}"))
    assert "už nečeká" in answer() and _get(conn, solo).status == "rejected"
    # Zkusit znovu: celá sada zpět, pokusy vynulované, hned se pošle
    bot.handle(_cb(f"sendretry:{ids[1]}", msg_id=56))
    assert (56, []) in _edits(api)
    reels = [_get(conn, i) for i in ids]
    assert [r.status for r in reels] == ["pending"] * 2
    assert [r.send_attempts for r in reels] == [0, 0] and {r.next_send_at for r in reels} == {None}
    bot.handle(_cb(f"drop:{ids[0]}"))
    assert "už nečeká" in answer() and _statuses(conn, ids) == ["pending"] * 2


@pg
def test_fronta_ukaze_held_s_tlacitky(bot, conn, api, media_dir):
    from hlidac_reels import queue
    ids = _add_set(conn, media_dir, 7, n=2, source="manual")
    for i in ids:
        queue.transition(conn, i, "queued", "held", send_attempts=5)
    _fronta_resend(bot, api)
    held = [b for b in api.sent() if any(d.startswith("sendretry:") for d in _buttons(b))]
    assert len(held) == 1 and _buttons(held[0]) == [f"sendretry:{ids[0]}", f"drop:{ids[0]}"]


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
    _fronta_resend(bot, api)
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
    _fronta_resend(bot, api)
    assert [_buttons(b) for b in api.sent() if _buttons(b)] == [
        [f"ok:{ids[0]}", f"edit:{ids[0]}", f"no:{ids[0]}"]]


@pg
def test_fronta_znovu_nabidne_sadu_a_stara_zprava_neplati(bot, conn, api, media_dir):
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    old = _set_msg_id(conn, ids[0])
    _fronta_resend(bot, api)
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
    """Výpadek sítě: celá sada zůstane queued a odloží se jako celek (R4), výjimka nejde ven."""
    ids = _add_set(conn, media_dir, 7)
    api.fail["sendVideo"] = httpx.ConnectError("down")
    bot.send_next()
    reels = [_get(conn, i) for i in ids]
    assert [r.status for r in reels] == ["queued"] * 3
    assert [r.send_attempts for r in reels] == [1] * 3
    assert len({r.next_send_at for r in reels}) == 1 and reels[0].next_send_at is not None


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
def test_cerstve_stazeni_sady(bot, conn, api, media_dir, monkeypatch):
    from hlidac_reels import fresh
    a = _add_set(conn, media_dir, 1)
    bot.send_next()
    set_msg = _set_msg_id(conn, a[0])

    def retire(c, now):
        for i in a:
            _to(c, i, "withdrawn")
        return [(1, "withdrawn")]
    monkeypatch.setattr(fresh, "detect", lambda c, now: [])
    monkeypatch.setattr(fresh, "retire", retire)
    bot.maybe_fresh()
    assert "#1 staženo: nález už není mezi hlavními" in api.texts()
    assert set_msg in _dropped(api)


def _later(bot, **delta):
    """Hodiny bota posunuté dopředu (lhůty queue.expire počítají s bot.now)."""
    from datetime import datetime, timedelta, timezone
    at = datetime.now(timezone.utc) + timedelta(**delta)
    bot.now = lambda: at
    bot._next_fresh = float("-inf")


@pg
def test_vyprseni_posle_zpravu_a_odebere_tlacitka(bot, conn, api, media_dir):
    ids = _add_set(conn, media_dir, 1)
    bot.send_next()
    set_msg = _set_msg_id(conn, ids[0])
    # těsně před lhůtou (7 dní od odeslání automatické sady) nic
    _later(bot, days=7, minutes=-5)
    api.calls.clear()
    bot.maybe_fresh()
    assert {_get(conn, i).status for i in ids} == {"pending"}
    _later(bot, days=7, minutes=5)
    bot.maybe_fresh()
    assert {_get(conn, i).status for i in ids} == {"expired"}
    assert "Sada #1 vypršela: 7 dní bez výběru." in api.texts()
    assert set_msg in _dropped(api)
    # podruhé už nic
    api.calls.clear()
    _later(bot, days=8)
    bot.maybe_fresh()
    assert api.calls == []


@pg
def test_vyprseni_zbylych_variant_po_rozhodnuti(bot, conn, api, media_dir):
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    set_msg = _set_msg_id(conn, ids[0])
    bot.handle(_cb(f"pick:{ids[0]}", msg_id=set_msg))
    preview = _get(conn, ids[0]).telegram_msg_id
    bot.handle(_cb(f"no:{ids[0]}", msg_id=preview))
    # po rozhodnutí přišla nová zpráva sady se zbylými variantami
    rest_msg = _set_msg_id(conn, ids[1])
    api.calls.clear()
    _later(bot, hours=25)
    bot.maybe_fresh()
    assert "Sada #7: nevybrané varianty vypršely (24 h od posledního rozhodnutí)." in api.texts()
    assert _dropped(api) == [rest_msg]
    assert [_get(conn, i).status for i in ids] == ["rejected", "expired", "expired"]


@pg
def test_stare_tlacitko_po_vyprseni(bot, conn, api, meta, media_dir):
    ids = _add_set(conn, media_dir, 7, source="manual")
    bot.send_next()
    bot.handle(_cb(f"pick:{ids[0]}", msg_id=_set_msg_id(conn, ids[0])))
    preview = _get(conn, ids[0]).telegram_msg_id
    api.calls.clear()
    _later(bot, days=14, minutes=5)
    bot.maybe_fresh()
    assert _get(conn, ids[0]).status == "expired"
    assert "Sada #7: vybraná varianta #%d vypršela (14 dní bez schválení)." % ids[0] in api.texts()
    assert preview in _dropped(api)
    # Review Focus 3: staré Schválit na vypršelé položce nic nezmění
    api.calls.clear()
    bot.handle(_cb(f"ok:{ids[0]}", msg_id=preview))
    assert api.sent("answerCallbackQuery")[-1].get("text") == "Položka už nečeká na schválení."
    assert _get(conn, ids[0]).status == "expired"
    assert sum(meta.calls.values()) == 0


@pg
def test_vyprseni_held_zamitne_a_odebere_tlacitka(bot, conn, api, media_dir):
    from hlidac_reels import queue
    rid = _add(conn, media_dir)
    api.fail["sendVideo"] = httpx.Response(429, json={"ok": False, "description": "Too Many Requests"})
    for _ in range(queue.SEND_MAX_ATTEMPTS):
        bot.send_next()
        queue.set_fields(conn, rid, next_send_at=None)
    assert _get(conn, rid).status == "held"
    held_msg = int(queue.get_state(conn, f"{queue.HELD_MSG_KEY}{rid}"))
    queue.set_fields(conn, rid, next_send_at=bot.now())
    api.fail.pop("sendVideo")
    api.calls.clear()
    _later(bot, days=7, minutes=5)
    bot.maybe_fresh()
    r = _get(conn, rid)
    assert r.status == "rejected"
    assert f"#{rid} zamítnuto: 7 dní se nepodařilo poslat ke schválení." in api.texts()
    assert held_msg in _dropped(api)
    # staré Zkusit znovu už nic nezmění
    bot.handle(_cb(f"sendretry:{rid}", msg_id=held_msg))
    assert api.sent("answerCallbackQuery")[-1].get("text") == "Položka už nečeká na odeslání."
    assert _get(conn, rid).status == "rejected"


@pg
def test_pripominka_automaticke_sady_jednou(bot, conn, api, media_dir):
    ids = _add_set(conn, media_dir, 3)
    bot.send_next()
    api.calls.clear()
    _later(bot, hours=12, minutes=5)
    bot.maybe_fresh()
    _later(bot, hours=13)
    bot.maybe_fresh()
    assert api.texts().count("Sada #3 čeká 12 h. Vybrat, nebo Hotovo.") == 1
    assert {_get(conn, i).status for i in ids} == {"pending"}


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
    # převzatou sadu pošle smyčka (maybe_send_next), ne převzetí samo (R3)
    assert {_get(conn, i).status for i in ids} == {"queued"}
    bot.maybe_send_next()
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
    b = _add_set(conn, media_dir, 2, source="manual")

    def retire(c, now):
        for i in a:
            _to(c, i, "withdrawn")
        return [(1, "withdrawn")]
    monkeypatch.setattr(fresh, "detect", lambda c, now: [])
    monkeypatch.setattr(fresh, "retire", retire)
    # neodeslaná sada nevyprší ani po měsíci (lhůty běží od sent_at) a stažení je bez zprávy
    _later(bot, days=30)
    bot.maybe_fresh()
    assert api.calls == []
    assert {_get(conn, i).status for i in a} == {"withdrawn"}
    assert {_get(conn, i).status for i in b} == {"queued"}


@pg
def test_sada_vypadek_site_u_druheho_videa(bot, conn, api, media_dir):
    """1. video sady odešlo, u 2. výpadek: sada zůstane queued, id 1. videa je uložené a znovu se nepošle."""
    from datetime import datetime, timedelta, timezone
    now = [datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)]
    bot.now = lambda: now[0]
    ids = _add_set(conn, media_dir, 7)
    orig = api.__call__
    down = [True]

    def flaky(request):
        if request.url.path.endswith("/sendVideo") and down[0] and len(api.sent("sendVideo")) >= 1:
            raise httpx.ConnectError("down")
        return orig(request)
    bot.tg._client = httpx.Client(transport=httpx.MockTransport(flaky))
    bot.send_next()
    assert len(api.sent("sendVideo")) == 1
    reels = [_get(conn, i) for i in ids]
    assert [r.status for r in reels] == ["queued"] * 3
    assert reels[0].telegram_video_msg_id and not reels[1].telegram_video_msg_id
    down[0] = False
    now[0] += timedelta(minutes=1)
    bot.send_next()
    assert len(api.sent("sendVideo")) == 3
    assert [_get(conn, i).status for i in ids] == ["pending"] * 3


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
    assert "Fronta je prázdná." in api.texts()[-1] and "PC zatím nebylo vidět" in api.texts()[-1]
    a = jobs.add(conn, source="manual", ct=221)
    queue.set_state(conn, "worker_seen_at", "2026-11-20T14:05:00+01:00")
    bot.handle(_msg("/fronta"))
    text = api.texts()[-1]
    assert f"Ve frontě zadání (1)\n#{a} tisk 221 · 1. v pořadí" in text
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
    assert f"#{j} PN 2376+2377 · 1. v pořadí" in api.texts()[-1]
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
    bot.send_next()
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
    from datetime import datetime, timedelta, timezone
    now = [datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)]
    bot.now = lambda: now[0]
    bot.tg = Telegram(TOKEN, client=httpx.Client(transport=httpx.MockTransport(flaky)))
    bot.send_next()
    r = _get(conn, rid)
    assert r.status == "queued" and r.telegram_video_msg_id and r.send_attempts == 1
    # další pokus pošle jen šířku, video na výšku podruhé ne
    now[0] += timedelta(minutes=1)
    bot.send_next()
    r = _get(conn, rid)
    assert count[0] == 3
    assert r.status == "pending" and r.telegram_landscape_msg_id and r.telegram_msg_id


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


@pg
def test_autopilot_off_smaze_priznak_rozpracovanych_uloh(bot, conn, api, tmp_path, monkeypatch):
    # on -> /pn X -> off (PC offline) -> on -> sada X se převezme: nesmí se zveřejnit bez schválení
    import json as _json
    from hlidac_reels import inbox, jobs, media, messages, queue
    from hlidac_reels.media import VideoInfo
    bot.handle(_msg("/autopilot on"))
    jid = jobs.add(conn, source="manual", ct=311, focus_pn=[2376], autopilot=True)
    done = jobs.add(conn, source="manual", ct=312, autopilot=True)
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status='done' WHERE id=%s", (done,))
    conn.commit()
    bot.handle(_msg("/autopilot off"))
    assert api.texts()[-1] == messages.autopilot_text(False, 0, 1)
    assert "Rozpracované úlohy autopilota (1)" in api.texts()[-1]
    by_id = {j.id: j for j in jobs.pending(conn)}
    assert by_id[jid].autopilot is False
    with conn.cursor() as cur:
        cur.execute("SELECT autopilot FROM reel_jobs WHERE id=%s", (done,))
        assert cur.fetchone()[0] is True  # hotová úloha se nemění
    conn.commit()
    bot.handle(_msg("/autopilot on"))
    # worker úlohu dokončil a sadu odevzdal do mezisložky
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status='done' WHERE id=%s", (jid,))
    conn.commit()
    folder = tmp_path / "inbox" / str(jid)
    folder.mkdir(parents=True)
    variants = []
    for n in (1, 2):
        (folder / f"variant-{n}.mp4").write_bytes(f"ok-{n}".encode())
        variants.append({"n": n, "template": "template_v3", "format": "pn", "caption": f"Popisek {n}", "card": {}})
    (folder / "manifest.json").write_text(
        _json.dumps({"job_id": jid, "ct": 311, "source": "manual", "variants": variants}), encoding="utf-8")
    monkeypatch.setattr(media, "probe", lambda path: VideoInfo("h264", "aac", 1080, 1920, 30.0, 10))
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media", autopilot_on=bot._autopilot_on())
    assert res[0].ok and res[0].autopilot is False
    assert [(r.status, r.autopilot) for r in queue.set_items(conn, jid)] == [("queued", False)] * 2


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


# --- autopilot: zveřejnění a hlášení ---------------------------------------------

def _autopilot_set(conn, media_dir: Path, set_id: int, n: int = 2, focus_pn=None) -> list[int]:
    """Sada autopilota jako po převzetí (Task 4): položky approved s autopilot=true, úloha s PN."""
    ids = _add_set(conn, media_dir, set_id, n=n, ct=300, source="manual")
    with conn.cursor() as cur:
        cur.execute("INSERT INTO reel_jobs (id, source, ct, focus_pn, autopilot, status) "
                    "VALUES (%s,'manual',300,%s,true,'done')", (set_id, focus_pn))
        cur.execute("UPDATE reel_queue SET status='approved', autopilot=true, decided_at=now(), sent_at=now() "
                    "WHERE id = ANY(%s)", (ids,))
    conn.commit()
    from hlidac_reels import queue
    from hlidac_reels.bot import AUTOPILOT_KEY
    queue.set_state(conn, AUTOPILOT_KEY, "1")  # převzetí jako approved jen se zapnutým přepínačem
    return ids


def _autopilot_bot(conn, api, meta, media_dir, threads=None):
    b = _make_bot(conn, api, meta, media_dir, threads=threads)
    t = [1000.0]
    b.clock = lambda: t[0]
    return b, t


@pg
def test_autopilot_publikuje_a_hlasi_odkazy(conn, api, meta, media_dir):
    from hlidac_reels import queue
    th = FakeThreads(media_dir)
    bot, t = _autopilot_bot(conn, api, meta, media_dir, threads=th)
    ids = _autopilot_set(conn, media_dir, 7, focus_pn=[2376])
    queue.set_state(conn, "autopilot_skipped:7",
                    json.dumps([{"n": 3, "format": "pn", "errors": ["render selhal"]}]))
    bot.maybe_autopublish()
    r = _get(conn, ids[0])
    assert r.status == "published"
    done = [x for x in api.texts() if x.startswith("Autopilot zveřejnil")]
    assert done == [f"Autopilot zveřejnil #{ids[0]} (PN 2376, tisk 300, varianta vysledek): "
                    f"FB https://www.facebook.com/reel/V1 · IG https://www.instagram.com/reel/M1/ · "
                    f"Threads {TH_LINK}"]
    # nevzniklá varianta se ohlásí až po poslední položce sady
    assert not any("nevznikla" in x for x in api.texts())
    t[0] += 50
    bot.maybe_autopublish()
    assert _get(conn, ids[1]).status == "published"
    assert f"#{ids[1]} (PN 2376, tisk 300, varianta v2)" in api.texts()[-2]
    assert api.texts()[-1] == "Varianta 3 (pn) nevznikla: render selhal"
    assert queue.get_state(conn, "autopilot_skipped:7") is None
    # už není co zveřejnit: nic dalšího
    n = len(api.calls)
    t[0] += 50
    bot.maybe_autopublish()
    assert len(api.calls) == n
    assert meta.calls["fb_upload_reel"] == 2


@pg
def test_autopilot_done_bez_chybejicich_odkazu(conn, media_dir):
    import dataclasses
    from hlidac_reels.messages import autopilot_done
    r = dataclasses.replace(_get(conn, _add(conn, media_dir, ct=300)), format="v2", fb_permalink="https://fb/x",
                            ig_permalink=None, threads_permalink="")
    assert autopilot_done(r, None) == f"Autopilot zveřejnil #{r.id} (tisk 300, varianta v2): FB https://fb/x"


@pg
def test_autopilot_chyba_publikace_pokracuje(conn, api, meta, media_dir):
    bot, t = _autopilot_bot(conn, api, meta, media_dir)
    ids = _autopilot_set(conn, media_dir, 8)
    meta.fail["ig_create"] = MetaError("(#2) služba nedostupná", status=500)
    bot.maybe_autopublish()
    assert _get(conn, ids[0]).status == "failed"
    last = api.sent()[-1]
    assert "služba nedostupná" in last["text"] and _buttons(last) == [f"retry:{ids[0]}"]
    assert _get(conn, ids[1]).status == "approved"
    del meta.fail["ig_create"]
    t[0] += 50
    bot.maybe_autopublish()
    assert _get(conn, ids[1]).status == "published"
    assert _get(conn, ids[0]).status == "failed"
    assert api.texts()[-1].startswith(f"Autopilot zveřejnil #{ids[1]}")


@pg
def test_autopilot_restart_publishing(conn, api, meta, media_dir, monkeypatch):
    from hlidac_reels import publish, queue
    bot, t = _autopilot_bot(conn, api, meta, media_dir)
    ids = _autopilot_set(conn, media_dir, 9, n=1)
    assert queue.transition(conn, ids[0], "approved", "publishing")
    bot.start()
    assert _get(conn, ids[0]).status == "unknown"
    assert any(f"retry:{ids[0]}" in _buttons(b) for b in api.sent())
    called = []
    monkeypatch.setattr(publish, "run", lambda *a, **k: called.append(a))
    bot.maybe_autopublish()
    assert called == [] and sum(meta.calls.values()) == 0
    assert _get(conn, ids[0]).status == "unknown"


@pg
def test_autopilot_po_jedne_za_cyklus(conn, api, meta, media_dir):
    from hlidac_reels.bot import SEND_INTERVAL
    bot, t = _autopilot_bot(conn, api, meta, media_dir)
    ids = _autopilot_set(conn, media_dir, 10, n=3)
    bot.maybe_autopublish()
    bot.maybe_autopublish()
    assert [_get(conn, i).status for i in ids] == ["published", "approved", "approved"]
    t[0] += SEND_INTERVAL - 1
    bot.maybe_autopublish()
    assert _get(conn, ids[1]).status == "approved"
    t[0] += 1
    bot.maybe_autopublish()
    assert [_get(conn, i).status for i in ids] == ["published", "published", "approved"]


@pg
def test_start_nechá_approved_autopilota(conn, api, meta, media_dir):
    from hlidac_reels import queue
    bot, t = _autopilot_bot(conn, api, meta, media_dir)
    ids = _autopilot_set(conn, media_dir, 11, n=1)
    manual = _add(conn, media_dir, 77)
    assert queue.transition(conn, manual, "queued", "pending")
    assert queue.transition(conn, manual, "pending", "approved")
    bot.start()
    assert _get(conn, ids[0]).status == "approved"
    assert _get(conn, manual).status == "failed"
    assert sum(meta.calls.values()) == 0
    bot.maybe_autopublish()
    assert _get(conn, ids[0]).status == "published"


@pg
def test_next_autopilot_nejstarsi(conn, media_dir):
    from hlidac_reels import queue
    assert queue.next_autopilot(conn) is None
    plain = _add(conn, media_dir, 88)
    assert queue.transition(conn, plain, "queued", "pending")
    assert queue.transition(conn, plain, "pending", "approved")
    assert queue.next_autopilot(conn) is None
    ids = _autopilot_set(conn, media_dir, 13)
    assert queue.next_autopilot(conn).id == ids[0]


@pg
def test_fronta_autopilot_ceka_na_zverejneni(conn, api, meta, media_dir):
    bot, t = _autopilot_bot(conn, api, meta, media_dir)
    ids = _autopilot_set(conn, media_dir, 12, n=1)
    bot.handle(_msg("/fronta"))
    (text,) = api.texts()
    assert "Čeká na tebe" not in text
    assert "Hotové, čekají na odeslání (1)\n#12 tisk 300 · varianta 1 · čeká na automatické zveřejnění" in text


@pg
def test_ingest_ulozi_nevznikle_varianty_autopilota(bot, conn, media_dir, monkeypatch):
    from hlidac_reels import inbox, queue
    from hlidac_reels.inbox import IngestResult
    skipped = [{"n": 2, "format": "pn", "errors": ["a", "b"]}]
    monkeypatch.setattr(inbox, "ingest_all", lambda c, i, m, autopilot_on: [
        IngestResult(5, [1], ok=True, autopilot=True, skipped=skipped),
        IngestResult(6, [2], ok=True, autopilot=False, skipped=skipped)])
    monkeypatch.setattr(bot, "_safe_send_next", lambda: None)
    bot.inbox_dir = str(media_dir / "inbox")
    bot._ingest()
    assert json.loads(queue.get_state(conn, "autopilot_skipped:5")) == skipped
    assert queue.get_state(conn, "autopilot_skipped:6") is None


def test_texty_nevzniklych_variant():
    from hlidac_reels.messages import autopilot_skipped
    assert autopilot_skipped([{"n": 2, "format": "pn", "errors": ["a", "b"]},
                              {"n": 1, "errors": []}]) == ("Varianta 2 (pn) nevznikla: a; b\n"
                                                          "Varianta 1 nevznikla: důvod neuveden")
    assert autopilot_skipped([]) == ""


@pg
def test_smycka_vola_autopublish_po_ingestu(conn, api, meta, media_dir, monkeypatch):
    bot = _make_bot(conn, api, meta, media_dir)
    order = []
    for name in ("maybe_send_next", "maybe_ingest", "maybe_autopublish", "maybe_notify_failed"):
        monkeypatch.setattr(bot, name, lambda n=name: order.append(n))

    class Stop(Exception):
        pass

    def stop():
        raise Stop

    monkeypatch.setattr(bot, "start", lambda: None)
    monkeypatch.setattr(bot, "maybe_fresh", stop)
    monkeypatch.setattr(bot, "_db_lost", lambda e: isinstance(e, Stop))
    with pytest.raises(SystemExit):
        bot.loop(sleep=lambda s: None)
    assert order == ["maybe_send_next", "maybe_ingest", "maybe_autopublish", "maybe_notify_failed"]


@pg
def test_autopilot_nevznikle_hlaseni_zkusi_znovu(conn, api, meta, media_dir):
    from hlidac_reels import queue
    bot, t = _autopilot_bot(conn, api, meta, media_dir)
    ids = _autopilot_set(conn, media_dir, 14, n=1)
    queue.set_state(conn, "autopilot_skipped:14", json.dumps([{"n": 2, "format": "pn", "errors": ["x"]}]))
    api.fail["sendMessage"] = httpx.ConnectError("down")
    bot.maybe_autopublish()
    assert _get(conn, ids[0]).status == "published"
    assert queue.get_state(conn, "autopilot_skipped:14") is not None
    del api.fail["sendMessage"]
    t[0] += 50
    bot.maybe_autopublish()
    assert api.texts() == ["Varianta 2 (pn) nevznikla: x"]
    assert queue.get_state(conn, "autopilot_skipped:14") is None


@pg
def test_autopilot_vypnuty_nezverejni_nic(conn, api, meta, media_dir):
    from hlidac_reels import queue
    from hlidac_reels.bot import AUTOPILOT_KEY
    bot, t = _autopilot_bot(conn, api, meta, media_dir)
    ids = _autopilot_set(conn, media_dir, 15, n=1)
    queue.set_state(conn, AUTOPILOT_KEY, "0")
    bot.maybe_autopublish()
    assert _get(conn, ids[0]).status == "approved"
    assert sum(meta.calls.values()) == 0 and api.calls == []


@pg
def test_autopilot_off_vrati_sadu_ke_schvaleni(conn, api, meta, media_dir):
    from hlidac_reels import queue
    bot, t = _autopilot_bot(conn, api, meta, media_dir)
    ids = _autopilot_set(conn, media_dir, 16)
    queue.set_state(conn, "autopilot_skipped:16", json.dumps([{"n": 3, "format": "pn", "errors": ["x"]}]))
    bot.maybe_autopublish()
    assert _get(conn, ids[0]).status == "published"
    api.calls.clear()
    bot.handle(_msg("/autopilot off"))
    r = _get(conn, ids[1])
    assert r.autopilot is False and r.decided_at is None
    off = api.texts()[0]
    assert off.startswith("Autopilot vypnut: sady jdou ke schválení. Dosud nezveřejněná videa autopilota (1) "
                          "půjdou ke schválení.")
    assert "Varianta 3 (pn) nevznikla: x" in off
    assert queue.get_state(conn, "autopilot_skipped:16") is None
    # běžná cesta ke schválení: sada (zbylá varianta) odešla do Telegramu hned po odpovědi
    assert _get(conn, ids[1]).status == "pending"
    assert any(f"pick:{ids[1]}" in _buttons(b) for b in api.sent())
    t[0] += 50
    bot.maybe_autopublish()
    assert _get(conn, ids[1]).status == "pending"
    assert meta.calls["fb_upload_reel"] == 1
    # ani znovu zapnutý autopilot ji nezveřejní (už není autopilot)
    bot.handle(_msg("/autopilot on"))
    t[0] += 50
    bot.maybe_autopublish()
    assert meta.calls["fb_upload_reel"] == 1


@pg
def test_autopilot_off_nesahne_na_publishing(conn, api, meta, media_dir):
    from hlidac_reels import queue
    bot, t = _autopilot_bot(conn, api, meta, media_dir)
    ids = _autopilot_set(conn, media_dir, 17, n=1)
    assert queue.transition(conn, ids[0], "approved", "publishing")
    bot.handle(_msg("/autopilot off"))
    r = _get(conn, ids[0])
    assert r.status == "publishing" and r.autopilot is True
    assert api.texts()[-1] == "Autopilot vypnut: sady jdou ke schválení."


@pg
def test_autopilot_vadny_klic_nevznikle_smazan(conn, api, meta, media_dir):
    from hlidac_reels import queue
    bot, t = _autopilot_bot(conn, api, meta, media_dir)
    _autopilot_set(conn, media_dir, 18, n=1)
    queue.set_state(conn, "autopilot_skipped:abc", "[]")
    bot.maybe_autopublish()
    assert queue.get_state(conn, "autopilot_skipped:abc") is None


@pg
def test_denni_souhrn_obsahuje_frontu(bot, conn, api, media_dir, monkeypatch):
    from hlidac_reels import hlidani, queue
    ids = _add_set(conn, media_dir, 5)
    for i in ids:
        queue.transition(conn, i, "queued", "withdrawn", decided_at=bot.now())
    monkeypatch.setattr(hlidani, "summary_due", lambda c, now: "2026-11-24")
    monkeypatch.setattr(hlidani, "summary_text", lambda c, now: "Stav dat")
    bot._daily_summary()
    text = api.texts()[-1]
    assert text.startswith("Stav dat") and "Sada #5 stažena: nález už není mezi hlavními" in text


@pg
def test_neodeslana_zprava_o_vyprseni_do_logu(bot, conn, api, media_dir, caplog):
    ids = _add_set(conn, media_dir, 1)
    bot.send_next()
    api.fail["sendMessage"] = httpx.Response(500, json={"ok": False, "description": "down"})
    _later(bot, days=8)
    with caplog.at_level(logging.WARNING):
        bot.maybe_fresh()
    assert {_get(conn, i).status for i in ids} == {"expired"}
    assert "zpráva o vypršení" in caplog.text


# --- Task 6 (F1): výjimka publikace, uvízlé položky za běhu, jediná instance ----

@pg
def test_publish_vyjimka_na_unknown(bot, conn, api, meta, media_dir, monkeypatch):
    from hlidac_reels import publish, queue

    def boom(c, m, reel_id, **kw):
        assert queue.transition(c, reel_id, "approved", "publishing")
        raise RuntimeError(f"spadlo s tokenem {TOKEN}")

    monkeypatch.setattr(publish, "run", boom)
    rid = _add(conn, media_dir)
    bot.send_next()
    api.calls.clear()
    bot.handle(_cb(f"ok:{rid}"))
    r = _get(conn, rid)
    assert r.status == "unknown"
    assert "RuntimeError" in r.error and "spadlo" in r.error and TOKEN not in r.error
    msg = [b for b in api.sent() if f"retry:{rid}" in _buttons(b)]
    assert len(msg) == 1 and "nejistý" in msg[0]["text"] and TOKEN not in msg[0]["text"]

    # výjimka ještě před začátkem publikace (položka zůstala approved) skončí stejně
    monkeypatch.setattr(publish, "run", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    rid2 = _add(conn, media_dir, 2)
    bot.send_next()
    bot.handle(_cb(f"ok:{rid2}"))
    assert _get(conn, rid2).status == "unknown"
    assert any(f"retry:{rid2}" in _buttons(b) for b in api.sent())


@pg
def test_publish_vyjimka_autopilot(conn, api, meta, media_dir, monkeypatch):
    from hlidac_reels import publish
    bot, t = _autopilot_bot(conn, api, meta, media_dir)
    ids = _autopilot_set(conn, media_dir, 21, n=1)
    monkeypatch.setattr(publish, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("síť")))
    bot.maybe_autopublish()
    assert _get(conn, ids[0]).status == "unknown"
    assert any(f"retry:{ids[0]}" in _buttons(b) for b in api.sent())


@pg
def test_uvizle_publishing_za_behu(conn, api, meta, media_dir):
    from datetime import datetime, timedelta, timezone
    from hlidac_reels import queue
    from hlidac_reels.bot import SEND_INTERVAL
    bot, t = _autopilot_bot(conn, api, meta, media_dir)
    old = datetime.now(timezone.utc) - timedelta(minutes=20)
    new = datetime.now(timezone.utc)
    pub_old = _add(conn, media_dir, 1)
    queue.transition(conn, pub_old, "queued", "publishing", decided_at=old)
    pub_new = _add(conn, media_dir, 2)
    queue.transition(conn, pub_new, "queued", "publishing", decided_at=new)
    appr_old = _add(conn, media_dir, 3)
    queue.transition(conn, appr_old, "queued", "approved", decided_at=old)
    appr_new = _add(conn, media_dir, 4)
    queue.transition(conn, appr_new, "queued", "approved", decided_at=new)
    auto = _autopilot_set(conn, media_dir, 22, n=1)
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_queue SET decided_at=%s WHERE id=%s", (old, auto[0]))
    conn.commit()
    bot.maybe_send_next()
    assert _get(conn, pub_old).status == "unknown"
    assert _get(conn, pub_new).status == "publishing"
    r = _get(conn, appr_old)
    assert r.status == "failed" and r.error == "přerušeno před zveřejněním"
    assert _get(conn, appr_new).status == "approved"
    # approved autopilota zveřejní maybe_autopublish, ne převod na failed
    assert _get(conn, auto[0]).status == "approved"
    for rid in (pub_old, appr_old):
        assert len([b for b in api.sent() if f"retry:{rid}" in _buttons(b)]) == 1
    assert sum(meta.calls.values()) == 0
    # další cyklus už nic znovu neohlásí
    t[0] += SEND_INTERVAL
    bot.maybe_send_next()
    for rid in (pub_old, appr_old):
        assert len([b for b in api.sent() if f"retry:{rid}" in _buttons(b)]) == 1


@pg
def test_druha_instance_skonci(bot, conn, api, meta, media_dir):
    from hlidac_reels import db
    from hlidac_reels.bot import Bot
    from hlidac_reels.telegram import Telegram
    bot.start()
    api2 = FakeTelegramApi()
    c2 = db.connect()
    try:
        tg2 = Telegram(TOKEN, client=httpx.Client(transport=httpx.MockTransport(api2)))
        bot2 = Bot(c2, tg2, meta, CHAT, str(media_dir), PUBLIC_BASE)
        with pytest.raises(SystemExit) as e:
            bot2.start()
        assert e.value.code == 0
        assert api2.calls == []
        # opakovaný start téže instance zámek znovu získá
        bot.start()
    finally:
        c2.close()


# --- /fronta jako přehled (Task 7) -------------------------------------------

def _fronta_resend(bot, api) -> None:
    """/fronta a pod přehledem tlačítko Poslat tlačítka znovu (resend:0); zaznamenaná volání jsou jen z obnovy."""
    bot.handle(_msg("/fronta"))
    api.calls.clear()
    bot.handle(_cb("resend:0"))


def _sections(text: str) -> list[str]:
    """Nadpisy sekcí přehledu (první řádek každého bloku kromě stavu PC)."""
    return [b.splitlines()[0] for b in text.split("\n\n") if not b.startswith("PC")]


@pg
def test_fronta_prehled_sekce(bot, conn, api, media_dir):
    from hlidac_reels import jobs, queue
    # Čeká na tebe: nedotčená sada v Telegramu
    mine = _add_set(conn, media_dir, 7)
    bot.send_next()
    # Hotové, čekají na odeslání: samostatné video ve frontě
    ready = _add(conn, media_dir, 1, ct=174)
    # Problémy: sada, kterou nejde doručit
    held = _add_set(conn, media_dir, 9, n=2, source="manual", ct=311)
    for i in held:
        queue.transition(conn, i, "queued", "held", send_attempts=5, send_error="Telegram timeout")
    # Generuje se + Ve frontě zadání
    gen = jobs.add(conn, source="manual", ct=300, focus_pn=[2377])
    waiting = jobs.add(conn, source="manual", ct=90)
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status='claimed', claimed_at=now() WHERE id=%s", (gen,))
    conn.commit()
    api.calls.clear()
    bot.handle(_msg("/fronta"))
    (msg,) = api.sent()
    text = msg["text"]
    assert _sections(text) == ["Čeká na tebe (1)", "Hotové, čekají na odeslání (1)", "Generuje se",
                               "Ve frontě zadání (1)", "Problémy (1)"]
    assert "#7 tisk 221 · nedotčená" in text
    assert f"#{ready} tisk 174 · na řadě" in text
    assert f"#{gen} PN 2377 · generuje se" in text
    assert f"#{waiting} tisk 90 · 1. v pořadí" in text
    assert "#9 tisk 311 · nejde doručit (Telegram timeout, 5×) · Zkusit znovu / Zahodit" in text
    assert text.splitlines()[-1].startswith("PC ")
    assert _buttons(msg) == ["resend:0"]
    # prázdná sekce se vynechá
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status='done' WHERE id=%s", (gen,))
    conn.commit()
    api.calls.clear()
    bot.handle(_msg("/fronta"))
    assert "Generuje se" not in _sections(api.texts()[0])


@pg
def test_fronta_neposila_tlacitka_sama(bot, conn, api, media_dir):
    p = _add(conn, media_dir, 1)
    bot.send_next()
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    api.calls.clear()
    bot.handle(_msg("/fronta"))
    assert len(api.sent()) == 1 and _buttons(api.sent()[0]) == ["resend:0"]
    assert "editMessageReplyMarkup" not in api.methods()
    api.calls.clear()
    bot.handle(_cb("resend:0"))
    btns = [_buttons(b) for b in api.sent() if _buttons(b)]
    assert [f"ok:{p}", f"edit:{p}", f"no:{p}"] in btns
    assert [f"pick:{i}" for i in ids] + ["doneset:7"] in btns
    assert "answerCallbackQuery" in api.methods()


@pg
def test_fronta_ceka_na_tebe_radky(bot, conn, api, media_dir):
    """Každý řádek říká, na co položka čeká: zbývá varianta, vybraná čeká na schválení, samostatné video."""
    ids = _add_set(conn, media_dir, 7)
    bot.send_next()
    set_msg = _set_msg_id(conn, ids[0])
    bot.handle(_cb(f"pick:{ids[1]}", msg_id=set_msg))
    solo = _add(conn, media_dir, 1)
    bot.send_next()
    api.calls.clear()
    bot.handle(_msg("/fronta"))
    text = api.texts()[0]
    assert "Čeká na tebe (3)" in text
    assert "#7 tisk 221 · vybraná varianta 2 čeká na schválení" in text
    assert "#7 tisk 221 · zbývají varianty 1, 3" in text and "Hotovo?" in text
    assert f"#{solo} tisk 221 · čeká na schválení" in text


@pg
def test_fronta_automaticka_ceka_na_limit(bot, conn, api, media_dir, monkeypatch):
    from hlidac_reels import bot as bot_mod
    monkeypatch.setattr(bot_mod, "SETS_PER_DAY", 1)
    first = _add_set(conn, media_dir, 7)
    bot.send_next()
    bot.handle(_cb(f"pick:{first[0]}", msg_id=_set_msg_id(conn, first[0])))
    _add_set(conn, media_dir, 8, ct=174)
    api.calls.clear()
    bot.handle(_msg("/fronta"))
    text = api.texts()[0]
    assert "#8 tisk 174 (automatická) · čeká: denní limit 1/1 · /dalsi" in text


@pg
def test_fronta_odlozene_po_chybe(bot, conn, api, media_dir):
    """Jednotka odložená po chybě odeslání: řádek řekne, do kdy (Europe/Prague) a kolikátý pokus."""
    from datetime import datetime, timezone
    from hlidac_reels import queue
    rid = _add(conn, media_dir, 1)
    queue.defer_send(conn, rid, "timeout", datetime(2026, 11, 24, 13, 0, tzinfo=timezone.utc))
    bot.now = lambda: datetime(2026, 11, 24, 13, 0, 30, tzinfo=timezone.utc)
    bot.handle(_msg("/fronta"))
    assert f"#{rid} tisk 221 · odloženo do 14:01 (chyba odeslání, pokus 1)" in api.texts()[0]


# --- závěrečná revize F1: R3 u autopilota, pořadí připomínky, nečekaná chyba odeslání ----------

@pg
def test_autopilot_a_fronta_jedna_jednotka_za_cyklus(conn, api, meta, media_dir):
    """R3: zveřejnění autopilota v témže cyklu nepošle další jednotku navíc."""
    bot, t = _autopilot_bot(conn, api, meta, media_dir)
    auto = _autopilot_set(conn, media_dir, 20, n=1)
    a = _add_set(conn, media_dir, 21, n=2, source="manual")
    b = _add_set(conn, media_dir, 22, n=2, source="manual")
    api.calls.clear()
    # jeden cyklus smyčky se stojícími hodinami
    bot.maybe_send_next()
    bot.maybe_autopublish()
    assert _get(conn, auto[0]).status == "published"
    assert len(_set_offers(api)) == 1
    sent_sets = {s for s in (20, 21, 22) for v in api.sent("sendVideo") if f"#{s} ·" in v["_multipart"]}
    assert sent_sets == {21}
    assert _statuses(conn, a) == ["pending", "pending"]
    assert _statuses(conn, b) == ["queued", "queued"]
    # další jednotka až v dalším cyklu
    from hlidac_reels.bot import SEND_INTERVAL
    t[0] += SEND_INTERVAL
    bot.maybe_send_next()
    assert _statuses(conn, b) == ["pending", "pending"]


@pg
def test_zbyva_prijde_hned_po_schvaleni_pred_dalsi_sadou(bot, conn, api, meta, media_dir):
    """Připomínka zbylé varianty přijde hned po zveřejnění, ne pod videi jiné sady."""
    ids = _add_set(conn, media_dir, 6, n=2, source="manual")
    bot.send_next()
    set_msg = _set_msg_id(conn, ids[0])
    other = _add_set(conn, media_dir, 8, n=2, source="manual")
    bot.handle(_cb(f"pick:{ids[1]}", msg_id=set_msg))
    api.calls.clear()
    bot.handle(_cb(f"ok:{ids[1]}"))
    assert _get(conn, ids[1]).status == "published"
    sends = [(m, b) for m, b in api.calls if m in ("sendMessage", "sendVideo")]
    texts = [b.get("text", "") for _, b in sends]
    assert texts[0] == "Zveřejňuji…"
    assert "https://www.facebook.com/reel/V1" in texts[1]
    # hned po výsledku publikace připomínka téže sady, žádné video jiné sady mezi tím
    assert sends[2][0] == "sendMessage" and "Sada #6: zbývá varianta 1" in texts[2]
    # tlačítko pak frontu posune jako ostatní rozhodnutí
    assert any(m == "sendVideo" and "#8 ·" in b["_multipart"] for m, b in sends[3:])
    assert _statuses(conn, other) == ["pending", "pending"]


@pg
def test_necekana_chyba_odeslani_odlozi_jednotku(bot, conn, api, media_dir, monkeypatch):
    """Výjimka mimo TelegramError (vadná data) jednotku odloží, fronta jde dál."""
    from hlidac_reels import queue
    a = _add_set(conn, media_dir, 30, n=2, source="manual")
    b = _add_set(conn, media_dir, 31, n=2, source="manual")
    orig = bot._send_set

    def broken(set_id):
        if set_id == 30:
            raise ValueError("vadná data sady")
        return orig(set_id)

    monkeypatch.setattr(bot, "_send_set", broken)
    bot.send_next()
    ra = _get(conn, a[0])
    assert ra.status == "queued" and ra.send_attempts == 1 and ra.next_send_at is not None
    assert "ValueError" in (ra.send_error or "")
    assert _statuses(conn, b) == ["queued", "queued"]
    # další cyklus: odložená A nepřekáží, odejde B
    bot.send_next()
    assert _statuses(conn, b) == ["pending", "pending"]
    assert _get(conn, a[0]).status == "queued"
    assert queue.get(conn, a[1]).send_attempts == 1
