"""Deterministická normalizace karty a popisku od modelu před kontrolou.

Fakta se nemění: text modelu se jen přeskládá nebo zkrátí a doplní se údaje ze serveru. Každý zásah
se vrací jako věta pro `variant-N/normalize.json`.
"""
from __future__ import annotations

import copy
import re

from .. import attribution
from .validate import CAPTION_LIMIT, CLOSING, caption_authors, club_before, find_name, name_in_paren

# „800 mil. Kč“, „1,5 mld. Kč“, „2 000 mil. Kč“: číslo (tisíce oddělené mezerou, desetinná čárka) + jednotka
# začínající písmenem a bez číslic; „10 → 4“, „10 z 12“, „4 roky místo 10“ ani samotné „2 000“ se nerozdělí
# (jednotka s dalším číslem by změnila, jak se fakt čte)
_NUM_UNIT = re.compile(r"^\s*(\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(,\d+)?\s+([^\W\d_][^\d]*?)\s*$")
# přípona začínající předložkou nebo spojkou („10 z dvanácti“, „2 a více“) je s číslem jeden údaj, ne jednotka:
# v jednotce by ji šablona v3 (řádek háčku #hk-row se láme) zalomila od čísla
FUNCTION_WORDS = ("a", "i", "k", "ke", "o", "s", "se", "u", "v", "ve", "z", "ze", "na", "do", "od", "po", "za")
# obyčejná mezera za číslem před písmenem („10 z“) a za jednopísmenným slovem („z dvanácti“, „V rozpočtu“)
_NBSP_NUMBER = re.compile(r"(?<=\d) (?=[^\W\d_])")
_NBSP_SHORT = re.compile(r"(?<!\w)([ksvzouaiKSVZOUAI]) ")


def _split_hook(card: dict, notes: list[str]) -> None:
    hook = card.get("hook")
    if not isinstance(hook, dict) or not isinstance(hook.get("big"), str):
        return
    m = _NUM_UNIT.match(hook["big"])
    if not m:
        return
    number, unit = m.group(1) + (m.group(2) or ""), m.group(3)
    if unit.split()[0].lower() in FUNCTION_WORDS:
        return
    old_unit = hook.get("unit") if isinstance(hook.get("unit"), str) else ""
    if not old_unit.strip():
        new_unit = unit
    elif unit in old_unit:
        new_unit = old_unit
    else:
        new_unit = f"{unit} {old_unit.strip()}"
    notes.append(f"hook.big „{hook['big']}“ rozdělen: big „{number}“, unit „{new_unit}“ (dříve „{old_unit}“)")
    hook["big"], hook["unit"] = number, new_unit


def _nbsp_hook(card: dict, notes: list[str]) -> None:
    """V `hook.big` a `hook.unit` nahradí obyčejnou mezeru za číslem před písmenem a za jednopísmenným slovem
    nezalomitelnou (`\\u00a0`): „10 z dvanácti“ ani „z dvanácti“ v jednotce se v háčku nerozdělí na dva řádky.
    Text se jinak nemění; mezery mezi čísly („2 000“, „10 → 4“) zůstanou."""
    hook = card.get("hook")
    if not isinstance(hook, dict):
        return
    for key in ("big", "unit"):
        text = hook.get(key)
        if not isinstance(text, str):
            continue
        fixed = _NBSP_SHORT.sub("\\1\u00a0", _NBSP_NUMBER.sub("\u00a0", text))
        if fixed != text:
            hook[key] = fixed
            notes.append(f"hook.{key}: nezalomitelné mezery")


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

    `spolu` = počet spolupodepsaných z hlavičky textu PN (ReelJobs::pnInfo), jen když ho server posílá: číslo,
    nebo `None` (počet nejde určit). Starší server klíč neposílá a záznam ho pak nemá.
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
        if jmeno and "spolu" in item:
            by_num[cislo]["spolu"] = _count(item["spolu"])
    return {n: by_num[n] for n in sorted(by_num)}


def _count(value) -> int | None:
    """Počet spolupodepsaných ze serveru: nezáporné celé číslo, jinak None (nejde určit)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


EVIDENCE = "předkladatel podle evidence psp.cz"


def dalsi_text(n: int | None) -> str:
    """„a 1 další poslanec“, „a 3 další poslanci“, „a 5 dalších poslanců“; neznámý počet „a další poslanci“."""
    if n is None:
        return "a další poslanci"
    word = "další poslanec" if n == 1 else "další poslanci" if 2 <= n <= 4 else "dalších poslanců"
    return f"a {n} {word}"


def _set_authors(card: dict, authors: list[dict], notes: list[str], source: str = "zadaných PN") -> None:
    """`pn_autor` píše jen normalizace (údaje serveru); bez údajů serveru se pole od modelu odstraní."""
    if not authors:
        if "pn_autor" in card:
            del card["pn_autor"]
            notes.append("pn_autor: odstraněn, zadání autory PN neuvádí")
        return
    if card.get("pn_autor") == authors:
        return
    card["pn_autor"] = authors
    which = ", ".join(f"PN {a['cislo']} {a['jmeno']}" + (f" ({a['klub']})" if a["klub"] else "") for a in authors)
    notes.append(f"pn_autor: autoři {source} ze serveru ({which})")


def _headline_authors(server_card: dict | None) -> list[dict]:
    """Autor PN, ze kterého je titulek karty serveru (`card_v2.pn_autor`, i bez zadaných PN), ve tvaru
    `{"cislo", "jmeno", "klub"}`; položky bez čísla nebo jména se vynechají. Starší server pole neposílá → []."""
    rows = server_card.get("pn_autor") if isinstance(server_card, dict) else None
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        cislo, jmeno, klub = _as_int(row.get("cislo")), row.get("jmeno"), row.get("klub")
        if cislo is None or not isinstance(jmeno, str) or not jmeno.strip():
            continue
        item = {"cislo": cislo, "jmeno": jmeno.strip(),
                "klub": klub.strip() if isinstance(klub, str) and klub.strip() else None}
        if "spolu" in row:
            item["spolu"] = _count(row["spolu"])
        out.append(item)
    return out


def _set_proposer(card: dict, server_card: dict, notes: list[str]) -> None:
    """`predkladatel` (scéna „Kdo to přináší“) platí jen ze serveru, model ho nepíše ani nemění. Bez platného
    předkladatele ze serveru (jméno a typ; starší server pole nemá) se pole od modelu odstraní."""
    who = server_card.get("predkladatel")
    valid = isinstance(who, dict) and all(isinstance(who.get(k), str) and who[k].strip() for k in ("typ", "jmeno"))
    if not valid:
        if "predkladatel" in card:
            del card["predkladatel"]
            notes.append("predkladatel: odstraněn, server předkladatele neuvádí")
        return
    # citace z úvodního slova (scéna s-said) zůstává zamítnutá (docs/reels-runbook.md): do karty nepatří ani ze serveru
    who = {k: v for k, v in who.items() if k != "citace"}
    if card.get("predkladatel") == who:
        return
    card["predkladatel"] = copy.deepcopy(who)
    notes.append(f"predkladatel: ze serveru ({who['jmeno']})")


def _drop_said(card: dict, notes: list[str]) -> None:
    """`predkladatel.citace` (výrok z úvodního slova, scéna s-said) se odstraní vždy: výrok se osobě nepřisuzuje."""
    who = card.get("predkladatel")
    if isinstance(who, dict) and "citace" in who:
        del who["citace"]
        notes.append("predkladatel.citace: odstraněna (citace úvodního slova se ve videu neukazuje)")


def _set_spolu(item: dict, pn: dict, where: str, notes: list[str]) -> None:
    """Počet spolupodepsaných jen ze serveru: číslo se zapíše, neznámý počet (null nebo starší server) pole smaže."""
    n = pn.get("spolu") if "spolu" in pn else None
    if n is None:
        if "spolu" in item:
            notes.append(f"{where}: spolu {_quoted(item['spolu'])} odstraněno (server počet neuvádí)")
            del item["spolu"]
    elif item.get("spolu") != n:
        notes.append(f"{where}: spolu {_quoted(item.get('spolu'))} → {n} (hlavička textu PN)")
        item["spolu"] = n


def _fix_top(card: dict, server: dict[int, dict], notes: list[str], autopilot: bool = False) -> None:
    """V `pn.top` zadaných PN platí autor, klub a počet spolupodepsaných ze serveru; klub od modelu (MCP vrací
    poslední klub) se bez údaje serveru odstraní, autor bez údaje serveru zůstane. U ostatních PN počet `spolu`
    od modelu zmizí (nikdo ho neověřil); v autopilotu zmizí i jejich klub (neověřený klub ke dni)."""
    block = card.get("pn")
    top = block.get("top") if isinstance(block, dict) else None
    if not isinstance(top, list):
        return
    for item in top:
        pn = server.get(_as_int(item.get("cislo"))) if isinstance(item, dict) else None
        if pn is None:
            if not isinstance(item, dict):
                continue
            if "spolu" in item:
                notes.append(f"pn.top PN {item.get('cislo')}: spolu odstraněno (PN není zadaný, server počet neuvádí)")
                del item["spolu"]
            if autopilot and "klub" in item:
                notes.append(f"pn.top PN {item.get('cislo')}: klub {_quoted(item['klub'])} odstraněn "
                             "(autopilot: klub ke dni podání server nepotvrdil)")
                del item["klub"]
            continue
        _set_spolu(item, pn, f"pn.top PN {pn['cislo']}", notes)
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


def _fix_story_author(story: dict, server: dict[int, dict], notes: list[str]) -> None:
    """V `pribeh.autor` platí autor a klub ze serveru (klub ke dni podání), stejně jako v `pn.top`.

    Klub od modelu (MCP vrací poslední klub) bez údaje serveru zmizí, autor bez údaje serveru zůstane.
    Počet spolupodepsaných (`spolu`) platí jen ze serveru; když ho server neuvádí, pole zmizí a `pozn` řekne
    „předkladatel podle evidence psp.cz“ (rozhodnutí majitele 2026-11-28)."""
    pn = server.get(_as_int(story.get("cislo")))
    autor = story.get("autor")
    if pn is None or not isinstance(autor, dict):
        return
    _set_spolu(autor, pn, f"pribeh PN {pn['cislo']}", notes)
    if "spolu" not in autor and autor.get("pozn") != EVIDENCE:
        autor["pozn"] = EVIDENCE
        notes.append(f"pribeh PN {pn['cislo']}: počet spolupodepsaných neznámý, pozn „{EVIDENCE}“")
    elif "spolu" in autor and "pozn" in autor:
        del autor["pozn"]
    n, jmeno, klub = pn["cislo"], pn["jmeno"], pn["klub"]
    if jmeno and autor.get("jmeno") != jmeno:
        notes.append(f"pribeh PN {n}: autor {_quoted(autor.get('jmeno'))} → „{jmeno}“ (server)")
        autor["jmeno"] = jmeno
    if klub is None:
        if "klub" in autor:
            notes.append(f"pribeh PN {n}: klub {_quoted(autor['klub'])} odstraněn (server klub neuvádí)")
            del autor["klub"]
    elif autor.get("klub") != klub:
        notes.append(f"pribeh PN {n}: klub {_quoted(autor.get('klub'))} → „{klub}“ (server)")
        autor["klub"] = klub


def _fix_autori(card: dict, server: dict[int, dict], notes: list[str], autopilot: bool = False) -> None:
    """V pruzích `pn.autori` platí u autora zadaných PN klub ze serveru (ke dni podání), aby video neuvádělo
    v háčku jiný klub než v pruzích (model ho bere z MCP, které vrací poslední klub).

    Mají-li všechny zadané PN téhož jména stejný klub ze serveru, platí ten. Když ho server neuvádí, nebo se kluby
    téhož jména mezi zadanými PN liší, klub z pruhu zmizí (nic se neodhaduje). Ostatní autoři zůstanou, jak je
    model napsal; v autopilotu jejich klub zmizí (MCP vrací poslední klub, ne klub ke dni podání).
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
            if autopilot and isinstance(row, dict) and "klub" in row:
                notes.append(f"pn.autori {jmeno}: klub {_quoted(row['klub'])} odstraněn "
                             "(autopilot: klub ke dni podání server nepotvrdil)")
                del row["klub"]
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
    """Za první výskyt celého jména autora doplní ` (KLUB)`, když za ním závorka není; jméno uvnitř závorky
    („(Jan Novák)“) dostane klub za čárku do téže závorky („(Jan Novák, ANO)“), ne vnořenou závorku.

    Nic nemění, když se celé jméno nenajde (skloňované „návrh Jana Nováka“), když za ním už závorka je (správný
    klub, nebo chyba pro kontrolu), když za jménem v jeho závorce už nějaký text je („(Jan Novák, ODS)“: správný
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
        inner = name_in_paren(caption, start, end)
        if inner is not None and inner.strip():
            continue
        insert, where = (f" ({klub})", "") if inner is None else (f", {klub}", " v závorce")
        new = f"{caption[:end]}{insert}{caption[end:]}"
        if len(new) > CAPTION_LIMIT:
            continue
        caption = new
        notes.append(f"popisek: za „{jmeno}“{where} doplněn klub ({klub})")
    return caption


def _after_name(caption: str, jmeno: str) -> int | None:
    """Kam za první výskyt celého jména (a jeho závorku s klubem) patří značka dalších navrhovatelů; None, když
    se celé jméno nenajde (skloňovaný tvar) nebo stojí uvnitř závorky."""
    found = find_name(caption, jmeno)
    if found is None:
        return None
    start, end, paren = found
    if paren is None and name_in_paren(caption, start, end) is not None:
        return None
    if paren is not None:
        close = caption.find(")", end)
        return close + 1 if close >= 0 else None
    return end


def _insert_marker(caption: str, jmeno: str, n: int | None, unknown: str, notes: list[str]) -> str:
    """Vloží za jméno „ a N dalších poslanců“ (neznámý počet: PN „podle evidence psp.cz“ do závorky s klubem,
    předkladatel tisku „ a další poslanci“). Nic nemění, když značka už stojí za jménem (správně, nebo chyba pro
    kontrolu), když se celé jméno nenajde a když by popisek přesáhl limit."""
    if n == 0:
        return caption
    errs = (attribution.cosigner_errors(caption, where="popisek", authors=[{"jmeno": jmeno, "spolu": n}])
            if unknown == "evidence" else
            attribution.proposer_errors(caption, where="popisek",
                                        proposer={"jmeno": jmeno, "vice": True, "dalsi": n}))
    if not errs or "špatný počet" in errs[0]:
        return caption
    at = _after_name(caption, jmeno)
    if at is None:
        return caption
    if n is None and unknown == "evidence":
        if caption[:at].endswith(")"):
            new = f"{caption[:at - 1]}, {attribution.EVIDENCE}){caption[at:]}"
        else:
            new = f"{caption[:at]} ({attribution.EVIDENCE}){caption[at:]}"
        what = attribution.EVIDENCE
    else:
        what = dalsi_text(n)
        new = f"{caption[:at]} {what}{caption[at:]}"
    if len(new) > CAPTION_LIMIT:
        return caption
    notes.append(f"popisek: za „{jmeno}“ doplněno „{what}“")
    return new


def _caption_cosigners(card: dict, caption: str, notes: list[str]) -> str:
    """Za první celé jméno autora PN (`pn_autor` se `spolu` ze serveru) a předkladatele poslaneckého návrhu
    (`predkladatel.vice`) doplní „a N dalších poslanců“. Skloňované jméno nechá být; chybu vrátí kontrola."""
    for row in card.get("pn_autor") or []:
        if isinstance(row, dict) and "spolu" in row and isinstance(row.get("jmeno"), str):
            caption = _insert_marker(caption, row["jmeno"], row["spolu"], "evidence", notes)
    who = card.get("predkladatel")
    if isinstance(who, dict) and who.get("vice") is True and isinstance(who.get("jmeno"), str):
        dalsi = who.get("dalsi")
        caption = _insert_marker(caption, who["jmeno"], _count(dalsi), "dalsi", notes)
    return caption


def _set_kdo(card: dict, server_card: dict | None, notes: list[str]) -> None:
    """Pole `kdo` (v3 a příběh PN) skládá systém z `server_card.predkladatel`: poslanecký „Jméno a 3 další
    poslanci“, vládní „předkládá <ministerstvo>“, jinak jméno instituce. Bez předkladatele ze serveru pole zmizí;
    bez server_card (starší volání) zůstane karta beze změny."""
    if not isinstance(server_card, dict):
        return
    who = server_card.get("predkladatel") if isinstance(server_card, dict) else None
    if not isinstance(who, dict) or not isinstance(who.get("jmeno"), str) or not who["jmeno"].strip():
        if "kdo" in card:
            del card["kdo"]
            notes.append("kdo: odstraněno (server předkladatele neuvádí)")
        return
    jmeno, typ = who["jmeno"].strip(), str(who.get("typ") or "")
    if typ.startswith("Poslanec") and who.get("vice") is True:
        kdo = f"{jmeno} {dalsi_text(_count(who.get('dalsi')))}"
    elif typ.startswith("Vládní"):
        kdo = f"předkládá {jmeno}"
    else:
        kdo = jmeno
    if card.get("kdo") != kdo:
        notes.append(f"kdo: {_quoted(card.get('kdo'))} → „{kdo}“ (předkladatel ze serveru)")
        card["kdo"] = kdo


def _initial(jmeno: str) -> str:
    """„Alena Schillerová“ → „A. Schillerová“."""
    parts = jmeno.split()
    return f"{parts[0][0]}. {' '.join(parts[1:])}" if len(parts) > 1 else jmeno


def _rozprava_sources(card: dict, notes: list[str]) -> None:
    """Zdroj bodu rozpravy skládá systém z `recnik` a `datum`: „projev: A. Schillerová · 13. 10.“, u více řečníků
    „projevy: Jakob, Munzar · 13. 10.“. Text zdroje od modelu se zahodí; bez řečníka a data zůstane (chybu hlásí
    kontrola). `kdo` u stran a `lab` (nadpis od modelu) zmizí: nadpisy scén dává šablona."""
    block = card.get("rozprava")
    if not isinstance(block, dict):
        return
    for side in ("pro", "namitky", "odpovedi"):
        part = block.get(side)
        if not isinstance(part, dict):
            continue
        for key in ("kdo", "lab"):
            if key in part:
                notes.append(f"rozprava.{side}.{key}: odstraněno (nadpis a řečníka ukazuje šablona podle bodů)")
                del part[key]
        for b in part.get("body") or []:
            if not isinstance(b, dict):
                continue
            recnik = b.get("recnik")
            m = re.match(r"^\d{4}-(\d{2})-(\d{2})$", str(b.get("datum") or ""))
            names = [r.strip() for r in (recnik if isinstance(recnik, list) else [recnik])
                     if isinstance(r, str) and r.strip()]
            if not names or not m:
                continue
            day = f"{int(m[2])}. {int(m[1])}."
            if len(names) == 1:
                zdroj = f"projev: {_initial(names[0])} · {day}"
            else:
                zdroj = f"projevy: {', '.join(n.split()[-1] for n in names)} · {day}"
            if b.get("zdroj") != zdroj:
                b["zdroj"] = zdroj


# stavy výsledku PN, které šablona template_pn_v1 ukáže (app/PnOutcome.php bez „neurceno“)
PN_STATES = ("prijat", "castecne", "zamitnut", "ceka", "nehlasovatelny", "nezaznel")
VOTE_WORDS = {"prijato": "přijato", "zamitnuto": "zamítnuto"}


def _fix_signs(story: dict, notes: list[str]) -> None:
    """Znaky bez zdroje vynechá (šablona by je stejně nezařadila); znak typu `ai` dostane štítek AI."""
    signs = story.get("znaky")
    if not isinstance(signs, list):
        return
    kept = []
    for i, sign in enumerate(signs, 1):
        if not isinstance(sign, dict) or not isinstance(sign.get("zdroj"), str) or not sign["zdroj"].strip():
            notes.append(f"pribeh: vynechán znak {i} bez zdroje")
            continue
        if sign.get("typ") == "ai" and sign.get("ai") is not True:
            sign["ai"] = True
            notes.append(f"pribeh: znak {i} (typ ai) dostal štítek hodnocení AI")
        kept.append(sign)
    story["znaky"] = kept


def _server_outcome(story: dict, focus_info: list[dict]) -> tuple[bool, dict | None]:
    """(server výsledek posílá, výsledek PN karty) z claimu; bez klíče `vysledek` (starší server) (False, None)."""
    cislo = _as_int(story.get("cislo"))
    items = [i for i in focus_info or [] if isinstance(i, dict) and "vysledek" in i]
    item = next((i for i in items if _as_int(i.get("cislo")) == cislo), None)
    if item is None:
        return False, None
    vysledek = item["vysledek"]
    return True, vysledek if isinstance(vysledek, dict) else None


def _fix_outcome(story: dict, focus_info: list[dict], notes: list[str]) -> None:
    """Výsledek PN platí ze serveru (pn_vysledek): stav, hlasování s počty, kluby a odkazem na psp.cz.

    Od modelu zůstane jen popis (`text`, `o_cem` u stejného čísla hlasování, `dal`); text ke stavu, který server
    neuvádí, zmizí. Bez výsledku ze serveru (null) zůstane nejvýš „čeká“, výsledek „neurceno“ vypadne celý: verdikt
    bez doloženého hlasování šablona ukázat nesmí.

    `stanoviska` od modelu zmizí vždy (výrok ministra ze stenozáznamu do videa nepatří); když server posílá
    stanovisko garančního výboru (`vysledek.stanovisko`), pole bude „garanční výbor: <stanovisko>“.
    """
    model = story.get("vysledek") if isinstance(story.get("vysledek"), dict) else None
    if model is not None and "stanoviska" in model:
        del model["stanoviska"]
        notes.append("pribeh: stanoviska od modelu odstraněna (výrok se osobě nepřisuzuje)")
    known, srv = _server_outcome(story, focus_info)
    if not known:
        return
    stav = srv.get("stav") if srv else None
    if stav not in PN_STATES:
        if model is not None and (stav == "neurceno" or model.get("stav") != "ceka"):
            del story["vysledek"]
            notes.append(f"pribeh: výsledek {_quoted(model.get('stav'))} vynechán (server výsledek "
                         f"{'neurčil' if stav == 'neurceno' else 'neuvádí'})")
        elif model is not None and model.get("hlasovani"):
            model.pop("hlasovani")
            notes.append("pribeh: hlasování vynecháno (server výsledek neuvádí)")
        return
    new = dict(model or {})
    if model is None:
        notes.append(f"pribeh: výsledek doplněn ze serveru („{stav}“)")
    elif model.get("stav") != stav:
        notes.append(f"pribeh: stav výsledku {_quoted(model.get('stav'))} → „{stav}“ (server)")
        new.pop("text", None)
    new["stav"] = stav
    described = {_as_int(h.get("cislo")): h.get("o_cem") for h in (model or {}).get("hlasovani") or []
                 if isinstance(h, dict) and isinstance(h.get("o_cem"), str) and h["o_cem"].strip()}
    votes = []
    for h in srv.get("hlasovani") or []:
        if not isinstance(h, dict):
            continue
        o_cem = described.get(_as_int(h.get("cislo"))) or (f"návrh {h['pismeno']}" if h.get("pismeno") else None)
        vote = {"o_cem": o_cem, "cislo": h.get("cislo"), "schuze": h.get("schuze"), "datum": h.get("datum"),
                "vysledek": VOTE_WORDS.get(str(h.get("vysledek")), h.get("vysledek")), "pro": h.get("pro"),
                "proti": h.get("proti"), "zdrzel": h.get("zdrzel"), "nehlasoval": h.get("nehlasoval"),
                "kluby": h.get("kluby") or [], "url": h.get("url_psp")}
        votes.append({k: v for k, v in vote.items() if v is not None})
    if (model or {}).get("hlasovani") != votes:
        notes.append(f"pribeh: hlasování ze serveru ({len(votes)})")
    new["hlasovani"] = votes
    stanovisko = srv.get("stanovisko")
    if isinstance(stanovisko, str) and stanovisko.strip():
        new["stanoviska"] = f"garanční výbor: {stanovisko.strip()}"
    story["vysledek"] = new


def normalize(card: dict, caption: str, *, fmt: str | None,
              focus_info: list[dict], server_card: dict | None = None,
              autopilot: bool = False) -> tuple[dict, str, list[str]]:
    """Vrací novou kartu, nový popisek a seznam zásahů; vstupní karta zůstane beze změny.

    focus_info = údaje o zadaných PN z claimu serveru (/pn): autor a klub ke dni podání jdou do
    `pn_autor` (každá varianta, v2 i v3), do `pn.top`, do pruhů `pn.autori` a do popisku; u formátu `pribeh`
    platí výsledek hlasování ze serveru (`pribeh.vysledek`).
    server_card = karta tisku ze serveru (`card_v2` z claimu). Ve variantě v2 (`fmt` None) z ní platí
    `predkladatel` a bez zadaných PN i `pn_autor` (autor PN, ze kterého je titulek, s klubem ke dni podání);
    model je nemůže přepsat. Bez server_card (starší volání) se předkladatel nekontroluje. V3 a příběh PN z ní
    berou pole `kdo` (předkladatel s „a N dalších poslanců“).
    autopilot = úloha autopilota: klub autora, kterého server nepotvrdil (pn.top, pn.autori), zmizí.

    Atribuce (docs/reels-citace-bezpecne.md): `predkladatel.citace` a `pribeh.vysledek.stanoviska` od modelu
    zmizí, počet spolupodepsaných (`spolu`) platí jen ze serveru, zdroj bodu rozpravy se skládá z řečníka a data
    a za jméno autora nebo předkladatele v popisku se doplní „a N dalších poslanců“. Volný text se nepřepisuje.
    """
    card = copy.deepcopy(card)
    notes: list[str] = []
    _split_hook(card, notes)
    _nbsp_hook(card, notes)
    _drop_said(card, notes)
    if fmt == "zmena":
        _drop_uncited(card, notes)
    if fmt == "rozprava":
        _rozprava_sources(card, notes)
    if fmt is not None:
        _set_kdo(card, server_card, notes)
    if fmt == "pribeh" and isinstance(card.get("pribeh"), dict):
        _fix_signs(card["pribeh"], notes)
        _fix_outcome(card["pribeh"], focus_info, notes)
    if CLOSING not in caption:
        caption = f"{caption.rstrip()}\n\n{CLOSING}" if caption.strip() else CLOSING
        notes.append(f"popisek: doplněna věta „{CLOSING}“")
    server = _server_pn(focus_info)
    authors = [pn for pn in server.values() if pn["jmeno"]]
    v2 = fmt is None and isinstance(server_card, dict)
    if v2:
        _set_proposer(card, server_card, notes)
    if not authors and v2 and _headline_authors(server_card):
        _set_authors(card, _headline_authors(server_card), notes, "PN z titulku")
    else:
        _set_authors(card, authors, notes)
    if fmt == "pribeh" and isinstance(card.get("pribeh"), dict):
        _fix_story_author(card["pribeh"], server, notes)
    _fix_top(card, server, notes, autopilot)
    _fix_autori(card, server, notes, autopilot)
    caption = _caption_clubs(card, caption, notes)
    caption = _caption_cosigners(card, caption, notes)
    return card, caption, notes
