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
- PC s generátorem Reels (`worker_seen_at`) neviděno 24 h: jen v denním souhrnu, PC nemusí běžet pořád.

Bot volá `zkontroluj` každou hodinu: nový nález (nebo změněný podpis, např. jiná fáze tisku) pošle jednou
jako výstrahu, nález, který zmizel, jako „obnoveno“. Stav drží reel_state `hlidani_aktivni` (klíč nálezu →
podpis), poslední výsledek pro MCP `stav_dat` reel_state `hlidani_vystrahy`. Denní souhrn jde v 8:00
Europe/Prague, `/stav` ho pošle na požádání.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import queue

ACTIVE_KEY = "hlidani_aktivni"
SNAPSHOT_KEY = "hlidani_vystrahy"
SUMMARY_DAY_KEY = "hlidani_souhrn_den"
WORKER_SEEN_KEY = "worker_seen_at"
SUMMARY_HOUR = 8  # Europe/Prague

SYNC_MAX = timedelta(hours=2)
STENO_MAX = timedelta(hours=3)
AI_WAIT_MAX = timedelta(hours=12)
AI_RESULT_MAX = timedelta(hours=24)
WORKER_MAX = timedelta(hours=24)
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
    raw = queue.get_state(conn, WORKER_SEEN_KEY)
    try:
        seen = datetime.fromisoformat(raw) if raw else None
    except ValueError:
        seen = None
    st.lines.append(f"PC s generátorem Reels: naposledy {fmt(conn, seen)}")
    if seen is None or now - seen > WORKER_MAX:
        st.findings.append(Finding("worker", f"PC s generátorem Reels nebylo vidět {_age(now, seen) if seen else 'nikdy'}"
                                             " (úloha cosepece-reels-worker).", alert=False))


CHECKS = (_sync, _steno, _ai, _worker, _rec, _sbirka, _podklady)


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
    queue.set_state(conn, SNAPSHOT_KEY, json.dumps(
        {"at": now.isoformat(), "vystrahy": [f.text for f in st.findings if f.alert and f.summary]},
        ensure_ascii=False))
    return new, [_RECOVERED.get(k.split(":")[0], "{}").format(k.partition(":")[2] or k) for k in recovered]


_RECOVERED = {
    "sync": "Synchronizace s psp.cz zase běží.",
    "steno": "Stenoprotokoly (rag-ingest) zase běží.",
    "ai": "AI rozbor zase běží.",
    "rec": "Schůze {} už má údaje řečník–bod (rec).",
    "sbirka": "Tisk {} už má číslo Sbírky.",
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
