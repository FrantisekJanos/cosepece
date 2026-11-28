"""Jednorázový průchod frontou Reels kontrolou atribuce (docs/reels-citace-bezpecne.md, U3).

Po nasazení kontroly: položky, které čekají na zveřejnění (vyrobené před změnou), a na přání i zveřejněná videa.
Nic nemění, jen vypíše nálezy (seznam pro majitele). Spuštění v kontejneru bota:

    python -m hlidac_reels.audit_atribuce            # čekající položky (queued, pending, approved, held)
    python -m hlidac_reels.audit_atribuce --vse      # i zveřejněná videa
"""
from __future__ import annotations

import argparse
import json
import sys

from . import attribution, db, inbox, queue

WAITING = ("queued", "pending", "approved", "held", "failed", "unknown")


def audit(conn, statuses: tuple[str, ...] = WAITING) -> list[tuple[int, str, list[str]]]:
    """(id položky, stav, chyby) položek v daných stavech, které kontrola atribuce nepustí."""
    found = []
    for reel in queue.by_status(conn, statuses):
        card = None
        raw = queue.get_state(conn, f"{inbox.CARD_KEY}{reel.id}")
        if raw:
            try:
                card = json.loads(raw)
            except ValueError:
                card = None
        errs = attribution.public_text_errors(card, reel.caption, reel.ct)
        if errs:
            found.append((reel.id, reel.status, errs))
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vse", action="store_true", help="i zveřejněná videa (published)")
    args = ap.parse_args(argv)
    statuses = WAITING + (("published",) if args.vse else ())
    conn = db.connect()
    rows = audit(conn, statuses)
    for rid, status, errs in rows:
        print(f"#{rid} ({status})")
        for e in errs:
            print(f"  - {e}")
    print(f"nálezů: {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
