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


# --- Threads ---------------------------------------------------------------

from hlidac_reels.threads import ThreadsError, ThreadsNoToken, ThreadsTimeout  # noqa: E402


class FakeThreads:
    """Falešný klient Threads: počítá volání a hlídá, zda veřejný soubor existoval."""

    def __init__(self, media_dir: Path):
        self.calls: Counter[str] = Counter()
        self.fail: dict[str, Exception] = {}
        self.media_dir = media_dir
        self.urls: list[str] = []
        self.texts: list[str] = []
        self.public_seen: list[tuple[str, bool]] = []
        self.create_bytes: list[bytes | None] = []  # obsah souboru za URL při create_video
        self.found: tuple[str, str] | None = None
        self.find_args: list[tuple[str, datetime]] = []

    def _seen(self, step: str) -> None:
        name = self.urls[-1].rsplit("/", 1)[1]
        if step == "create":
            f = self.media_dir / "public" / name
            self.create_bytes.append(f.read_bytes() if f.exists() else None)
        self.public_seen.append((step, (self.media_dir / "public" / name).exists()))

    def _call(self, name: str) -> None:
        self.calls[name] += 1
        if name in self.fail:
            raise self.fail[name]

    def create_video(self, video_url: str, text: str) -> str:
        self.urls.append(video_url)
        self.texts.append(text)
        self._seen("create")
        self._call("create_video")
        return f"TC{self.calls['create_video']}"

    def wait(self, container_id: str, timeout: float = 600, interval: float = 10) -> None:
        self._seen("wait")
        self._call("wait")

    def publish(self, container_id: str) -> tuple[str, str]:
        self._seen("publish")
        self._call("publish")
        return "T1", "https://www.threads.net/@cosepece/post/T1"

    def find_recent(self, text_prefix: str, since: datetime):
        self.find_args.append((text_prefix, since))
        self._call("find_recent")
        return self.found


def _run_th(conn, meta, th, rid, media_dir):
    from hlidac_reels import publish
    return publish.run(conn, meta, rid, media_dir=str(media_dir), public_base=PUBLIC_BASE, threads=th)


def _retry_th(conn, meta, th, rid, media_dir):
    from hlidac_reels import publish
    return publish.retry(conn, meta, rid, media_dir=str(media_dir), public_base=PUBLIC_BASE, threads=th)


def _approved_extra(conn, media_dir: Path, sha: str, to: str = "approved") -> int:
    """Další položka (jiné video) ve stavu approved nebo unknown."""
    from hlidac_reels import queue
    video = media_dir / "videos" / f"{sha}.mp4"
    video.write_bytes(sha.encode())
    rid = queue.add(conn, video_path=str(video), video_sha256=sha, caption=f"Popisek {sha}", ct=5)
    steps = [("queued", "pending"), ("pending", "approved")]
    if to == "unknown":
        steps += [("approved", "publishing"), ("publishing", "unknown")]
    for a, b in steps:
        assert queue.transition(conn, rid, a, b)
    return rid


def test_run_publikuje_i_threads(conn, media_dir):
    from hlidac_reels.caption import for_network
    rid = _approved(conn, media_dir)
    meta, th = FakeMeta(media_dir), FakeThreads(media_dir)
    r = _run_th(conn, meta, th, rid, media_dir)
    assert r.status == "published" and r.error is None and r.published_at is not None
    assert r.threads_container_id == "TC1"
    assert (r.threads_media_id, r.threads_permalink) == ("T1", "https://www.threads.net/@cosepece/post/T1")
    assert r.ig_media_id == "M1" and r.fb_video_id == "V1"
    assert th.texts == [for_network(r.caption, 221, "threads")]
    # Threads stahuje stejnou URL jako IG
    assert th.urls == meta.ig_urls == [f"{PUBLIC_BASE}/{r.public_token}.mp4"]
    assert th.calls["publish"] == 1 and meta.calls["fb_upload_reel"] == 1


def test_threads_chyba_retry_jen_threads(conn, media_dir):
    from hlidac_reels.caption import for_network
    rid = _approved(conn, media_dir)
    meta, th = FakeMeta(media_dir), FakeThreads(media_dir)
    th.fail["publish"] = ThreadsError("Invalid parameter", status=400, code=100)
    r = _run_th(conn, meta, th, rid, media_dir)
    # Threads selže, IG i FB projdou; položka čeká na opakování jen pro Threads
    assert r.status == "failed" and r.attempts == 1
    assert r.ig_media_id == "M1" and r.fb_video_id == "V1"
    assert r.threads_container_id == "TC1" and r.threads_media_id is None
    assert r.error.startswith("Threads:") and "Invalid parameter" in r.error
    assert r.published_at is None
    assert list((media_dir / "public").iterdir()) == []

    del th.fail["publish"]
    first_token = r.public_token
    r = _retry_th(conn, meta, th, rid, media_dir)
    assert r.status == "published" and r.error is None
    assert r.threads_media_id == "T1"
    # dohledání jen Threads, IG a FB se znovu nevolají
    assert th.calls["find_recent"] == 1
    assert th.find_args == [(for_network(r.caption, 221, "threads")[:80], r.sent_at)]
    assert meta.calls["ig_find_recent"] == 0 and meta.calls["fb_find_recent"] == 0
    assert meta.calls["ig_create"] == 1 and meta.calls["fb_upload_reel"] == 1
    assert th.calls["create_video"] == 2
    # pro Threads vznikla nová veřejná kopie a po publikaci zmizela
    assert r.public_token != first_token
    assert th.urls[-1] == f"{PUBLIC_BASE}/{r.public_token}.mp4"
    assert th.public_seen[-3:] == [("create", True), ("wait", True), ("publish", True)]
    assert list((media_dir / "public").iterdir()) == []


def test_bez_threads_publikuje_fb_ig(conn, media_dir):
    from hlidac_reels import publish
    rid = _approved(conn, media_dir)
    meta = FakeMeta(media_dir)
    r = _run_th(conn, meta, None, rid, media_dir)
    assert r.status == "published" and r.error is None
    assert r.threads_media_id is None and r.threads_container_id is None
    # dohledání bez Threads: IG a FB stačí k published
    rid2 = _approved_extra(conn, media_dir, "def", to="unknown")
    meta2 = FakeMeta(media_dir)
    meta2.ig_found = ("M9", "https://www.instagram.com/reel/M9/")
    meta2.fb_found = ("V9", "https://www.facebook.com/reel/V9")
    assert publish.reconcile(conn, meta2, rid2).status == "published"


def test_verejny_soubor_az_po_ig_a_threads(conn, media_dir):
    rid = _approved(conn, media_dir)
    meta, th = FakeMeta(media_dir), FakeThreads(media_dir)
    r = _run_th(conn, meta, th, rid, media_dir)
    assert r.status == "published"
    assert meta.public_seen == [True]
    assert th.public_seen == [("create", True), ("wait", True), ("publish", True)]
    assert list((media_dir / "public").iterdir()) == []

    # smaže se i po chybě Threads
    rid2 = _approved_extra(conn, media_dir, "x")
    th2 = FakeThreads(media_dir)
    th2.fail["wait"] = ThreadsError("kontejner Threads TC1 skončil ve stavu ERROR")
    r2 = _run_th(conn, FakeMeta(media_dir), th2, rid2, media_dir)
    assert r2.status == "unknown"
    assert th2.public_seen == [("create", True), ("wait", True)]
    assert list((media_dir / "public").iterdir()) == []


def test_reconcile_najde_threads(conn, media_dir):
    from hlidac_reels import publish
    rid = _approved(conn, media_dir)
    meta, th = FakeMeta(media_dir), FakeThreads(media_dir)
    th.fail["wait"] = ThreadsTimeout("kontejner Threads TC1 není hotový")
    r = _run_th(conn, meta, th, rid, media_dir)
    assert r.status == "unknown" and r.fb_video_id == "V1"
    th.found = ("T9", "https://www.threads.net/@cosepece/post/T9")
    r = publish.reconcile(conn, meta, rid, threads=th)
    assert r.status == "published" and r.published_at is not None
    assert (r.threads_media_id, r.threads_permalink) == ("T9", "https://www.threads.net/@cosepece/post/T9")
    assert th.calls["publish"] == 0 and th.calls["create_video"] == 1


@pytest.mark.parametrize("err", [
    ThreadsTimeout("kontejner Threads TC1 není hotový ani po 600 s (stav IN_PROGRESS)"),
    ThreadsError("ConnectError: spojení selhalo"),  # bez HTTP odpovědi
    ThreadsError("kontejner Threads TC1 už je zveřejněný (PUBLISHED)"),
])
def test_threads_timeout_unknown(conn, media_dir, err):
    from hlidac_reels import publish
    rid = _approved(conn, media_dir)
    meta, th = FakeMeta(media_dir), FakeThreads(media_dir)
    th.fail["wait"] = err
    r = _run_th(conn, meta, th, rid, media_dir)
    assert r.status == "unknown" and r.attempts == 1
    assert r.error == f"Threads: {err.message}"
    assert r.threads_container_id == "TC1" and r.threads_media_id is None
    assert r.ig_media_id == "M1" and r.fb_video_id == "V1"
    # nejistý výsledek: dohledání nenajde → failed, nic se znovu nepublikuje
    del th.fail["wait"]
    r = publish.reconcile(conn, meta, rid, threads=th)
    assert r.status == "failed" and th.calls["find_recent"] == 1
    assert th.calls["create_video"] == 1


def test_threads_bez_tokenu_failed_fb_projde(conn, media_dir):
    rid = _approved(conn, media_dir)
    meta, th = FakeMeta(media_dir), FakeThreads(media_dir)
    th.fail["create_video"] = ThreadsNoToken("chybí token Threads")
    r = _run_th(conn, meta, th, rid, media_dir)
    assert r.status == "failed" and "token" in r.error
    assert r.fb_video_id == "V1" and r.ig_media_id == "M1"


def test_threads_neocekavana_chyba_i_fb_chyba(conn, media_dir):
    rid = _approved(conn, media_dir)
    meta, th = FakeMeta(media_dir), FakeThreads(media_dir)
    th.fail["publish"] = RuntimeError("token=SECRET")
    meta.fail["fb_upload_reel"] = MetaError("(#100) chybný parametr", status=400, code=100)
    r = _run_th(conn, meta, th, rid, media_dir)
    # horší z výsledků: unknown; obě chyby v textu, bez tajemství
    assert r.status == "unknown" and r.attempts == 1
    assert "RuntimeError" in r.error and "chybný parametr" in r.error and "SECRET" not in r.error


def test_ig_chyba_threads_ani_fb_nezkousi(conn, media_dir):
    rid = _approved(conn, media_dir)
    meta, th = FakeMeta(media_dir), FakeThreads(media_dir)
    meta.fail["ig_wait"] = MetaTimeout("IG kontejner C1 není hotový")
    r = _run_th(conn, meta, th, rid, media_dir)
    assert r.status == "unknown"
    assert sum(th.calls.values()) == 0 and meta.calls["fb_upload_reel"] == 0
    assert list((media_dir / "public").iterdir()) == []


def test_dohledani_threads_selze_bez_run(conn, media_dir):
    rid = _approved(conn, media_dir)
    meta, th = FakeMeta(media_dir), FakeThreads(media_dir)
    th.fail["publish"] = ThreadsError("HTTP 500", status=500)
    assert _run_th(conn, meta, th, rid, media_dir).status == "failed"
    del th.fail["publish"]
    th.fail["find_recent"] = ThreadsNoToken("chybí token Threads")
    r = _retry_th(conn, meta, th, rid, media_dir)
    assert r.status == "failed" and r.error.startswith("dohledání selhalo")
    assert "token" in r.error
    assert th.calls["create_video"] == 1


def test_migrace_stare_tabulky(conn):
    """Tabulka ze schématu bez sloupců Threads: migrace je doplní a staré řádky se načtou."""
    from hlidac_reels import db, queue
    with conn.cursor() as cur:
        cur.execute("DROP TABLE reel_queue")
        cur.execute("""
            CREATE TABLE reel_queue (
                id bigserial PRIMARY KEY, source text NOT NULL DEFAULT 'ukazka', ct integer, format text,
                video_path text NOT NULL, video_sha256 text NOT NULL, caption text NOT NULL,
                status text NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued','pending','approved','publishing','published','rejected',
                                      'failed','unknown')),
                telegram_video_msg_id bigint, telegram_msg_id bigint, ig_container_id text, ig_media_id text,
                ig_permalink text, fb_video_id text, fb_permalink text, public_token text, error text,
                attempts integer NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now(),
                sent_at timestamptz, decided_at timestamptz, published_at timestamptz,
                UNIQUE (video_sha256), UNIQUE (public_token))""")
        cur.execute("INSERT INTO reel_queue (video_path, video_sha256, caption, status, ig_media_id, fb_video_id, "
                    "published_at) VALUES ('v.mp4', 's1', 'c', 'published', 'M1', 'V1', now())")
    conn.commit()
    db.migrate(conn)
    db.migrate(conn)  # opakovaná migrace nic nerozbije
    r = queue.get(conn, 1)
    assert r.status == "published" and (r.ig_media_id, r.fb_video_id) == ("M1", "V1")
    assert (r.threads_container_id, r.threads_media_id, r.threads_permalink) == (None, None, None)
    assert queue.by_status(conn, ("published",))[0].id == 1
    queue.set_fields(conn, 1, threads_media_id="T1")
    assert queue.get(conn, 1).threads_media_id == "T1"


def test_chyba_threads_az_za_chybou_fb(conn, media_dir):
    """Chyba Threads (nastane před FB) je v reel.error poslední; bot z ní bere důvod do konce textu."""
    rid = _approved(conn, media_dir)
    meta, th = FakeMeta(media_dir), FakeThreads(media_dir)
    th.fail["publish"] = ThreadsError("Invalid parameter; video_url", status=400)
    meta.fail["fb_upload_reel"] = MetaError("(#100) chybný parametr", status=400, code=100)
    r = _run_th(conn, meta, th, rid, media_dir)
    assert r.status == "failed"
    assert r.error == "(#100) chybný parametr; Threads: Invalid parameter; video_url"


# --- video na šířku: Threads dostane vlastní kopii 16:9 ---

def _approved_landscape(conn, media_dir: Path, missing: bool = False) -> int:
    from hlidac_reels import queue
    video = media_dir / "videos" / "v.mp4"
    video.write_bytes(b"svisle")
    wide = media_dir / "videos" / "w.mp4"
    if not missing:
        wide.write_bytes(b"naplocho")
    rid = queue.add(conn, video_path=str(video), video_sha256="v1", caption="Popisek sirka", ct=5,
                    video_landscape_path=str(wide), video_landscape_sha256="w1")
    for a, b in [("queued", "pending"), ("pending", "approved")]:
        assert queue.transition(conn, rid, a, b)
    return rid


def test_threads_dostane_sirku(conn, media_dir):
    rid = _approved_landscape(conn, media_dir)
    meta, th = FakeMeta(media_dir), FakeThreads(media_dir)
    r = _run_th(conn, meta, th, rid, media_dir)
    assert r.status == "published"
    assert th.urls != meta.ig_urls and len(th.urls) == 1 and len(meta.ig_urls) == 1
    assert th.create_bytes == [b"naplocho"]
    assert list((media_dir / "public").iterdir()) == []


def test_threads_bez_sirky_sdili_kopii(conn, media_dir):
    rid = _approved(conn, media_dir)
    meta, th = FakeMeta(media_dir), FakeThreads(media_dir)
    _run_th(conn, meta, th, rid, media_dir)
    assert th.urls == meta.ig_urls and len(th.urls) == 1
    assert th.create_bytes == [b"fake mp4"]


def test_chybejici_soubor_sirky_nezastavi_fb(conn, media_dir):
    rid = _approved_landscape(conn, media_dir, missing=True)
    meta, th = FakeMeta(media_dir), FakeThreads(media_dir)
    r = _run_th(conn, meta, th, rid, media_dir)
    assert r.fb_video_id == "V1" and r.ig_media_id == "M1"
    assert r.status == "failed"
    assert r.error.startswith("Threads: kopie videa pro Threads selhala")
    assert th.calls["create_video"] == 0
    assert list((media_dir / "public").iterdir()) == []


def test_backfill_bere_sirku(conn, media_dir):
    from hlidac_reels import publish, queue
    rid = _approved_landscape(conn, media_dir)
    th = FakeThreads(media_dir)
    publish.publish_threads_only(conn, th, queue.get(conn, rid), media_dir=str(media_dir),
                                 public_base=PUBLIC_BASE)
    assert th.create_bytes == [b"naplocho"]
    assert list((media_dir / "public").iterdir()) == []
