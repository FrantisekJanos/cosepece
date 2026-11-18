"""Správa přístupových tokenů MCP: python -m hlidac_rag.tokens add <jméno> | list | revoke <id>

Token se vypíše jen jednou při vytvoření, v DB je uložený jen jeho sha256.
"""
from __future__ import annotations

import hashlib
import secrets
import sys

from . import db


def main(argv: list[str]) -> int:
    conn = db.connect()
    db.migrate(conn)
    cmd = argv[0] if argv else "list"
    with conn.cursor() as cur:
        if cmd == "add" and len(argv) == 2:
            token = "hz_" + secrets.token_urlsafe(32)
            cur.execute("INSERT INTO mcp.token (jmeno, hash) VALUES (%s, %s) RETURNING id",
                        (argv[1], hashlib.sha256(token.encode()).hexdigest()))
            print(f"id {cur.fetchone()[0]}: {token}")
        elif cmd == "revoke" and len(argv) == 2:
            cur.execute("UPDATE mcp.token SET zrusen = now() WHERE id = %s", (int(argv[1]),))
            print("zrušeno" if cur.rowcount else "nenalezeno")
        elif cmd == "list":
            cur.execute("""SELECT t.id, t.jmeno, t.vytvoren, t.naposledy, t.zrusen,
                                  (SELECT count(*) FROM mcp.volani v WHERE v.token_id = t.id) FROM mcp.token t ORDER BY id""")
            for r in cur.fetchall():
                print(*r, sep="\t")
        else:
            print(__doc__)
            return 2
    conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
