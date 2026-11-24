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
        if data.startswith(b"wide"):  # video na šířku 16:9
            return VideoInfo("h264", "aac", 1920, 1080, 30.0, len(data))
        return VideoInfo("h264", "aac", 1080, 1920, 30.0, len(data))
    monkeypatch.setattr(media, "probe", probe)


def _job(conn, status="done"):
    from hlidac_reels import jobs
    jid = jobs.add(conn, source="manual", ct=311)
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status=%s WHERE id=%s", (status, jid))
    conn.commit()
    return jid


def _set(tmp_path, jid, kinds, manifest=True, templates=None, land=None):
    """land: n -> obsah videa na šířku (wide = 16:9, ok = 9:16, None = příznak bez souboru)."""
    folder = tmp_path / "inbox" / str(jid)
    folder.mkdir(parents=True)
    variants = []
    for n, kind in enumerate(kinds, start=1):
        (folder / f"variant-{n}.mp4").write_bytes(f"{kind}-{n}".encode())
        tpl = (templates or {}).get(n, "template_v3")
        variants.append({"n": n, "template": tpl, "format": "pn" if tpl == "template_v3" else None,
                         "caption": f"Popisek {n}", "card": {}})
        if land and n in land:
            variants[-1]["landscape"] = True
            if land[n] is not None:
                (folder / f"variant-{n}-sirka.mp4").write_bytes(f"{land[n]}-{n}".encode())
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


def test_sada_s_jednou_variantou_projde(conn, tmp_path):
    from hlidac_reels import inbox, queue
    jid = _job(conn)
    folder = _set(tmp_path, jid, ["ok"])
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    m["skipped"] = [{"n": 2, "format": "pn", "errors": ["render selhal"]}]
    (folder / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert len(res) == 1 and res[0].ok and not res[0].rejected and len(res[0].reel_ids) == 1
    assert [r.variant for r in queue.set_items(conn, jid)] == [1]
    assert not folder.exists()


def test_jedna_platna_z_vadnych_projde(conn, tmp_path):
    from hlidac_reels import inbox, queue
    jid = _job(conn)
    _set(tmp_path, jid, ["ok", "broken", "broken"])
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert res[0].ok and set(res[0].rejected) == {2, 3}
    assert [r.variant for r in queue.set_items(conn, jid)] == [1]


def test_zadna_platna_varianta(conn, tmp_path):
    from hlidac_reels import inbox, queue
    jid = _job(conn)
    folder = _set(tmp_path, jid, ["broken", "broken", "broken"])
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert len(res) == 1 and res[0].ok is False and set(res[0].rejected) == {1, 2, 3}
    assert queue.set_items(conn, jid) == []
    with conn.cursor() as cur:
        cur.execute("SELECT status, error, notified_at FROM reel_jobs WHERE id=%s", (jid,))
        status, error, notified = cur.fetchone()
    assert status == "failed" and "varianta 2" in error and len(error) <= 500 and notified is None
    assert error.startswith("žádná platná varianta. ")
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


def _rewrite(folder, jid, mutate):
    mp = folder / "manifest.json"
    m = json.loads(mp.read_text())
    mutate(m)
    mp.write_text(json.dumps(m))


def test_format_v3_povinny_a_platny(conn, tmp_path):
    from hlidac_reels import inbox
    jid = _job(conn)
    folder = _set(tmp_path, jid, ["ok", "ok"])

    def mut(m):
        m["variants"][0]["format"] = None
        m["variants"][1]["format"] = "neznamy"
    _rewrite(folder, jid, mut)
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert res[0].ok is False and set(res[0].rejected) == {1, 2}


def test_format_v2_musi_byt_null(conn, tmp_path):
    from hlidac_reels import inbox
    jid = _job(conn)
    folder = _set(tmp_path, jid, ["ok", "ok", "ok"], templates={1: "template_v2", 2: "template_v2"})
    _rewrite(folder, jid, lambda m: m["variants"][0].update(format="pn"))
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert res[0].ok and list(res[0].rejected) == [1]


def test_job_id_musi_odpovidat_slozce(conn, tmp_path):
    from hlidac_reels import inbox, queue
    jid = _job(conn)
    other = _job(conn)
    folder = _set(tmp_path, jid, ["ok", "ok"])
    _rewrite(folder, jid, lambda m: m.update(job_id=other))
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert res[0].job_id == jid and res[0].ok is False
    assert queue.set_items(conn, other) == [] and not folder.exists()
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM reel_jobs WHERE id=%s", (other,))
        assert cur.fetchone()[0] == "done"


def test_probe_oserror_nezhrouti_ingest(conn, tmp_path, monkeypatch):
    from hlidac_reels import inbox
    jid = _job(conn)
    _set(tmp_path, jid, ["ok", "ok"])

    def boom(path):
        raise FileNotFoundError("ffprobe")
    monkeypatch.setattr(media, "probe", boom)
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert res[0].ok is False and set(res[0].rejected) == {1, 2}


def test_selhani_uprostred_sady_uklidi(conn, tmp_path, monkeypatch):
    from hlidac_reels import inbox, queue
    jid = _job(conn)
    _set(tmp_path, jid, ["ok", "ok", "ok"])
    real = queue.add
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise queue.DuplicateVideo("x")
        return real(*a, **k)
    monkeypatch.setattr(queue, "add", flaky)
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert res[0].ok is False and res[0].reel_ids == []
    assert queue.set_items(conn, jid) == []
    assert not list((tmp_path / "media").rglob("*.mp4"))
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM reel_jobs WHERE id=%s", (jid,))
        assert cur.fetchone()[0] == "failed"


def test_uz_prevzata_sada_jen_smaze_slozku(conn, tmp_path):
    """Pád mezi commitem sady a smazáním složky: další průchod složku jen smaže, úloha zůstane done."""
    import shutil
    from hlidac_reels import inbox, queue
    jid = _job(conn)
    folder = _set(tmp_path, jid, ["ok", "ok"])
    backup = tmp_path / "zaloha"
    shutil.copytree(folder, backup)
    assert inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")[0].ok
    shutil.copytree(backup, folder)  # složka „přežila“ pád
    assert inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media") == []
    assert not folder.exists()
    assert [r.variant for r in queue.set_items(conn, jid)] == [1, 2]
    with conn.cursor() as cur:
        cur.execute("SELECT status, error FROM reel_jobs WHERE id=%s", (jid,))
        assert cur.fetchone() == ("done", None)
    conn.commit()


def test_sada_se_sirkou(conn, tmp_path):
    from hlidac_reels import inbox, queue
    jid = _job(conn)
    _set(tmp_path, jid, ["ok", "ok", "ok"], templates={1: "template_v2"}, land={1: "wide", 3: "wide"})
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert res[0].ok and not res[0].warnings
    items = queue.set_items(conn, jid)
    assert [r.video_landscape_sha256 is not None for r in items] == [True, False, True]
    for r in (items[0], items[2]):
        assert r.video_landscape_path == str(tmp_path / "media" / "videos" / f"{r.video_landscape_sha256}.mp4")
        assert (tmp_path / "media" / "videos" / f"{r.video_landscape_sha256}.mp4").read_bytes().startswith(b"wide")
        assert r.video_landscape_sha256 != r.video_sha256


def test_vadna_sirka_nevyradi_variantu(conn, tmp_path):
    from hlidac_reels import inbox, queue
    jid = _job(conn)
    # 1: šířka s poměrem 9:16, 2: poškozená, 3: příznak bez souboru
    _set(tmp_path, jid, ["ok", "ok", "ok"], land={1: "ok", 2: "broken", 3: None})
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert res[0].ok and len(res[0].reel_ids) == 3 and not res[0].rejected
    assert "16:9" in " ".join(res[0].warnings[1])
    assert "selhala" in " ".join(res[0].warnings[2])
    assert "chybí" in " ".join(res[0].warnings[3])
    assert all(r.video_landscape_path is None for r in queue.set_items(conn, jid))


def test_sirka_duplicitni_s_frontou(conn, tmp_path):
    from hlidac_reels import inbox, media as m, queue
    jid = _job(conn)
    folder = _set(tmp_path, jid, ["ok", "ok"], land={1: "wide"})
    digest = m.sha256(folder / "variant-1-sirka.mp4")
    queue.add(conn, video_path="/x.mp4", video_sha256="jine", caption="c",
              video_landscape_path="/y.mp4", video_landscape_sha256=digest)
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert res[0].ok and "už ve frontě" in " ".join(res[0].warnings[1])
    assert queue.set_items(conn, jid)[0].video_landscape_path is None


def test_sirka_neni_bool_je_vadny_manifest(conn, tmp_path):
    from hlidac_reels import inbox
    jid = _job(conn)
    folder = _set(tmp_path, jid, ["ok", "ok"])
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    m["variants"][0]["landscape"] = "ano"
    (folder / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert res and not res[0].ok and not folder.exists()


def _autopilot_job(conn):
    from hlidac_reels import jobs
    jid = jobs.add(conn, source="manual", ct=311, autopilot=True)
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status='done' WHERE id=%s", (jid,))
    conn.commit()
    return jid


def test_ingest_autopilot_schvaleno(conn, tmp_path):
    from hlidac_reels import inbox, queue
    jid = _autopilot_job(conn)
    _set(tmp_path, jid, ["ok", "ok"])
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media", autopilot_on=True)
    assert res[0].ok and res[0].autopilot is True and len(res[0].reel_ids) == 2
    items = queue.set_items(conn, jid)
    assert [(r.status, r.autopilot) for r in items] == [("approved", True)] * 2
    assert all(r.decided_at is not None and r.sent_at is not None for r in items)


def test_ingest_autopilot_vypnuty_ke_schvaleni(conn, tmp_path):
    # úloha vznikla s autopilotem, ale přepínač je při převzetí vypnutý: běžné schvalování
    from hlidac_reels import inbox, queue
    jid = _autopilot_job(conn)
    _set(tmp_path, jid, ["ok", "ok"])
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media", autopilot_on=False)
    assert res[0].ok and res[0].autopilot is False
    items = queue.set_items(conn, jid)
    assert [(r.status, r.autopilot, r.decided_at, r.sent_at) for r in items] == [("queued", False, None, None)] * 2


def test_ingest_bez_autopilota_ulohy_ke_schvaleni(conn, tmp_path):
    # zapnutý přepínač nestačí: úloha bez autopilota (např. /video) jde ke schválení
    from hlidac_reels import inbox, queue
    jid = _job(conn)
    _set(tmp_path, jid, ["ok"])
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media", autopilot_on=True)
    assert res[0].ok and res[0].autopilot is False
    assert [(r.status, r.autopilot) for r in queue.set_items(conn, jid)] == [("queued", False)]


def test_ingest_jedna_varianta_autopilot(conn, tmp_path):
    from hlidac_reels import inbox, queue
    jid = _autopilot_job(conn)
    folder = _set(tmp_path, jid, ["ok"])
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    m["skipped"] = [{"n": 2, "format": "pn", "errors": ["render selhal"]}]
    (folder / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media", autopilot_on=True)
    assert res[0].ok and res[0].autopilot is True
    assert res[0].skipped == [{"n": 2, "format": "pn", "errors": ["render selhal"]}]
    assert [(r.variant, r.status) for r in queue.set_items(conn, jid)] == [(1, "approved")]


def test_ingest_vadny_skipped_se_ignoruje(conn, tmp_path):
    from hlidac_reels import inbox
    jid = _job(conn)
    folder = _set(tmp_path, jid, ["ok"])
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    m["skipped"] = "nesmysl"
    (folder / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    res = inbox.ingest_all(conn, tmp_path / "inbox", tmp_path / "media")
    assert res[0].ok and res[0].skipped == []
