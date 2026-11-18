"""Import otevřených dat psp.cz: osoby, kluby, schůze, body pořadu, kostra částí a záznamy řečníků (rec).

Soubory .unl: oddělovač „|“, windows-1250, všechna období dohromady.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime
from typing import Iterator

import psycopg

from .http import Fetcher

OPENDATA = "/eknih/cdrom/opendata/"


def rows(zf: zipfile.ZipFile, name: str) -> Iterator[list[str]]:
    with zf.open(name) as f:
        for line in io.TextIOWrapper(f, encoding="cp1250", errors="replace"):
            line = line.rstrip("\r\n")
            if line:
                yield [c.strip() for c in line.split("|")]


def _int(s: str) -> int | None:
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _dmy(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%d.%m.%Y").date()
    except ValueError:
        return None


def _ymd(s: str) -> date | None:
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _ts(s: str) -> datetime | None:
    # „2026-11-13 09“ (hodina) nebo prázdné
    try:
        return datetime.strptime(s, "%Y-%m-%d %H")
    except ValueError:
        return _ymd(s) and datetime.combine(_ymd(s), datetime.min.time())


def download(fetcher: Fetcher, package: str) -> zipfile.ZipFile:
    raw = fetcher.get(OPENDATA + package + ".zip")
    if raw is None:
        raise RuntimeError(f"{package}.zip nenalezen")
    return zipfile.ZipFile(io.BytesIO(raw))


def current_period(poslanci: zipfile.ZipFile) -> tuple[int, int]:
    """(id_org, číslo) aktuálního období: orgán typu 11 „PSPn“ bez data konce."""
    best = None
    for c in rows(poslanci, "organy.unl"):
        if c[2] != "11" or not c[3].startswith("PSP") or not c[3][3:].isdigit():
            continue
        num, open_ = int(c[3][3:]), c[7] == ""
        if best is None or (open_, num) > (best[2], best[1]):
            best = (int(c[0]), num, open_)
    if best is None:
        raise RuntimeError("Nepodařilo se určit volební období.")
    return best[0], best[1]


def import_all(conn: psycopg.Connection, fetcher: Fetcher, zips: dict[str, zipfile.ZipFile] | None = None) -> dict:
    """Načte poslanci, schuze, tisky a steno do tabulek steno.*. Vrací (období, statistiky)."""
    zips = zips or {}
    for p in ("poslanci", "schuze", "tisky", "steno"):
        if p not in zips:
            zips[p] = download(fetcher, p)
    id_org, obdobi = current_period(zips["poslanci"])
    stats: dict = {"obdobi": obdobi, "id_org": id_org}

    # kluby období: orgány typu 1 pod orgánem období
    kluby = {}
    for c in rows(zips["poslanci"], "organy.unl"):
        if c[2] == "1" and c[1] == str(id_org):
            kluby[int(c[0])] = (c[3], c[4])
    clenstvi = []
    for c in rows(zips["poslanci"], "zarazeni.unl"):
        of = _int(c[1])
        if of in kluby and c[2] == "0":
            clenstvi.append((int(c[0]), kluby[of][0], kluby[of][1], _ymd(c[3]), _ymd(c[4])))
    osoby = {}
    for c in rows(zips["poslanci"], "osoby.unl"):
        osoby[int(c[0])] = (int(c[0]), c[1] or None, c[3], c[2], c[4] or None)

    # schůze období (řádky se opakují pro navržený a schválený pořad)
    schuze = {}
    for c in rows(zips["schuze"], "schuze.unl"):
        if c[1] == str(id_org):
            sid = int(c[0])
            prev = schuze.get(sid)
            row = (obdobi, int(c[2]), sid, _ts(c[3]), _ts(c[4]))
            if prev is None or (row[4] and not prev[4]):
                schuze[sid] = row

    # tisky: id_tisk → (ct, cislo_za)
    tisky = {}
    for c in rows(zips["tisky"], "tisky.unl"):
        if c[7] == str(id_org):
            tisky[int(c[0])] = (int(c[3]), _int(c[4]) or 0)

    # body pořadu: id_bod není unikátní (pozvánka × schválený pořad), přednost má schválený (pozvanka != 1)
    body = {}
    for c in rows(zips["schuze"], "bod_schuze.unl"):
        sid = _int(c[1])
        if sid not in schuze:
            continue
        idb = int(c[0])
        id_tisk = _int(c[2]) or None
        ct, ct_za = tisky.get(id_tisk, (None, None)) if id_tisk else (None, None)
        row = (idb, obdobi, schuze[sid][1], _int(c[4]), c[5], c[6], id_tisk, ct, ct_za)
        if idb not in body or c[9] != "1":
            body[idb] = row

    # části a záznamy řečníků
    steno = {}
    for c in rows(zips["steno"], "steno.unl"):
        if c[1] == str(id_org):
            steno[int(c[0])] = (int(c[2]), int(c[3]), _ymd(c[4]), _int(c[5]), _int(c[6]), _int(c[7]))
    rec = []
    for c in rows(zips["steno"], "rec.unl"):
        ids = int(c[0])
        if ids in steno:
            s = steno[ids]
            rec.append((s[0], s[1], int(c[2]), _int(c[1]), _int(c[3]) or None, _int(c[4])))

    with conn.cursor() as cur:
        # všechny osoby (řádově desítky tisíc řádků): členové vlády nemají id v HTML a páruje se jménem
        cur.execute("DELETE FROM steno.osoba")
        with cur.copy("COPY steno.osoba FROM STDIN") as cp:
            for o in osoby.values():
                cp.write_row(o)
        used = {r[3] for r in rec if r[3]}
        cur.execute("DELETE FROM steno.klub_clenstvi")
        cur.executemany("INSERT INTO steno.klub_clenstvi VALUES (%s,%s,%s,%s,%s)", clenstvi)
        cur.execute("DELETE FROM steno.schuze WHERE obdobi = %s", (obdobi,))
        cur.executemany(
            "INSERT INTO steno.schuze (obdobi, schuze, id_schuze, od, \"do\") VALUES (%s,%s,%s,%s,%s)",
            list({(r[0], r[1]): r for r in schuze.values()}.values()),
        )
        cur.execute("DELETE FROM steno.bod WHERE obdobi = %s", (obdobi,))
        cur.executemany("INSERT INTO steno.bod VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", list(body.values()))
        # kostra částí: nové vložit, u existujících doplnit metadata (obsah a stav nechat)
        cur.executemany(
            """INSERT INTO steno.cast (obdobi, schuze, turn, id_steno, datum, jd, od_min, do_min, url, dalsi_kontrola)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
               ON CONFLICT (obdobi, schuze, turn) DO UPDATE SET id_steno = EXCLUDED.id_steno,
                 datum = EXCLUDED.datum, jd = EXCLUDED.jd, od_min = EXCLUDED.od_min, do_min = EXCLUDED.do_min""",
            [(obdobi, s[0], s[1], ids, s[2], s[3], s[4], s[5], part_url(obdobi, s[0], s[1]))
             for ids, s in steno.items()],
        )
        cur.execute("CREATE TEMP TABLE IF NOT EXISTS tmp_rec (schuze int, turn int, aname int, id_osoba int, id_bod int, druh int) ON COMMIT DROP")
        cur.execute("TRUNCATE tmp_rec")
        with cur.copy("COPY tmp_rec FROM STDIN") as cp:
            for r in rec:
                cp.write_row(r)
        cur.execute("DROP TABLE IF EXISTS steno.rec")
        cur.execute("CREATE TABLE steno.rec AS SELECT %s::int AS obdobi, * FROM tmp_rec", (obdobi,))
        cur.execute("CREATE UNIQUE INDEX ON steno.rec (obdobi, schuze, turn, aname)")
    conn.commit()
    stats.update(osoby=len(used), kluby=len(clenstvi), schuze=len(schuze), body=len(body),
                 casti=len(steno), rec=len(rec))
    return stats


YEAR_DIR = {10: "2025ps", 9: "2021ps", 8: "2017ps"}


def term_dir(obdobi: int) -> str:
    if obdobi not in YEAR_DIR:
        raise ValueError(f"neznámý adresář období {obdobi}")
    return YEAR_DIR[obdobi]


def part_url(obdobi: int, schuze: int, turn: int) -> str:
    return f"/eknih/{term_dir(obdobi)}/stenprot/{schuze:03d}schuz/s{schuze:03d}{turn:03d}.htm"
