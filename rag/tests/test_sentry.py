"""Sentry v rag: filtr citlivých údajů shodný s PHP (app/Sentry.php) a vypnutí bez DSN."""
from mcp.server.mcpserver.exceptions import ToolError

import pytest

from hlidac_rag import sentry


@pytest.fixture(autouse=True)
def _reset_sentry():
    sentry.reset()
    yield
    sentry.reset()


def test_scrub_klice():
    vstup = {"X-Hlidac-Token": "a", "db_pass": "b", "api_key": "c", "Cookie": "d", "q": "x"}
    assert sentry.scrub(vstup) == {"X-Hlidac-Token": "[Filtered]", "db_pass": "[Filtered]",
                                   "api_key": "[Filtered]", "Cookie": "[Filtered]", "q": "x"}


def test_scrub_text():
    assert sentry.scrub("psal jan.novak@example.cz z 192.168.1.20 a 2001:db8::1") == "psal [email] z [ip] a [ip]"
    assert "Bearer ***" in sentry.scrub("Bearer abc.def")
    s = sentry.scrub("url ?token=tajne&x=1")
    assert "token=***" in s and "tajne" not in s


def test_scrub_cesty():
    assert sentry.scrub("chyba v /app/hlidac_rag/run.py") == "chyba v hlidac_rag/run.py"
    assert sentry.scrub("jiné /srv/app/x.py") == "jiné /srv/app/x.py"


def test_scrub_necha_casy_a_paragrafy():
    for s in ("sync 12:30:45", "§ 4.36", "verze 2.3.0"):
        assert sentry.scrub(s) == s


def test_before_send_filtruje_udalost():
    event = {
        "message": "psal jan.novak@example.cz",
        "request": {"headers": {"Authorization": "Bearer x"}, "url": "/mcp"},
        "extra": {"token": "t", "ip": "192.168.1.20"},
        "exception": {"values": [{"type": "E", "value": "z 192.168.1.20"}]},
        "breadcrumbs": {"values": [{"message": "a@b.cz", "data": {"api_key": "k"}}]},
    }
    out = sentry.before_send(event, {})
    assert out["message"] == "psal [email]"
    assert out["request"]["headers"]["Authorization"] == "[Filtered]"
    assert out["extra"] == {"token": "[Filtered]", "ip": "[ip]"}
    assert out["exception"]["values"][0]["value"] == "z [ip]"
    bc = out["breadcrumbs"]["values"][0]
    assert bc["message"] == "[email]" and bc["data"]["api_key"] == "[Filtered]"


def test_before_send_zahodi_tool_error():
    hint = {"exc_info": (ToolError, ToolError("nenalezeno"), None)}
    assert sentry.before_send({"message": "x"}, hint) is None


def test_init_bez_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert sentry.init("mcp") is False


def test_before_send_dedup_stejneho_otisku():
    udalost = lambda: {"logentry": {"message": "chyba %s", "params": ["a"]},
                       "exception": {"values": [{"type": "ValueError", "value": "x"}]}}
    assert sentry.before_send(udalost(), {}) is not None
    assert sentry.before_send(udalost(), {}) is None


def test_before_send_strop_20_udalosti():
    prosly = [sentry.before_send({"message": f"jiná chyba {i}"}, {}) for i in range(25)]
    assert sum(1 for e in prosly if e is not None) == 20


def test_before_send_okno_se_po_hodine_nuluje(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(sentry.time, "monotonic", lambda: t[0])
    assert sentry.before_send({"message": "opakovaná"}, {}) is not None
    assert sentry.before_send({"message": "opakovaná"}, {}) is None
    t[0] += sentry.WINDOW_SECONDS + 1
    assert sentry.before_send({"message": "opakovaná"}, {}) is not None


def test_tool_error_se_nepocita_do_stropu():
    hint = {"exc_info": (ToolError, ToolError("x"), None)}
    for _ in range(30):
        assert sentry.before_send({"message": "x"}, hint) is None
    assert sentry.before_send({"message": "skutečná chyba"}, {}) is not None
