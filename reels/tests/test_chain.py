"""Celý řetěz od výsledku workeru po zprávu sady v Telegramu (PostgreSQL, marker pg).

Ovladač tests/php_result.php odevzdá výsledek jako worker přes Hlidac\\ReelsApi (PUT video, POST result)
do mezisložky, inbox.ingest_all sadu převezme do fronty a Bot.send_next ji pošle ke schválení. PHP běží
lokálním php (potřebuje pdo_pgsql) nad stejnou testovací databází jako Python; Telegram je falešný.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
import pytest

from hlidac_reels import media
from hlidac_reels.media import VideoInfo
from test_bot import CHAT, PUBLIC_BASE, TOKEN, FakeMeta, FakeTelegramApi, _buttons

pytestmark = pytest.mark.pg

DRIVER = Path(__file__).with_name("php_result.php")
WORKER_TOKEN = "w" * 32
CLAIM = "c" * 32
# šablona a formát varianty n
TEMPLATES = {1: ("template_v3", "pn"), 2: ("template_v2", None), 3: ("template_v3", "cesta")}
# nevzniklé varianty: sada s n variantami hlásí ty s vyšším n (u 1 varianty obě)
SKIPPED = [
    {"n": 2, "format": "zmena", "errors": ["render selhal"]},
    {"n": 3, "format": "cesta", "errors": ["kontrola neprošla", "popisek je delší než limit"]},
]


@pytest.fixture(scope="module")
def php():
    exe = shutil.which("php")
    if exe is None:
        pytest.skip("chybí php na PATH")
    mods = subprocess.run([exe, "-m"], capture_output=True, text=True, check=False).stdout.split()
    if "pdo_pgsql" not in mods:
        pytest.skip("php nemá rozšíření pdo_pgsql")
    return exe


@pytest.fixture
def conn():
    if os.environ.get("REELS_TEST_DB") != "1":
        pytest.skip("chybí REELS_TEST_DB=1 (test maže tabulky reel_jobs, reel_queue, reel_state a reel_events)")
    from hlidac_reels import db
    c = db.connect()
    db.migrate(c)
    with c.cursor() as cur:
        cur.execute("TRUNCATE reel_jobs RESTART IDENTITY; TRUNCATE reel_queue RESTART IDENTITY; "
                    "TRUNCATE reel_state; TRUNCATE reel_events RESTART IDENTITY")
    c.commit()
    yield c
    c.close()


@pytest.fixture(autouse=True)
def fake_probe(monkeypatch):
    # bez ffprobe: každé video je platné 9:16, 30 s
    monkeypatch.setattr(media, "probe", lambda path: VideoInfo("h264", "aac", 1080, 1920, 30.0,
                                                               Path(path).stat().st_size))


@pytest.fixture
def media_dir(tmp_path):
    d = tmp_path / "media"
    (d / "videos").mkdir(parents=True)
    return d


@pytest.fixture
def api():
    return FakeTelegramApi()


@pytest.fixture
def bot(conn, api, media_dir):
    from hlidac_reels.bot import Bot
    from hlidac_reels.telegram import Telegram
    tg = Telegram(TOKEN, client=httpx.Client(transport=httpx.MockTransport(api)))
    return Bot(conn, tg, FakeMeta(), CHAT, str(media_dir), PUBLIC_BASE)


def _run_php(php: str, **chain) -> list:
    """Spustí ovladač s proměnnými CHAIN_*; připojení k databázi odvodí z DATABASE_URL."""
    url = urlsplit(os.environ["DATABASE_URL"])
    if url.scheme not in ("postgres", "postgresql"):
        pytest.skip("DATABASE_URL není adresa postgresql://")
    env = {**os.environ,
           "CHAIN_DSN": f"pgsql:host={url.hostname or 'localhost'};port={url.port or 5432};"
                        f"dbname={unquote(url.path.lstrip('/'))}",
           "CHAIN_USER": unquote(url.username or ""),
           "CHAIN_PASS": unquote(url.password or ""),
           **{f"CHAIN_{k.upper()}": str(v) for k, v in chain.items()}}
    env.pop("HLIDAC_CONFIG", None)  # z konfigurace webu čte ovladač jen limity RateLimit, cizí soubor nepotřebuje
    proc = subprocess.run([php, str(DRIVER)], env=env, capture_output=True, encoding="utf-8", timeout=120,
                          check=False)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return json.loads(proc.stdout)


@pytest.mark.parametrize("n", [3, 2, 1])
def test_cely_retez(php, conn, bot, api, media_dir, tmp_path, n):
    from hlidac_reels import inbox, jobs, queue
    jid = jobs.add(conn, source="manual", ct=311)
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status='claimed', claim_token=%s, claimed_at=now(), attempts=1 "
                    "WHERE id=%s", (CLAIM, jid))
    conn.commit()

    src, inbox_dir = tmp_path / "src", tmp_path / "inbox"
    src.mkdir()
    inbox_dir.mkdir()
    variants = []
    for k in range(1, n + 1):
        (src / f"variant-{k}.mp4").write_bytes(b"\0\0\0\x18ftypisom" + bytes([k]) * 64)
        template, fmt = TEMPLATES[k]
        variants.append({"n": k, "template": template, "format": fmt,
                         "caption": f"Varianta {k}: Sněmovna zkrátila lhůtu", "card": {"headline": f"Varianta {k}"}})
    skipped = [s for s in SKIPPED if s["n"] > n]

    # worker: n× PUT video, POST result
    out = _run_php(php, token=WORKER_TOKEN, inbox=inbox_dir, job_id=jid, claim=CLAIM, src=src,
                   variants=json.dumps(variants, ensure_ascii=False), skipped=json.dumps(skipped, ensure_ascii=False))
    assert [status for status, _ in out] == [200] * (n + 1), out
    assert out[-1][1] == {"ok": True, "variants": n}
    with conn.cursor() as cur:
        cur.execute("SELECT status, detail FROM reel_jobs WHERE id=%s", (jid,))
        status, detail = cur.fetchone()
    conn.commit()
    assert status == "done"
    assert json.loads(detail) == {"variants": list(range(1, n + 1)), "skipped": skipped}
    manifest = json.loads((inbox_dir / str(jid) / "manifest.json").read_text(encoding="utf-8"))
    assert [v["n"] for v in manifest["variants"]] == list(range(1, n + 1))
    assert manifest["skipped"] == skipped
    if n == 1:
        assert len(manifest["skipped"]) == 2

    # bot: převzetí sady z mezisložky
    res = inbox.ingest_all(conn, inbox_dir, media_dir)
    assert len(res) == 1 and res[0].ok and res[0].job_id == jid and not res[0].rejected
    assert len(res[0].reel_ids) == n and res[0].skipped == skipped
    assert not (inbox_dir / str(jid)).exists()

    # bot: sada ke schválení (videa a jedna zpráva s tlačítky)
    bot.send_next()
    assert len(api.sent("sendVideo")) == n
    set_msgs = [b for b in api.sent() if _buttons(b)]
    assert len(set_msgs) == 1
    assert _buttons(set_msgs[0]) == [f"pick:{i}" for i in res[0].reel_ids] + [f"doneset:{jid}"]
    assert [(r.status, r.set_id, r.variant) for r in queue.set_items(conn, jid)] == \
        [("pending", jid, k) for k in range(1, n + 1)]
