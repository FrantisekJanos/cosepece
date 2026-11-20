"""Testy publikace jedné položky (PostgreSQL, marker pg; Meta je falešná, bez sítě)."""
from __future__ import annotations

import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hlidac_reels.meta import MetaError, MetaTimeout

pytestmark = pytest.mark.pg

PUBLIC_BASE = "https://cosepece.cz/reels-media"


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


class FakeMeta:
    """Falešný klient Meta: počítá volání, chyby se nastavují slovníkem fail[metoda]."""

    def __init__(self, media_dir: Path | None = None):
        self.calls: Counter[str] = Counter()
        self.fail: dict[str, Exception] = {}
        self.media_dir = media_dir
        self.public_seen: list[bool] = []  # existoval veřejný soubor při ig_create?
        self.ig_urls: list[str] = []
        self.ig_captions: list[str] = []
        self.fb_captions: list[str] = []
        self.ig_found: tuple[str, str] | None = None
        self.fb_found: tuple[str, str] | None = None
        self.find_args: list[tuple[str, str, datetime]] = []

    def _call(self, name: str) -> None:
        self.calls[name] += 1
        if name in self.fail:
            raise self.fail[name]

    def ig_create(self, video_url: str, caption: str) -> str:
        self.ig_urls.append(video_url)
        self.ig_captions.append(caption)
        if self.media_dir is not None:
            name = video_url.rsplit("/", 1)[1]
            self.public_seen.append((self.media_dir / "public" / name).exists())
        self._call("ig_create")
        return f"C{self.calls['ig_create']}"

    def ig_wait(self, container_id: str, timeout: float = 600, interval: float = 10) -> None:
        self._call("ig_wait")

    def ig_publish(self, container_id: str) -> tuple[str, str]:
        self._call("ig_publish")
        return "M1", "https://www.instagram.com/reel/M1/"

    def fb_upload_reel(self, path, description: str) -> tuple[str, str]:
        self.fb_captions.append(description)
        self._call("fb_upload_reel")
        return "V1", "https://www.facebook.com/reel/V1"

    def ig_find_recent(self, caption_prefix: str, since: datetime):
        self.find_args.append(("ig", caption_prefix, since))
        self._call("ig_find_recent")
        return self.ig_found

    def fb_find_recent(self, caption_prefix: str, since: datetime):
        self.find_args.append(("fb", caption_prefix, since))
        self._call("fb_find_recent")
        return self.fb_found


@pytest.fixture
def media_dir(tmp_path):
    d = tmp_path / "media"
    (d / "videos").mkdir(parents=True)
    return d


def _approved(conn, media_dir: Path, caption: str = "Sněmovna zkrátila lhůtu na 4 roky", ct: int | None = 221) -> int:
    from hlidac_reels import queue
    video = media_dir / "videos" / "abc.mp4"
    video.write_bytes(b"fake mp4")
    rid = queue.add(conn, video_path=str(video), video_sha256="abc", caption=caption, ct=ct)
    sent = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)
    assert queue.transition(conn, rid, "queued", "pending", sent_at=sent)
    assert queue.transition(conn, rid, "pending", "approved")
    return rid


def _run(conn, meta, rid, media_dir):
    from hlidac_reels import publish
    return publish.run(conn, meta, rid, media_dir=str(media_dir), public_base=PUBLIC_BASE)


def test_run_publikuje_obe_site(conn, media_dir):
    from hlidac_reels.caption import for_network
    rid = _approved(conn, media_dir)
    meta = FakeMeta(media_dir)
    r = _run(conn, meta, rid, media_dir)
    assert r.status == "published"
    assert r.ig_container_id == "C1"
    assert (r.ig_media_id, r.ig_permalink) == ("M1", "https://www.instagram.com/reel/M1/")
    assert (r.fb_video_id, r.fb_permalink) == ("V1", "https://www.facebook.com/reel/V1")
    assert r.published_at is not None
    assert r.error is None and r.attempts == 0
    # veřejná URL obsahuje token, soubor existoval při ig_create a pak zmizel
    assert r.public_token and meta.ig_urls == [f"{PUBLIC_BASE}/{r.public_token}.mp4"]
    assert meta.public_seen == [True]
    assert not (media_dir / "public" / f"{r.public_token}.mp4").exists()
    assert meta.ig_captions == [for_network(r.caption, 221, "ig")]
    assert meta.fb_captions == [for_network(r.caption, 221, "fb")]


def test_run_dvakrat_publikuje_jednou(conn, media_dir):
    from hlidac_reels import queue
    rid = _approved(conn, media_dir)
    meta = FakeMeta(media_dir)
    _run(conn, meta, rid, media_dir)
    r = _run(conn, meta, rid, media_dir)
    assert r.status == "published"
    assert meta.calls["ig_create"] == 1 and meta.calls["fb_upload_reel"] == 1
    # položka, která není approved, se nemění a API se nevolá
    pid = queue.add(conn, video_path="x.mp4", video_sha256="x", caption="c")
    meta2 = FakeMeta()
    r2 = _run(conn, meta2, pid, media_dir)
    assert r2.status == "queued" and sum(meta2.calls.values()) == 0


def test_ig_ok_fb_chyba_retry_vola_jen_fb(conn, media_dir):
    from hlidac_reels import publish
    rid = _approved(conn, media_dir)
    meta = FakeMeta(media_dir)
    meta.fail["fb_upload_reel"] = MetaError("(#100) chybný parametr", status=400, code=100)
    r = _run(conn, meta, rid, media_dir)
    assert r.status == "failed"
    assert r.ig_media_id == "M1" and r.fb_video_id is None
    assert r.attempts == 1 and "chybný parametr" in r.error
    assert r.published_at is None

    del meta.fail["fb_upload_reel"]
    r = publish.retry(conn, meta, rid, media_dir=str(media_dir), public_base=PUBLIC_BASE)
    assert r.status == "published"
    assert meta.calls["ig_create"] == 1 and meta.calls["ig_publish"] == 1
    assert meta.calls["fb_upload_reel"] == 2
    assert r.fb_video_id == "V1" and r.published_at is not None
    assert r.error is None
    # před opakováním se dohledá jen chybějící síť (IG už má id)
    assert meta.calls["ig_find_recent"] == 0 and meta.calls["fb_find_recent"] == 1


@pytest.mark.parametrize("err", [
    MetaTimeout("IG kontejner C1 není hotový ani po 600 s (stav IN_PROGRESS)"),
    MetaError("síťová chyba: ConnectError"),  # bez HTTP odpovědi
    MetaError("IG kontejner C1 už je zveřejněný (PUBLISHED)"),
])
def test_timeout_ig_je_unknown(conn, media_dir, err):
    rid = _approved(conn, media_dir)
    meta = FakeMeta(media_dir)
    meta.fail["ig_wait"] = err
    r = _run(conn, meta, rid, media_dir)
    assert r.status == "unknown"
    assert r.ig_container_id == "C1" and r.ig_media_id is None
    assert r.attempts == 1 and r.error == err.message
    # FB se po nejistém IG nezkouší
    assert meta.calls["fb_upload_reel"] == 0


def test_reconcile_najde_ig_a_nezveřejní_znovu(conn, media_dir):
    from hlidac_reels import publish
    from hlidac_reels.caption import for_network
    rid = _approved(conn, media_dir)
    meta = FakeMeta(media_dir)
    meta.fail["ig_wait"] = MetaTimeout("IG kontejner C1 není hotový")
    assert _run(conn, meta, rid, media_dir).status == "unknown"

    # IG mezitím Reel zveřejnil, FB ještě neproběhl
    meta.ig_found = ("M9", "https://www.instagram.com/reel/M9/")
    r = publish.reconcile(conn, meta, rid)
    assert r.status == "failed"
    assert (r.ig_media_id, r.ig_permalink) == ("M9", "https://www.instagram.com/reel/M9/")
    kind, prefix, since = meta.find_args[0]
    assert kind == "ig" and prefix == for_network(r.caption, 221, "ig")[:80]
    assert since == r.sent_at
    assert ("fb", for_network(r.caption, 221, "fb")[:80], r.sent_at) in meta.find_args

    # Zkusit znovu: IG se znovu nezveřejní, doplní se jen FB
    del meta.fail["ig_wait"]
    r = publish.retry(conn, meta, rid, media_dir=str(media_dir), public_base=PUBLIC_BASE)
    assert r.status == "published"
    assert meta.calls["ig_create"] == 1 and meta.calls["ig_publish"] == 0
    assert meta.calls["fb_upload_reel"] == 1
    assert r.ig_media_id == "M9" and r.fb_video_id == "V1"


def test_reconcile_najde_obe_a_je_published(conn, media_dir):
    from hlidac_reels import publish
    rid = _approved(conn, media_dir)
    meta = FakeMeta(media_dir)
    meta.fail["ig_wait"] = MetaTimeout("IG kontejner C1 není hotový")
    _run(conn, meta, rid, media_dir)
    meta.ig_found = ("M9", "https://www.instagram.com/reel/M9/")
    meta.fb_found = ("V9", "https://www.facebook.com/reel/V9")
    r = publish.retry(conn, meta, rid, media_dir=str(media_dir), public_base=PUBLIC_BASE)
    assert r.status == "published" and r.published_at is not None
    assert (r.ig_media_id, r.fb_video_id) == ("M9", "V9")
    assert meta.calls["ig_publish"] == 0 and meta.calls["fb_upload_reel"] == 0


def test_verejny_soubor_smazan_i_po_chybe(conn, media_dir):
    rid = _approved(conn, media_dir)
    meta = FakeMeta(media_dir)
    meta.fail["ig_publish"] = MetaError("(#9007) Media not ready", status=400, code=9007)
    r = _run(conn, meta, rid, media_dir)
    assert r.status == "failed"
    assert meta.public_seen == [True]
    assert r.public_token
    assert list((media_dir / "public").iterdir()) == []
    # nový pokus = nový token
    from hlidac_reels import publish
    del meta.fail["ig_publish"]
    first_token = r.public_token
    r = publish.retry(conn, meta, rid, media_dir=str(media_dir), public_base=PUBLIC_BASE)
    assert r.status == "published" and r.public_token != first_token
    assert list((media_dir / "public").iterdir()) == []


def test_chybejici_video_je_failed_bez_volani_api(conn, media_dir):
    rid = _approved(conn, media_dir)
    (media_dir / "videos" / "abc.mp4").unlink()
    meta = FakeMeta(media_dir)
    r = _run(conn, meta, rid, media_dir)
    assert r.status == "failed" and r.attempts == 1
    assert "kopie videa" in r.error
    assert sum(meta.calls.values()) == 0


def test_neocekavana_chyba_je_unknown_bez_textu(conn, media_dir):
    rid = _approved(conn, media_dir)
    meta = FakeMeta(media_dir)
    meta.fail["fb_upload_reel"] = RuntimeError("token=SECRET")
    r = _run(conn, meta, rid, media_dir)
    assert r.status == "unknown" and r.ig_media_id == "M1"
    assert "SECRET" not in r.error and "RuntimeError" in r.error


def test_failed_dohledani_najde_obe_je_published_bez_run(conn, media_dir):
    # HTTP 5xx po zveřejnění: stav failed, ale obsah na sítích je
    from hlidac_reels import publish
    rid = _approved(conn, media_dir)
    meta = FakeMeta(media_dir)
    meta.fail["ig_publish"] = MetaError("HTTP 500", status=500)
    assert _run(conn, meta, rid, media_dir).status == "failed"
    meta.ig_found = ("M9", "https://www.instagram.com/reel/M9/")
    meta.fb_found = ("V9", "https://www.facebook.com/reel/V9")
    r = publish.retry(conn, meta, rid, media_dir=str(media_dir), public_base=PUBLIC_BASE)
    assert r.status == "published" and r.published_at is not None
    assert (r.ig_media_id, r.fb_video_id) == ("M9", "V9")
    assert meta.calls["ig_create"] == 1 and meta.calls["ig_publish"] == 1
    assert meta.calls["fb_upload_reel"] == 0


@pytest.mark.parametrize("start", ["failed", "unknown"])
@pytest.mark.parametrize("err", [MetaError("síťová chyba: ConnectError"), RuntimeError("token=SECRET")])
def test_dohledani_selze_bez_run_stav_zustane(conn, media_dir, start, err):
    from hlidac_reels import publish
    rid = _approved(conn, media_dir)
    meta = FakeMeta(media_dir)
    meta.fail["ig_wait"] = (MetaTimeout("timeout") if start == "unknown"
                            else MetaError("HTTP 400", status=400))
    assert _run(conn, meta, rid, media_dir).status == start
    del meta.fail["ig_wait"]
    meta.fail["ig_find_recent"] = err
    r = publish.retry(conn, meta, rid, media_dir=str(media_dir), public_base=PUBLIC_BASE)
    assert r.status == start
    assert r.error.startswith("dohledání selhalo")
    assert "SECRET" not in r.error
    assert meta.calls["ig_create"] == 1 and meta.calls["fb_upload_reel"] == 0
