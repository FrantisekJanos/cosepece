"""Fronta reelů: stavový automat nad tabulkou reel_queue."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, fields as dc_fields
from datetime import date, datetime, timedelta

import psycopg

# reel_state set_picked:<set_id> = id vybraných variant oddělená čárkou (dřívější hodnota je jedno id);
# sada s výběrem se nestáhne (fresh.retire), vybraná varianta má vlastní lhůtu PICKED_MAX
PICKED_KEY = "set_picked:"
# reel_state reminded:<set_id> = nedotčená automatická sada už dostala připomínku po AUTO_HOLD h
REMINDED_KEY = "reminded:"
# reel_state held_msg:<reel_id> = id zprávy s tlačítky Zkusit znovu / Zahodit u jednotky v held
HELD_MSG_KEY = "held_msg:"

# Vyžádaná jednotka (uživatel si o ni řekl: /video, /pn, /vyber, ukázka) se posílá hned (R1);
# automatická (source 'fresh') brzdí denní limit a jiná nedotčená automatická sada (R2)
REQUESTED_SOURCES = ("manual", "auto_pick", "ukazka")
AUTO_SOURCE = "fresh"
# nedotčená automatická sada brzdí další automatickou nejdéle tak dlouho od odeslání (hodiny)
AUTO_HOLD = 12
# odklad po neúspěšném odeslání do Telegramu (minuty podle pořadí pokusu, dál poslední hodnota)
SEND_BACKOFF = (1, 5, 15, 60)
# po tolika neúspěšných pokusech jde jednotka do stavu held (zpráva s tlačítky)
SEND_MAX_ATTEMPTS = 5
# lhůty čekání (queue.expire); běží od sent_at, u vybrané varianty od picked_at, u held od posledního pokusu
UNPICKED_AFTER_DECISION = timedelta(hours=24)  # nevybrané varianty dotčené sady od posledního rozhodnutí
AUTO_SET_MAX = timedelta(days=7)  # nedotčená automatická jednotka
REQUESTED_SET_MAX = timedelta(days=14)  # nedotčená vyžádaná jednotka
PICKED_MAX = timedelta(days=14)  # vybraná varianta bez Schválit / Zamítnout
HELD_MAX = timedelta(days=7)  # jednotka v held bez Zkusit znovu / Zahodit → rejected

STATUSES = ("queued", "pending", "approved", "publishing", "published", "rejected", "failed", "unknown",
            "expired", "withdrawn", "held")


class DuplicateVideo(Exception):
    """Video se stejným video_sha256 už ve frontě je."""


@dataclass
class Reel:
    id: int
    source: str
    ct: int | None
    format: str | None
    video_path: str
    video_sha256: str
    caption: str
    status: str
    telegram_video_msg_id: int | None
    telegram_msg_id: int | None
    ig_container_id: str | None
    ig_media_id: str | None
    ig_permalink: str | None
    fb_video_id: str | None
    fb_permalink: str | None
    public_token: str | None
    error: str | None
    attempts: int
    created_at: datetime
    sent_at: datetime | None
    decided_at: datetime | None
    published_at: datetime | None
    # Threads: sloupce přidané ALTER TABLE na konec tabulky (SELECT je stejně vybírá jménem)
    threads_container_id: str | None
    threads_media_id: str | None
    threads_permalink: str | None
    # video na šířku 16:9 (volitelné)
    video_landscape_path: str | None
    video_landscape_sha256: str | None
    telegram_landscape_msg_id: int | None
    telegram_landscape_sha256: str | None  # otisk šířky odeslané do Telegramu
    # sada variant z generátoru (reel_jobs.id)
    set_id: int | None
    variant: int | None
    template: str | None
    autopilot: bool = False  # z úlohy s autopilotem: zveřejní se bez schválení
    send_attempts: int = 0  # pokusy o odeslání do Telegramu (fronta ke schválení)
    send_error: str | None = None  # poslední chyba odeslání
    next_send_at: datetime | None = None  # nejdřív kdy zkusit odeslat znovu
    picked_at: datetime | None = None  # kdy vlastník variantu vybral (tlačítko Vybrat, Bot._pick)


_COLS = ", ".join(f.name for f in dc_fields(Reel))
# autopilot nastavuje jen add (převzetí sady) a autopilot_to_approval, set_fields ho změnit nesmí
_ALLOWED = {f.name for f in dc_fields(Reel)} - {"id", "status", "autopilot"}


@contextmanager
def _tx(conn):
    """Zápis s commitem; při chybě DB rollback, aby spojení nezůstalo v přerušené transakci."""
    try:
        yield
        conn.commit()
    except psycopg.Error:
        conn.rollback()
        raise


def _check(fields: dict) -> None:
    # názvy sloupců se vkládají do SQL, proto jen ze seznamu polí Reel;
    # status se mění jen přes transition (podmíněný přechod), autopilot jen přes add a autopilot_to_approval
    bad = set(fields) - _ALLOWED
    if bad:
        raise ValueError(f"neznámá pole: {sorted(bad)}")


def _row(cur) -> Reel | None:
    r = cur.fetchone()
    return Reel(*r) if r else None


def add(conn, *, video_path: str, video_sha256: str, caption: str,
        ct: int | None = None, format: str | None = None, source: str = "ukazka",
        video_landscape_path: str | None = None,
        video_landscape_sha256: str | None = None,
        set_id: int | None = None, variant: int | None = None, template: str | None = None,
        autopilot: bool = False, commit: bool = True) -> int:
    """Vloží položku. commit=False nechá transakci otevřenou (volající commitne, při chybě rollback)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO reel_queue (source, ct, format, video_path, video_sha256, caption, "
                "video_landscape_path, video_landscape_sha256, set_id, variant, template, autopilot) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (source, ct, format, video_path, video_sha256, caption,
                 video_landscape_path, video_landscape_sha256, set_id, variant, template, bool(autopilot)),
            )
            new_id = cur.fetchone()[0]
        if commit:
            conn.commit()
        return new_id
    except psycopg.errors.UniqueViolation as e:
        conn.rollback()
        raise DuplicateVideo(video_sha256) from e


def get(conn, id: int) -> Reel | None:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM reel_queue WHERE id=%s", (id,))
        r = _row(cur)
    conn.commit()
    return r


def next_sendable(conn, now: datetime, auto_ok: bool) -> Reel | None:
    """Další položka k odeslání do Telegramu (R1, R2, R4).

    Jednotka s nejstarší queued položkou (podle id), jejíž next_send_at není v budoucnu; automatické
    (source 'fresh') jen při auto_ok. U sady vrací její první queued variantu. Stav ostatních jednotek
    (pending, nerozhodnuté) na výběr nemá vliv.
    """
    only_requested = "" if auto_ok else " AND source = ANY(%(req)s)"
    ready = "(next_send_at IS NULL OR next_send_at <= %(now)s::timestamptz)"
    with conn.cursor() as cur:
        cur.execute(
            f"WITH u AS (SELECT id, set_id FROM reel_queue WHERE status='queued' AND {ready}{only_requested} "
            "ORDER BY id LIMIT 1) "
            f"SELECT {', '.join('r.' + f.name for f in dc_fields(Reel))} FROM reel_queue r, u "
            f"WHERE r.status='queued' AND {ready.replace('next_send_at', 'r.next_send_at')} "
            "AND (r.id = u.id OR (u.set_id IS NOT NULL AND r.set_id = u.set_id)) "
            "ORDER BY r.variant NULLS LAST, r.id LIMIT 1",
            {"now": now, "req": list(REQUESTED_SOURCES)},
        )
        r = _row(cur)
    conn.commit()
    return r


def auto_blocked(conn, now: datetime) -> bool:
    """Brzdí nedotčená automatická jednotka další automatickou (R2)?

    Automatická (source 'fresh', ne autopilot) jednotka v Telegramu (sent_at), žádná varianta rozhodnutá
    (decided_at) ani vybraná (set_picked:<set_id>), dosud čeká (pending) a od odeslání neuběhlo AUTO_HOLD h.
    Autopilot má sent_at, i když do Telegramu nešel, proto se vylučuje výslovně.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM reel_queue q WHERE q.source = %(auto)s AND NOT q.autopilot "
            "GROUP BY coalesce(q.set_id, -q.id) "
            "HAVING bool_or(q.status = 'pending') AND bool_and(q.decided_at IS NULL) "
            "AND min(q.sent_at) IS NOT NULL "
            "AND %(now)s::timestamptz - min(q.sent_at) < make_interval(hours => %(hold)s) "
            "AND NOT EXISTS (SELECT 1 FROM reel_state s WHERE max(q.set_id) IS NOT NULL "
            "AND s.key = concat(%(pk)s::text, max(q.set_id)) AND s.value <> ''))",
            {"auto": AUTO_SOURCE, "now": now, "hold": AUTO_HOLD, "pk": PICKED_KEY},
        )
        blocked = cur.fetchone()[0]
    conn.commit()
    return bool(blocked)


@dataclass
class Expired:
    """Výsledek vypršení jedné jednotky: kind unpicked | auto_set | requested_set | picked | held.

    reel_ids = položky, které změnily stav, msg_ids = zprávy, kterým se mají odebrat tlačítka.
    """
    kind: str
    set_id: int | None
    reel_ids: list[int]
    msg_ids: list[int]


def _overdue(start: datetime | None, limit: timedelta, now: datetime) -> bool:
    return start is not None and now - start >= limit


def expire(conn, now: datetime) -> list[Expired]:
    """Lhůty čekání (spec 2.2); vypršení jen zamítá, nikdy neschvaluje.

    - nevybrané varianty dotčené sady (něco vybrané nebo rozhodnuté): UNPICKED_AFTER_DECISION od posledního
      rozhodnutí (max decided_at), jen když žádná vybraná varianta nečeká na schválení → expired (unpicked),
    - nedotčená jednotka: od sent_at AUTO_SET_MAX (source 'fresh', auto_set), jinak REQUESTED_SET_MAX
      (requested_set) → expired,
    - vybraná nerozhodnutá varianta: PICKED_MAX od picked_at (starší výběry bez něj od sent_at) → expired,
    - held: HELD_MAX od posledního pokusu (next_send_at) → rejected.

    Čekající položka bez sent_at (nebyla v Telegramu) nevyprší. Autopilot (approved se sent_at, v Telegramu
    nebyl) se vylučuje výslovně. Vypršení nastaví decided_at = now: u sady s vypršelou vybranou variantou
    tak od té chvíle běží lhůta zbylých nevybraných. Vrací jen jednotky, kde se stav opravdu změnil
    (přechod je podmíněný, souběžné kliknutí vyhraje).
    """
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_COLS} FROM reel_queue WHERE NOT autopilot AND coalesce(set_id, -id) IN "
            "(SELECT coalesce(set_id, -id) FROM reel_queue WHERE status IN ('pending','held') AND NOT autopilot) "
            "ORDER BY id"
        )
        rows = [Reel(*r) for r in cur.fetchall()]
    conn.commit()
    units: dict[int, list[Reel]] = {}
    for r in rows:
        units.setdefault(r.set_id if r.set_id is not None else -r.id, []).append(r)

    out: list[Expired] = []

    def run(kind: str, set_id: int | None, items: list[Reel], to_status: str, **fields) -> None:
        done = [r for r in items if transition(conn, r.id, r.status, to_status, decided_at=now, **fields)]
        if not done:
            return
        msg_ids = sorted({r.telegram_msg_id for r in done if r.telegram_msg_id})
        if kind == "held":
            for r in done:
                raw = get_state(conn, f"{HELD_MSG_KEY}{r.id}")
                if raw and raw.isdigit():
                    msg_ids.append(int(raw))
                delete_state(conn, f"{HELD_MSG_KEY}{r.id}")
        out.append(Expired(kind, set_id, [r.id for r in done], msg_ids))

    for items in units.values():
        set_id = items[0].set_id
        held = [r for r in items if r.status == "held"]
        if held and _overdue(max(r.next_send_at or r.created_at for r in held), HELD_MAX, now):
            run("held", set_id, held, "rejected", error="vypršelo: odeslání do Telegramu se nepovedlo")
        waiting = [r for r in items if r.status == "pending" and r.sent_at is not None]
        if not waiting:
            continue
        chosen = picked(conn, set_id) if set_id is not None else set()
        if not chosen and all(r.decided_at is None for r in items):
            auto = any(r.source == AUTO_SOURCE for r in items)
            limit = AUTO_SET_MAX if auto else REQUESTED_SET_MAX
            if _overdue(min(r.sent_at for r in waiting), limit, now):
                run("auto_set" if auto else "requested_set", set_id, waiting, "expired")
            continue
        old_picks = [r for r in waiting if r.id in chosen and _overdue(r.picked_at or r.sent_at, PICKED_MAX, now)]
        if old_picks:
            run("picked", set_id, old_picks, "expired")
        if set_id is None:
            continue
        # po případném vypršení vybraných: stav a decided_at znovu z DB
        current = set_items(conn, set_id)
        if any(r.status == "pending" and r.id in chosen for r in current):
            continue
        rest = [r for r in current if r.status == "pending" and r.sent_at is not None and r.id not in chosen]
        decided = [r.decided_at for r in current if r.decided_at is not None]
        if rest and decided and _overdue(max(decided), UNPICKED_AFTER_DECISION, now):
            run("unpicked", set_id, rest, "expired")
    return out


def auto_reminders(conn, now: datetime) -> list[int]:
    """Automatické sady nedotčené aspoň AUTO_HOLD h od sent_at, které ještě nedostaly připomínku.

    Stejná „nedotčenost“ jako auto_blocked (žádná varianta rozhodnutá ani vybraná, něco čeká), autopilot
    vyloučený. Připomínku zapíše volající po odeslání zprávy (mark_reminded).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT q.set_id FROM reel_queue q WHERE q.source = %(auto)s AND NOT q.autopilot "
            "AND q.set_id IS NOT NULL GROUP BY q.set_id "
            "HAVING bool_or(q.status = 'pending') AND bool_and(q.decided_at IS NULL) "
            "AND min(q.sent_at) IS NOT NULL "
            "AND %(now)s::timestamptz - min(q.sent_at) >= make_interval(hours => %(hold)s) "
            "AND NOT EXISTS (SELECT 1 FROM reel_state s WHERE s.value <> '' "
            "AND s.key IN (concat(%(pk)s::text, q.set_id), concat(%(rk)s::text, q.set_id))) "
            "ORDER BY q.set_id",
            {"auto": AUTO_SOURCE, "now": now, "hold": AUTO_HOLD, "pk": PICKED_KEY, "rk": REMINDED_KEY},
        )
        ids = [r[0] for r in cur.fetchall()]
    conn.commit()
    return ids


@dataclass
class Unit:
    """Řádek přehledu /fronta: jednotka (sada nebo samostatné video), nebo její část v jednom stavu.

    state říká, na co čeká:
    - na vlastníka: approval (samostatné video), untouched (nedotčená sada), picked (vybrané varianty
      čekají na schválení), remaining (zbylé nevybrané varianty dotčené sady),
    - hotové: next (na řadě k odeslání), deferred (odloženo po chybě, note = HH:MM v Europe/Prague),
      limit (note = „odeslané/limit“), blocked (nedotčená jiná automatická sada), autopilot, approved,
      publishing,
    - problémy: held (note = poslední chyba odeslání, attempts), failed a unknown (note = chyba
      publikace), stale (zveřejňování uvízlo).
    variants = čísla variant sady, kterých se řádek týká (u samostatného videa prázdné).
    """
    set_id: int | None
    reel_ids: list[int]
    source: str
    ct: int | None
    state: str
    since: datetime
    note: str | None = None
    variants: list[int] = field(default_factory=list)
    focus_pn: list[int] | None = None  # PN, o kterých je úloha sady (reel_jobs.focus_pn)
    attempts: int = 0

    @property
    def label(self) -> int:
        """Číslo v přehledu: sada podle set_id, samostatné video podle id."""
        return self.set_id if self.set_id is not None else self.reel_ids[0]


OPEN_STATUSES = ("queued", "pending", "approved", "publishing", "failed", "unknown", "held")


def open_units(conn, now: datetime, per_day: int | None = None,
               stale_publishing: timedelta = timedelta(minutes=15)) -> list[Unit]:
    """Otevřené položky fronty seskupené do řádků přehledu /fronta (jeden dotaz na položky).

    per_day = denní limit automatických sad: automatická queued jednotka nad limitem dostane stav
    limit, brzděná nedotčenou automatickou sadou blocked (queue.auto_blocked). Řádky jsou seřazené
    podle nejstarší položky.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT r.id, r.set_id, r.variant, r.source, r.ct, r.status, r.autopilot, r.created_at, "
            "r.sent_at, r.decided_at, r.picked_at, r.next_send_at, "
            "to_char(r.next_send_at AT TIME ZONE 'Europe/Prague', 'FMHH24:MI'), r.send_attempts, "
            "r.send_error, r.error, j.focus_pn, s.value, "
            # dotčenost sady: poslední výběr nebo rozhodnutí kterékoli varianty (i už uzavřené)
            "(SELECT max(greatest(x.decided_at, x.picked_at)) FROM reel_queue x WHERE x.set_id = r.set_id) "
            "FROM reel_queue r LEFT JOIN reel_jobs j ON j.id = r.set_id "
            "LEFT JOIN reel_state s ON r.set_id IS NOT NULL AND s.key = concat(%s::text, r.set_id) "
            "WHERE r.status = ANY(%s) ORDER BY r.id",
            (PICKED_KEY, list(OPEN_STATUSES)),
        )
        rows = cur.fetchall()
    conn.commit()

    names = ("id", "set_id", "variant", "source", "ct", "status", "autopilot", "created_at", "sent_at",
             "decided_at", "picked_at", "next_send_at", "next_send_hhmm", "send_attempts", "send_error",
             "error", "focus_pn", "picked", "touched_at")
    groups: dict[tuple[int, str], list[dict]] = {}
    for row in rows:
        r = dict(zip(names, row))
        if r["status"] == "pending":
            if r["set_id"] is None:
                state = "approval"
            elif r["id"] in _parse_picked(r["picked"]):
                state = "picked"
            elif r["picked"] or r["touched_at"] is not None:
                state = "remaining"
            else:
                state = "untouched"
        elif r["status"] == "queued":
            state = "deferred" if r["next_send_at"] is not None and r["next_send_at"] > now else "next"
        elif r["status"] == "approved":
            state = "autopilot" if r["autopilot"] else "approved"
        elif r["status"] == "publishing":
            started = r["decided_at"] or r["sent_at"] or r["created_at"]
            state = "stale" if now - started >= stale_publishing else "publishing"
        else:
            state = r["status"]  # failed, unknown, held
        key = r["set_id"] if r["set_id"] is not None else -r["id"]
        groups.setdefault((key, state), []).append(r)

    def first_of(items: list[dict], *cols: str) -> list[datetime]:
        # pro každou položku první vyplněný čas z cols (created_at je vždy)
        return [next(r[c] for c in (*cols, "created_at") if r[c] is not None) for r in items]

    auto_wait: tuple[str, str | None] | None = None
    out: list[Unit] = []
    for (_key, state), items in groups.items():
        first = items[0]
        if state in ("approval", "untouched"):
            since = min(first_of(items, "sent_at"))
        elif state == "picked":
            since = min(first_of(items, "picked_at", "sent_at"))
        elif state == "remaining":
            since = first["touched_at"] or min(first_of(items, "sent_at"))
        elif state == "held":
            since = max(first_of(items, "next_send_at"))
        elif state in ("next", "deferred"):
            since = min(first_of(items))
        else:
            since = min(first_of(items, "decided_at", "sent_at"))
        note = {"deferred": first["next_send_hhmm"], "held": first["send_error"],
                "failed": first["error"], "unknown": first["error"]}.get(state)
        if state == "next" and first["source"] == AUTO_SOURCE and per_day is not None:
            # automatická jednotka čeká na denní limit nebo na výběr z jiné automatické sady (R2)
            if auto_wait is None:
                sent_today = sets_sent_on(conn, local_date(conn, now))
                if sent_today >= per_day:
                    auto_wait = ("limit", f"{sent_today}/{per_day}")
                elif auto_blocked(conn, now):
                    auto_wait = ("blocked", None)
                else:
                    auto_wait = ("next", None)
            state, note = auto_wait
        set_id = first["set_id"]
        out.append(Unit(set_id=set_id, reel_ids=[r["id"] for r in items], source=first["source"], ct=first["ct"],
                        state=state, since=since, note=note,
                        variants=[r["variant"] for r in items if set_id is not None and r["variant"] is not None],
                        focus_pn=list(first["focus_pn"]) if first["focus_pn"] else None,
                        attempts=max(r["send_attempts"] for r in items)))
    out.sort(key=lambda u: min(u.reel_ids))
    return out


SUMMARY_WINDOW = timedelta(hours=24)


def summary_lines(conn, now: datetime) -> list[str]:
    """Řádky fronty pro denní souhrn (spec 2.2), autopilot vyloučený.

    - vypršelé nevybrané varianty sady za posledních SUMMARY_WINDOW (expired, decided_at v okně, nevybrané),
    - stažené jednotky za posledních SUMMARY_WINDOW (withdrawn, decided_at v okně; i ty, které v Telegramu nebyly),
    - vybrané nerozhodnuté varianty, kterým lhůta PICKED_MAX vyprší během příštích SUMMARY_WINDOW.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_COLS} FROM reel_queue WHERE NOT autopilot AND ("
            "(status IN ('expired','withdrawn') AND decided_at > %(since)s AND decided_at <= %(now)s) "
            "OR (status = 'pending' AND set_id IS NOT NULL AND sent_at IS NOT NULL)) ORDER BY id",
            {"since": now - SUMMARY_WINDOW, "now": now},
        )
        rows = [Reel(*r) for r in cur.fetchall()]
    conn.commit()
    expired: dict[int, list[int]] = {}
    withdrawn: list[str] = []
    seen_withdrawn: set[int] = set()
    soon: list[str] = []
    chosen: dict[int, set[int]] = {}

    def picks(set_id: int) -> set[int]:
        if set_id not in chosen:
            chosen[set_id] = picked(conn, set_id)
        return chosen[set_id]

    for r in rows:
        if r.status == "withdrawn":
            key = r.set_id if r.set_id is not None else -r.id
            if key not in seen_withdrawn:
                seen_withdrawn.add(key)
                what = f"Sada #{r.set_id} stažena" if r.set_id is not None else f"#{r.id} staženo"
                withdrawn.append(f"{what}: nález už není mezi hlavními")
        elif r.status == "expired":
            # nevybraná varianta dotčené sady: lhůtu jí spustilo dřívější rozhodnutí o jiné variantě
            # (nedotčená sada vyprší celá najednou, vybraná varianta má vlastní zprávu)
            if (r.set_id is not None and r.id not in picks(r.set_id)
                    and any(o.decided_at is not None and o.decided_at < r.decided_at
                            for o in set_items(conn, r.set_id))):
                expired.setdefault(r.set_id, []).append(r.id)
        elif r.id in picks(r.set_id):
            deadline = (r.picked_at or r.sent_at) + PICKED_MAX
            if now < deadline <= now + SUMMARY_WINDOW:
                soon.append(f"Sada #{r.set_id}: vybraná varianta #{r.id} zítra vyprší "
                            f"({PICKED_MAX.days} dní bez schválení)")
    lines = [f"Sada #{sid}: vypršely nevybrané varianty " + ", ".join(f"#{i}" for i in ids)
             for sid, ids in expired.items()]
    return lines + withdrawn + soon


def mark_reminded(conn, set_id: int) -> None:
    """Připomínka sady odešla, znovu se neposílá."""
    set_state(conn, f"{REMINDED_KEY}{set_id}", "1")


def mark_video_sent(conn, id: int, msg_id: int) -> None:
    """Uloží id zprávy s videem hned po odeslání (stav se nemění)."""
    with _tx(conn), conn.cursor() as cur:
        cur.execute("UPDATE reel_queue SET telegram_video_msg_id=%s WHERE id=%s", (msg_id, id))


def defer_send(conn, id: int, error: str, now: datetime) -> int:
    """Odeslání se nepovedlo: odloží jednotku (R4) a vrátí nový počet pokusů.

    Odklad SEND_BACKOFF podle pokusu platí pro položku i pro ostatní queued varianty její sady (sada se
    posílá jako celek). Od SEND_MAX_ATTEMPTS pokusů jdou queued položky jednotky do stavu held.
    """
    with _tx(conn), conn.cursor() as cur:
        cur.execute("SELECT send_attempts + 1, set_id FROM reel_queue WHERE id=%s FOR UPDATE", (id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"položka {id} neexistuje")
        n, set_id = row
        mins = SEND_BACKOFF[min(n - 1, len(SEND_BACKOFF) - 1)]
        unit = "(id = %(id)s OR (%(set)s::bigint IS NOT NULL AND set_id = %(set)s::bigint AND status = 'queued'))"
        cur.execute(
            f"UPDATE reel_queue SET send_attempts=%(n)s, send_error=%(err)s, "
            f"next_send_at=%(now)s::timestamptz + make_interval(mins => %(mins)s) WHERE {unit}",
            {"id": id, "set": set_id, "n": n, "err": error, "now": now, "mins": mins},
        )
        if n >= SEND_MAX_ATTEMPTS:
            cur.execute(f"UPDATE reel_queue SET status='held' WHERE {unit} AND status='queued'",
                        {"id": id, "set": set_id})
    return n


def held_items(conn) -> list[Reel]:
    """Položky odložené po opakovaných chybách odeslání (čekají na sendretry nebo drop)."""
    return by_status(conn, ("held",))


def next_autopilot(conn) -> Reel | None:
    """Nejstarší schválená položka autopilota (čeká na zveřejnění bez Schválit)."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM reel_queue WHERE status='approved' AND autopilot ORDER BY id LIMIT 1")
        r = _row(cur)
    conn.commit()
    return r


def autopilot_to_approval(conn) -> list[int]:
    """Vypnutý autopilot: dosud nezveřejněné položky autopilota (jen approved) jdou ke schválení jako běžné.

    Položka, která se už zveřejňuje (publishing), zůstane beze změny.
    """
    with _tx(conn), conn.cursor() as cur:
        cur.execute("UPDATE reel_queue SET status='queued', autopilot=false, sent_at=NULL, decided_at=NULL "
                    "WHERE status='approved' AND autopilot RETURNING id")
        ids = sorted(r[0] for r in cur.fetchall())
    return ids


def by_telegram_msg(conn, msg_id: int) -> Reel | None:
    """Položka, k níž patří zpráva v Telegramu (video nebo náhled s tlačítky)."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_COLS} FROM reel_queue WHERE telegram_video_msg_id=%s OR telegram_msg_id=%s "
            "OR telegram_landscape_msg_id=%s ORDER BY id DESC LIMIT 1",
            (msg_id, msg_id, msg_id),
        )
        r = _row(cur)
    conn.commit()
    return r


def by_status(conn, statuses: tuple[str, ...]) -> list[Reel]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM reel_queue WHERE status = ANY(%s) ORDER BY id", (list(statuses),))
        out = [Reel(*r) for r in cur.fetchall()]
    conn.commit()
    return out


def set_items(conn, set_id: int) -> list[Reel]:
    """Položky jedné sady podle čísla varianty."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM reel_queue WHERE set_id=%s ORDER BY variant, id", (set_id,))
        out = [Reel(*r) for r in cur.fetchall()]
    conn.commit()
    return out


def local_date(conn, at: datetime) -> date:
    """Den v Europe/Prague pro okamžik at (časová pásma z PostgreSQL, obraz nepotřebuje tzdata)."""
    with conn.cursor() as cur:
        cur.execute("SELECT (%s::timestamptz AT TIME ZONE 'Europe/Prague')::date", (at,))
        d = cur.fetchone()[0]
    conn.commit()
    return d


def sets_sent_on(conn, day: date) -> int:
    """Kolik automatických sad (source 'fresh') odešlo ke schválení v daném dni (Europe/Prague);
    samostatná videa a vyžádané sady (manual, auto_pick z /vyber) se nepočítají."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(DISTINCT set_id) FROM reel_queue WHERE set_id IS NOT NULL AND sent_at IS NOT NULL "
            "AND source = %s AND NOT autopilot "
            "AND (sent_at AT TIME ZONE 'Europe/Prague')::date = %s",
            (AUTO_SOURCE, day),
        )
        n = cur.fetchone()[0]
    conn.commit()
    return n


def counts(conn) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT status, count(*) FROM reel_queue GROUP BY status")
        out = {s: n for s, n in cur.fetchall()}
    conn.commit()
    return out


def transition(conn, id: int, from_status: str | tuple[str, ...], to_status: str, **fields) -> bool:
    """Atomický přechod: proběhne jen z očekávaného stavu, vrací, zda se změnil řádek."""
    _check(fields)
    if to_status not in STATUSES:
        raise ValueError(f"neznámý stav: {to_status}")
    froms = [from_status] if isinstance(from_status, str) else list(from_status)
    sets = ["status=%s", *(f"{k}=%s" for k in fields)]
    with _tx(conn), conn.cursor() as cur:
        cur.execute(
            f"UPDATE reel_queue SET {', '.join(sets)} WHERE id=%s AND status = ANY(%s) RETURNING id",
            (to_status, *fields.values(), id, froms),
        )
        done = cur.fetchone() is not None
    return done


def approve(conn, id: int, decided_at) -> bool:
    """Schválení: pending → approved. Jedno Schválit platí pro video na výšku i na šířku; šířku vlastník
    znovu neschvaluje, ani když dorazí později (rozhodnutí 2026-11-24). Atomické v jednom UPDATE."""
    with _tx(conn), conn.cursor() as cur:
        cur.execute(
            "UPDATE reel_queue SET status='approved', decided_at=%s WHERE id=%s AND status='pending' RETURNING id",
            (decided_at, id),
        )
        return cur.fetchone() is not None


def set_fields(conn, id: int, **fields) -> None:
    if not fields:
        return
    _check(fields)
    sets = ", ".join(f"{k}=%s" for k in fields)
    with _tx(conn), conn.cursor() as cur:
        cur.execute(f"UPDATE reel_queue SET {sets} WHERE id=%s", (*fields.values(), id))


def set_landscape(conn, id: int, path: str, sha256: str) -> bool:
    """Doplní nebo vymění video na šířku; jen u queued a pending. Vrací, zda se řádek změnil."""
    with _tx(conn), conn.cursor() as cur:
        cur.execute(
            "UPDATE reel_queue SET video_landscape_path=%s, video_landscape_sha256=%s, "
            "telegram_landscape_msg_id=NULL, telegram_landscape_sha256=NULL WHERE id=%s AND status IN ('queued','pending') RETURNING id",
            (path, sha256, id),
        )
        return cur.fetchone() is not None


def set_landscape_sent(conn, id: int, msg_id: int, sha256: str | None) -> bool:
    """Zapíše odeslanou šířku, jen když je v řádku pořád tentýž soubor (podle otisku)."""
    with _tx(conn), conn.cursor() as cur:
        cur.execute(
            "UPDATE reel_queue SET telegram_landscape_msg_id=%s, telegram_landscape_sha256=%s "
            "WHERE id=%s AND video_landscape_sha256 IS NOT DISTINCT FROM %s RETURNING id",
            (msg_id, sha256, id, sha256),
        )
        return cur.fetchone() is not None


def sha_in_use(conn, sha: str) -> bool:
    """Patří otisk některému řádku fronty (jako video na výšku nebo na šířku)?"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM reel_queue WHERE video_sha256=%s OR video_landscape_sha256=%s LIMIT 1",
            (sha, sha),
        )
        found = cur.fetchone() is not None
    conn.commit()
    return found


def recover_after_restart(conn) -> list[int]:
    """Publikace přerušená restartem má nejistý výsledek: publishing -> unknown."""
    with _tx(conn), conn.cursor() as cur:
        cur.execute("UPDATE reel_queue SET status='unknown' WHERE status='publishing' RETURNING id")
        ids = sorted(r[0] for r in cur.fetchall())
    return ids


def _parse_picked(value: str | None) -> set[int]:
    """Hodnota set_picked:<set_id>: "12" (dřívější jediný výběr) i "12,15"; neplatné části se vynechají."""
    out = set()
    for part in (value or "").split(","):
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


def picked(conn, set_id: int) -> set[int]:
    """Id variant vybraných ze sady, prázdná množina = ještě bez výběru."""
    return _parse_picked(get_state(conn, f"{PICKED_KEY}{set_id}"))


def mark_picked(conn, set_id: int, reel_id: int) -> None:
    """Přidá variantu k vybraným (bot zpracovává tlačítka jedno po druhém)."""
    ids = picked(conn, set_id) | {reel_id}
    set_state(conn, f"{PICKED_KEY}{set_id}", ",".join(str(i) for i in sorted(ids)))


def get_state(conn, key: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM reel_state WHERE key=%s", (key,))
        r = cur.fetchone()
    conn.commit()
    return r[0] if r else None


def set_state(conn, key: str, value: str) -> None:
    with _tx(conn), conn.cursor() as cur:
        cur.execute(
            "INSERT INTO reel_state (key, value) VALUES (%s,%s) "
            "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
            (key, value),
        )


def state_keys(conn, prefix: str) -> list[str]:
    """Klíče reel_state začínající prefixem (prefix se bere doslova, bez zástupných znaků LIKE)."""
    with conn.cursor() as cur:
        cur.execute("SELECT key FROM reel_state WHERE left(key, %s) = %s ORDER BY key", (len(prefix), prefix))
        out = [r[0] for r in cur.fetchall()]
    conn.commit()
    return out


def delete_state(conn, key: str) -> None:
    with _tx(conn), conn.cursor() as cur:
        cur.execute("DELETE FROM reel_state WHERE key=%s", (key,))
