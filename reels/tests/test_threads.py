"""Testy klienta Threads API (bez sítě: httpx.MockTransport, sleep bez čekání)."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from hlidac_reels.threads import Threads, ThreadsError, ThreadsTimeout

G = "https://graph.threads.net/v1.0"
TOKEN = "THTOKEN123"


def _form(req: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(req.content.decode()).items()}


def _threads(handler, calls: list | None = None, token_provider=lambda: TOKEN) -> Threads:
    def wrapped(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(req)
        return handler(req)
    client = httpx.Client(transport=httpx.MockTransport(wrapped))
    return Threads("U1", token_provider, client=client, sleep=lambda s: None)


def test_threads_cela_cesta():
    statuses = iter(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        assert req.headers["Authorization"] == f"Bearer {TOKEN}"
        if req.method == "POST" and url == f"{G}/U1/threads":
            assert _form(req) == {
                "media_type": "VIDEO",
                "video_url": "https://cosepece.cz/v.mp4",
                "text": "Text",
            }
            return httpx.Response(200, json={"id": "C1"})
        if req.method == "GET" and url.startswith(f"{G}/C1?"):
            assert req.url.params["fields"] == "status,error_message"
            return httpx.Response(200, json={"status": next(statuses), "id": "C1"})
        if req.method == "POST" and url == f"{G}/U1/threads_publish":
            assert _form(req) == {"creation_id": "C1"}
            return httpx.Response(200, json={"id": "T1"})
        if req.method == "GET" and url.startswith(f"{G}/T1?"):
            assert req.url.params["fields"] == "permalink"
            return httpx.Response(200, json={"permalink": "https://www.threads.net/@cosepece/post/abc", "id": "T1"})
        raise AssertionError(f"neočekávaný požadavek {req.method} {url}")

    th = _threads(handler, calls)
    cid = th.create_video("https://cosepece.cz/v.mp4", "Text")
    assert cid == "C1"
    th.wait(cid, timeout=600, interval=10)
    assert th.publish(cid) == ("T1", "https://www.threads.net/@cosepece/post/abc")
    assert len([c for c in calls if "C1?" in str(c.url)]) == 3
    for c in calls:
        assert TOKEN not in str(c.url) and TOKEN not in c.content.decode()


def test_threads_token_z_provideru_pri_kazdem_volani():
    tokens = iter(["T-A", "T-B"])
    seen = []

    def handler(req):
        seen.append(req.headers["Authorization"])
        return httpx.Response(200, json={"id": "C1"})

    th = _threads(handler, token_provider=lambda: next(tokens))
    th.create_video("u", "x")
    th.create_video("u", "x")
    assert seen == ["Bearer T-A", "Bearer T-B"]


def test_threads_wait_error_timeout_published():
    def status(value, **extra):
        return lambda req: httpx.Response(200, json={"status": value, "id": "C1", **extra})

    with pytest.raises(ThreadsError) as e:
        _threads(status("ERROR", error_message="FAILED_DOWNLOADING_VIDEO")).wait("C1")
    assert not isinstance(e.value, ThreadsTimeout)
    assert "ERROR" in str(e.value) and "FAILED_DOWNLOADING_VIDEO" in str(e.value)

    with pytest.raises(ThreadsError) as e:
        _threads(status("EXPIRED")).wait("C1")
    assert not isinstance(e.value, ThreadsTimeout) and "EXPIRED" in str(e.value)

    with pytest.raises(ThreadsError) as e:
        _threads(status("PUBLISHED")).wait("C1")
    assert not isinstance(e.value, ThreadsTimeout)
    assert e.value.status is None and "PUBLISHED" in str(e.value)

    slept: list[float] = []
    calls: list[httpx.Request] = []
    th = _threads(status("IN_PROGRESS"), calls)
    th._sleep = slept.append
    with pytest.raises(ThreadsTimeout):
        th.wait("C1", timeout=30, interval=10)
    assert slept == [10, 10, 10]
    assert len(calls) == 4


def test_threads_publish_odkaz_selze_vrati_id():
    def handler(req):
        if str(req.url).endswith("/threads_publish"):
            return httpx.Response(200, json={"id": "T1"})
        return httpx.Response(500, json={"error": {"message": "chyba", "code": 1}})

    assert _threads(handler).publish("C1") == ("T1", "")

    def handler_net(req):
        if str(req.url).endswith("/threads_publish"):
            return httpx.Response(200, json={"id": "T1"})
        raise httpx.ConnectError("spadlo", request=req)

    assert _threads(handler_net).publish("C1") == ("T1", "")


def test_threads_chyba_bez_tokenu():
    def handler(req):
        return httpx.Response(400, json={"error": {
            "message": f"Invalid https://graph.threads.net/v1.0/U1?access_token=SECRET&x=1 and {TOKEN}",
            "type": "OAuthException",
            "code": 190,
        }})

    with pytest.raises(ThreadsError) as e:
        _threads(handler).create_video("u", "x")
    err = e.value
    assert "SECRET" not in str(err) and TOKEN not in str(err)
    assert "access_token=***" in err.message
    assert err.status == 400 and err.code == 190

    def handler_net(req):
        raise httpx.ConnectError(f"spojení selhalo {req.url} {TOKEN}", request=req)

    with pytest.raises(ThreadsError) as e:
        _threads(handler_net).create_video("u", "x")
    assert TOKEN not in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__

    def handler_text(req):
        return httpx.Response(502, text=f"Bad gateway access_token=SECRET {TOKEN}")

    with pytest.raises(ThreadsError) as e:
        _threads(handler_text).create_video("u", "x")
    assert "SECRET" not in str(e.value) and TOKEN not in str(e.value)
    assert e.value.status == 502


def test_threads_find_recent():
    prefix = "A" * 80 + " konec se liší"
    since = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        assert url.startswith(f"{G}/U1/threads?")
        assert req.url.params["fields"] == "id,text,timestamp,permalink"
        assert req.url.params["limit"] == "10"
        return httpx.Response(200, json={"data": [
            {"id": "OLD", "text": "A" * 80 + " jiné", "timestamp": "2026-11-20T09:59:59+0000",
             "permalink": "https://www.threads.net/p/old"},
            {"id": "X", "text": "B" * 90, "timestamp": "2026-11-20T11:00:00+0000",
             "permalink": "https://www.threads.net/p/x"},
            {"id": "Y", "timestamp": "2026-11-20T11:00:00+0000"},
            {"id": "NEW", "text": "A" * 80 + " jiné", "timestamp": "2026-11-20T10:00:00+0000",
             "permalink": "https://www.threads.net/p/new"},
        ]})

    th = _threads(handler)
    assert th.find_recent(prefix, since) == ("NEW", "https://www.threads.net/p/new")
    assert th.find_recent("C" * 10, since) is None
    # naivní since = UTC; pozdější čas nic nenajde
    assert th.find_recent(prefix, datetime(2026, 11, 20, 10, 1)) is None


def test_threads_refresh():
    calls: list[httpx.Request] = []

    def handler(req):
        assert req.method == "GET"
        assert str(req.url).startswith("https://graph.threads.net/refresh_access_token?")
        assert req.url.params["grant_type"] == "th_refresh_token"
        assert req.url.params["access_token"] == "OLDTOKEN"
        return httpx.Response(200, json={"access_token": "NEWTOKEN", "token_type": "bearer",
                                         "expires_in": 5183944})

    assert _threads(handler, calls).refresh_token("OLDTOKEN") == ("NEWTOKEN", 5183944)
    assert len(calls) == 1

    # chyba neobsahuje starý token ani URL s ním
    def handler_err(req):
        return httpx.Response(400, json={"error": {"message": f"bad token OLDTOKEN {req.url}", "code": 190}})

    with pytest.raises(ThreadsError) as e:
        _threads(handler_err).refresh_token("OLDTOKEN")
    assert "OLDTOKEN" not in str(e.value) and e.value.code == 190

    def handler_net(req):
        raise httpx.ConnectError(f"spojení selhalo {req.url}", request=req)

    with pytest.raises(ThreadsError) as e:
        _threads(handler_net).refresh_token("OLDTOKEN")
    assert "OLDTOKEN" not in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__

    # odpověď bez tokenu je chyba a nový token se v chybě neobjeví
    with pytest.raises(ThreadsError):
        _threads(lambda r: httpx.Response(200, json={"expires_in": 1})).refresh_token("OLDTOKEN")
    with pytest.raises(ThreadsError) as e:
        _threads(lambda r: httpx.Response(200, json={"access_token": "NEWTOKEN"})).refresh_token("OLDTOKEN")
    assert "NEWTOKEN" not in str(e.value)
