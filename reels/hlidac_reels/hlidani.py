"""Hlídání čerstvosti dat (audit aktualizace dat 2026-11-21, díry 1, 2, 3, 5, 7).

Kontroly nad databází webu (PostgreSQL), bez sítě:

- synchronizace s psp.cz (`sync_beh`, dřív jen meta `last_sync`): poslední úspěšný běh starší než 2 h,
- stenoprotokoly (`steno.beh`): poslední úspěšný běh starší než 3 h,
- AI rozbor (fronta jako `ai_api.php?action=queue`, jen nové texty): nejstarší čekající text starší než 12 h,
  nebo poslední výsledek starší než 24 h a klient AI se na frontu neptal 24 h (meta `ai_queue_seen_at`;
  bez ní platí jen stáří výsledku),
- schůze bez `rec` (řečník–bod z otevřených dat) déle než 7 dní po posledním dni jednání,
- tisk ve fázi Vyhlášeno bez čísla Sbírky déle než 14 dní,
- tisk měnící jednací řád (90/1995 Sb.) nebo Ústavu (1/1993 Sb.) ve 3. čtení a dál: podklady generátoru
  (`generator/examples/lhuty.md`, `pravidla.md`) pak můžou být neplatné,
- PC s generátorem Reels (`worker_seen_at`) neviděno 24 h: jen v denním souhrnu, PC nemusí běžet pořád,
- Reels jen do souhrnu a `/stav` (řádek přehledu „Reels ke schválení“ vždy): jednotka čekající na rozhodnutí vlastníka
  déle než 24 h, položky held, pozastavené PC (`worker_state`), vydaná zadání bez známky života (`jobs.silent`);
  held, pozastavení a vydání bez známky života mají vlastní zprávu (F1, F3), nerozhodnuté jednotky připomíná F1,
- Reels s výstrahou: naléhavé zprávy generátoru (`reel_events` urgency 1) neodeslané déle než 1 h, a PC bez
  heartbeatu déle než `jobs.WORKER_STALE`, když čeká vyžádané zadání (manual, auto_pick); pozastavené PC
  (`worker_state` paused s `until`) pošle heartbeat jen na začátku pauzy a spí do `until`, výpadek se mu proto
  počítá až od `until` + `jobs.WORKER_STALE`.

Bot volá `zkontroluj` každou hodinu: nový nález (nebo změněný podpis, např. jiná fáze tisku) pošle jednou
jako výstrahu, nález, který zmizel, jako „obnoveno“. Stav drží reel_state `hlidani_aktivni` (klíč nálezu →
podpis), poslední výsledek pro MCP `stav_dat` reel_state `hlidani_vystrahy` (jen čerstvost dat, bez nálezů
Reels: MCP je veřejné). Denní souhrn jde v 8:00 Europe/Prague, `/stav` ho pošle na požádání.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import events, jobs, messages, queue

ACTIVE_KEY = "hlidani_aktivni"
SNAPSHOT_KEY = "hlidani_vystrahy"
SUMMARY_DAY_KEY = "hlidani_souhrn_den"
WORKER_SEEN_KEY = "worker_seen_at"
WORKER_STATE_KEY = "worker_state"  # JSON {state, reason, detail, until, at} z heartbeat workeru
SUMMARY_HOUR = 8  # Europe/Prague

SYNC_MAX = timedelta(hours=2)
STENO_MAX = timedelta(hours=3)
AI_WAIT_MAX = timedelta(hours=12)
AI_RESULT_MAX = timedelta(hours=24)
WORKER_MAX = timedelta(hours=24)
REELS_UNDECIDED_MAX = timedelta(hours=24)
REELS_EVENTS_MAX = timedelta(hours=1)
# stavy jednotek fronty (queue.Unit.state), které čekají na rozhodnutí vlastníka
REELS_OWNER_STATES = ("approval", "untouched", "picked", "remaining")
# vyžádaná zadání (vlastník na ně čeká); fresh je automatické
REELS_REQUESTED = ("manual", "auto_pick")
# klíče nálezů o provozu Reels: do Telegramu a /stav ano, do veřejného snapshotu pro MCP stav_dat ne
REELS_PREFIX = "reels_"
REC_DAYS = 7
SBIRKA_DAYS = 14
# vyhlášený tisk měnící podklady se v souhrnu ukazuje ještě tolik dní
PODKLADY_SUMMARY_DAYS = 30
PODKLADY_ZAKONY = ("90/1995", "1/1993")
PODKLADY_FAZE = ("3. čtení", "Schváleno Sněmovnou", "V Senátu", "Sněmovna po Senátu", "U prezidenta",
                 "Sněmovna po vetu", "Vyhlášeno")
# schéma stenoprotokolů (služba rag/); testy ho přesměrují
STENO = "steno"

# řetězec PHP 'Y-m-d H:i:s' v místním čase → timestamptz
_LOCAL = "(NULLIF({}, '')::timestamp AT TIME ZONE 'Europe/Prague')"


@dataclass(frozen=True)
class Finding:
    key: str            # např. sync, steno, ai, rec:36, sbirka:144, podklady:47
    text: str           # řádek pro Telegram
    sig: str = "1"      # změna podpisu = nová výstraha (fáze tisku)
    recover: bool = True  # po zmizení poslat „obnoveno“
    alert: bool = True    # False = jen v denním souhrnu (PC s generátorem)
    summary: bool = True  # False = do souhrnu nepatří (starý vyhlášený tisk), stav se drží dál


@dataclass
class Status:
    lines: list[str]          # přehled (vždy)
    findings: list[Finding]   # problémy


def _one(conn, sql: str, params=()) -> tuple | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    conn.commit()
    return row


def _all(conn, sql: str, params=()) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    conn.commit()
    return rows


def _exists(conn, name: str) -> bool:
    return bool(_one(conn, "SELECT to_regclass(%s) IS NOT NULL", (name,))[0])


def fmt(conn, at: datetime | None) -> str:
    """Čas v Europe/Prague jako „21. 11. 13:05“ (přes PostgreSQL, obraz nemá tzdata)."""
    if at is None:
        return "nikdy"
    return _one(conn, "SELECT to_char(%s::timestamptz AT TIME ZONE 'Europe/Prague', 'FMDD. FMMM. HH24:MI')",
                (at,))[0]


def _when(conn, at: datetime) -> str:
    """Čas v Europe/Prague jako „20. 11. 9:05“: hodina bez úvodní nuly jako ve zprávách bota Reels."""
    return _one(conn, "SELECT to_char(%s::timestamptz AT TIME ZONE 'Europe/Prague', 'FMDD. FMMM. FMHH24:MI')",
                (at,))[0]


def _parse_time(raw) -> datetime | None:
    """ISO čas z reel_state; nečitelný = None, bez časové zóny = UTC (jako bot._parse_time)."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        t = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)


def _worker_seen(conn) -> datetime | None:
    return _parse_time(queue.get_state(conn, WORKER_SEEN_KEY))


def _worker_state(conn) -> dict | None:
    """reel_state worker_state ({state, reason, detail, until, at}); nečitelný nebo jiný než objekt = None."""
    try:
        state = json.loads(queue.get_state(conn, WORKER_STATE_KEY) or "null")
    except ValueError:
        return None
    return state if isinstance(state, dict) else None


def _age(now: datetime, at: datetime) -> str:
    hours = (now - at).total_seconds() / 3600
    return f"{hours:.0f} h" if hours < 48 else f"{hours / 24:.0f} dní"


# --- jednotlivé kontroly ------------------------------------------------------

def _sync(conn, now: datetime, st: Status) -> None:
    last = failed = None
    if _exists(conn, "sync_beh"):
        last = _one(conn, f"SELECT max({_LOCAL.format('konec')}) FROM sync_beh WHERE ok = 1")[0]
        row = _one(conn, f"SELECT {_LOCAL.format('konec')} FROM sync_beh WHERE ok = 0 ORDER BY id DESC LIMIT 1")
        failed = row[0] if row else None
    if last is None and _exists(conn, "meta"):
        # před první historií běhů: čas posledního importu
        last = _one(conn, f"SELECT {_LOCAL.format('v')} FROM meta WHERE k = 'last_sync'") or (None,)
        last = last[0]
    line = f"Synchronizace s psp.cz: poslední úspěšný běh {fmt(conn, last)}"
    if failed is not None and (last is None or failed > last):
        line += f", poslední běh skončil chybou ({fmt(conn, failed)})"
    st.lines.append(line)
    if last is None or now - last > SYNC_MAX:
        age = f"před {_age(now, last)}" if last else "žádný"
        st.findings.append(Finding("sync", f"Synchronizace s psp.cz stojí: poslední úspěšný běh {age} "
                                           f"({fmt(conn, last)})."))


def _steno(conn, now: datetime, st: Status) -> None:
    if not _exists(conn, f"{STENO}.beh"):
        return
    last = _one(conn, f"""SELECT max(konec) FROM {STENO}.beh WHERE konec IS NOT NULL
                          AND NOT coalesce(vysledek ? 'error', false)
                          AND coalesce(vysledek->>'nedokonceno', 'false') <> 'true'""")[0]
    st.lines.append(f"Stenoprotokoly: poslední úspěšný běh {fmt(conn, last)}")
    if last is None or now - last > STENO_MAX:
        age = f"před {_age(now, last)}" if last else "žádný"
        st.findings.append(Finding("steno", f"Stenoprotokoly (rag-ingest) stojí: poslední úspěšný běh {age} "
                                            f"({fmt(conn, last)})."))


def ai_queue(conn) -> dict | None:
    """Fronta AI rozboru jako ai_api.php?action=queue (nové texty: ai_at prázdné, text je, tisk není vyhlášený).

    Opakované rozbory po změně promptu a ověření se nepočítají (nejsou naléhavé). None = tabulky webu chybí.
    """
    if not _exists(conn, "tisk_text") or not _exists(conn, "pn_text"):
        return None
    live = "EXISTS (SELECT 1 FROM tisky t WHERE t.ct = x.ct AND t.faze <> 'Vyhlášeno')"
    row = _one(conn, f"""
        SELECT count(*), min(cekajici_od) FROM (
            SELECT {_LOCAL.format('x.fetched_at')} AS cekajici_od FROM tisk_text x
             WHERE x.ai_at IS NULL AND x.chars > 0 AND {live}
            UNION ALL
            SELECT {_LOCAL.format('x.fetched_at')} FROM pn_text x
             WHERE x.ai_at IS NULL AND x.chars > 0 AND {live}
               AND EXISTS (SELECT 1 FROM pn p WHERE p.id_dokument = x.id_dokument)) q""")
    last = _one(conn, f"""SELECT greatest((SELECT max({_LOCAL.format('ai_at')}) FROM tisk_text),
                                          (SELECT max({_LOCAL.format('ai_at')}) FROM pn_text))""")[0]
    seen = None
    if _exists(conn, "meta"):
        r = _one(conn, f"SELECT {_LOCAL.format('v')} FROM meta WHERE k = 'ai_queue_seen_at'")
        seen = r[0] if r else None
    return {"cekajicich": row[0], "nejstarsi": row[1], "posledni_vysledek": last, "klient_naposledy": seen}


def _ai(conn, now: datetime, st: Status) -> None:
    q = ai_queue(conn)
    if q is None:
        return
    line = f"AI rozbor: ve frontě {q['cekajicich']}"
    if q["nejstarsi"] is not None:
        line += f" (nejstarší od {fmt(conn, q['nejstarsi'])})"
    line += f", poslední výsledek {fmt(conn, q['posledni_vysledek'])}"
    if q["klient_naposledy"] is not None:
        line += f", klient se ptal {fmt(conn, q['klient_naposledy'])}"
    st.lines.append(line)
    problems = []
    if q["nejstarsi"] is not None and now - q["nejstarsi"] > AI_WAIT_MAX:
        problems.append(f"{q['cekajicich']} textů čeká na rozbor, nejstarší {_age(now, q['nejstarsi'])}")
    last, seen = q["posledni_vysledek"], q["klient_naposledy"]
    if (last is None or now - last > AI_RESULT_MAX) and (seen is None or now - seen > AI_RESULT_MAX):
        problems.append(f"poslední výsledek {fmt(conn, last)}"
                        + (f", klient AI se neptal od {fmt(conn, seen)}" if seen else ""))
    if problems:
        st.findings.append(Finding("ai", "AI rozbor stojí: " + "; ".join(problems) + "."))


def _rec(conn, now: datetime, st: Status) -> None:
    if not _exists(conn, f"{STENO}.schuze") or not _exists(conn, f"{STENO}.rec"):
        return
    rows = _all(conn, f"""
        SELECT s.schuze, s."do" FROM {STENO}.schuze s
         WHERE s.obdobi = (SELECT max(obdobi) FROM {STENO}.schuze)
           AND s."do" IS NOT NULL
           AND (s."do" AT TIME ZONE 'Europe/Prague') < %s::timestamptz - make_interval(days => %s)
           AND NOT EXISTS (SELECT 1 FROM {STENO}.rec r WHERE r.obdobi = s.obdobi AND r.schuze = s.schuze)
         ORDER BY s.schuze""", (now, REC_DAYS))
    for schuze, do in rows:
        st.findings.append(Finding(f"rec:{schuze}", f"Schůze {schuze} (jednala do {do:%d. %m.}) nemá v otevřených "
                                                    f"datech psp.cz údaje řečník–bod (rec) ani po {REC_DAYS} dnech; "
                                                    "projevy jsou k tiskům navázané jen podle nadpisu."))


def _sbirka(conn, now: datetime, st: Status) -> None:
    if not _exists(conn, "tisky") or not _exists(conn, "kroky"):
        return
    rows = _all(conn, f"""
        SELECT t.ct, min(coalesce(k.od, {_LOCAL.format('t.posledni')})) FROM tisky t
          LEFT JOIN LATERAL (SELECT min({_LOCAL.format('datum')}) AS od FROM kroky
                              WHERE kroky.ct = t.ct AND kroky.typ_stavu = 11) k ON true
         WHERE t.faze = 'Vyhlášeno' AND coalesce(t.sbirka, '') = ''
         GROUP BY t.ct
        HAVING min(coalesce(k.od, {_LOCAL.format('t.posledni')})) < %s::timestamptz - make_interval(days => %s)
         ORDER BY t.ct""", (now, SBIRKA_DAYS))
    for ct, od in rows:
        st.findings.append(Finding(f"sbirka:{ct}", f"Tisk {ct} je ve fázi Vyhlášeno od {fmt(conn, od).removesuffix(" 00:00")}, ale nemá "
                                                   "číslo Sbírky (tisk_podle_sbirky ho nenajde)."))


def _podklady(conn, now: datetime, st: Status) -> None:
    """Tisky měnící jednací řád nebo Ústavu (jako MCP tisky_menici_zakon: název, PN, celky textů)."""
    if not _exists(conn, "tisky"):
        return
    parts, params = [], []
    for zakon in PODKLADY_ZAKONY:
        parts.append("SELECT ct, %s AS zakon FROM tisky WHERE ',' || zakony || ',' LIKE %s")
        params += [zakon, f"%,{zakon},%"]
        if _exists(conn, "pn_text"):
            parts.append("SELECT ct, %s FROM pn_text WHERE ',' || laws || ',' LIKE %s")
            params += [zakon, f"%,{zakon},%"]
        if _exists(conn, "texty.cel"):
            parts.append("SELECT d.ct, %s FROM texty.cel c JOIN texty.dokument d ON d.id = c.dokument_id "
                         "WHERE c.meni_zakon = %s")
            params += [zakon, zakon]
    rows = _all(conn, f"""
        WITH x AS ({' UNION '.join(parts)})
        SELECT t.ct, min(t.faze), string_agg(DISTINCT x.zakon, ', ' ORDER BY x.zakon),
               max({_LOCAL.format('t.posledni')})
          FROM x JOIN tisky t ON t.ct = x.ct
         WHERE t.faze = ANY(%s)
         GROUP BY t.ct ORDER BY t.ct""", (*params, list(PODKLADY_FAZE)))
    for ct, faze, zakony, posledni in rows:
        # vyhlášený před víc než 30 dny: zůstává ve stavu (výstraha už odešla), souhrn ho neopakuje
        recent = faze != "Vyhlášeno" or posledni is None or now - posledni < timedelta(days=PODKLADY_SUMMARY_DAYS)
        st.findings.append(Finding(
            f"podklady:{ct}",
            f"Tisk {ct} mění {zakony} Sb. a je ve fázi {faze}: zkontroluj podklady generátoru Reels "
            "(generator/examples/lhuty.md, pravidla.md).",
            sig=faze, recover=False, summary=recent))


def _worker(conn, now: datetime, st: Status) -> None:
    seen = _worker_seen(conn)
    st.lines.append(f"PC s generátorem Reels: naposledy {fmt(conn, seen)}")
    if seen is None or now - seen > WORKER_MAX:
        st.findings.append(Finding("worker", f"PC s generátorem Reels nebylo vidět {_age(now, seen) if seen else 'nikdy'}"
                                             " (úloha cosepece-reels-worker).", alert=False))


def _unit_name(u: queue.Unit) -> str:
    return f"sada #{u.set_id}" if u.set_id is not None else f"video #{u.label}"


def _unit_names(units: list[queue.Unit]) -> list[str]:
    """Názvy jednotek od nejstarší; sada s víc řádky (vybrané a zbylé varianty) jen jednou."""
    return list(dict.fromkeys(_unit_name(u) for u in sorted(units, key=lambda u: u.since)))


def _job_what(job: jobs.Job) -> str:
    """Popis zadání jako bot._job_what („PN 2376+2377, tisk 300“); bot importuje hlidani, opačně import nejde."""
    if job.ct is None:
        return "automatický výběr"
    if job.focus_pn:
        return "PN " + "+".join(str(n) for n in job.focus_pn) + f", tisk {job.ct}"
    return f"tisk {job.ct}"


def _reels_waiting(conn, now: datetime, st: Status) -> None:
    """Jednotky Reels čekající na vlastníka: řádek přehledu vždy, déle než 24 h nález jen do souhrnu
    (nerozhodnuté jednotky připomíná F1)."""
    if not _exists(conn, "reel_queue"):
        return
    waiting = [u for u in queue.open_units(conn, now) if u.state in REELS_OWNER_STATES]
    if not waiting:
        st.lines.append("Reels ke schválení: nic nečeká")
        return
    oldest = min(waiting, key=lambda u: u.since)
    since = _when(conn, oldest.since)
    st.lines.append(f"Reels ke schválení: {len(_unit_names(waiting))} (nejstarší {_unit_name(oldest)} od {since})")
    old = [u for u in waiting if now - u.since > REELS_UNDECIDED_MAX]
    if old:
        st.findings.append(Finding("reels_ceka", f"Reels bez rozhodnutí déle než 24 h: {', '.join(_unit_names(old))} "
                                                 f"(nejstarší od {since}).", alert=False))


def _reels_held(conn, now: datetime, st: Status) -> None:
    """Položky, které nejde doručit do Telegramu (held): vlastní zprávu má F1, tady jen souhrn."""
    if not _exists(conn, "reel_queue"):
        return
    held = [u for u in queue.open_units(conn, now) if u.state == "held"]
    if held:
        st.findings.append(Finding("reels_held", f"Reels nejde doručit do Telegramu: {', '.join(_unit_names(held))} "
                                                 "(/fronta: Zkusit znovu / Zahodit).", alert=False))


def _reels_paused(conn, now: datetime, st: Status) -> None:
    """Pozastavené PC (reel_state worker_state): zprávu o pozastavení posílá F3, tady jen souhrn."""
    state = _worker_state(conn)
    if state is None or state.get("state") != "paused":
        return
    until = _parse_time(state.get("until"))
    st.findings.append(Finding("reels_pauza", messages.worker_paused_text(
        state.get("reason"), state.get("detail"), jobs.hm(conn, until) if until is not None else None), alert=False))


def _reels_silent(conn, now: datetime, st: Status) -> None:
    """Vydaná zadání bez známky života (jobs.silent): zprávu posílá F3, tady jen souhrn, všechna v jednom nálezu."""
    if not _exists(conn, "reel_jobs"):
        return
    silent = jobs.silent(conn, now)
    if not silent:
        return
    seen = _worker_seen(conn)
    parts = []
    for job in silent:
        # poslední známka života jako bot._check_silent: fáze, vydání nebo heartbeat workeru
        since = max((t for t in (job.stage_at, job.claimed_at, seen) if t is not None), default=job.created_at)
        parts.append(f"Zadání #{job.id} ({_job_what(job)}) bez známky života od {jobs.hm(conn, since)}.")
    st.findings.append(Finding("reels_ticho", " ".join(parts), alert=False))


def _reels_events(conn, now: datetime, st: Status) -> None:
    """Naléhavé zprávy generátoru (reel_events urgency 1), které bot neodeslal déle než REELS_EVENTS_MAX."""
    if not _exists(conn, "reel_events"):
        return
    count, oldest = _one(conn, "SELECT count(*), min(at) FROM reel_events "
                               "WHERE urgency = %s AND notified_at IS NULL AND at < %s",
                         (events.URGENT, now - REELS_EVENTS_MAX))
    if not count:
        return
    if count == 1:
        what = "1 zpráva generátoru Reels čeká"
    elif count <= 4:
        what = f"{count} zprávy generátoru Reels čekají"
    else:
        what = f"{count} zpráv generátoru Reels čeká"
    st.findings.append(Finding("reels_udalosti",
                               f"{what} na odeslání déle než 1 h (nejstarší od {_when(conn, oldest)})."))


def _reels_pc(conn, now: datetime, st: Status) -> None:
    """PC bez heartbeatu déle než jobs.WORKER_STALE, když čeká vyžádané zadání (manual, auto_pick).

    Pozastavený worker (limit, chyba prostředí) pošle heartbeat se stavem paused a `until` jednou a pak spí až
    do `until` (worker._resume), teprve potom sonda a další heartbeat: do `until` + WORKER_STALE se proto
    výpadek nehlásí (zprávu o pozastavení posílá F3). Pauza bez `until` (chybí nástroje) heartbeat posílá dál,
    výjimku nepotřebuje. Podpis se s novými zadáními nemění: jedna výstraha za výpadek, „obnoveno“ až po
    heartbeatu, nebo když už žádné vyžádané zadání nečeká."""
    if not _exists(conn, "reel_jobs"):
        return
    ids = [r[0] for r in _all(conn, "SELECT id FROM reel_jobs WHERE status = ANY(%s) AND source = ANY(%s) "
                                    "ORDER BY id", (list(jobs.ACTIVE), list(REELS_REQUESTED)))]
    if not ids:
        return
    seen = _worker_seen(conn)
    if seen is not None and now - seen <= jobs.WORKER_STALE:
        return
    state = _worker_state(conn)
    until = _parse_time(state.get("until")) if state is not None and state.get("state") == "paused" else None
    if until is not None and now <= until + jobs.WORKER_STALE:
        return
    who = f"PC s generátorem neodpovídá od {_when(conn, seen)}" if seen else "PC s generátorem zatím nebylo vidět"
    what = "čekají vyžádaná zadání" if len(ids) > 1 else "čeká vyžádané zadání"
    st.findings.append(Finding("reels_pc", f"{who} a {what}: {', '.join(f'#{i}' for i in ids)}."))


CHECKS = (_sync, _steno, _ai, _worker, _reels_waiting, _reels_held, _reels_paused, _reels_silent, _reels_events,
          _reels_pc, _rec, _sbirka, _podklady)


def status(conn, now: datetime) -> Status:
    st = Status([], [])
    for check in CHECKS:
        check(conn, now, st)
    return st


# --- výstrahy a souhrn ----------------------------------------------------------

def _load_active(conn) -> dict[str, str]:
    try:
        data = json.loads(queue.get_state(conn, ACTIVE_KEY) or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def check(conn, now: datetime) -> tuple[list[Finding], list[str]]:
    """Hodinová kontrola: (nové nebo změněné nálezy k výstraze, texty obnovených).

    Stav se uloží hned; když se zpráva nepošle, výstraha se neopakuje (stejně jako ostatní hlášení bota
    se raději ztratí jedna zpráva, než aby chodila každou hodinu)."""
    st = status(conn, now)
    current = {f.key: f for f in st.findings if f.alert}
    active = _load_active(conn)
    new = [f for f in current.values() if active.get(f.key) != f.sig]
    gone = [k for k in active if k not in current]
    recovered = [k for k in gone if not k.startswith("podklady:")]
    queue.set_state(conn, ACTIVE_KEY, json.dumps({k: f.sig for k, f in current.items()}, ensure_ascii=False))
    # veřejný MCP stav_dat ukazuje čerstvost dat: provoz Reels (zprávy, PC) do snapshotu nepatří
    queue.set_state(conn, SNAPSHOT_KEY, json.dumps(
        {"at": now.isoformat(), "vystrahy": [f.text for f in st.findings if f.alert and f.summary
                                             and not f.key.startswith(REELS_PREFIX)]},
        ensure_ascii=False))
    return new, [_RECOVERED.get(k.split(":")[0], "{}").format(k.partition(":")[2] or k) for k in recovered]


_RECOVERED = {
    "sync": "Synchronizace s psp.cz zase běží.",
    "steno": "Stenoprotokoly (rag-ingest) zase běží.",
    "ai": "AI rozbor zase běží.",
    "rec": "Schůze {} už má údaje řečník–bod (rec).",
    "sbirka": "Tisk {} už má číslo Sbírky.",
    "reels_udalosti": "Zprávy generátoru Reels zase odcházejí do Telegramu.",
    "reels_pc": "PC s generátorem se ozvalo, nebo už nečeká žádné vyžádané zadání.",
}


def alert_text(new: list[Finding], recovered: list[str]) -> str | None:
    parts = []
    if new:
        parts.append("Výstraha – data:\n" + "\n".join(f"- {f.text}" for f in new))
    if recovered:
        parts.append("Obnoveno:\n" + "\n".join(f"- {t}" for t in recovered))
    return "\n\n".join(parts) or None


def summary_text(conn, now: datetime) -> str:
    """Denní souhrn i odpověď na /stav: přehled a všechny aktuální problémy (i ty, které jen ve souhrnu)."""
    st = status(conn, now)
    problems = [f for f in st.findings if f.summary]
    text = f"Stav dat k {fmt(conn, now)}:\n" + "\n".join(f"- {line}" for line in st.lines)
    if problems:
        text += "\n\nProblémy:\n" + "\n".join(f"- {f.text}" for f in problems)
    else:
        text += "\n\nVše v pořádku."
    return text


def summary_due(conn, now: datetime) -> str | None:
    """Den (Europe/Prague), za který má odejít denní souhrn; None = už odešel, nebo ještě není 8:00."""
    day, hour = _one(conn, "SELECT to_char(%s::timestamptz AT TIME ZONE 'Europe/Prague', 'YYYY-MM-DD'), "
                           "extract(hour FROM %s::timestamptz AT TIME ZONE 'Europe/Prague')::int", (now, now))
    if hour < SUMMARY_HOUR or queue.get_state(conn, SUMMARY_DAY_KEY) == day:
        return None
    return day


def mark_summary(conn, day: str) -> None:
    queue.set_state(conn, SUMMARY_DAY_KEY, day)
