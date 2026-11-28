"""docker/docker-compose.yml: izolace služeb podle spec chatbota 21 (MCP nejede jako superuser DB, kontejner utvrzený)."""
from __future__ import annotations

from pathlib import Path

import yaml

KOREN = Path(__file__).resolve().parents[2]
COMPOSE = KOREN / "docker/docker-compose.yml"
SLUZBY = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def test_mcp_je_utvrzeny():
    m = SLUZBY["mcp"]
    assert m["read_only"] is True and m["tmpfs"] == ["/tmp"]
    assert m["cap_drop"] == ["ALL"] and "no-new-privileges:true" in m["security_opt"]
    assert m["pids_limit"] == 128 and "volumes" not in m and not m.get("privileged")


def test_heslo_mcp_je_v_vzoru_env_bez_hodnoty():
    radky = (KOREN / "docker/.env.example").read_text(encoding="utf-8").splitlines()
    assert "MCP_DB_PASSWORD=" in radky


def test_zadna_sluzba_nema_docker_socket():
    assert "docker.sock" not in COMPOSE.read_text(encoding="utf-8")
