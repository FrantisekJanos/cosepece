"""Spuštění služby chatu: `python -m hlidac_rag.chat` (uvicorn, 1 proces; čítače souběhu a tahy jsou v paměti procesu).

Spojení do DB: role `chat_ro` (čtení dat, `CHAT_DB_RO_USER`/`CHAT_DB_RO_PASSWORD`) a `chat_rw` (schéma `chat`,
`CHAT_DB_RW_USER`/`CHAT_DB_RW_PASSWORD`) na `PGHOST`/`PGPORT`/`PGDATABASE`. Bez jména role (lokální vývoj) se použije
`DATABASE_URL` nebo `PG*` (db.dsn). Neplatná konfigurace nebo model mimo ceník = služba nenastartuje.
"""
from __future__ import annotations

import logging
import os

import httpx2 as httpx
import psycopg
import uvicorn
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool

from .. import db, sentry
from ..embed import hledani
from . import config, rozpocet
from .app import Tajemstvi, vytvor
from .cenik import nacti_cenik
from .llm import AnthropicLLM
from .pg import PgData, PgUloziste

POOL_TIMEOUT_S = 5
POOL_RO = 3      # spec 8.2
POOL_RW = 4


def dsn_role(env, uzivatel: str, heslo: str) -> str:
    jmeno = (env.get(uzivatel) or "").strip()
    if not jmeno:
        return db.dsn()
    return make_conninfo(host=env.get("PGHOST", "db"), port=env.get("PGPORT", "5432"),
                         dbname=env.get("PGDATABASE", "hlidac"), user=jmeno, password=env.get(heslo, ""))


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    sentry.init("chat")
    env = os.environ
    n = config.nacti(env)
    cenik = nacti_cenik(env)
    rozpocet.over_modely(n, cenik)
    tajemstvi = Tajemstvi.z_prostredi(env)
    dsn_ro = dsn_role(env, "CHAT_DB_RO_USER", "CHAT_DB_RO_PASSWORD")
    dsn_rw = dsn_role(env, "CHAT_DB_RW_USER", "CHAT_DB_RW_PASSWORD")
    with psycopg.connect(dsn_rw, connect_timeout=5) as conn:
        if conn.execute("SELECT to_regclass('chat.relace')").fetchone()[0] is None:
            raise SystemExit("Chybí schéma chat: spusťte nejdřív rag-ingest (provede migraci).")
    pool_rw = ConnectionPool(dsn_rw, min_size=1, max_size=POOL_RW, kwargs={"autocommit": True}, open=True,
                             check=ConnectionPool.check_connection, timeout=POOL_TIMEOUT_S)
    pool_ro = ConnectionPool(dsn_ro, min_size=1, max_size=POOL_RO, open=True,
                             check=ConnectionPool.check_connection, timeout=POOL_TIMEOUT_S)
    # čítač embeddingů dotazů: vlastní krátké spojení (pool drží transakce jen pro čtení)
    hledani.nastav_citac(lambda: psycopg.connect(dsn_ro, connect_timeout=2))
    app = vytvor(n, AnthropicLLM.z_prostredi(), PgUloziste(pool_rw), httpx.AsyncClient(), tajemstvi,
                 data=PgData(pool_ro), cenik=cenik)
    # IP klienta čte služba sama z X-Forwarded-For od Caddy (relace.ip_z_hlavicky), proto proxy_headers=False
    uvicorn.run(app, host=env.get("CHAT_HOST", "0.0.0.0"), port=int(env.get("CHAT_PORT", "8000")), workers=1,
                proxy_headers=False, log_level="info")


if __name__ == "__main__":
    main()
