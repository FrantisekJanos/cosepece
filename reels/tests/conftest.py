"""Společné fixture a přeskakování testů bez Chromia, ffmpeg nebo PostgreSQL."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _has_chromium() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    render_ok = shutil.which("ffmpeg") is not None and _has_chromium()
    pg_ok = bool(os.environ.get("DATABASE_URL"))
    for item in items:
        if "render" in item.keywords and not render_ok:
            item.add_marker(pytest.mark.skip(reason="chybí Chromium nebo ffmpeg"))
        if "pg" in item.keywords and not pg_ok:
            item.add_marker(pytest.mark.skip(reason="chybí DATABASE_URL"))
