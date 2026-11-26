"""Worker na PC: kopie videí do složky Pro X (vlákno x_sync) a klient akcí x_pending / x_ready."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pytest

from hlidac_reels.generator import xsync
from hlidac_reels.generator.api import ApiError
from test_gen_worker import _api, server  # noqa: F401 – fixture


class FakeXApi:
    def __init__(self, wanted=(), remove=(), error=None, fail_ready=None):
        self.wanted = list(wanted)
        self.remove = list(remove)
        self.error = error
        self.fail_ready = fail_ready
        self.pending_calls = 0
        self.reports: list[tuple[int, str]] = []

    def x_pending(self):
        self.pending_calls += 1
        if self.error is not None:
            raise self.error
        return {"wanted": self.wanted, "remove": self.remove}

    def x_ready(self, reel_id, result):
        if self.fail_ready is not None:
            raise self.fail_ready
        self.reports.append((reel_id, result))


def _variant(root: Path, set_id: int, n: int, *names: str) -> Path:
    d = root / str(set_id) / f"variant-{n}"
    d.mkdir(parents=True)
    for i, name in enumerate(names):
        p = d / name
        p.write_bytes(name.encode())
        os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))
    return d


def test_find_video_prednost_sirky(tmp_path):
    _variant(tmp_path, 7, 2, "reel-221-pn.mp4", "reel-221-pn-sirka.mp4")
    _variant(tmp_path, 7, 1, "reel-221-v2.mp4")
    assert xsync.find_video(tmp_path, 7, 2).name == "reel-221-pn-sirka.mp4"
    assert xsync.find_video(tmp_path, 7, 1).name == "reel-221-v2.mp4"
    assert xsync.find_video(tmp_path, 7, 3) is None
    assert xsync.find_video(tmp_path, None, 1) is None
    assert xsync.find_video(tmp_path, 7, None) is None


def test_sync_kopie_ready_a_missing(tmp_path):
    root, xdir = tmp_path / "gen", tmp_path / "Pro X"
    _variant(root, 7, 2, "reel-221-pn.mp4", "reel-221-pn-sirka.mp4")
    api = FakeXApi(wanted=[{"id": 12, "set_id": 7, "variant": 2, "ct": 221},
                           {"id": 13, "set_id": None, "variant": None, "ct": None},
                           {"id": 14, "set_id": 99, "variant": 1, "ct": 5}])
    xsync.sync_once(api, root, xdir)
    assert (xdir / "0012-tisk221.mp4").read_bytes() == b"reel-221-pn-sirka.mp4"
    assert api.reports == [(12, "ready"), (13, "missing"), (14, "missing")]
    assert sorted(p.name for p in xdir.iterdir()) == ["0012-tisk221.mp4"]  # žádné .part


def test_sync_smaze_vyrizene(tmp_path):
    xdir = tmp_path / "Pro X"
    xdir.mkdir()
    for name in ("0012-tisk221.mp4", "0013.mp4", "00120-tisk1.mp4", "0014-tisk5.mp4"):
        (xdir / name).write_bytes(b"x")
    xsync.sync_once(FakeXApi(remove=[12, 13]), tmp_path / "gen", xdir)
    assert sorted(p.name for p in xdir.iterdir()) == ["00120-tisk1.mp4", "0014-tisk5.mp4"]


def test_sync_chyba_kopie_nezastavi_ostatni(tmp_path, monkeypatch, caplog):
    root, xdir = tmp_path / "gen", tmp_path / "Pro X"
    _variant(root, 7, 1, "reel-1-v2.mp4")
    _variant(root, 8, 1, "reel-2-v2.mp4")
    real = xsync.shutil.copyfile

    def copy(src, dst):
        if "reel-1" in str(src):
            raise OSError("disk je plný")
        return real(src, dst)

    monkeypatch.setattr(xsync.shutil, "copyfile", copy)
    api = FakeXApi(wanted=[{"id": 1, "set_id": 7, "variant": 1, "ct": 1}, {"id": 2, "set_id": 8, "variant": 1, "ct": 2}])
    with caplog.at_level(logging.WARNING):
        xsync.sync_once(api, root, xdir)
    assert api.reports == [(2, "ready")]  # položka 1 zůstane wanted, příště další pokus
    assert "disk je plný" in caplog.text
    assert sorted(p.name for p in xdir.iterdir()) == ["0002-tisk2.mp4"]


def test_tick_stary_server(tmp_path, caplog):
    xs = xsync.XSync(FakeXApi(error=ApiError(400, '{"error": "Neznámá akce"}')), tmp_path, tmp_path / "x")
    with caplog.at_level(logging.INFO):
        assert xs.tick() == xsync.UNSUPPORTED_RETRY_S
        assert xs.tick() == xsync.UNSUPPORTED_RETRY_S
    assert caplog.text.count("neumí") == 1
    xs.api = FakeXApi(error=ApiError(404, "nenalezeno"))
    assert xs.tick() == xsync.UNSUPPORTED_RETRY_S


def test_tick_chyby_jen_loguje(tmp_path, caplog):
    xs = xsync.XSync(FakeXApi(error=OSError("síť")), tmp_path, tmp_path / "x")
    with caplog.at_level(logging.WARNING):
        assert xs.tick() == xsync.INTERVAL_S
        assert xs.tick() == xsync.INTERVAL_S  # stejná chyba se neopakuje v logu
        xs.api = FakeXApi(error=ApiError(500, "chyba"))
        assert xs.tick() == xsync.INTERVAL_S
        xs.api = FakeXApi(error=RuntimeError("cokoli"))
        assert xs.tick() == xsync.INTERVAL_S
    assert caplog.text.count("síť") == 1 and "cokoli" in caplog.text
    xs.api = FakeXApi()
    assert xs.tick() == xsync.INTERVAL_S


def test_vlakno_bezi_a_zastavi(tmp_path):
    api = FakeXApi()
    xs = xsync.XSync(api, tmp_path, tmp_path / "x", interval=0.01)
    xs.start()
    deadline = time.monotonic() + 5
    while api.pending_calls < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    xs.stop()
    assert api.pending_calls >= 3
    assert not xs._thread.is_alive()
    assert xs._thread.daemon


def test_default_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("REELS_X_DIR", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert xsync.default_dir({}) == tmp_path / "Videos" / "Pro X"
    assert xsync.default_dir({"REELS_X_DIR": str(tmp_path / "jinde")}) == tmp_path / "jinde"
    monkeypatch.setenv("REELS_X_DIR", str(tmp_path / "proc"))
    assert xsync.default_dir({}) == tmp_path / "proc"


def test_api_x_pozadavky(server):  # noqa: F811
    api = _api(server)
    server.response = (200, "application/json", json.dumps(
        {"wanted": [{"id": 12, "set_id": 7, "variant": 2, "ct": 221}, "x"], "remove": [3, "4", True]}).encode())
    assert api.x_pending() == {"wanted": [{"id": 12, "set_id": 7, "variant": 2, "ct": 221}], "remove": [3]}
    server.response = (200, "application/json", b'{"ok": true, "updated": true}')
    api.x_ready(12, "ready")
    reqs = server.requests
    assert [(r["method"], r["path"]) for r in reqs] == [("GET", "/reels_api.php?action=x_pending"),
                                                        ("POST", "/reels_api.php?action=x_ready")]
    assert json.loads(reqs[1]["body"]) == {"id": 12, "result": "ready"}
    server.response = (400, "application/json", '{"error": "Neznámá akce"}'.encode())
    with pytest.raises(ApiError) as ei:
        api.x_pending()
    assert ei.value.status == 400


def test_worker_main_spusti_xsync(monkeypatch, tmp_path):
    """main() pustí vlákno x_sync se složkou z REELS_X_DIR a po skončení smyčky ho zastaví; --once ne."""
    from hlidac_reels.generator import worker
    env = tmp_path / "w.env"
    env.write_text(f"REELS_API_URL=https://example.invalid\nREELS_WORKER_TOKEN=t\nREELS_X_DIR={tmp_path / 'px'}\n",
                   encoding="utf-8")
    mcp = tmp_path / "mcp.json"
    mcp.write_text("{}", encoding="utf-8")
    seen = {}

    class FakeXSync:
        def __init__(self, api, root, x_dir, interval=xsync.INTERVAL_S):
            seen["x_dir"] = x_dir

        def start(self):
            seen["started"] = True

        def stop(self):
            seen["stopped"] = True

    monkeypatch.setattr(worker.xsync, "XSync", FakeXSync)
    monkeypatch.setattr(worker, "run", lambda api, **kw: seen.setdefault("run", True))
    argv = ["--env", str(env), "--root", str(tmp_path / "root"), "--mcp-config", str(mcp)]
    assert worker.main(argv) == 0
    assert seen == {"x_dir": tmp_path / "px", "started": True, "run": True, "stopped": True}
    seen.clear()
    assert worker.main(argv + ["--once"]) == 0
    assert seen == {"run": True}
