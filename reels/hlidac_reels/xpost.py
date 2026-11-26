"""Reels na X přes složku Pro X (poloautomat, X se nikdy nepostuje bez vlastníka).

Tok: zpráva o zveřejnění má tlačítko Na X (x:<id>) → x_status 'wanted' → worker na PC (generator/xsync.py) zkopíruje
video do složky Pro X jako NNNN-tiskCT.mp4 a přes reels_api.php nahlásí 'ready' (nebo 'missing', když video na PC
není) → bot (Bot._x_notify) pošle zprávu s textem pro X, tlačítkem Otevřít X (intent) a Hotovo na X / Zrušit →
'done' / 'skipped'; worker soubor při dalším běhu smaže. x_msg_id = zpráva o ready/missing už odešla.
Tlačítko Na X má jen položka ze sady generátoru (set_id): ručně nahraná videa na PC nejsou.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .caption import x_intent_url, x_text

X_STATUSES = ("wanted", "ready", "missing", "done", "skipped")
# znovu požádat jde po missing (worker video nenašel) a skipped (vlastník zrušil)
WANT_FROM = ("missing", "skipped")
X_FOLDER = "Pro X"  # název složky ve zprávě (skutečnou cestu zná jen worker na PC)


@dataclass
class XItem:
    id: int
    ct: int | None
    caption: str
    x_status: str


def file_name(reel_id: int, ct: int | None) -> str:
    """Jméno souboru ve složce Pro X: id na 4 místa (podle něj se video najde v okně výběru souboru)."""
    return f"{reel_id:04d}-tisk{ct}.mp4" if ct is not None else f"{reel_id:04d}.mp4"


def want(conn, reel_id: int, now: datetime) -> str:
    """Vlastník chce video na X. 'wanted' = žádost platí (i opakovaná); jinak dosavadní stav (ready, done),
    'gone' = položka není zveřejněná nebo není ze sady generátoru."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE reel_queue SET x_status='wanted', x_requested_at=%s, x_ready_at=NULL, x_msg_id=NULL "
            "WHERE id=%s AND status='published' AND set_id IS NOT NULL "
            "AND (x_status IS NULL OR x_status = ANY(%s)) RETURNING id",
            (now, reel_id, list(WANT_FROM)))
        changed = cur.fetchone() is not None
        row = None
        if not changed:
            cur.execute("SELECT status, set_id, x_status FROM reel_queue WHERE id=%s", (reel_id,))
            row = cur.fetchone()
    conn.commit()
    if changed:
        return "wanted"
    if row is None or row[0] != "published" or row[1] is None:
        return "gone"
    return row[2] or "gone"


def finish(conn, reel_id: int, status: str) -> bool:
    """Hotovo na X (done) nebo Zrušit (skipped); platí jen pro čekající nebo připravenou položku."""
    if status not in ("done", "skipped"):
        raise ValueError(f"neplatný konečný stav X: {status!r}")
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_queue SET x_status=%s WHERE id=%s AND x_status IN ('wanted','ready')",
                    (status, reel_id))
        ok = cur.rowcount == 1
    conn.commit()
    return ok


def to_notify(conn, limit: int = 20) -> list[XItem]:
    """Položky, které worker nahlásil (ready, missing) a o kterých bot ještě nenapsal."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, ct, caption, x_status FROM reel_queue "
                    "WHERE x_status IN ('ready','missing') AND x_msg_id IS NULL ORDER BY id LIMIT %s", (limit,))
        rows = cur.fetchall()
    conn.commit()
    return [XItem(*r) for r in rows]


def mark_notified(conn, reel_id: int, status: str, msg_id: int) -> bool:
    """Zpráva o ready/missing odešla; False = stav se mezitím změnil nebo už je zapsaná."""
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_queue SET x_msg_id=%s WHERE id=%s AND x_status=%s AND x_msg_id IS NULL",
                    (msg_id, reel_id, status))
        ok = cur.rowcount == 1
    conn.commit()
    return ok


# --- zprávy a tlačítka -------------------------------------------------

def publish_buttons(reel_id: int) -> list[tuple[str, str]]:
    return [("Na X", f"x:{reel_id}")]


def wanted_text(reel_id: int) -> str:
    return f"Na X #{reel_id:04d}: čekám na PC"


def ready_message(item: XItem) -> tuple[str, str]:
    """Text zprávy Pro X (s celým textem příspěvku ke zkopírování) a odkaz intent X."""
    post = x_text(item.caption, item.ct)
    head = f"Pro X: #{item.id:04d}" + (f" · tisk {item.ct}" if item.ct is not None else "")
    text = f"{head}\nVideo: {file_name(item.id, item.ct)} (složka {X_FOLDER})\n\n{post}"
    return text, x_intent_url(post)


def ready_buttons(reel_id: int, url: str | None) -> list[tuple[str, str]]:
    """url=None: Telegram tlačítko s odkazem odmítl, odkaz je pak v textu zprávy."""
    buttons = [("Otevřít X", url)] if url else []
    return buttons + [("Hotovo na X", f"xdone:{reel_id}"), ("Zrušit", f"xskip:{reel_id}")]


def missing_text(reel_id: int) -> str:
    return f"Video #{reel_id:04d} na PC není (ručně nahrané video, nebo složka sady chybí)."


def missing_buttons(reel_id: int) -> list[tuple[str, str]]:
    return [("Zkusit znovu", f"x:{reel_id}")]
