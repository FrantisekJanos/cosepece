"""Připojení k PostgreSQL a migrace schématu."""
from __future__ import annotations

import os
import time
from importlib import resources

import psycopg


def dsn() -> str:
    """DATABASE_URL, jinak PG* proměnné (stejná DB jako PHP web)."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    return " ".join(
        f"{k}={v}" for k, v in {
            "host": os.environ.get("PGHOST", "db"),
            "port": os.environ.get("PGPORT", "5432"),
            "dbname": os.environ.get("PGDATABASE", "hlidac"),
            "user": os.environ.get("PGUSER", "hlidac"),
            "password": os.environ.get("PGPASSWORD", os.environ.get("DB_PASSWORD", "")),
        }.items()
    )


def connect(autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(dsn(), autocommit=autocommit)


def connect_wait(timeout: float = 60, step: float = 2, autocommit: bool = False) -> psycopg.Connection:
    """Jako connect(), ale při nedostupné databázi zkouší spojení znovu, nejdéle timeout sekund.

    Jen pro úlohy na pozadí (hlidac_rag.run): po restartu serveru startuje jejich kontejner zároveň
    s databází a první pokus by jinak selhal. MCP server nečeká, dotaz má při výpadku selhat hned.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            return connect(autocommit=autocommit)
        except psycopg.OperationalError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(step)


def schema_files() -> list[str]:
    """schema.sql a po něm schémata okruhů schema_<okruh>.sql v abecedním pořadí."""
    pkg = resources.files("hlidac_rag")
    dalsi = sorted(f.name for f in pkg.iterdir() if f.name.startswith("schema_") and f.name.endswith(".sql"))
    return ["schema.sql", *dalsi]


def migrate(conn: psycopg.Connection) -> None:
    pkg = resources.files("hlidac_rag")
    with conn.cursor() as cur:
        for name in schema_files():
            cur.execute(pkg.joinpath(name).read_text(encoding="utf-8"))
    conn.commit()
