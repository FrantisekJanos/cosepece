"""Worker na PC: zpracování úlohy, smyčka, HTTP klient reels_api.php a zámek proti druhé instanci."""
from __future__ import annotations

import http.client
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from hlidac_reels.generator import worker
from hlidac_reels.generator.api import ApiError, ReelsApi
from hlidac_reels.generator.generate import GenResult, Variant

JOB = {"job_id": 7, "claim_token": "c" * 32, "ct": 221, "source": "manual", "format_hint": None,
       "reason_keys": ["retro"], "has_amendments": True, "card_v2": {"ct": 221}}


class FakeApi:
    def __init__(self, job=None, conflict_on: str | None = None):
        self.job = job
        self.conflict_on = conflict_on
        self.calls: list[tuple] = []

    def heartbeat(self):
        self.calls.append(("heartbeat",))

    def claim(self):
        self.calls.append(("claim",))
        return self.job

    def upload_video(self, job_id, claim_token, n, path):
        assert Path(path).is_file()
        self.calls.append(("upload_video", job_id, claim_token, n, Path(path).name))
        if self.conflict_on == "upload":
            raise ApiError(409, '{"error": "claim"}')

    def result(self, job_id, claim_token, variants):
        self.calls.append(("result", job_id, claim_token, variants))
        if self.conflict_on == "result":
            raise ApiError(409, '{"error": "claim"}')

    def fail(self, job_id, claim_token, error):
        self.calls.append(("fail", job_id, claim_token, error))
        return "queued"

    def names(self):
        return [c[0] for c in self.calls]


def fake_generate(ok_ns: set[int], seen: dict | None = None):
    """Falešný generate(): zapíše karty a videa jako část 1, varianty z ok_ns jsou platné."""
    def gen(*, ct, card_v2, reason_keys, has_amendments, out, mcp_config, model, hint, render, timeout_s):
        if seen is not None:
            seen.update(ct=ct, out=Path(out), model=model, hint=hint, mcp_config=mcp_config,
                        reason_keys=reason_keys, has_amendments=has_amendments)
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
            else:
                v.errors = [f"chyba v {out / 'x'}"]
        return GenResult(ct=ct, formats=["cesta", "pn"], variants=variants, ok=len(ok_ns) >= 2)
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


def test_process_dve_platne(tmp_path):
    api = FakeApi()
    assert _process(api, tmp_path, fake_generate({1, 3})) is True
    assert api.names() == ["upload_video", "upload_video", "result"]
    assert [v["n"] for v in api.calls[-1][3]] == [1, 3]


def test_process_malo_variant(tmp_path):
    api = FakeApi()
    assert _process(api, tmp_path, fake_generate({2})) is False
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
    assert _process(api, tmp_path, _oversize(fake_generate({1, 2}), 2)) is False
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
    monkeypatch.setattr(worker, "generate", fake_generate({1}))
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
    api.result(7, "claim-x", [{"n": 1}])
    server.response = (200, "application/json", b'{"status": "failed"}')
    assert api.fail(7, "claim-x", "chyba") == "failed"
    server.response = (200, "application/json", b'{"ok": true}')
    api.heartbeat()

    reqs = server.requests
    assert [(r["method"], r["path"]) for r in reqs] == [
        ("GET", "/reels_api.php?action=claim"), ("GET", "/reels_api.php?action=claim"),
        ("PUT", "/reels_api.php?action=video&job_id=7&n=2"), ("POST", "/reels_api.php?action=result"),
        ("POST", "/reels_api.php?action=fail"), ("POST", "/reels_api.php?action=heartbeat")]
    assert all(r["headers"]["X-Hlidac-Token"] == "tok-123" for r in reqs)
    put = reqs[2]
    assert put["headers"]["X-Claim-Token"] == "claim-x"
    assert put["headers"]["Content-Length"] == "12" and put["body"] == video.read_bytes()
    assert json.loads(reqs[3]["body"]) == {"job_id": 7, "variants": [{"n": 1}]}
    assert reqs[3]["headers"]["X-Claim-Token"] == "claim-x"
    assert json.loads(reqs[4]["body"]) == {"job_id": 7, "error": "chyba"}
    assert "X-Claim-Token" not in reqs[5]["headers"]


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
