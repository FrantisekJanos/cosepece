"""Hlídání čerstvosti dat (hlidac_reels.hlidani) a jeho napojení na bota: výstrahy, obnovení, souhrn, /stav.

Tabulky webu a stenoprotokolů si test vytvoří ve vlastním schématu (search_path), reel_state je v public.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from test_bot import CHAT, PUBLIC_BASE, TOKEN, FakeMeta, FakeTelegramApi, _msg

pytestmark = pytest.mark.pg
SCHEMA = "test_hlidani"
# 21. 11. 2026 12:00 UTC = 13:00 v Praze
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)

TABULKY = f"""
DROP SCHEMA IF EXISTS {SCHEMA} CASCADE;
CREATE SCHEMA {SCHEMA};
CREATE TABLE {SCHEMA}.meta (k varchar(190) PRIMARY KEY, v text);
CREATE TABLE {SCHEMA}.sync_beh (id int PRIMARY KEY, zacatek varchar(19) NOT NULL, konec varchar(19), ok int,
                                vysledek text, chyba text);
CREATE TABLE {SCHEMA}.tisky (id_tisk int PRIMARY KEY, ct int NOT NULL, faze varchar(60), posledni varchar(19),
                             zakony text, sbirka varchar(20));
CREATE TABLE {SCHEMA}.kroky (id_hist int PRIMARY KEY, id_tisk int, ct int, datum varchar(19), typ_stavu int);
CREATE TABLE {SCHEMA}.pn (id_dokument int PRIMARY KEY, cislo int, ct int);
CREATE TABLE {SCHEMA}.tisk_text (ct int PRIMARY KEY, chars int NOT NULL DEFAULT 0, fetched_at varchar(19),
                                 ai_at varchar(19));
CREATE TABLE {SCHEMA}.pn_text (id_dokument int PRIMARY KEY, ct int, chars int NOT NULL DEFAULT 0,
                               fetched_at varchar(19), ai_at varchar(19), laws text);
CREATE TABLE {SCHEMA}.beh (id bigserial PRIMARY KEY, zacatek timestamptz NOT NULL DEFAULT now(), konec timestamptz,
                           vysledek jsonb);
CREATE TABLE {SCHEMA}.schuze (obdobi int, schuze int, od timestamp, "do" timestamp, PRIMARY KEY (obdobi, schuze));
CREATE TABLE {SCHEMA}.rec (obdobi int, schuze int, turn int, aname int, id_osoba int, id_bod int, druh int);
"""


def _local(dt: datetime) -> str:
    """Čas jako zápis PHP (Y-m-d H:i:s v Europe/Prague; v listopadu UTC+1)."""
    return (dt + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def conn(monkeypatch):
    if os.environ.get("REELS_TEST_DB") != "1":
        pytest.skip("chybí REELS_TEST_DB=1 (testy mažou tabulky reel_queue a reel_state)")
    from hlidac_reels import db, hlidani
    c = db.connect()
    db.migrate(c)
    with c.cursor() as cur:
        cur.execute("TRUNCATE reel_queue RESTART IDENTITY; TRUNCATE reel_state; TRUNCATE reel_jobs RESTART IDENTITY")
        cur.execute(TABULKY)
        cur.execute(f"SET search_path TO {SCHEMA}, public")
    c.commit()
    monkeypatch.setattr(hlidani, "STENO", SCHEMA)
    _healthy(c)
    yield c
    c.rollback()
    with c.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    c.commit()
    c.close()


def sql(conn, q: str, params=()):
    with conn.cursor() as cur:
        cur.execute(q, params)
    conn.commit()


def _healthy(conn) -> None:
    """Vše v pořádku: sync před hodinou, steno před hodinou, AI před 2 h, PC před 10 min."""
    from hlidac_reels import queue
    sql(conn, "INSERT INTO sync_beh VALUES (1, %s, %s, 1, '{}', NULL)",
        (_local(NOW - timedelta(minutes=70)), _local(NOW - timedelta(minutes=60))))
    sql(conn, "INSERT INTO beh (zacatek, konec, vysledek) VALUES (%s, %s, '{}')",
        (NOW - timedelta(minutes=70), NOW - timedelta(minutes=60)))
    sql(conn, "INSERT INTO tisky VALUES (1, 221, '2. čtení', %s, '159/2006', NULL)", (_local(NOW - timedelta(days=3)),))
    sql(conn, "INSERT INTO tisk_text VALUES (221, 100, %s, %s)",
        (_local(NOW - timedelta(hours=5)), _local(NOW - timedelta(hours=2))))
    sql(conn, "INSERT INTO schuze VALUES (10, 35, '2026-10-14 09:00', '2026-10-14 14:00')")
    sql(conn, "INSERT INTO rec VALUES (10, 35, 1, 1, 1, 1, 0)")
    queue.set_state(conn, "worker_seen_at", (NOW - timedelta(minutes=10)).isoformat())


def _keys(conn, now=NOW) -> list[str]:
    from hlidac_reels import hlidani
    return [f.key for f in hlidani.status(conn, now).findings]


def test_vse_v_poradku(conn):
    from hlidac_reels import hlidani
    st = hlidani.status(conn, NOW)
    assert st.findings == []
    assert any(line.startswith("Synchronizace s psp.cz: poslední úspěšný běh 21. 11. 12:00") for line in st.lines)
    assert "Vše v pořádku." in hlidani.summary_text(conn, NOW)


def test_sync_po_2_h_a_chybny_beh(conn):
    sql(conn, "INSERT INTO sync_beh VALUES (2, %s, %s, 0, NULL, 'chyba')",
        (_local(NOW - timedelta(minutes=10)), _local(NOW - timedelta(minutes=5))))
    assert _keys(conn) == []
    from hlidac_reels import hlidani
    assert "poslední běh skončil chybou" in hlidani.status(conn, NOW).lines[0]
    assert _keys(conn, NOW + timedelta(minutes=61)) == ["sync"]


def test_sync_bez_historie_bere_last_sync(conn):
    sql(conn, "DROP TABLE sync_beh")
    sql(conn, "INSERT INTO meta VALUES ('last_sync', %s)", (_local(NOW - timedelta(hours=3)),))
    assert _keys(conn) == ["sync"]
    sql(conn, "UPDATE meta SET v = %s", (_local(NOW - timedelta(minutes=30)),))
    assert _keys(conn) == []


def test_steno_po_3_h_neuspesne_a_nedokoncene_se_nepocitaji(conn):
    sql(conn, "INSERT INTO beh (zacatek, konec, vysledek) VALUES (%s, %s, '{\"error\": \"x\"}'), "
              "(%s, %s, '{\"nedokonceno\": true}')", (NOW, NOW, NOW, NOW))
    # úspěšný běh před hodinou: steno stojí až po dalších 2 h
    assert _keys(conn, NOW + timedelta(hours=1, minutes=59)) == ["sync"]
    assert _keys(conn, NOW + timedelta(hours=2, minutes=1)) == ["sync", "steno"]


def test_ai_fronta_nejstarsi_po_12_h(conn):
    # čekající PN (text bez rozboru), vyhlášený tisk a prázdný text se nepočítají
    sql(conn, "INSERT INTO pn VALUES (500, 2362, 221)")
    sql(conn, "INSERT INTO pn_text VALUES (500, 221, 50, %s, NULL, NULL)", (_local(NOW - timedelta(hours=13)),))
    sql(conn, "INSERT INTO tisky VALUES (2, 90, 'Vyhlášeno', NULL, NULL, '155/2026')")
    sql(conn, "INSERT INTO tisk_text VALUES (90, 100, %s, NULL)", (_local(NOW - timedelta(days=5)),))
    sql(conn, "INSERT INTO tisky VALUES (3, 91, '1. čtení', NULL, NULL, NULL)")
    sql(conn, "INSERT INTO tisk_text VALUES (91, 0, %s, NULL)", (_local(NOW - timedelta(days=5)),))
    from hlidac_reels import hlidani
    q = hlidani.ai_queue(conn)
    assert q["cekajicich"] == 1
    st = hlidani.status(conn, NOW)
    assert [f.key for f in st.findings] == ["ai"] and "1 textů čeká" in st.findings[0].text
    sql(conn, "UPDATE pn_text SET fetched_at = %s", (_local(NOW - timedelta(hours=11)),))
    assert _keys(conn) == []


def test_ai_posledni_vysledek_po_24_h_jen_bez_klienta(conn):
    sql(conn, "UPDATE tisk_text SET ai_at = %s", (_local(NOW - timedelta(hours=25)),))
    # bez údaje o klientovi rozhoduje stáří výsledku
    assert _keys(conn) == ["ai"]
    # klient se na (prázdnou) frontu ptá: klidný den bez nových textů není výpadek
    sql(conn, "INSERT INTO meta VALUES ('ai_queue_seen_at', %s)", (_local(NOW - timedelta(minutes=30)),))
    assert _keys(conn) == []
    sql(conn, "UPDATE meta SET v = %s WHERE k = 'ai_queue_seen_at'", (_local(NOW - timedelta(hours=25)),))
    assert _keys(conn) == ["ai"]


def test_worker_jen_v_souhrnu(conn):
    from hlidac_reels import hlidani, queue
    queue.set_state(conn, "worker_seen_at", (NOW - timedelta(hours=25)).isoformat())
    f = hlidani.status(conn, NOW).findings
    assert [x.key for x in f] == ["worker"] and f[0].alert is False
    assert hlidani.check(conn, NOW) == ([], [])
    assert "PC s generátorem Reels nebylo vidět" in hlidani.summary_text(conn, NOW)


def test_schuze_bez_rec_po_7_dnech(conn):
    sql(conn, "INSERT INTO schuze VALUES (10, 36, '2026-11-13 09:00', '2026-11-13 13:00')")
    sql(conn, "INSERT INTO schuze VALUES (10, 37, '2026-11-24 14:00', NULL)")
    assert _keys(conn, datetime(2026, 11, 20, 11, 0, tzinfo=timezone.utc)) == []
    assert _keys(conn) == ["rec:36"]


def test_vyhlaseno_bez_sbirky_po_14_dnech(conn):
    sql(conn, "INSERT INTO tisky VALUES (4, 144, 'Vyhlášeno', '2026-11-09 00:00:00', NULL, NULL)")
    sql(conn, "INSERT INTO kroky VALUES (1, 4, 144, '2026-11-04 00:00:00', 11)")
    assert _keys(conn, datetime(2026, 11, 17, 0, 0, tzinfo=timezone.utc)) == []
    assert _keys(conn, datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc)) == ["sbirka:144"]
    sql(conn, "UPDATE tisky SET sbirka = '400/2026' WHERE ct = 144")
    assert _keys(conn, datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc)) == []


def test_tisky_menici_podklady(conn):
    sql(conn, "INSERT INTO tisky VALUES (5, 15, '1. čtení', NULL, '90/1995', NULL)")
    sql(conn, "INSERT INTO tisky VALUES (6, 47, '3. čtení', NULL, '1/1993', NULL)")
    sql(conn, "INSERT INTO tisky VALUES (7, 72, 'Vyhlášeno', '2026-08-28 00:00:00', '90/1995', '157/2026')")
    # PN k tisku 221 mění jednací řád a tisk je ve 3. čtení
    sql(conn, "UPDATE tisky SET faze = '3. čtení' WHERE ct = 221")
    sql(conn, "INSERT INTO pn_text VALUES (600, 221, 10, NULL, NULL, '159/2006,90/1995')")
    from hlidac_reels import hlidani
    f = {x.key: x for x in hlidani.status(conn, NOW).findings}
    assert sorted(f) == ["podklady:221", "podklady:47", "podklady:72"]
    assert f["podklady:47"].sig == "3. čtení" and "1/1993" in f["podklady:47"].text
    # vyhlášený před víc než 30 dny: ve stavu ano, v souhrnu ne
    assert f["podklady:72"].summary is False and f["podklady:47"].summary is True
    assert "Tisk 72" not in hlidani.summary_text(conn, NOW)


def test_vystraha_jednou_obnoveno_a_zmena_faze(conn):
    from hlidac_reels import hlidani
    sql(conn, "INSERT INTO tisky VALUES (6, 47, '3. čtení', NULL, '1/1993', NULL)")
    later = NOW + timedelta(hours=3)  # sync i steno stojí
    new, rec = hlidani.check(conn, later)
    assert sorted(f.key for f in new) == ["podklady:47", "steno", "sync"] and rec == []
    assert hlidani.check(conn, later + timedelta(hours=1)) == ([], [])
    # sync zase běží, tisk se posunul do Senátu
    sql(conn, "INSERT INTO sync_beh VALUES (3, %s, %s, 1, '{}', NULL)", (_local(later), _local(later)))
    sql(conn, "UPDATE tisky SET faze = 'V Senátu' WHERE ct = 47")
    new, rec = hlidani.check(conn, later + timedelta(hours=1))
    assert [f.key for f in new] == ["podklady:47"] and rec == ["Synchronizace s psp.cz zase běží."]
    snap = json.loads(__import__("hlidac_reels").queue.get_state(conn, hlidani.SNAPSHOT_KEY))
    assert len(snap["vystrahy"]) == 2 and snap["at"].startswith("2026-11-21T16:00")
    # tisk zamítnut: podklady nemají „obnoveno“
    sql(conn, "UPDATE tisky SET faze = 'Zamítnuto' WHERE ct = 47")
    assert hlidani.check(conn, later + timedelta(hours=1))[1] == []


def test_souhrn_v_8_rano_jednou_denne(conn):
    from hlidac_reels import hlidani
    before = datetime(2026, 11, 21, 6, 59, tzinfo=timezone.utc)  # 7:59 v Praze
    assert hlidani.summary_due(conn, before) is None
    day = hlidani.summary_due(conn, before + timedelta(minutes=1))
    assert day == "2026-11-21"
    hlidani.mark_summary(conn, day)
    assert hlidani.summary_due(conn, NOW) is None
    assert hlidani.summary_due(conn, NOW + timedelta(days=1)) == "2026-11-22"


# --- bot ---------------------------------------------------------------------

@pytest.fixture
def bot(conn, tmp_path):
    from hlidac_reels.bot import Bot
    from hlidac_reels.telegram import Telegram
    api = FakeTelegramApi()
    tg = Telegram(TOKEN, client=httpx.Client(transport=httpx.MockTransport(api)))
    (tmp_path / "videos").mkdir()
    b = Bot(conn, tg, FakeMeta(), CHAT, str(tmp_path), PUBLIC_BASE)
    b.api = api
    b.now = lambda: NOW
    return b


def test_bot_stav_prikaz(bot, conn):
    bot.handle(_msg("/stav"))
    text = bot.api.texts()[-1]
    assert text.startswith("Stav dat k 21. 11. 13:00") and "Vše v pořádku." in text


def test_bot_hodinova_kontrola_a_souhrn(bot, conn):
    t = [0.0]
    bot.clock = lambda: t[0]
    bot.now = lambda: NOW + timedelta(hours=3)  # 16:00 v Praze, sync a steno stojí
    bot.maybe_watch()
    texts = bot.api.texts()
    # výstraha jednou, pak denní souhrn (je po 8:00 a dnes ještě neodešel)
    assert texts[0].startswith("Výstraha – data:") and "Synchronizace s psp.cz stojí" in texts[0]
    assert texts[1].startswith("Stav dat k 21. 11. 16:00") and "Problémy:" in texts[1]
    assert len(texts) == 2
    # další minuta ani hodina nic neopakují
    t[0] = 60
    bot.maybe_watch()
    t[0] = 3700
    bot.maybe_watch()
    assert len(bot.api.texts()) == 2
