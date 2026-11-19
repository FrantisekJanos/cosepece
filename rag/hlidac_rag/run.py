"""Jeden běh ingestu stenoprotokolů: python -m hlidac_rag.run [--limit N] [--no-opendata] [--reassemble]
                                                       [--prepocitej-rozpravu]

1. otevřená data (osoby, kluby, schůze, body, kostra částí, rec) – nejvýš jednou za 20 hodin
2. objevení nových částí podle indexu a jednacích dnů posledních schůzí
3. stažení částí s prošlou kontrolou, hlídání změn (hash)
4. složení projevů u schůzí, kde se něco změnilo, a přepočet rozpravy (projev_tisk, druh, projev_pn)
5. texty návrhů zákonů a PN (schéma texty), samostatně: chyba neshodí běh stenoprotokolů
--prepocitej-rozpravu: jen krok 4 pro všechny schůze z uložených částí, bez sítě (po změně pravidel).
Výsledek se zapíše do steno.beh a meta.steno_last_result.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from . import db
from .psp import ingest, opendata
from .psp.http import Fetcher
from .texty.ingest import sync_texty

LOCK_ID = 74210001  # pg_advisory_lock proti souběhu dvou běhů

log = logging.getLogger("hlidac_rag")


def _meta(cur, key: str, value: str | None = None) -> str | None:
    if value is None:
        cur.execute("SELECT v FROM meta WHERE k = %s", (key,))
        r = cur.fetchone()
        return r[0] if r else None
    cur.execute("INSERT INTO meta (k, v) VALUES (%s, %s) ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v", (key, value))
    return value


def _schuze_s_html(conn, obdobi: int) -> set[int]:
    """Schůze, které mají aspoň jednu uloženou část (lze je složit bez sítě)."""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT schuze FROM steno.cast WHERE obdobi = %s AND html IS NOT NULL", (obdobi,))
        return {r[0] for r in cur.fetchall()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1500, help="nejvýš částí ke stažení za běh")
    ap.add_argument("--no-opendata", action="store_true")
    ap.add_argument("--force-opendata", action="store_true")
    ap.add_argument("--reassemble", action="store_true", help="znovu složit projevy všech schůzí")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--texty-limit", type=int, default=40, help="nejvýš dokumentů textů ke stažení za běh")
    ap.add_argument("--prepocitej-rozpravu", action="store_true",
                    help="jen znovu složit projevy všech schůzí z uložených částí a přepočítat rozpravu, bez stahování")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    started = time.time()
    conn = db.connect()
    db.migrate(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,))
        if not cur.fetchone()[0]:
            log.info("jiný běh ještě neskončil")
            return 0
        cur.execute("INSERT INTO steno.beh DEFAULT VALUES RETURNING id")
        run_id = cur.fetchone()[0]
    conn.commit()

    # přepočet rozpravy jde jen z uložených částí: bez stahování a bez textů
    fetcher = None if args.prepocitej_rozpravu else Fetcher(delay=args.delay)
    result: dict = {}
    try:
        if fetcher is None:
            with conn.cursor() as cur:
                obdobi = int(_meta(cur, "steno_obdobi") or 10)
            dirty = _schuze_s_html(conn, obdobi)
        else:
            with conn.cursor() as cur:
                last = _meta(cur, "steno_opendata_at")
            if not args.no_opendata and (args.force_opendata or not last or time.time() - float(last) > 20 * 3600):
                result["opendata"] = opendata.import_all(conn, fetcher)
                with conn.cursor() as cur:
                    _meta(cur, "steno_opendata_at", str(time.time()))
                    _meta(cur, "steno_obdobi", str(result["opendata"]["obdobi"]))
                conn.commit()
            with conn.cursor() as cur:
                obdobi = int(_meta(cur, "steno_obdobi") or 10)
            result["discover"] = ingest.discover(conn, fetcher, obdobi)
            cr = ingest.crawl(conn, fetcher, obdobi, limit=args.limit)
            result["crawl"] = {k: v for k, v in cr.__dict__.items() if k != "dirty"}
            dirty = cr.dirty
            if args.reassemble or "opendata" in result:
                # nová rec data mohou doplnit osoby a body u všech schůzí
                dirty = _schuze_s_html(conn, obdobi)
        assembled = {}
        for s in sorted(dirty):
            st = ingest.assemble(conn, obdobi, s)
            assembled[s] = st
        result["assemble"] = {k: sum(st[k] for st in assembled.values()) for k in ("nove", "zmenene", "zrusene", "smazane")}
        result["assemble"]["schuze"] = len(assembled)
        result["rozprava"] = {k: sum(st["rozprava"][k] for st in assembled.values())
                              for k in ("projevu", "projev_tisk", "projev_pn")}
        ok = True
    except Exception as e:
        log.exception("běh selhal")
        conn.rollback()
        result["error"] = str(e)[:500]
        ok = False
    if fetcher is not None:
        try:
            with conn.cursor() as cur:
                obdobi = int(_meta(cur, "steno_obdobi") or 10)
            conn.commit()
            result["texty"] = {"ok": True, **sync_texty(conn, fetcher, obdobi, limit=args.texty_limit)}
        except Exception:
            log.exception("texty selhaly")
            conn.rollback()
            result["texty"] = {"ok": False}
    result["requests"] = fetcher.requests if fetcher else 0
    result["seconds"] = round(time.time() - started, 1)
    with conn.cursor() as cur:
        cur.execute("UPDATE steno.beh SET konec = now(), vysledek = %s WHERE id = %s", (json.dumps(result), run_id))
        _meta(cur, "steno_last_result", json.dumps(result, ensure_ascii=False))
        cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    conn.commit()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
