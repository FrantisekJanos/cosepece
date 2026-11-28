"""docker/docker-compose.yml: izolace služeb podle spec chatbota 21 (MCP ani chat nejedou jako superuser DB, kontejnery
utvrzené, chat na vlastní síti jen s db), záloha bez schématu chat a obraz s kouřovým evalem."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

KOREN = Path(__file__).resolve().parents[2]
COMPOSE = KOREN / "docker/docker-compose.yml"
TEXT = COMPOSE.read_text(encoding="utf-8")
DATA = yaml.safe_load(TEXT)
SLUZBY = DATA["services"]
# proměnné CHAT_*, které kód čte jen při lokálním vývoji: do produkčního compose nepatří
CHAT_JEN_VYVOJ = {"CHAT_FALESNE_LLM", "CHAT_TURNSTILE_HOSTNAME", "CHAT_HOST", "CHAT_PORT"}
# proměnné, které služba čte pod jiným jménem (compose je přejmenuje), a proměnné s pevnou hodnotou
CHAT_PREJMENOVANE = {"CHAT_ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY", "CHAT_OPENAI_API_KEY": "OPENAI_API_KEY"}
CHAT_PEVNE = {"CHAT_DB_RO_USER": "chat_ro", "CHAT_DB_RW_USER": "chat_rw"}


def test_mcp_nebezi_jako_superuser_a_je_utvrzeny():
    m = SLUZBY["mcp"]
    env = m["environment"]
    assert env["PGUSER"] == "mcp_ro" and env["PGPASSWORD"] == "${MCP_DB_PASSWORD}"
    assert "DB_PASSWORD" not in env and "${DB_PASSWORD}" not in [str(v) for v in env.values()]
    assert m["read_only"] is True and m["tmpfs"] == ["/tmp"]
    assert m["cap_drop"] == ["ALL"] and "no-new-privileges:true" in m["security_opt"]
    assert m["pids_limit"] == 128 and "volumes" not in m and not m.get("privileged")


def test_heslo_mcp_je_v_vzoru_env_bez_hodnoty():
    radky = (KOREN / "docker/.env.example").read_text(encoding="utf-8").splitlines()
    assert "MCP_DB_PASSWORD=" in radky


def test_zadna_sluzba_nema_docker_socket():
    assert "docker.sock" not in TEXT


# ---------------------------------------------------------------- služba chat (spec 21.2)

def _promenne_chatu_v_kodu() -> set[str]:
    jmena: set[str] = set()
    for f in (KOREN / "rag/hlidac_rag/chat").glob("*.py"):
        jmena |= set(re.findall(r"\bCHAT_[A-Z0-9_]*[A-Z0-9]\b", f.read_text(encoding="utf-8")))
    return jmena


def test_chat_je_utvrzeny_a_oddeleny():
    c = SLUZBY["chat"]
    env = c["environment"]
    assert c["read_only"] is True and c["tmpfs"] == ["/tmp"] and c["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in c["security_opt"] and c["pids_limit"] == 128 and c["mem_limit"] == "384m"
    assert c["networks"] == ["chatdb"] and "volumes" not in c and not c.get("privileged") and "network_mode" not in c
    assert not ({"PGUSER", "PGPASSWORD", "DB_PASSWORD", "META_SYSTEM_TOKEN", "REELS_TELEGRAM_TOKEN"} & set(env))
    assert env["OPENAI_API_KEY"] == "${CHAT_OPENAI_API_KEY:-}" and env["CHAT_DB_RO_USER"] == "chat_ro"
    assert "env_file" not in c and not c.get("cap_add") and not c.get("devices") and "pid" not in c
    assert "ipc" not in c and "userns_mode" not in c and "user" not in c   # uživatel app (uid 10001) z rag/Dockerfile
    assert not [v for v in env.values() if "DB_PASSWORD}" in str(v) and "CHAT_DB_" not in str(v)]


def test_chat_nepouziva_kotvu_ragenv():
    blok = re.search(r"\n  chat:\n(.*?)(?=\n  \w[\w-]*:\n|\nvolumes:|\nnetworks:)", TEXT, re.S).group(1)
    assert "*ragenv" not in blok and "*appenv" not in blok and "<<:" not in blok


def test_na_siti_chatdb_je_jen_chat_a_db():
    assert sorted(n for n, s in SLUZBY.items() if "chatdb" in (s.get("networks") or [])) == ["chat", "db"]
    assert set(SLUZBY["db"]["networks"]) == {"default", "chatdb"}
    assert set(DATA["networks"]) == {"chatdb"} and not (DATA["networks"]["chatdb"] or {}).get("external")
    assert all("networks" not in s for n, s in SLUZBY.items() if n not in ("chat", "db"))   # ostatní jen na default


def test_chat_sluzba_a_port():
    c = SLUZBY["chat"]
    assert c["build"] == SLUZBY["mcp"]["build"] == {"context": "../rag"}
    assert c["command"] == ["python", "-m", "hlidac_rag.chat"]
    assert c["ports"] == ["${CHAT_BIND:-127.0.0.1:8103}:8000"]
    assert float(c["cpus"]) == 0.75 and c["restart"] == "unless-stopped" and c["depends_on"] == ["db"]
    env = c["environment"]
    assert env["PGHOST"] == "db" and env["PGDATABASE"] == "hlidac" and env["CHAT_DB_RW_USER"] == "chat_rw"
    assert env["ANTHROPIC_API_KEY"] == "${CHAT_ANTHROPIC_API_KEY:-}"
    assert env["CHAT_DB_RO_PASSWORD"] == "${CHAT_DB_RO_PASSWORD:-}"
    assert env["CHAT_DB_RW_PASSWORD"] == "${CHAT_DB_RW_PASSWORD:-}"
    # embedding dotazu stejným modelem, jakým indexuje rag-ingest; denní strop embeddingů je společný (rag.dotazy_den)
    ingest = SLUZBY["rag-ingest"]["environment"]
    assert env["EMBED_MODEL"] == ingest["EMBED_MODEL"] and env["EMBED_DOTAZY_DEN"] == ingest["EMBED_DOTAZY_DEN"]
    assert env["OPENAI_API_KEY"] != ingest["OPENAI_API_KEY"]   # vlastní klíč s nízkým limitem, ne klíč ingestu


def test_kazda_promenna_chatu_z_kodu_je_v_compose():
    """Každou proměnnou CHAT_*, kterou služba čte, compose předá jako ${CHAT_X:-} (prázdná = výchozí z kódu)."""
    env = SLUZBY["chat"]["environment"]
    kod = _promenne_chatu_v_kodu()
    assert {"CHAT_MAX_KOL", "CHAT_BETA_KLIC", "CHAT_ZAPNUTO"} <= kod   # regex opravdu něco našel
    for jmeno in sorted(kod - CHAT_JEN_VYVOJ):
        if jmeno in CHAT_PREJMENOVANE:
            assert env[CHAT_PREJMENOVANE[jmeno]] == "${%s:-}" % jmeno, jmeno
        elif jmeno in CHAT_PEVNE:
            assert env[jmeno] == CHAT_PEVNE[jmeno], jmeno
        else:
            assert env.get(jmeno) == "${%s:-}" % jmeno, jmeno
    assert not (CHAT_JEN_VYVOJ & set(env))   # falešný model ani cizí hostname Turnstile v provozu
    navic = {k for k in env if k.startswith("CHAT_")} - kod
    assert navic == set(), navic   # v compose nic, co kód nečte (např. zrušená CHAT_SUL)


def test_promenne_chatu_jsou_ve_vzoru_env_bez_hodnoty():
    radky = (KOREN / "docker/.env.example").read_text(encoding="utf-8").splitlines()
    jmena = set(re.findall(r"\$\{(CHAT_[A-Z0-9_]+)", yaml.safe_dump(SLUZBY["chat"])))
    assert {"CHAT_BIND", "CHAT_ANTHROPIC_API_KEY", "CHAT_DB_RO_PASSWORD"} <= jmena
    for jmeno in sorted(jmena):
        assert f"{jmeno}=" in radky, jmeno
    assert not [r for r in radky if r.startswith("CHAT_") and not r.endswith("=")]   # žádné hodnoty
    assert not [r for r in radky if r.startswith("CHAT_SUL")]


def test_zaloha_bez_schematu_chat():
    zaloha = (KOREN / "docker/backup.sh").read_text(encoding="utf-8")
    assert "pg_dump -U hlidac -Fc --exclude-schema=chat hlidac" in zaloha


def test_obraz_obsahuje_kourovy_eval_chatu():
    dockerfile = (KOREN / "rag/Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^COPY eval/chat \./eval/chat$", dockerfile, re.M)
    assert re.search(r"^COPY eval/__init__\.py \./eval/__init__\.py$", dockerfile, re.M)
