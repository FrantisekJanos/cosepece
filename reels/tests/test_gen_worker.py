"""Worker na PC: zpracování úlohy, smyčka, HTTP klient reels_api.php a zámek proti druhé instanci."""
from __future__ import annotations

import http.client
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from hlidac_reels.generator import generate as gen_mod
from hlidac_reels.generator import worker
from hlidac_reels.generator.api import ApiError, ReelsApi
from hlidac_reels.generator.claude import EnvError
from hlidac_reels.generator.generate import GenResult, Variant

JOB = {"job_id": 7, "claim_token": "c" * 32, "ct": 221, "source": "manual", "format_hint": None,
       "reason_keys": ["retro"], "has_amendments": True, "card_v2": {"ct": 221}}


@pytest.fixture(autouse=True)
def _tools_ok(monkeypatch):
    """Testy smyčky nemají záviset na nástrojích PC; test kontroly si ji podvrhne sám."""
    monkeypatch.setattr(worker, "check_tools", lambda mcp_config: [])


class FakeApi:
    def __init__(self, job=None, conflict_on=None, landscape_reply: dict | None = None, progress_error=None):
        self.job = job
        self.conflict_on = conflict_on  # "upload" | "result" | "landscape" (409) | ApiError při nahrání šířky
        # odpověď nového serveru; starší server orientation nevrací
        self.landscape_reply = {"ok": True, "orientation": "landscape"} if landscape_reply is None else landscape_reply
        self.calls: list[tuple] = []
        # progress (z vlákna Progress) se zapisuje zvlášť, ať pořadí v calls nezávisí na rychlosti vlákna
        self.progress_error = progress_error  # ApiError, kterou progress vyhodí (409 zrušeno, 400 starý server)
        self.progress_calls: list[tuple] = []  # (fáze, poznámka) v pořadí odeslání
        self._progress_cv = threading.Condition()

    @property
    def stages(self) -> list:
        return [stage for stage, _ in self.progress_calls]

    def progress(self, job_id, claim_token, stage, note=None):
        with self._progress_cv:
            self.progress_calls.append((stage, note))
            self._progress_cv.notify_all()
        if self.progress_error is not None:
            raise self.progress_error
        return {"ok": True, "lease_s": 900}

    def wait_progress(self, pred, timeout: float = 5.0) -> None:
        """Počká, až vlákno Progress pošle, co test čeká (pred nad seznamem fází)."""
        with self._progress_cv:
            assert self._progress_cv.wait_for(lambda: pred(self.stages), timeout), self.stages

    def heartbeat(self):
        self.calls.append(("heartbeat",))

    def claim(self):
        self.calls.append(("claim",))
        return self.job

    def upload_video(self, job_id, claim_token, n, path, orientation="portrait"):
        assert Path(path).is_file()
        if orientation == "landscape":
            self.calls.append(("upload_landscape", job_id, claim_token, n, Path(path).name))
            if isinstance(self.conflict_on, ApiError):
                raise self.conflict_on
            if self.conflict_on == "landscape":
                raise ApiError(409, '{"error": "claim"}')
            return self.landscape_reply
        self.calls.append(("upload_video", job_id, claim_token, n, Path(path).name))
        if self.conflict_on == "upload":
            raise ApiError(409, '{"error": "claim"}')
        return {"ok": True}

    def result(self, job_id, claim_token, variants, skipped=None):
        self.calls.append(("result", job_id, claim_token, variants))
        self.skipped = skipped
        if self.conflict_on == "result":
            raise ApiError(409, '{"error": "claim"}')

    def fail(self, job_id, claim_token, error, kind=None, retry_after_s=None, reason=None):
        self.calls.append(("fail", job_id, claim_token, error,
                           {"kind": kind, "retry_after_s": retry_after_s, "reason": reason}))
        return "queued"

    def names(self):
        return [c[0] for c in self.calls]


def fake_generate(ok_ns: set[int], seen: dict | None = None, land_ns: set[int] = frozenset()):
    """Falešný generate(): zapíše karty a videa jako část 1, varianty z ok_ns jsou platné, z land_ns mají šířku."""
    def gen(*, ct, card_v2, reason_keys, has_amendments, out, mcp_config, model, hint, render, timeout_s,
            published=None, focus_pn=None, focus_info=None, autopilot=False, **kw):
        if seen is not None:
            seen.update(ct=ct, out=Path(out), model=model, hint=hint, mcp_config=mcp_config,
                        reason_keys=reason_keys, has_amendments=has_amendments, published=published,
                        focus_pn=focus_pn, focus_info=focus_info, autopilot=autopilot)
        out = Path(out)
        variants = [Variant(1, "template_v2", None), Variant(2, "template_v3", "cesta"),
                    Variant(3, "template_v3", "pn")]
        for v in variants:
            d = out / f"variant-{v.n}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "card.json").write_text(json.dumps({"ct": ct, "n": v.n}), encoding="utf-8")
            v.caption = f"Popisek {v.n}"
            if v.n in ok_ns:
                v.video = d / f"reel-{ct}-{v.format or 'v2'}.mp4"
                v.video.write_bytes(b"\0\0\0\x18ftypmp42")
                v.ok = True
                if v.n in land_ns:
                    v.video_landscape = d / f"reel-{ct}-{v.format or 'v2'}-sirka.mp4"
                    v.video_landscape.write_bytes(b"\0\0\0\x18ftypmp42land")
            else:
                v.errors = [f"chyba v {out / 'x'}"]
        return GenResult(ct=ct, formats=["cesta", "pn"], variants=variants, ok=len(ok_ns) >= 1)
    return gen


def _process(api, tmp_path, gen):
    return worker.process(api, dict(JOB), root=tmp_path, mcp_config=tmp_path / "mcp.json", model="m", generate=gen)


def test_process_odesle_sadu(tmp_path):
    api = FakeApi()
    seen: dict = {}
    assert _process(api, tmp_path, fake_generate({1, 2, 3}, seen)) is True
    assert seen["out"] == tmp_path / "7" and seen["ct"] == 221 and seen["model"] == "m"
    assert api.names() == ["upload_video"] * 3 + ["result"]
    assert [c[3] for c in api.calls[:3]] == [1, 2, 3]
    assert all(c[2] == JOB["claim_token"] for c in api.calls)
    variants = api.calls[-1][3]
    assert variants[0] == {"n": 1, "template": "template_v2", "format": None, "caption": "Popisek 1",
                           "card": {"ct": 221, "n": 1}}
    assert [v["format"] for v in variants] == [None, "cesta", "pn"]
    assert [v["template"] for v in variants] == ["template_v2", "template_v3", "template_v3"]


def test_process_preda_focus_pn(tmp_path):
    seen: dict = {}
    _process(FakeApi(), tmp_path, fake_generate({1, 2, 3}, seen))
    assert seen["focus_pn"] is None and seen["focus_info"] == []
    info = [{"cislo": 2376, "osoba": "Jana Malá", "cas": "2026-11-23 20:09:00", "titulek": "T",
             "url_psp": "https://www.psp.cz/sqw/sd.sqw?cd=2376&o=10"}]
    job = dict(JOB, focus_pn=[2376], focus_pn_info=info)
    assert worker.process(FakeApi(), job, root=tmp_path, mcp_config=tmp_path / "mcp.json", model="m",
                          generate=fake_generate({1, 2, 3}, seen)) is True
    assert seen["focus_pn"] == [2376] and seen["focus_info"] == info


def test_process_dve_platne(tmp_path):
    api = FakeApi()
    assert _process(api, tmp_path, fake_generate({1, 3})) is True
    assert api.names() == ["upload_video", "upload_video", "result"]
    assert [v["n"] for v in api.calls[-1][3]] == [1, 3]


def test_sada_s_jednou_variantou_projde(tmp_path):
    api = FakeApi()
    assert _process(api, tmp_path, fake_generate({2})) is True
    assert api.names() == ["upload_video", "result"]
    assert [v["n"] for v in api.calls[-1][3]] == [2]


def test_skipped_v_manifestu(tmp_path):
    api = FakeApi()
    assert _process(api, tmp_path, fake_generate({1})) is True
    assert [(s["n"], s["format"]) for s in api.skipped] == [(2, "cesta"), (3, "pn")]
    # chyby bez cest k PC
    assert all(s["errors"] and str(tmp_path) not in " ".join(s["errors"]) for s in api.skipped)
    assert "<root>" in api.skipped[0]["errors"][0]


def test_bez_vypadku_prazdny_skipped(tmp_path):
    api = FakeApi()
    assert _process(api, tmp_path, fake_generate({1, 2, 3})) is True
    assert api.skipped == []


def test_process_preda_autopilota(tmp_path):
    seen: dict = {}
    _process(FakeApi(), tmp_path, fake_generate({1, 2}, seen))
    assert seen["autopilot"] is False  # starší server pole neposílá
    job = dict(JOB, autopilot=True)
    worker.process(FakeApi(), job, root=tmp_path, mcp_config=tmp_path / "mcp.json", model="m",
                   generate=fake_generate({1, 2}, seen))
    assert seen["autopilot"] is True


def test_process_malo_variant(tmp_path):
    api = FakeApi()
    assert _process(api, tmp_path, fake_generate(set())) is False
    assert api.names() == ["fail"]
    error = api.calls[0][3]
    assert len(error) <= 500
    assert str(tmp_path) not in error and "<root>" in error


def _oversize(gen, n: int):
    """Obal falešného generate(): video varianty n bude větší než limit."""
    def wrapped(**kw):
        res = gen(**kw)
        for v in res.variants:
            if v.n == n and v.video:
                v.video.write_bytes(b"\0\0\0\x18ftypmp42" + b"x" * 200)
        return res
    return wrapped


def test_process_velke_video_vypadne(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "MAX_VIDEO_BYTES", 100)
    api = FakeApi()
    assert _process(api, tmp_path, _oversize(fake_generate({1, 2, 3}), 2)) is True
    assert api.names() == ["upload_video", "upload_video", "result"]
    assert [c[3] for c in api.calls[:2]] == [1, 3]
    assert [v["n"] for v in api.calls[-1][3]] == [1, 3]


def test_process_velke_video_malo_variant(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "MAX_VIDEO_BYTES", 100)
    api = FakeApi()
    assert _process(api, tmp_path, _oversize(fake_generate({2}), 2)) is False
    assert api.names() == ["fail"]
    assert "30 MB" in api.calls[0][3]


def test_process_vyjimka(tmp_path):
    def boom(**kw):
        raise RuntimeError(f"claude nejede v {Path.home() / 'tajne'}")
    api = FakeApi()
    assert _process(api, tmp_path, boom) is False
    assert api.names() == ["fail"]
    error = api.calls[0][3]
    assert error.startswith("RuntimeError")
    assert str(Path.home()) not in error


def test_process_dlouha_chyba_oriznuta(tmp_path):
    def boom(**kw):
        raise ValueError("x" * 2000)
    api = FakeApi()
    _process(api, tmp_path, boom)
    assert len(api.calls[0][3]) == 500


def test_process_konflikt_bez_fail(tmp_path):
    for where in ("upload", "result"):
        api = FakeApi(conflict_on=where)
        assert _process(api, tmp_path, fake_generate({1, 2, 3})) is False
        assert "fail" not in api.names()


def test_process_chyba_nahrani_hlasi_fail(tmp_path):
    class Api(FakeApi):
        def upload_video(self, job_id, claim_token, n, path):
            raise ApiError(403, "<!DOCTYPE html><title>Just a moment...</title>")
    api = Api()
    assert _process(api, tmp_path, fake_generate({1, 2, 3})) is False
    assert api.names() == ["fail"]
    assert "403" in api.calls[0][3]


def test_process_sirka_pred_vyskou(tmp_path):
    api = FakeApi()
    assert _process(api, tmp_path, fake_generate({1, 2, 3}, land_ns={1, 3})) is True
    # šířka před výškou: starší server by šířku přepsal videem na výšku
    assert api.names() == ["upload_landscape", "upload_video", "upload_video", "upload_landscape", "upload_video",
                           "result"]
    assert [(c[3], c[4]) for c in api.calls if c[0] == "upload_landscape"] == \
        [(1, "reel-221-v2-sirka.mp4"), (3, "reel-221-pn-sirka.mp4")]
    assert "video_landscape" not in api.calls[-1][3][0]


def test_process_chyba_sirky_nevyradi_variantu(tmp_path):
    api = FakeApi(conflict_on=ApiError(413, "<html>too large</html>"))
    assert _process(api, tmp_path, fake_generate({1, 2}, land_ns={1, 2})) is True
    assert api.names() == ["upload_landscape", "upload_video", "upload_landscape", "upload_video", "result"]


def test_process_konflikt_pri_sirce(tmp_path):
    api = FakeApi(conflict_on="landscape")
    assert _process(api, tmp_path, fake_generate({1, 2}, land_ns={1})) is False
    assert api.names() == ["upload_landscape"]


def test_process_starsi_server_bez_sirky(tmp_path, caplog):
    api = FakeApi(landscape_reply={"ok": True, "bytes": 12})
    with caplog.at_level("WARNING", logger="hlidac_reels.worker"):
        assert _process(api, tmp_path, fake_generate({1, 2}, land_ns={2})) is True
    assert api.names() == ["upload_video", "upload_landscape", "upload_video", "result"]
    assert "starší" in caplog.text


def test_process_velka_sirka_vynechana(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "MAX_VIDEO_BYTES", 13)  # výška má 12 B, šířka 16 B
    api = FakeApi()
    assert _process(api, tmp_path, fake_generate({1, 2}, land_ns={1, 2})) is True
    assert api.names() == ["upload_video", "upload_video", "result"]


def test_run_bez_ulohy_ceka(tmp_path):
    api = FakeApi(job=None)
    sleeps: list[float] = []
    worker.run(api, root=tmp_path, mcp_config=tmp_path / "mcp.json", model="m", sleep=sleeps.append, once=True)
    assert api.names() == ["heartbeat", "claim"]
    assert sleeps == [300]


def test_run_chyba_site_ceka(tmp_path):
    class Api(FakeApi):
        def heartbeat(self):
            raise OSError("síť nejede")
    api = Api()
    sleeps: list[float] = []
    worker.run(api, root=tmp_path, mcp_config=tmp_path / "mcp.json", model="m", sleep=sleeps.append, once=True)
    assert "claim" not in api.names()
    assert sleeps == [300]


def test_run_s_ulohou(tmp_path, monkeypatch):
    api = FakeApi(job=dict(JOB))
    sleeps: list[float] = []
    monkeypatch.setattr(worker, "generate", fake_generate({1, 2, 3}))
    worker.run(api, root=tmp_path, mcp_config=tmp_path / "mcp.json", model="m", sleep=sleeps.append, once=True)
    assert api.names() == ["heartbeat", "claim"] + ["upload_video"] * 3 + ["result"]
    assert sleeps == []


def test_process_neplatna_uloha(tmp_path):
    api = FakeApi()
    for bad in ({"job_id": 7}, {"claim_token": "x"}, {"job_id": "abc", "claim_token": "x"}, {"job_id": 7, "claim_token": ""}):
        assert worker.process(api, bad, root=tmp_path, mcp_config=tmp_path / "m.json", model="m",
                              generate=fake_generate({1, 2, 3})) is False
    assert api.calls == []
    # job_id a claim_token v pořádku, zbytek vadný → chyba se nahlásí
    job = {"job_id": 7, "claim_token": "c"}
    assert worker.process(api, job, root=tmp_path, mcp_config=tmp_path / "m.json", model="m",
                          generate=fake_generate({1, 2, 3})) is False
    assert api.names() == ["fail"] and api.calls[0][3].startswith("KeyError")


def test_run_prezije_jakoukoli_chybu(tmp_path):
    class Api(FakeApi):
        def claim(self):
            raise http.client.IncompleteRead(b"")
    sleeps: list[float] = []
    worker.run(Api(), root=tmp_path, mcp_config=tmp_path / "m.json", model="m", sleep=sleeps.append, once=True)
    assert sleeps == [300]
    # vadná úloha od serveru smyčku neukončí
    api = FakeApi(job={"nic": 1})
    sleeps = []
    worker.run(api, root=tmp_path, mcp_config=tmp_path / "m.json", model="m", sleep=sleeps.append, once=True)
    assert sleeps == [60] and "fail" not in api.names()


def test_run_ctrl_c_skonci(tmp_path):
    class Api(FakeApi):
        def claim(self):
            raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        worker.run(Api(), root=tmp_path, mcp_config=tmp_path / "m.json", model="m", sleep=lambda s: None)


def test_run_po_chybe_pauza(tmp_path, monkeypatch):
    api = FakeApi(job=dict(JOB))
    sleeps: list[float] = []
    monkeypatch.setattr(worker, "generate", fake_generate(set()))
    worker.run(api, root=tmp_path, mcp_config=tmp_path / "m.json", model="m", sleep=sleeps.append, once=True)
    assert api.names() == ["heartbeat", "claim", "fail"]
    assert sleeps == [60]


# --- HTTP klient nad lokálním serverem ---

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self):
        srv = self.server
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        srv.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body})
        status, ctype, payload, *extra = srv.response
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        for k, v in (extra[0] if extra else {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PUT = _reply


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.requests = []
    srv.response = (200, "application/json", b'{"ok": true}')
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _api(srv) -> ReelsApi:
    return ReelsApi(f"http://127.0.0.1:{srv.server_address[1]}/reels_api.php", "tok-123", timeout_s=5)


def test_api_html_odpoved(server):
    html = "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>" + "x" * 500 + "</body></html>"
    server.response = (403, "text/html", html.encode())
    with pytest.raises(ApiError) as ei:
        _api(server).heartbeat()
    assert ei.value.status == 403
    assert ei.value.snippet.startswith("<!DOCTYPE html>")
    assert len(ei.value.snippet) <= 200
    assert "403" in str(ei.value) and "tok-123" not in str(ei.value)


def test_api_ne_json_pri_200(server):
    server.response = (200, "text/html", b"<html>Cloudflare</html>")
    with pytest.raises(ApiError) as ei:
        _api(server).claim()
    assert ei.value.status == 200 and ei.value.snippet.startswith("<html>")


def test_api_konflikt(server):
    server.response = (409, "application/json", '{"error": "Úloha už workeru nepatří"}'.encode())
    with pytest.raises(ApiError) as ei:
        _api(server).result(7, "ct", [])
    assert ei.value.status == 409 and "workeru" in ei.value.snippet


def test_api_pozadavky(server, tmp_path):
    api = _api(server)
    server.response = (200, "application/json", b'{"job": null}')
    assert api.claim() is None
    server.response = (200, "application/json", json.dumps({"job": JOB}).encode())
    assert api.claim() == JOB
    server.response = (200, "application/json", b'{"ok": true, "bytes": 12}')
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\0\0\0\x18ftypmp42")
    api.upload_video(7, "claim-x", 2, video)
    server.response = (200, "application/json", b'{"ok": true, "bytes": 12, "orientation": "landscape"}')
    assert api.upload_video(7, "claim-x", 2, video, orientation="landscape")["orientation"] == "landscape"
    server.response = (200, "application/json", b'{"ok": true, "bytes": 12}')
    api.result(7, "claim-x", [{"n": 1}], skipped=[{"n": 2, "format": "pn", "errors": ["x"]}])
    server.response = (200, "application/json", b'{"status": "failed"}')
    assert api.fail(7, "claim-x", "chyba") == "failed"
    server.response = (200, "application/json", b'{"ok": true}')
    api.heartbeat()

    reqs = server.requests
    assert [(r["method"], r["path"]) for r in reqs] == [
        ("GET", "/reels_api.php?action=claim"), ("GET", "/reels_api.php?action=claim"),
        ("PUT", "/reels_api.php?action=video&job_id=7&n=2"),
        ("PUT", "/reels_api.php?action=video&job_id=7&n=2&orientation=landscape"),
        ("POST", "/reels_api.php?action=result"),
        ("POST", "/reels_api.php?action=fail"), ("POST", "/reels_api.php?action=heartbeat")]
    assert all(r["headers"]["X-Hlidac-Token"] == "tok-123" for r in reqs)
    put = reqs[2]
    assert reqs[3]["body"] == video.read_bytes() and reqs[3]["headers"]["X-Claim-Token"] == "claim-x"
    assert put["headers"]["X-Claim-Token"] == "claim-x"
    assert put["headers"]["Content-Length"] == "12" and put["body"] == video.read_bytes()
    assert json.loads(reqs[4]["body"]) == {"job_id": 7, "variants": [{"n": 1}],
                                           "skipped": [{"n": 2, "format": "pn", "errors": ["x"]}]}
    assert reqs[4]["headers"]["X-Claim-Token"] == "claim-x"
    assert json.loads(reqs[5]["body"]) == {"job_id": 7, "error": "chyba"}
    assert "X-Claim-Token" not in reqs[6]["headers"]


def test_api_progress_druh_chyby_a_stav(server):
    api = _api(server)
    server.response = (200, "application/json", b'{"ok": true, "lease_s": 900}')
    assert api.progress(7, "claim-x", None) == {"ok": True, "lease_s": 900}
    api.progress(7, "claim-x", "oprava", "varianty 2, 3")
    server.response = (200, "application/json", b'{"status": "queued"}')
    assert api.fail(7, "claim-x", "síť", kind="infra", retry_after_s=900) == "queued"
    api.fail(7, "claim-x", "prostředí: limit", kind="env", retry_after_s=900, reason="limit")
    server.response = (200, "application/json", b'{"ok": true}')
    api.heartbeat({"state": "paused", "reason": "limit"})
    api.heartbeat()

    reqs = server.requests
    assert [(r["method"], r["path"]) for r in reqs] == (
        [("POST", "/reels_api.php?action=progress")] * 2 + [("POST", "/reels_api.php?action=fail")] * 2
        + [("POST", "/reels_api.php?action=heartbeat")] * 2)
    # pole jen když nejsou None: starý server je nedostane, fail bez kind = dnešní chování
    assert [json.loads(r["body"]) if r["body"] else None for r in reqs] == [
        {"job_id": 7}, {"job_id": 7, "stage": "oprava", "note": "varianty 2, 3"},
        {"job_id": 7, "error": "síť", "kind": "infra", "retry_after_s": 900},
        {"job_id": 7, "error": "prostředí: limit", "kind": "env", "retry_after_s": 900, "reason": "limit"},
        {"state": "paused", "reason": "limit"}, None]
    assert all(r["headers"]["X-Claim-Token"] == "claim-x" for r in reqs[:4])
    assert all("X-Claim-Token" not in r["headers"] for r in reqs[4:])
    server.response = (409, "application/json", b'{"error": "claim", "status": "cancelled"}')
    with pytest.raises(ApiError) as ei:
        api.progress(7, "claim-x", "render", "varianta 1")
    assert ei.value.status == 409 and "cancelled" in ei.value.snippet


def test_api_nesleduje_presmerovani(server):
    server.response = (302, "text/html", b"<a>jinam</a>", {"Location": "/jinam.php"})
    with pytest.raises(ApiError) as ei:
        _api(server).heartbeat()
    assert ei.value.status == 302
    assert [r["path"] for r in server.requests] == ["/reels_api.php?action=heartbeat"]


def test_api_jen_https():
    for url in ("http://example.org", "ftp://example.org/reels_api.php", "http://10.0.0.1/reels_api.php", "nic"):
        with pytest.raises(ValueError):
            ReelsApi(url, "t")
    for url in ("http://localhost:8100", "http://127.0.0.1/reels_api.php", "https://example.org"):
        ReelsApi(url, "t")


def test_api_url_bez_php():
    assert ReelsApi("https://example.org", "t").url == "https://example.org/reels_api.php"
    assert ReelsApi("https://example.org/reels_api.php", "t").url == "https://example.org/reels_api.php"


# --- konfigurace a zámek ---

def test_load_env(tmp_path):
    env = tmp_path / "secrets.env"
    env.write_text('# komentář\n\nREELS_API_URL="https://example.org"\nexport REELS_WORKER_TOKEN=\'abc\'\n'
                   "JINE = x=y\n", encoding="utf-8")
    assert worker.load_env(env) == {"REELS_API_URL": "https://example.org", "REELS_WORKER_TOKEN": "abc",
                                    "JINE": "x=y"}


class _RecordingApi(FakeApi):
    instances: list = []

    def __init__(self, base_url, token, timeout_s=60):
        super().__init__(job=None)
        self.base_url, self.token = base_url, token
        _RecordingApi.instances.append(self)


def _main_args(tmp_path):
    env = tmp_path / "secrets.env"
    env.write_text("REELS_API_URL=http://127.0.0.1:9/reels_api.php\nREELS_WORKER_TOKEN=tajny\n", encoding="utf-8")
    (tmp_path / "mcp.json").write_text("{}", encoding="utf-8")
    return ["--env", str(env), "--root", str(tmp_path / "root"), "--mcp-config", str(tmp_path / "mcp.json"),
            "--once"]


def test_main_once(tmp_path, monkeypatch, capsys):
    _RecordingApi.instances = []
    monkeypatch.setattr(worker, "ReelsApi", _RecordingApi)
    assert worker.main(_main_args(tmp_path)) == 0
    api = _RecordingApi.instances[0]
    assert api.token == "tajny" and api.names() == ["heartbeat", "claim"]
    assert "tajny" not in capsys.readouterr().err
    assert "tajny" not in (tmp_path / "root" / "worker.log").read_text(encoding="utf-8")


def test_main_chybi_token(tmp_path, monkeypatch):
    args = _main_args(tmp_path)
    (tmp_path / "secrets.env").write_text("REELS_API_URL=http://127.0.0.1:9\n", encoding="utf-8")
    monkeypatch.setattr(worker, "ReelsApi", _RecordingApi)
    assert worker.main(args) == 1


def test_main_odmitne_http(tmp_path):
    args = _main_args(tmp_path)
    (tmp_path / "secrets.env").write_text("REELS_API_URL=http://example.org\nREELS_WORKER_TOKEN=t\n", encoding="utf-8")
    assert worker.main(args) == 1


def test_druha_instance(tmp_path, monkeypatch, capsys):
    """Zámek drží jiný proces (jako naplánovaná úloha) → main skončí s 0 a nezavolá claim."""
    args = _main_args(tmp_path)
    root = tmp_path / "root"
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, pathlib; from hlidac_reels.generator import worker; "
         "lock = worker.acquire_lock(pathlib.Path(sys.argv[1])); print('ok' if lock else 'busy', flush=True); "
         "sys.stdin.read()", str(root)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "ok"
        _RecordingApi.instances = []
        monkeypatch.setattr(worker, "ReelsApi", _RecordingApi)
        assert worker.main(args) == 0
        assert "worker už běží" in capsys.readouterr().out
        assert _RecordingApi.instances == []
    finally:
        holder.stdin.close()
        holder.wait(timeout=10)
    # po skončení prvního procesu je zámek volný
    assert worker.main(args) == 0
    assert _RecordingApi.instances and _RecordingApi.instances[-1].names() == ["heartbeat", "claim"]


def test_process_preda_vydana_videa(tmp_path):
    seen: dict = {}
    job = dict(JOB, published=[{"format": "cesta", "caption": "Výbory měly 30 dní.", "published_at": "2026-11-21"}])
    worker.process(FakeApi(), job, root=tmp_path, mcp_config=tmp_path / "mcp.json", model="m",
                   generate=fake_generate({1, 2, 3}, seen))
    assert seen["published"] == job["published"]


# --- chyba prostředí, kontrola nástrojů, opakované nahrávání (F2) ---

class _Stop(BaseException):
    """Ukončí nekonečnou smyčku run() v testu (Exception by smyčka spolkla)."""


def _env_generate(**kw):
    raise EnvError("You've hit your limit")


def test_limit_nespali_frontu(tmp_path, monkeypatch):
    api = FakeApi(job=dict(JOB))
    monkeypatch.setattr(worker, "generate", _env_generate)
    answers = iter([False, False, True])

    def probe(model):
        ok = next(answers)
        api.calls.append(("probe", ok))
        return ok

    def sleep(s):
        api.calls.append(("sleep", s))
        if api.names().count("claim") >= 2:
            raise _Stop

    with pytest.raises(_Stop):
        worker.run(api, root=tmp_path, mcp_config=tmp_path / "m.json", model="m", sleep=sleep, probe=probe)
    fails = [c for c in api.calls if c[0] == "fail"]
    assert fails[0][3].startswith("prostředí: ") and "limit" in fails[0][3]
    assert fails[0][4]["kind"] == "env"  # pokus se vrací, úloha čeká ve frontě
    seq = [c for c in api.calls if c[0] in ("claim", "fail", "probe", "sleep")]
    # po prvním fail: odstup 900 → sonda ne → 3600 → sonda ne → 10800 → sonda ano → teprve pak claim
    assert seq[:9] == [("claim",), fails[0], ("sleep", 900), ("probe", False), ("sleep", 3600), ("probe", False),
                       ("sleep", 10800), ("probe", True), ("claim",)]
    # po úspěšné sondě a další chybě prostředí začíná odstup znovu od 900 s
    assert seq[9][0] == "fail" and seq[10] == ("sleep", 900)


def test_limit_sonda_drzi_posledni_krok(tmp_path, monkeypatch):
    api = FakeApi(job=dict(JOB))
    monkeypatch.setattr(worker, "generate", _env_generate)
    sleeps: list = []

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 5:
            raise _Stop

    with pytest.raises(_Stop):
        worker.run(api, root=tmp_path, mcp_config=tmp_path / "m.json", model="m", sleep=sleep,
                   probe=lambda m: False)
    assert sleeps == [900, 3600, 10800, 10800, 10800]
    assert api.names().count("claim") == 1


def test_chybi_nastroj_nebere_ulohy(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "check_tools", lambda mcp_config: ["ffmpeg"])
    api = FakeApi(job=dict(JOB))
    sleeps: list = []
    worker.run(api, root=tmp_path, mcp_config=tmp_path / "m.json", model="m", sleep=sleeps.append, once=True)
    assert "claim" not in api.names()
    assert sleeps == [300]


def test_check_tools(tmp_path, monkeypatch):
    monkeypatch.undo()  # skutečná check_tools, podvržené jen hledání programů a Chromia
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    found = {"claude", "ffmpeg", "ffprobe"}
    monkeypatch.setattr(worker.shutil, "which", lambda name: f"/bin/{name}" if name in found else None)
    monkeypatch.setattr(worker, "_chromium_ok", lambda: True)
    mcp = tmp_path / "mcp.json"
    mcp.write_text("{}", encoding="utf-8")
    assert worker.check_tools(mcp) == []
    found.discard("ffprobe")
    monkeypatch.setattr(worker, "_chromium_ok", lambda: False)
    missing = worker.check_tools(tmp_path / "chybi.json")
    assert any("ffprobe" in m for m in missing) and any("Chromium" in m for m in missing)
    assert any("chybi.json" in m for m in missing) and not any("ffmpeg" == m for m in missing)


def test_nahravani_zkusi_znovu(tmp_path):
    class Api(FakeApi):
        failed = False

        def upload_video(self, job_id, claim_token, n, path, orientation="portrait"):
            if not Api.failed:
                Api.failed = True
                self.calls.append(("upload_error",))
                raise OSError("spojení přerušeno")
            return super().upload_video(job_id, claim_token, n, path, orientation)

    api = Api()
    sleeps: list = []
    assert worker.process(api, dict(JOB), root=tmp_path, mcp_config=tmp_path / "m.json", model="m",
                          generate=fake_generate({1, 2, 3}), sleep=sleeps.append) is True
    assert "fail" not in api.names()
    assert api.names() == ["upload_error"] + ["upload_video"] * 3 + ["result"]
    assert sleeps == [60]


def test_nahravani_vzda_po_trech(tmp_path):
    class Api(FakeApi):
        def result(self, job_id, claim_token, variants, skipped=None):
            self.calls.append(("result",))
            raise ApiError(502, "Bad Gateway")

    api = Api()
    sleeps: list = []
    assert worker.process(api, dict(JOB), root=tmp_path, mcp_config=tmp_path / "m.json", model="m",
                          generate=fake_generate({1, 2, 3}), sleep=sleeps.append) is False
    assert api.names().count("result") == 4 and api.names()[-1] == "fail"
    assert sleeps == [60, 300, 900]


def test_nahravani_409_neopakuje(tmp_path):
    api = FakeApi(conflict_on="upload")
    sleeps: list = []
    assert worker.process(api, dict(JOB), root=tmp_path, mcp_config=tmp_path / "m.json", model="m",
                          generate=fake_generate({1, 2, 3}), sleep=sleeps.append) is False
    assert api.names() == ["upload_video"] and sleeps == []


def test_sirka_opakuje_pri_vypadku(tmp_path):
    api = FakeApi(conflict_on=ApiError(503, "unavailable"))
    sleeps: list = []
    assert worker.process(api, dict(JOB), root=tmp_path, mcp_config=tmp_path / "m.json", model="m",
                          generate=fake_generate({1, 2}, land_ns={1}), sleep=sleeps.append) is True
    # šířka selže i po třech opakováních → jen varování, výška a sada projdou
    assert api.names() == ["upload_landscape"] * 4 + ["upload_video", "upload_video", "result"]
    assert sleeps == [60, 300, 900]


# --- F3: průběh z vlákna (progress), druh chyby ve fail, konec při 409 ---

def _collapse(items: list) -> list:
    """Sloučí opakované hlášení téže fáze (vlákno ji posílá znovu každých PROGRESS_S)."""
    out: list = []
    for x in items:
        if not out or out[-1] != x:
            out.append(x)
    return out


def _wait_stop(kw) -> None:
    """Falešný generate() čeká jako kontrola v generate(), dokud should_stop() nevrátí True (409 na progress)."""
    deadline = time.monotonic() + 5
    while not kw["should_stop"]():
        assert time.monotonic() < deadline, "should_stop() nevrátil True"
        time.sleep(0.001)


class _UploadWaitsApi(FakeApi):
    """Nahrání počká, až vlákno ohlásí fázi upload (pořadí v testu nesmí záviset na rychlosti vlákna)."""

    def upload_video(self, job_id, claim_token, n, path, orientation="portrait"):
        self.wait_progress(lambda s: "upload" in s)
        return super().upload_video(job_id, claim_token, n, path, orientation)


def test_progress_z_vlakna_a_faze(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "PROGRESS_S", 0.01)
    api = _UploadWaitsApi()
    inner = fake_generate({1, 2, 3})

    def gen(**kw):
        api.wait_progress(lambda s: len(s) >= 1)  # první progress hned po startu vlákna
        kw["on_stage"]("model", None)
        api.wait_progress(lambda s: "model" in s)
        kw["on_stage"]("render", "varianta 1")
        api.wait_progress(lambda s: "render" in s)
        return inner(**kw)

    assert _process(api, tmp_path, gen) is True
    assert _collapse(api.stages) == [None, "model", "render", "upload"]
    assert ("render", "varianta 1") in api.progress_calls
    assert api.names() == ["upload_video"] * 3 + ["result"]


def test_progress_opakuje_a_neztrati_zmenu():
    # krátký interval: bez změny fáze se progress opakuje (prodlužuje vydání)
    api = FakeApi()
    with worker.Progress(api, 7, "c" * 32, interval=0.01):
        api.wait_progress(lambda s: len(s) >= 3)
    assert set(api.stages) == {None}
    # dlouhý interval: změny jdou hned po set() a všechny v pořadí, nic se neslučuje
    api = FakeApi()
    with worker.Progress(api, 7, "c" * 32, interval=60) as prog:
        prog.set("model")
        prog.set("kontrola")
        prog.set("oprava", "varianty 2, 3")
        api.wait_progress(lambda s: len(s) >= 4)
    assert api.progress_calls == [(None, None), ("model", None), ("kontrola", None), ("oprava", "varianty 2, 3")]
    assert not prog.cancelled.is_set() and not prog.unsupported


def test_vlakno_progress_skonci_s_ulohou(tmp_path):
    def alive() -> bool:
        return any(t.name == "reels-progress-7" for t in threading.enumerate())

    seen: list[bool] = []
    inner = fake_generate({1, 2, 3})

    def ok(**kw):
        seen.append(alive())  # vlákno běží během generování
        return inner(**kw)

    def boom(**kw):
        seen.append(alive())
        raise RuntimeError("generování spadlo")

    def env(**kw):
        seen.append(alive())
        raise EnvError("You've hit your limit")

    assert _process(FakeApi(), tmp_path, ok) is True
    assert not alive()
    assert _process(FakeApi(), tmp_path, boom) is False
    assert not alive()
    with pytest.raises(EnvError):
        _process(FakeApi(), tmp_path, env)
    assert not alive()
    assert seen == [True, True, True]


def test_progress_409_zastavi_bez_fail(tmp_path, monkeypatch, caplog):
    # Review Focus 1: vydání propadlo a úloha odešla jinému vydání (nebo ji uživatel zrušil)
    monkeypatch.setattr(worker, "PROGRESS_S", 0.01)
    api = FakeApi(progress_error=ApiError(409, '{"error": "Úloha už workeru nepatří", "status": "cancelled"}'))
    inner = fake_generate({1, 2, 3})

    def gen(**kw):
        inner(**kw)  # varianty jsou ve složce
        _wait_stop(kw)
        raise gen_mod.Cancelled()

    with caplog.at_level("WARNING", logger="hlidac_reels.worker"):
        assert _process(api, tmp_path, gen) is False
    assert api.names() == []  # žádné upload_video, result ani fail
    assert api.stages == [None]  # po 409 vlákno další progress neposílá
    assert (tmp_path / "7" / "variant-1" / "card.json").is_file()
    assert "úloha 7: zrušena nebo propadla (409), práce zůstává ve složce" in caplog.text


def test_progress_409_po_generovani_nic_nenahraje(tmp_path, monkeypatch):
    # zrušení přišlo po poslední kontrole v generate(): sada je hotová, ale nenahraje se
    monkeypatch.setattr(worker, "PROGRESS_S", 0.01)
    api = FakeApi(progress_error=ApiError(409, '{"error": "claim"}'))
    inner = fake_generate({1, 2, 3})

    def gen(**kw):
        res = inner(**kw)
        _wait_stop(kw)
        return res

    assert _process(api, tmp_path, gen) is False
    assert api.names() == []
    assert (tmp_path / "7" / "variant-1" / "card.json").is_file()


def test_progress_409_pri_nahravani(tmp_path, monkeypatch):
    created: list = []

    class Prog(worker.Progress):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            created.append(self)

    monkeypatch.setattr(worker, "Progress", Prog)

    class Api(FakeApi):
        def __init__(self):
            super().__init__()
            self.uploaded = threading.Event()

        def progress(self, job_id, claim_token, stage, note=None):
            if stage == "upload":  # uživatel úlohu zrušil, zatímco se nahrává první video
                assert self.uploaded.wait(5)
                raise ApiError(409, '{"error": "claim", "status": "cancelled"}')
            return super().progress(job_id, claim_token, stage, note)

        def upload_video(self, job_id, claim_token, n, path, orientation="portrait"):
            r = super().upload_video(job_id, claim_token, n, path, orientation)
            self.uploaded.set()
            assert created[0].cancelled.wait(5)
            return r

    api = Api()
    assert _process(api, tmp_path, fake_generate({1, 2, 3})) is False
    assert api.names() == ["upload_video"]  # další videa, result ani fail už ne


def test_novy_worker_stary_server(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(worker, "PROGRESS_S", 0.01)
    api = FakeApi(progress_error=ApiError(400, '{"error": "Neznámá akce"}'))
    inner = fake_generate({1, 2, 3})

    def gen(**kw):
        api.wait_progress(lambda s: len(s) >= 1)
        kw["on_stage"]("model", None)
        kw["on_stage"]("render", "varianta 1")
        assert not kw["should_stop"]()
        return inner(**kw)

    with caplog.at_level("INFO", logger="hlidac_reels.worker"):
        assert _process(api, tmp_path, gen) is True
    assert api.stages == [None]  # progress jen jednou, pak už ne
    assert "result" in api.names() and "fail" not in api.names()
    assert caplog.text.count("server progress neumí") == 1


def _gen_timeout(**kw):
    res = fake_generate(set())(**kw)
    res.runs = [{"ok": True, "timed_out": False}, {"ok": False, "timed_out": True}]
    return res


def _gen_unrendered(timed_out: bool):
    """0 platných videí, ale varianta 2 je platná a čeká na render (ten selhal): další vydání ji jen vyrenderuje."""
    def gen(**kw):
        res = fake_generate(set())(**kw)
        res.unrendered = [2]
        if timed_out:  # ani vypršený běh modelu nemění, že stačí znovu render
            res.runs = [{"ok": False, "timed_out": True}]
        return res
    return gen


def _gen_keyerror(**kw):
    raise KeyError("card_v2")


class _Api503(FakeApi):
    def upload_video(self, job_id, claim_token, n, path, orientation="portrait"):
        self.calls.append(("upload_error",))
        raise ApiError(503, "Service Unavailable")


class _ApiOffline(FakeApi):
    def result(self, job_id, claim_token, variants, skipped=None):
        self.calls.append(("result_error",))
        raise OSError("síť nejede")


class _Api403(FakeApi):
    def upload_video(self, job_id, claim_token, n, path, orientation="portrait"):
        raise ApiError(403, "<!DOCTYPE html><title>Just a moment...</title>")


_FAIL_CASES = {
    # případ: (generate, třída API, druh, odklad)
    "0-platnych": (fake_generate(set()), FakeApi, "content", None),
    "0-platnych-timeout": (_gen_timeout, FakeApi, "timeout", None),
    # render selhal u platné varianty: bez druhu = 3 pokusy, další vydání jen renderuje (spec 3.3)
    "render-selhal": (_gen_unrendered(False), FakeApi, None, None),
    "render-selhal-timeout": (_gen_unrendered(True), FakeApi, None, None),
    "nahravani-stale-503": (fake_generate({1, 2, 3}), _Api503, "infra", 900),
    "sit-pri-result": (fake_generate({1, 2, 3}), _ApiOffline, "infra", 900),
    "403-pri-nahravani": (fake_generate({1, 2, 3}), _Api403, "content", None),
    "keyerror-z-generate": (_gen_keyerror, FakeApi, None, None),
}


@pytest.mark.parametrize("case", [*_FAIL_CASES, "enverror"])
def test_fail_druhy(tmp_path, case):
    if case == "enverror":
        api = FakeApi()
        with pytest.raises(EnvError):
            worker.process(api, dict(JOB), root=tmp_path, mcp_config=tmp_path / "m.json", model="m",
                           generate=_env_generate, sleep=lambda s: None)
        kw = [c for c in api.calls if c[0] == "fail"][0][4]
        assert kw["kind"] == "env" and isinstance(kw["retry_after_s"], int) and kw["retry_after_s"] > 0
        assert kw["reason"] in {"limit", "login", "mcp", "nastroje", "env"}
        return
    gen, api_cls, kind, retry = _FAIL_CASES[case]
    api = api_cls()
    assert worker.process(api, dict(JOB), root=tmp_path, mcp_config=tmp_path / "m.json", model="m",
                          generate=gen, sleep=lambda s: None) is False
    fails = [c for c in api.calls if c[0] == "fail"]
    assert len(fails) == 1
    assert (fails[0][4]["kind"], fails[0][4]["retry_after_s"]) == (kind, retry)
    assert worker.RETRY_INFRA_S == 900
