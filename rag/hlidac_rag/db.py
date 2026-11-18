"""Připojení k PostgreSQL a migrace schématu."""
from __future__ import annotations

import os
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


def migrate(conn: psycopg.Connection) -> None:
    sql = resources.files("hlidac_rag").joinpath("schema.sql").read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
