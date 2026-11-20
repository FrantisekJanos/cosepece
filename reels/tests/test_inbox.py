"""Testy převzetí sady z mezisložky (PostgreSQL, marker pg)."""
from __future__ import annotations

import json
import os

import pytest

from hlidac_reels import media
from hlidac_reels.media import VideoInfo

pytestmark = pytest.mark.pg

OK = VideoInfo("h264", "aac", 1080, 1920, 30.0)


@pytest.fixture
def conn():
    if os.environ.get("REELS_TEST_DB") != "1":
        pytest.skip("chybí REELS_TEST_DB=1 (testy mažou tabulky reel_jobs a reel_queue)")
    from hlidac_reels import db
    c = db.connect()
    db.migrate(c)
    with c.cursor() as cur:
        cur.execute("TRUNCATE reel_jobs RESTART IDENTITY; TRUNCATE reel_queue RESTART IDENTITY")
    c.commit()
    yield c
    c.close()


@pytest.fixture(autouse=True)
def fake_probe(monkeypatch):
    # obsah souboru začíná značkou: ok / short / broken
    def probe(path):
        data = open(path, "rb").read()
        if data.startswith(b"broken"):
            raise RuntimeError("ffprobe selhal: poškozený soubor")
        if data.startswith(b"short"):
            return VideoInfo("h264", "aac", 1080, 1920, 1.0, len(data))
        return VideoInfo("h264", "aac", 1080, 1920, 30.0, len(data))
    monkeypatch.setattr(media, "probe", probe)


def _job(conn, status="done"):
    from hlidac_reels import jobs
    jid = jobs.add(conn, source="manual", ct=311)
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status=%s WHERE id=%s", (status, jid))
    conn.commit()
    return jid


def _set(tmp_path, jid, kinds, manifest=True, templates=None):
    folder = tmp_path / "inbox" / str(jid)
    folder.mkdir(parents=True)
    variants = []
    for n, kind in enumerate(kinds, start=1):
        (folder / f"variant-{n}.mp4").write_bytes(f"{kind}-{n}".encode())
        tpl = (templates or {}).get(n, "template_v3")
        variants.append({"n": n, "template": tpl, "format": "pn" if tpl == "template_v3" else None,
                         "caption": f"Popisek {n}", "card": {}})
    if manifest:
        (folder / "manifest.json").write_text(
            json.dumps({"job_id": jid, "ct": 311, "source": "manual", "variants": variants}), encoding="utf-8")
    return folder


def test_sada_tri_variant(conn, tmp_path):
    from hlidac_reels import inbox, queue
    jid = _job(conn)
    folder = _set(tmp_path, jid, ["ok", "ok", "ok"], templates={2: "template_v2"})
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert len(res) == 1 and res[0].ok and res[0].job_id == jid and not res[0].rejected
    items = queue.set_items(conn, jid)
    assert [(r.variant, r.status, r.set_id, r.source, r.ct) for r in items] == \
        [(n, "queued", jid, "manual", 311) for n in (1, 2, 3)]
    assert [r.format for r in items] == ["pn", "v2", "pn"]
    assert [r.template for r in items] == ["template_v3", "template_v2", "template_v3"]
    assert all((tmp_path / "media" / "videos" / f"{r.video_sha256}.mp4").is_file() for r in items)
    assert not folder.exists()


def test_bez_manifestu_se_ceka(conn, tmp_path):
    from hlidac_reels import inbox
    jid = _job(conn)
    folder = _set(tmp_path, jid, ["ok", "ok"], manifest=False)
    assert inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media") == []
    assert folder.exists()


def test_uloha_neni_done_se_ceka(conn, tmp_path):
    from hlidac_reels import inbox
    jid = _job(conn, "claimed")
    folder = _set(tmp_path, jid, ["ok", "ok"])
    assert inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media") == []
    assert folder.exists()


def test_zrusena_uloha_smaze_slozku(conn, tmp_path):
    from hlidac_reels import inbox, queue
    jid = _job(conn, "cancelled")
    folder = _set(tmp_path, jid, ["ok", "ok"])
    assert inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media") == []
    assert not folder.exists() and queue.set_items(conn, jid) == []


def test_jedna_platna_varianta(conn, tmp_path):
    from hlidac_reels import inbox, queue
    jid = _job(conn)
    folder = _set(tmp_path, jid, ["ok", "broken", "broken"])
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert len(res) == 1 and res[0].ok is False and set(res[0].rejected) == {2, 3}
    assert queue.set_items(conn, jid) == []
    with conn.cursor() as cur:
        cur.execute("SELECT status, error, notified_at FROM reel_jobs WHERE id=%s", (jid,))
        status, error, notified = cur.fetchone()
    assert status == "failed" and "varianta 2" in error and len(error) <= 500 and notified is None
    assert not folder.exists()
    assert not list((tmp_path / "media").rglob("*.mp4"))  # žádné osiřelé kopie


def test_dve_platne(conn, tmp_path):
    from hlidac_reels import inbox, queue
    jid = _job(conn)
    _set(tmp_path, jid, ["ok", "ok", "short"])
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert res[0].ok and list(res[0].rejected) == [3] and len(res[0].reel_ids) == 2
    assert [r.variant for r in queue.set_items(conn, jid)] == [1, 2]


def test_poskozeny_manifest(conn, tmp_path):
    from hlidac_reels import inbox
    jid = _job(conn)
    folder = _set(tmp_path, jid, ["ok", "ok"])
    (folder / "manifest.json").write_text("{nejson", encoding="utf-8")
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert len(res) == 1 and res[0].ok is False
    with conn.cursor() as cur:
        cur.execute("SELECT status, error FROM reel_jobs WHERE id=%s", (jid,))
        assert cur.fetchone()[0] == "failed"
    assert not folder.exists()


def test_obrovske_video_a_duplicita(conn, tmp_path, monkeypatch):
    from hlidac_reels import inbox, queue
    monkeypatch.setattr(inbox, "MAX_VIDEO_BYTES", 5)
    jid = _job(conn)
    _set(tmp_path, jid, ["ok", "ok"])  # "ok-1" má 4 bajty, projde; přidáme velké
    folder = tmp_path / "inbox" / str(jid)
    (folder / "variant-3.mp4").write_bytes(b"ok-3-prilis-velke")
    m = json.loads((folder / "manifest.json").read_text())
    m["variants"].append({"n": 3, "template": "template_v3", "format": "pn", "caption": "x", "card": {}})
    (folder / "manifest.json").write_text(json.dumps(m))
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert res[0].ok and "MB" in res[0].rejected[3][0]
    # druhá sada se stejnými videi: duplicita = neplatná varianta
    jid2 = _job(conn)
    ((tmp_path / "inbox") / str(jid2)).mkdir(parents=True)
    f2 = tmp_path / "inbox" / str(jid2)
    for n in (1, 2):
        (f2 / f"variant-{n}.mp4").write_bytes(f"ok-{n}".encode())
    (f2 / "manifest.json").write_text(json.dumps({"job_id": jid2, "ct": None, "source": "manual", "variants": [
        {"n": n, "template": "template_v2", "format": None, "caption": "c", "card": {}} for n in (1, 2)]}))
    res2 = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert res2[0].ok is False and set(res2[0].rejected) == {1, 2}
    assert queue.set_items(conn, jid2) == []
