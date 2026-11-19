"""Stahování částí stenoprotokolů, hlídání změn a skládání projevů.

Změny: psp.cz zveřejní část neautorizovanou a během 2–3 týdnů ji nahradí autorizovanou verzí.
Last-Modified je nepoužitelné (stránky se přegenerovávají), proto se porovnává hash obsahu včetně
hlášky o autorizaci. Každá část má termín další kontroly podle stáří a stavu (next_check).
Projev, jehož text se změnil, dostane verze + 1 a předchozí znění se uloží do steno.projev_verze.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

import psycopg

from . import parser
from .http import Fetcher
from .opendata import part_url, term_dir
from .rozprava_pravidla import druh_vystoupeni, osloveny_recnik, pn_z_textu, shoda_prijmeni, tisky_z_konce

log = logging.getLogger(__name__)


def next_check(stav: str, datum: date | None, zmeneno: datetime | None, now: datetime) -> datetime:
    """Kdy část znovu stáhnout."""
    today = now.date()
    if stav == "chybi":
        return now + timedelta(hours=6)
    if datum is None or (today - datum).days <= 2:
        return now + timedelta(hours=2)
    if stav == "neautorizovano":
        return now + timedelta(days=1)
    if zmeneno and (now - zmeneno).days <= 60:
        return now + timedelta(days=7)
    return now + timedelta(days=90)


def _ensure_part(cur, obdobi: int, schuze: int, turn: int) -> bool:
    cur.execute(
        """INSERT INTO steno.cast (obdobi, schuze, turn, url, dalsi_kontrola) VALUES (%s,%s,%s,%s, now())
           ON CONFLICT DO NOTHING""",
        (obdobi, schuze, turn, part_url(obdobi, schuze, turn)),
    )
    return cur.rowcount > 0


def discover(conn: psycopg.Connection, fetcher: Fetcher, obdobi: int, recent: int = 2) -> int:
    """Doplní části, které otevřená data ještě neznají: podle indexu a jednacích dnů posledních schůzí."""
    base = f"/eknih/{term_dir(obdobi)}/stenprot/"
    raw = fetcher.get(base + "index.htm")
    if raw is None:
        raise RuntimeError("index stenoprotokolů nenalezen")
    days = parser.parse_index(parser.page_text(raw))
    added = 0
    with conn.cursor() as cur:
        cur.execute("SELECT schuze, count(*) FROM steno.cast WHERE obdobi = %s GROUP BY schuze", (obdobi,))
        known = dict(cur.fetchall())
        last = sorted(days)[-recent:]
        for s, jds in days.items():
            if s not in known or s in last:
                for jd in jds:
                    page = fetcher.get(f"{base}{s:03d}schuz/{s}-{jd}.html")
                    if page is None:
                        continue
                    for turn in parser.parse_day_turns(parser.page_text(page)):
                        added += _ensure_part(cur, obdobi, s, turn)
    conn.commit()
    return added


@dataclass
class CrawlResult:
    fetched: int = 0
    changed: int = 0
    new: int = 0
    missing: int = 0
    errors: int = 0
    dirty: set[int] = field(default_factory=set)


def crawl(conn: psycopg.Connection, fetcher: Fetcher, obdobi: int, limit: int = 1000) -> CrawlResult:
    """Stáhne části s prošlou kontrolou, uloží změny a vrátí schůze ke složení projevů."""
    res = CrawlResult()
    with conn.cursor() as cur:
        cur.execute(
            """SELECT schuze, turn, url, hash, stav, datum, zmeneno FROM steno.cast
               WHERE obdobi = %s AND (dalsi_kontrola IS NULL OR dalsi_kontrola <= now())
               ORDER BY hash IS NOT NULL, schuze, turn LIMIT %s""",
            (obdobi, limit),
        )
        due = cur.fetchall()
    for schuze, turn, url, old_hash, old_stav, datum, zmeneno in due:
        now = datetime.now(timezone.utc)
        try:
            raw = fetcher.get(url)
        except Exception as e:  # síťová chyba jedné stránky běh nezastaví
            log.warning("chyba %s: %s", url, e)
            res.errors += 1
            with conn.cursor() as cur:
                cur.execute("UPDATE steno.cast SET chyba = %s, dalsi_kontrola = %s WHERE obdobi=%s AND schuze=%s AND turn=%s",
                            (str(e)[:500], now + timedelta(hours=1), obdobi, schuze, turn))
            conn.commit()
            continue
        res.fetched += 1
        with conn.cursor() as cur:
            if raw is None:
                res.missing += 1
                cur.execute("UPDATE steno.cast SET stav = COALESCE(stav, 'chybi'), dalsi_kontrola = %s WHERE obdobi=%s AND schuze=%s AND turn=%s",
                            (next_check("chybi", datum, zmeneno, now), obdobi, schuze, turn))
                conn.commit()
                continue
            try:
                part = parser.parse_part(parser.page_text(raw))
            except ValueError as e:
                res.errors += 1
                cur.execute("UPDATE steno.cast SET chyba = %s, dalsi_kontrola = %s WHERE obdobi=%s AND schuze=%s AND turn=%s",
                            (str(e), now + timedelta(days=1), obdobi, schuze, turn))
                conn.commit()
                continue
            stav = "konecna" if part.authorized else "neautorizovano"
            datum = datum or part.datum
            if part.next_turn:
                res.new += _ensure_part(cur, obdobi, schuze, part.next_turn)
            if part.hash != old_hash:
                if old_hash is not None:
                    res.changed += 1
                    zmeneno = now
                res.dirty.add(schuze)
                cur.execute(
                    """UPDATE steno.cast SET html = %s, hash = %s, stav = %s, stav_text = %s, datum = COALESCE(datum, %s),
                         jd = COALESCE(jd, %s), stazeno = %s, zmeneno = %s, zmen = zmen + %s, chyba = NULL,
                         dalsi_kontrola = %s
                       WHERE obdobi=%s AND schuze=%s AND turn=%s""",
                    (part.fragment, part.hash, stav, part.status_text, part.datum, part.jd, now,
                     zmeneno if old_hash else now, 1 if old_hash else 0,
                     next_check(stav, datum, zmeneno, now), obdobi, schuze, turn),
                )
            else:
                cur.execute(
                    "UPDATE steno.cast SET stazeno = %s, chyba = NULL, dalsi_kontrola = %s WHERE obdobi=%s AND schuze=%s AND turn=%s",
                    (now, next_check(stav, datum, zmeneno, now), obdobi, schuze, turn),
                )
        conn.commit()
    return res


@dataclass
class Speech:
    turn: int
    aname: int
    datum: date | None
    time: str | None
    label: str
    href_osoba: int | None
    authorized: bool
    texts: list[str] = field(default_factory=list)
    turns: list[int] = field(default_factory=list)
    votes: list[int] = field(default_factory=list)
    heading_ct: int | None = None


def build_speeches(casts: list[tuple]) -> list[Speech]:
    """Ze seřazených částí (turn, datum, jd, stav, html) složí projevy, i přes hranice částí."""
    out: list[Speech] = []
    current: Speech | None = None
    prev_turn, prev_day = None, None
    day_ct: int | None = None
    for turn, datum, jd, stav, html in casts:
        if jd != prev_day:
            current, day_ct = None, None
        elif prev_turn is not None and turn != prev_turn + 1:
            current = None  # chybí část: pokračování nepřipojovat
        prev_turn, prev_day = turn, jd
        time = parser.part_time(html)
        for seg in parser.segments_of(html):
            if seg.aname is None and current is not None:
                sp = current
                if seg.text:
                    sp.texts.append(seg.text)
                if turn not in sp.turns:
                    sp.turns.append(turn)
            else:
                sp = Speech(turn=turn, aname=seg.aname or 0, datum=datum, time=time, label=seg.label,
                            href_osoba=seg.id_osoba, authorized=(stav == "konecna"),
                            texts=[seg.text] if seg.text else [], turns=[turn], heading_ct=day_ct)
                out.append(sp)
                current = sp
            sp.votes.extend(seg.votes)
            for ct in seg.headings:
                day_ct = ct
                if sp.heading_ct is None:
                    sp.heading_ct = ct
    return out


def _speech_hash(label: str, text: str) -> str:
    return hashlib.sha256((label + "\n" + text).encode("utf-8")).hexdigest()


def assemble(conn: psycopg.Connection, obdobi: int, schuze: int) -> dict:
    """Složí projevy schůze z uložených částí a uloží rozdíly (nové, změněné, zrušené)."""
    stats = {"nove": 0, "zmenene": 0, "zrusene": 0, "smazane": 0}
    with conn.cursor() as cur:
        cur.execute(
            """SELECT turn, datum, jd, stav, html FROM steno.cast
               WHERE obdobi=%s AND schuze=%s AND html IS NOT NULL ORDER BY turn""",
            (obdobi, schuze),
        )
        speeches = build_speeches(cur.fetchall())

        cur.execute("SELECT aname, turn, id_osoba, id_bod, druh FROM steno.rec WHERE obdobi=%s AND schuze=%s",
                    (obdobi, schuze))
        rec = {(r[1], r[0]): r[2:] for r in cur.fetchall()}
        cur.execute("SELECT id_bod, ct FROM steno.bod WHERE obdobi=%s", (obdobi,))
        bod_ct = dict(cur.fetchall())
        names = _name_index(cur)
        cur.execute("SELECT id_osoba, klub, od, \"do\" FROM steno.klub_clenstvi")
        kluby: dict[int, list] = {}
        for i, k, od, do in cur.fetchall():
            kluby.setdefault(i, []).append((k, od, do))

        cur.execute(
            """SELECT turn, aname, id, hash, verze, text, autorizovano, zruseno FROM steno.projev
               WHERE obdobi=%s AND schuze=%s""",
            (obdobi, schuze),
        )
        existing = {(r[0], r[1]): r[2:] for r in cur.fetchall()}
        seen = set()
        now = datetime.now(timezone.utc)
        for poradi, sp in enumerate(speeches, 1):
            key = (sp.turn, sp.aname)
            seen.add(key)
            text = "\n\n".join(sp.texts)
            r = rec.get(key)
            id_osoba = (r[0] if r else None) or sp.href_osoba or names.get(_name_tail(sp.label, names))
            id_bod = r[1] if r else None
            if id_bod and bod_ct.get(id_bod):
                ct, ct_src = bod_ct[id_bod], "rec"
            elif sp.heading_ct:
                ct, ct_src = sp.heading_ct, "nadpis"
            else:
                ct, ct_src = None, None
            chair = (r[2] in (2, 4)) if r and r[2] else parser.is_chair(sp.label) or sp.label.startswith(
                ("Místopředseda PSP", "Místopředsedkyně PSP", "Předseda PSP", "Předsedkyně PSP"))
            name = _osoba_name(cur, id_osoba) if id_osoba else None
            klub = _club_at(kluby.get(id_osoba, []), sp.datum) if id_osoba else None
            h = _speech_hash(sp.label, text)
            url = "https://www.psp.cz" + part_url(obdobi, schuze, sp.turn) + (f"#r{sp.aname}" if sp.aname else "")
            row = dict(datum=sp.datum, cas=sp.time, poradi=poradi, id_osoba=id_osoba, recnik=sp.label or None,
                       role=parser.split_role(sp.label, name) if sp.label else None, klub=klub,
                       predsedajici=bool(chair), id_bod=id_bod, ct=ct, ct_zdroj=ct_src, text=text,
                       znaku=len(text), hash=h, autorizovano=sp.authorized, turny=sp.turns,
                       hlasovani=sorted(set(sp.votes)), url=url)
            old = existing.get(key)
            if old is None:
                cols = ", ".join(row)
                cur.execute(
                    f"INSERT INTO steno.projev (obdobi, schuze, turn, aname, {cols}) VALUES (%s,%s,%s,%s, {', '.join(['%s'] * len(row))})",
                    (obdobi, schuze, sp.turn, sp.aname, *row.values()),
                )
                stats["nove"] += 1
                continue
            pid, old_hash, verze, old_text, old_auth, zruseno = old
            sets = ", ".join(f"{k} = %s" for k in row)
            if old_hash != h:
                # uložit předchozí znění a zvýšit verzi
                cur.execute(
                    "INSERT INTO steno.projev_verze (projev_id, verze, text, hash, autorizovano) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (pid, verze, old_text, old_hash, old_auth),
                )
                cur.execute(f"UPDATE steno.projev SET {sets}, verze = verze + 1, zmeneno = %s, zruseno = NULL WHERE id = %s",
                            (*row.values(), now, pid))
                stats["zmenene"] += 1
            else:
                cur.execute(f"UPDATE steno.projev SET {sets}, zruseno = NULL WHERE id = %s", (*row.values(), pid))
        # projevy, které v nové verzi chybí: napoprvé označit, při dalším složení smazat
        for key, old in existing.items():
            if key in seen:
                continue
            pid, zruseno = old[0], old[5]
            if zruseno is None:
                cur.execute("UPDATE steno.projev SET zruseno = %s WHERE id = %s", (now, pid))
                stats["zrusene"] += 1
            else:
                cur.execute("DELETE FROM steno.projev WHERE id = %s", (pid,))
                stats["smazane"] += 1
        stats["rozprava"] = prepocitej_rozpravu(cur, obdobi, schuze)
    conn.commit()
    return stats


class _ProjevRozpravy(NamedTuple):
    id: int
    datum: date | None
    id_bod: int | None
    ct: int | None
    ct_zdroj: str | None
    predsedajici: bool
    role: str | None
    recnik: str | None
    id_osoba: int | None
    text: str


# stejné ct z více zdrojů: zůstane zdroj s nižším číslem
_PRIORITA_ZDROJE = {"rec": 0, "bod": 1, "nadpis": 2}
MAX_KANDIDATU = 15  # adresát faktické se hledá mezi tolika posledními řečníky bodu


def prepocitej_rozpravu(cur, obdobi: int, schuze: int) -> dict:
    """Přepočítá rozpravu u projevů schůze: projev_tisk, druh, predchozi_recnik_id, reaguje_na_odhad, projev_pn.

    Idempotentní: řádky projev_tisk a projev_pn projevů schůze se smažou a vloží znovu, sloupce se přepíšou.
    Zrušené projevy (zruseno IS NOT NULL) nedostanou řádky ani druh a nikomu nejsou předchozím řečníkem.
    """
    cur.execute(
        """SELECT id, datum, id_bod, ct, ct_zdroj, predsedajici, role, recnik, id_osoba, text, zruseno IS NOT NULL
           FROM steno.projev WHERE obdobi = %s AND schuze = %s ORDER BY poradi, id""",
        (obdobi, schuze),
    )
    projevy = [_ProjevRozpravy(*r[:10]) for r in cur.fetchall() if not r[10]]
    cur.execute("SELECT id_bod, konec, nazev FROM steno.bod WHERE id_bod = ANY(%s)",
                (list({p.id_bod for p in projevy if p.id_bod is not None}),))
    tisky_bodu = {i: tisky_z_konce(konec) or tisky_z_konce(nazev) for i, konec, nazev in cur.fetchall()}
    cur.execute("SELECT id_osoba, jmeno, prijmeni FROM steno.osoba WHERE id_osoba = ANY(%s)",
                (list({p.id_osoba for p in projevy if p.id_osoba is not None}),))
    osoby = {i: (j, pr) for i, j, pr in cur.fetchall()}

    tisk_rows, pn_rows, sloupce = [], [], []
    # nepředsedající projevy dne podle bodu a podle ct (kandidáti na předchozího řečníka)
    podle_bodu: dict[tuple, list[_ProjevRozpravy]] = {}
    podle_ct: dict[tuple, list[_ProjevRozpravy]] = {}
    predchozi: _ProjevRozpravy | None = None
    for p in projevy:
        tisk_rows += [(p.id, ct, zdroj) for ct, zdroj in _tisky_projevu(p, tisky_bodu).items()]
        pn_rows += [(p.id, cislo) for cislo in pn_z_textu(p.text)]

        text_predsedajiciho = (predchozi.text if predchozi and predchozi.predsedajici
                               and predchozi.datum == p.datum else None)
        druh = druh_vystoupeni(text_predsedajiciho, p.role, p.predsedajici, _prijmeni(p, osoby))
        # stejný bod: stejné id_bod, u projevu bez bodu stejné ct
        if p.id_bod is not None:
            kandidati = podle_bodu.get((p.datum, p.id_bod), [])
        elif p.ct is not None:
            kandidati = podle_ct.get((p.datum, p.ct), [])
        else:
            kandidati = []
        reaguje = _odhad_adresata(p.text, kandidati, osoby) if druh == "fakticka" else None
        sloupce.append((p.id, druh, kandidati[-1].id if kandidati else None, reaguje))
        if not p.predsedajici:
            if p.id_bod is not None:
                podle_bodu.setdefault((p.datum, p.id_bod), []).append(p)
            if p.ct is not None:
                podle_ct.setdefault((p.datum, p.ct), []).append(p)
        predchozi = p

    cur.execute("""DELETE FROM steno.projev_tisk WHERE projev_id IN
                   (SELECT id FROM steno.projev WHERE obdobi = %s AND schuze = %s)""", (obdobi, schuze))
    cur.execute("""DELETE FROM steno.projev_pn WHERE projev_id IN
                   (SELECT id FROM steno.projev WHERE obdobi = %s AND schuze = %s)""", (obdobi, schuze))
    # přepisují se jen změněné řádky: zápis řádku projevu je drahý (~1 ms) a při běžném složení se nic nemění
    cur.execute("""UPDATE steno.projev SET druh = NULL, predchozi_recnik_id = NULL, reaguje_na_odhad = NULL
                   WHERE obdobi = %s AND schuze = %s AND zruseno IS NOT NULL
                     AND (druh IS NOT NULL OR predchozi_recnik_id IS NOT NULL OR reaguje_na_odhad IS NOT NULL)""",
                (obdobi, schuze))
    cur.executemany(
        """UPDATE steno.projev SET druh = %(druh)s, predchozi_recnik_id = %(pred)s, reaguje_na_odhad = %(reag)s
           WHERE id = %(id)s
             AND (druh, predchozi_recnik_id, reaguje_na_odhad) IS DISTINCT FROM (%(druh)s, %(pred)s, %(reag)s)""",
        [dict(id=i, druh=d, pred=pr, reag=r) for i, d, pr, r in sloupce],
    )
    cur.executemany("INSERT INTO steno.projev_tisk (projev_id, ct, zdroj) VALUES (%s, %s, %s)", tisk_rows)
    cur.executemany("INSERT INTO steno.projev_pn (projev_id, cislo_pn) VALUES (%s, %s)", pn_rows)
    return {"projevu": len(projevy), "projev_tisk": len(tisk_rows), "projev_pn": len(pn_rows)}


def _tisky_projevu(p: _ProjevRozpravy, tisky_bodu: dict[int, list[int]]) -> dict[int, str]:
    """ct -> zdroj: ct projevu s jeho zdrojem (rec | nadpis) a tisky z konce či názvu bodu (bod)."""
    zdroje: dict[int, str] = {}
    vlastni = [(p.ct, p.ct_zdroj)] if p.ct is not None else []
    for ct, zdroj in vlastni + [(t, "bod") for t in tisky_bodu.get(p.id_bod, [])]:
        if ct not in zdroje or _PRIORITA_ZDROJE[zdroj] < _PRIORITA_ZDROJE[zdroje[ct]]:
            zdroje[ct] = zdroj
    return zdroje


def _prijmeni(p: _ProjevRozpravy, osoby: dict[int, tuple]) -> str | None:
    """Příjmení řečníka: ze steno.osoba, jinak poslední slovo textu odkazu."""
    if p.id_osoba in osoby and osoby[p.id_osoba][1]:
        return osoby[p.id_osoba][1]
    slova = (p.recnik or "").split()
    return slova[-1] if slova else None


def _odhad_adresata(text: str, kandidati: list[_ProjevRozpravy], osoby: dict[int, tuple]) -> str | None:
    """Na koho faktická poznámka reaguje: jméno nejbližšího z posledních řečníků bodu, jehož příjmení
    odpovídá oslovenému tvaru; bez shody tvar z textu, bez osloveného jména None."""
    tvar = osloveny_recnik(text)
    if tvar is None:
        return None
    for k in reversed(kandidati[-MAX_KANDIDATU:]):
        prijmeni = _prijmeni(k, osoby)
        if prijmeni and shoda_prijmeni(tvar, prijmeni):
            return " ".join(x for x in osoby[k.id_osoba] if x) if k.id_osoba in osoby else k.recnik
    return tvar


_osoba_cache: dict[int, str] = {}


def _osoba_name(cur, id_osoba: int) -> str | None:
    if id_osoba not in _osoba_cache:
        cur.execute("SELECT jmeno || ' ' || prijmeni FROM steno.osoba WHERE id_osoba = %s", (id_osoba,))
        r = cur.fetchone()
        _osoba_cache[id_osoba] = r[0] if r else None
    return _osoba_cache[id_osoba]


def _name_index(cur) -> dict[str, int]:
    """„Jméno Příjmení“ → id_osoba pro osoby s klubem v tomto období nebo jednoznačná jména (členové vlády)."""
    cur.execute("SELECT id_osoba, jmeno || ' ' || prijmeni FROM steno.osoba")
    seen: dict[str, int | None] = {}
    for i, n in cur.fetchall():
        seen[n] = None if n in seen else i
    # nejednoznačná jména: dát přednost osobám, které už v tomto období mluvily
    cur.execute("SELECT DISTINCT o.jmeno || ' ' || o.prijmeni, o.id_osoba FROM steno.osoba o JOIN steno.rec r USING (id_osoba)")
    for n, i in cur.fetchall():
        seen[n] = i
    return {n: i for n, i in seen.items() if i is not None}


def _name_tail(label: str, names: dict[str, int]) -> str:
    """Najde nejdelší konec textu odkazu, který je jménem osoby („Ministr financí ČR Alena Schillerová“)."""
    words = label.split()
    for k in (4, 3, 2):
        tail = " ".join(words[-k:])
        if tail in names:
            return tail
    return ""


def _club_at(memberships: list, d: date | None) -> str | None:
    for klub, od, do in memberships:
        if d is None or ((od is None or od <= d) and (do is None or d <= do)):
            return klub
    return None
