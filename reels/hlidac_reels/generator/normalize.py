"""Deterministická normalizace karty a popisku od modelu před kontrolou.

Fakta se nemění: text modelu se jen přeskládá nebo zkrátí a doplní se údaje ze serveru. Každý zásah
se vrací jako věta pro `variant-N/normalize.json`.
"""
from __future__ import annotations

import copy
import re

from .validate import CAPTION_LIMIT, CLOSING, caption_authors, club_before, find_name

# „800 mil. Kč“, „1,5 mld. Kč“, „2 000 mil. Kč“: číslo (tisíce oddělené mezerou, desetinná čárka) + jednotka
# začínající písmenem a bez číslic; „10 → 4“, „10 z 12“, „4 roky místo 10“ ani samotné „2 000“ se nerozdělí
# (jednotka s dalším číslem by změnila, jak se fakt čte)
_NUM_UNIT = re.compile(r"^\s*(\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(,\d+)?\s+([^\W\d_][^\d]*?)\s*$")


def _split_hook(card: dict, notes: list[str]) -> None:
    hook = card.get("hook")
    if not isinstance(hook, dict) or not isinstance(hook.get("big"), str):
        return
    m = _NUM_UNIT.match(hook["big"])
    if not m:
        return
    number, unit = m.group(1) + (m.group(2) or ""), m.group(3)
    old_unit = hook.get("unit") if isinstance(hook.get("unit"), str) else ""
    if not old_unit.strip():
        new_unit = unit
    elif unit in old_unit:
        new_unit = old_unit
    else:
        new_unit = f"{unit} {old_unit.strip()}"
    notes.append(f"hook.big „{hook['big']}“ rozdělen: big „{number}“, unit „{new_unit}“ (dříve „{old_unit}“)")
    hook["big"], hook["unit"] = number, new_unit


def _drop_uncited(card: dict, notes: list[str]) -> None:
    """Body `zmena` bez citace vynechá; když citaci nemá žádný bod, nechá je (kontrola pak vrátí chybu)."""
    block = card.get("zmena")
    if not isinstance(block, dict) or not isinstance(block.get("body"), list):
        return
    body = block["body"]
    cited = [b for b in body if isinstance(b, dict) and isinstance(b.get("citace"), str) and b["citace"].strip()]
    if not cited or len(cited) == len(body):
        return
    kept = {id(b) for b in cited}
    dropped = [i for i, b in enumerate(body, 1) if id(b) not in kept]
    notes.append(f"zmena: vynechány body bez citace ({', '.join(map(str, dropped))})")
    block["body"] = cited


def _as_int(value) -> int | None:
    """Číslo PN ze serveru nebo z karty: celé číslo, případně číslice v řetězci."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().isdecimal():
        return int(value)
    return None


def _quoted(value) -> str:
    return f"„{value}“" if value not in (None, "") else "(žádný)"


def _server_pn(focus_info: list[dict]) -> dict[int, dict]:
    """Údaje serveru o každém zadaném PN (číslo → `{"cislo", "jmeno", "klub"}`), seřazené podle čísla.

    Jméno chybí (`None`), když server autora neuvádí; klub platí jen s autorem ze serveru. Klub je popisek
    ke dni podání (`ANO`, ne `ANO2011`); starší server ho neposílá a bez údaje je `None`.
    """
    by_num: dict[int, dict] = {}
    for item in focus_info or []:
        cislo = _as_int(item.get("cislo")) if isinstance(item, dict) else None
        if cislo is None or (cislo in by_num and by_num[cislo]["jmeno"]):
            continue
        osoba, klub = item.get("osoba"), item.get("klub")
        jmeno = osoba.strip() if isinstance(osoba, str) and osoba.strip() else None
        klub = klub.strip() if jmeno and isinstance(klub, str) and klub.strip() else None
        by_num[cislo] = {"cislo": cislo, "jmeno": jmeno, "klub": klub}
    return {n: by_num[n] for n in sorted(by_num)}


def _set_authors(card: dict, authors: list[dict], notes: list[str]) -> None:
    """`pn_autor` píše jen normalizace (údaje serveru); bez zadaných PN se pole od modelu odstraní."""
    if not authors:
        if "pn_autor" in card:
            del card["pn_autor"]
            notes.append("pn_autor: odstraněn, zadání autory PN neuvádí")
        return
    if card.get("pn_autor") == authors:
        return
    card["pn_autor"] = authors
    which = ", ".join(f"PN {a['cislo']} {a['jmeno']}" + (f" ({a['klub']})" if a["klub"] else "") for a in authors)
    notes.append(f"pn_autor: autoři zadaných PN ze serveru ({which})")


def _fix_top(card: dict, server: dict[int, dict], notes: list[str]) -> None:
    """V `pn.top` zadaných PN platí autor a klub ze serveru; klub od modelu (MCP vrací poslední klub)
    se bez údaje serveru odstraní, autor bez údaje serveru zůstane. Ostatní PN zůstanou, jak je model napsal."""
    block = card.get("pn")
    top = block.get("top") if isinstance(block, dict) else None
    if not isinstance(top, list):
        return
    for item in top:
        pn = server.get(_as_int(item.get("cislo"))) if isinstance(item, dict) else None
        if pn is None:
            continue
        n, jmeno, klub = pn["cislo"], pn["jmeno"], pn["klub"]
        if jmeno and item.get("autor") != jmeno:
            notes.append(f"pn.top PN {n}: autor {_quoted(item.get('autor'))} → „{jmeno}“ (server)")
            item["autor"] = jmeno
        if klub is None:
            if "klub" in item:
                notes.append(f"pn.top PN {n}: klub {_quoted(item['klub'])} odstraněn (server klub neuvádí)")
                del item["klub"]
        elif item.get("klub") != klub:
            notes.append(f"pn.top PN {n}: klub {_quoted(item.get('klub'))} → „{klub}“ (server)")
            item["klub"] = klub


def _fix_autori(card: dict, server: dict[int, dict], notes: list[str]) -> None:
    """V pruzích `pn.autori` platí u autora zadaných PN klub ze serveru (ke dni podání), aby video neuvádělo
    v háčku jiný klub než v pruzích (model ho bere z MCP, které vrací poslední klub).

    Mají-li všechny zadané PN téhož jména stejný klub ze serveru, platí ten. Když ho server neuvádí, nebo se kluby
    téhož jména mezi zadanými PN liší, klub z pruhu zmizí (nic se neodhaduje). Ostatní autoři zůstanou, jak je
    model napsal.
    """
    block = card.get("pn")
    rows = block.get("autori") if isinstance(block, dict) else None
    if not isinstance(rows, list):
        return
    clubs: dict[str, set] = {}
    for pn in server.values():
        if pn["jmeno"]:
            clubs.setdefault(pn["jmeno"], set()).add(pn["klub"])
    for row in rows:
        jmeno = row["jmeno"].strip() if isinstance(row, dict) and isinstance(row.get("jmeno"), str) else ""
        if jmeno not in clubs:
            continue
        kluby = clubs[jmeno]
        klub = next(iter(kluby)) if len(kluby) == 1 else None
        if klub is None:
            if "klub" in row:
                why = "server klub neuvádí" if kluby == {None} else "kluby zadaných PN se liší"
                notes.append(f"pn.autori {jmeno}: klub {_quoted(row['klub'])} odstraněn ({why})")
                del row["klub"]
        elif row.get("klub") != klub:
            notes.append(f"pn.autori {jmeno}: klub {_quoted(row.get('klub'))} → „{klub}“ (server)")
            row["klub"] = klub


def _caption_clubs(card: dict, caption: str, notes: list[str]) -> str:
    """Za první výskyt celého jména autora doplní ` (KLUB)`, když za ním závorka není.

    Nic nemění, když se celé jméno nenajde (skloňované „návrh Jana Nováka“), když za ním už závorka je (správný
    klub, nebo chyba pro kontrolu), když klub stojí těsně před jménem („za ANO Jan Novák“) a když by popisek
    přesáhl limit. Chybu klubu pak vrátí kontrola (`validate._club_error`).
    """
    for author in caption_authors(card):
        jmeno, klub = author["jmeno"], author["klub"]
        found = find_name(caption, jmeno) if klub else None
        if found is None:
            continue
        start, end, paren = found
        if paren is not None or club_before(caption, start, klub):
            continue
        new = f"{caption[:end]} ({klub}){caption[end:]}"
        if len(new) > CAPTION_LIMIT:
            continue
        caption = new
        notes.append(f"popisek: za „{jmeno}“ doplněn klub ({klub})")
    return caption


def normalize(card: dict, caption: str, *, fmt: str | None,
              focus_info: list[dict]) -> tuple[dict, str, list[str]]:
    """Vrací novou kartu, nový popisek a seznam zásahů; vstupní karta zůstane beze změny.

    focus_info = údaje o zadaných PN z claimu serveru (/pn): autor a klub ke dni podání jdou do
    `pn_autor` (každá varianta, v2 i v3), do `pn.top`, do pruhů `pn.autori` a do popisku.
    """
    card = copy.deepcopy(card)
    notes: list[str] = []
    _split_hook(card, notes)
    if fmt == "zmena":
        _drop_uncited(card, notes)
    if CLOSING not in caption:
        caption = f"{caption.rstrip()}\n\n{CLOSING}" if caption.strip() else CLOSING
        notes.append(f"popisek: doplněna věta „{CLOSING}“")
    server = _server_pn(focus_info)
    _set_authors(card, [pn for pn in server.values() if pn["jmeno"]], notes)
    _fix_top(card, server, notes)
    _fix_autori(card, server, notes)
    caption = _caption_clubs(card, caption, notes)
    return card, caption, notes
