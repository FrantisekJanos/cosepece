"""Testy klienta Graph API (bez sítě: httpx.MockTransport, sleep bez čekání)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from hlidac_reels.meta import Meta, MetaError, MetaTimeout

G = "https://graph.facebook.com/v23.0"
TOKEN = "SYSTOKEN123"
PAGE_TOKEN = "PAGETOKEN456"


def _form(req: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(req.content.decode()).items()}


def _meta(handler, calls: list | None = None) -> Meta:
    def wrapped(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(req)
        return handler(req)
    client = httpx.Client(transport=httpx.MockTransport(wrapped))
    return Meta(TOKEN, "PAGE1", "IG1", client=client, sleep=lambda s: None)


def _no_token_in_url(calls):
    for r in calls:
        assert TOKEN not in str(r.url) and PAGE_TOKEN not in str(r.url)


def test_ig_cela_cesta():
    statuses = iter(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        assert req.headers["Authorization"] == f"OAuth {TOKEN}"
        if req.method == "POST" and url == f"{G}/IG1/media":
            f = _form(req)
            assert f == {
                "media_type": "REELS",
                "video_url": "https://cosepece.cz/v.mp4",
                "caption": "Popisek",
                "share_to_feed": "true",
            }
            return httpx.Response(200, json={"id": "C1"})
        if req.method == "GET" and url.startswith(f"{G}/C1?"):
            assert req.url.params["fields"] == "status_code"
            return httpx.Response(200, json={"status_code": next(statuses), "id": "C1"})
        if req.method == "POST" and url == f"{G}/IG1/media_publish":
            assert _form(req) == {"creation_id": "C1"}
            return httpx.Response(200, json={"id": "M1"})
        if req.method == "GET" and url.startswith(f"{G}/M1?"):
            assert req.url.params["fields"] == "permalink"
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/abc/", "id": "M1"})
        raise AssertionError(f"neočekávaný požadavek {req.method} {url}")

    m = _meta(handler, calls)
    cid = m.ig_create("https://cosepece.cz/v.mp4", "Popisek")
    assert cid == "C1"
    m.ig_wait(cid, timeout=600, interval=10)
    assert m.ig_publish(cid) == ("M1", "https://www.instagram.com/reel/abc/")
    assert len([c for c in calls if "C1?" in str(c.url)]) == 3
    _no_token_in_url(calls)


def test_ig_wait_error_vyhodi():
    def handler(req):
        return httpx.Response(200, json={"status_code": "ERROR", "id": "C1"})

    with pytest.raises(MetaError) as e:
        _meta(handler).ig_wait("C1")
    assert not isinstance(e.value, MetaTimeout)
    assert "ERROR" in str(e.value)


def test_ig_wait_expired_vyhodi():
    def handler(req):
        return httpx.Response(200, json={"status_code": "EXPIRED", "id": "C1"})

    with pytest.raises(MetaError):
        _meta(handler).ig_wait("C1")


def test_ig_wait_timeout_vyhodi_MetaTimeout():
    calls: list = []
    slept: list = []

    def handler(req):
        return httpx.Response(200, json={"status_code": "IN_PROGRESS", "id": "C1"})

    client = httpx.Client(transport=httpx.MockTransport(lambda r: (calls.append(r), handler(r))[1]))
    m = Meta(TOKEN, "PAGE1", "IG1", client=client, sleep=slept.append)
    with pytest.raises(MetaTimeout):
        m.ig_wait("C1", timeout=30, interval=10)
    # kontrola v 0, 10, 20 a 30 s, pak vypršení
    assert len(calls) == 4
    assert sum(slept) == 30


def test_fb_upload_tri_faze_a_hlavicky(tmp_path):
    video = tmp_path / "r.mp4"
    video.write_bytes(b"\x00\x01VIDEO" * 10)
    size = video.stat().st_size
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if req.method == "GET" and url.startswith(f"{G}/PAGE1?"):
            assert req.headers["Authorization"] == f"OAuth {TOKEN}"
            assert req.url.params["fields"] == "access_token"
            return httpx.Response(200, json={"access_token": PAGE_TOKEN, "id": "PAGE1"})
        if req.method == "POST" and url == f"{G}/PAGE1/video_reels":
            assert req.headers["Authorization"] == f"OAuth {PAGE_TOKEN}"
            f = _form(req)
            if f["upload_phase"] == "start":
                return httpx.Response(200, json={
                    "video_id": "V1",
                    "upload_url": "https://rupload.facebook.com/video-upload/v23.0/V1",
                })
            assert f == {
                "upload_phase": "finish",
                "video_id": "V1",
                "video_state": "PUBLISHED",
                "description": "Popis FB",
            }
            return httpx.Response(200, json={"success": True})
        if req.method == "POST" and url == "https://rupload.facebook.com/video-upload/v23.0/V1":
            assert req.headers["Authorization"] == f"OAuth {PAGE_TOKEN}"
            assert req.headers["offset"] == "0"
            assert req.headers["file_size"] == str(size)
            assert req.content == video.read_bytes()
            return httpx.Response(200, json={"success": True})
        if req.method == "GET" and url.startswith(f"{G}/V1?"):
            assert req.headers["Authorization"] == f"OAuth {PAGE_TOKEN}"
            assert req.url.params["fields"] == "permalink_url"
            return httpx.Response(200, json={"permalink_url": "/reel/V1/", "id": "V1"})
        raise AssertionError(f"neočekávaný požadavek {req.method} {url}")

    m = _meta(handler, calls)
    assert m.fb_upload_reel(video, "Popis FB") == ("V1", "https://www.facebook.com/reel/V1/")
    phases = [_form(c).get("upload_phase") for c in calls if str(c.url).endswith("/video_reels")]
    assert phases == ["start", "finish"]
    # page token se načte jen jednou
    assert len([c for c in calls if "/PAGE1?" in str(c.url)]) == 1
    _no_token_in_url(calls)


def test_fb_upload_neuspesny_upload_vyhodi(tmp_path):
    video = tmp_path / "r.mp4"
    video.write_bytes(b"x")

    def handler(req):
        url = str(req.url)
        if "/PAGE1?" in url:
            return httpx.Response(200, json={"access_token": PAGE_TOKEN})
        if url.endswith("/video_reels"):
            return httpx.Response(200, json={"video_id": "V1"})
        return httpx.Response(200, json={"success": False})

    with pytest.raises(MetaError):
        _meta(handler).fb_upload_reel(video, "x")


def test_chyba_neobsahuje_token():
    def handler(req):
        return httpx.Response(400, json={"error": {
            "message": f"Invalid request https://graph.facebook.com/v23.0/IG1/media?access_token=SECRET&x=1"
                       f" and {TOKEN}",
            "type": "OAuthException",
            "code": 100,
        }})

    with pytest.raises(MetaError) as e:
        _meta(handler).ig_create("https://cosepece.cz/v.mp4", "x")
    err = e.value
    assert "SECRET" not in str(err) and "SECRET" not in err.message
    assert TOKEN not in str(err)
    assert "access_token=***" in err.message
    assert err.status == 400 and err.code == 100


def test_chyba_site_neobsahuje_token():
    def handler(req):
        raise httpx.ConnectError(f"spojení selhalo {req.url}?access_token=SECRET", request=req)

    with pytest.raises(MetaError) as e:
        _meta(handler).ig_create("https://cosepece.cz/v.mp4", "x")
    assert "SECRET" not in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__


def test_chyba_ne_json_odpovedi():
    def handler(req):
        return httpx.Response(502, text="Bad gateway access_token=SECRET")

    with pytest.raises(MetaError) as e:
        _meta(handler).ig_create("u", "x")
    assert "SECRET" not in str(e.value)
    assert e.value.status == 502


def test_find_recent_shoda_a_cas():
    prefix = "A" * 80 + " konec se liší"
    since = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.startswith(f"{G}/IG1/media?"):
            assert req.url.params["fields"] == "id,caption,timestamp,permalink"
            assert req.url.params["limit"] == "10"
            return httpx.Response(200, json={"data": [
                # shoda textu, ale starší
                {"id": "OLD", "caption": "A" * 80 + " jiné", "timestamp": "2026-11-20T09:59:59+0000",
                 "permalink": "https://www.instagram.com/reel/old/"},
                # jiný text
                {"id": "X", "caption": "B" * 90, "timestamp": "2026-11-20T11:00:00+0000",
                 "permalink": "https://www.instagram.com/reel/x/"},
                # bez popisku
                {"id": "Y", "timestamp": "2026-11-20T11:00:00+0000"},
                {"id": "NEW", "caption": "A" * 80 + " jiné", "timestamp": "2026-11-20T10:00:00+0000",
                 "permalink": "https://www.instagram.com/reel/new/"},
            ]})
        if url.startswith(f"{G}/PAGE1?"):
            return httpx.Response(200, json={"access_token": PAGE_TOKEN})
        if url.startswith(f"{G}/PAGE1/video_reels?"):
            assert req.headers["Authorization"] == f"OAuth {PAGE_TOKEN}"
            assert req.url.params["fields"] == "id,description,created_time,permalink_url"
            assert req.url.params["limit"] == "10"
            return httpx.Response(200, json={"data": [
                {"id": "F1", "description": "A" * 80, "created_time": "2026-11-20T10:05:00+0000",
                 "permalink_url": "/reel/F1/"},
            ]})
        raise AssertionError(url)

    m = _meta(handler)
    assert m.ig_find_recent(prefix, since) == ("NEW", "https://www.instagram.com/reel/new/")
    assert m.ig_find_recent("C" * 10, since) is None
    assert m.fb_find_recent(prefix, since) == ("F1", "https://www.facebook.com/reel/F1/")
    # naivní since = UTC; pozdější čas nic nenajde
    assert m.fb_find_recent(prefix, datetime(2026, 11, 20, 10, 6)) is None


def test_ig_publish_ok_permalink_selze_vrati_id():
    def handler(req):
        url = str(req.url)
        if url.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "M1"})
        return httpx.Response(500, json={"error": {"message": "chyba", "code": 1}})

    assert _meta(handler).ig_publish("C1") == ("M1", "")


def test_ig_publish_ok_permalink_sit_selze_vrati_id():
    def handler(req):
        if str(req.url).endswith("/media_publish"):
            return httpx.Response(200, json={"id": "M1"})
        raise httpx.ConnectError("spadlo", request=req)

    assert _meta(handler).ig_publish("C1") == ("M1", "")


def _fb_handler(permalink_response):
    def handler(req):
        url = str(req.url)
        if url.startswith(f"{G}/PAGE1?"):
            return httpx.Response(200, json={"access_token": PAGE_TOKEN})
        if url.endswith("/video_reels"):
            if _form(req)["upload_phase"] == "start":
                return httpx.Response(200, json={"video_id": "V1"})
            return httpx.Response(200, json={"success": True})
        if url.startswith("https://rupload.facebook.com/"):
            return httpx.Response(200, json={"success": True})
        return permalink_response(req)
    return handler


@pytest.mark.parametrize("resp", [
    lambda r: httpx.Response(500, json={"error": {"message": "chyba", "code": 1}}),
    lambda r: httpx.Response(200, json={"id": "V1"}),
])
def test_fb_finish_ok_permalink_selze_vrati_fallback(tmp_path, resp):
    video = tmp_path / "r.mp4"
    video.write_bytes(b"x")
    assert _meta(_fb_handler(resp)).fb_upload_reel(video, "x") == (
        "V1", "https://www.facebook.com/reel/V1")
