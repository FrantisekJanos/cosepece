"""Deterministická normalizace karty a popisku od modelu před kontrolou.

Fakta se nemění: text modelu se jen přeskládá nebo zkrátí a doplní se údaje ze serveru. Každý zásah
se vrací jako věta pro `variant-N/normalize.json`.
"""
from __future__ import annotations

import copy
import re

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
        out.append({"cislo": cislo, "jmeno": jmeno.strip(),
                    "klub": klub.strip() if isinstance(klub, str) and klub.strip() else None})
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
    if card.get("predkladatel") == who:
        return
    card["predkladatel"] = copy.deepcopy(who)
    notes.append(f"predkladatel: ze serveru ({who['jmeno']})")


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


def _fix_story_author(story: dict, server: dict[int, dict], notes: list[str]) -> None:
    """V `pribeh.autor` platí autor a klub ze serveru (klub ke dni podání), stejně jako v `pn.top`.

    Klub od modelu (MCP vrací poslední klub) bez údaje serveru zmizí, autor bez údaje serveru zůstane."""
    pn = server.get(_as_int(story.get("cislo")))
    autor = story.get("autor")
    if pn is None or not isinstance(autor, dict):
        return
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

    Od modelu zůstane jen popis (`text`, `o_cem` u stejného čísla hlasování, `dal`, `stanoviska`); text ke stavu,
    který server neuvádí, zmizí. Bez výsledku ze serveru (null) zůstane nejvýš „čeká“, výsledek „neurceno“ vypadne
    celý: verdikt bez doloženého hlasování šablona ukázat nesmí.
    """
    known, srv = _server_outcome(story, focus_info)
    if not known:
        return
    model = story.get("vysledek") if isinstance(story.get("vysledek"), dict) else None
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
    story["vysledek"] = new


def normalize(card: dict, caption: str, *, fmt: str | None,
              focus_info: list[dict], server_card: dict | None = None) -> tuple[dict, str, list[str]]:
    """Vrací novou kartu, nový popisek a seznam zásahů; vstupní karta zůstane beze změny.

    focus_info = údaje o zadaných PN z claimu serveru (/pn): autor a klub ke dni podání jdou do
    `pn_autor` (každá varianta, v2 i v3), do `pn.top`, do pruhů `pn.autori` a do popisku; u formátu `pribeh`
    platí výsledek hlasování ze serveru (`pribeh.vysledek`).
    server_card = karta tisku ze serveru (`card_v2` z claimu). Ve variantě v2 (`fmt` None) z ní platí
    `predkladatel` a bez zadaných PN i `pn_autor` (autor PN, ze kterého je titulek, s klubem ke dni podání);
    model je nemůže přepsat. Bez server_card (starší volání) se předkladatel nekontroluje.
    """
    card = copy.deepcopy(card)
    notes: list[str] = []
    _split_hook(card, notes)
    _nbsp_hook(card, notes)
    if fmt == "zmena":
        _drop_uncited(card, notes)
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
    _fix_top(card, server, notes)
    _fix_autori(card, server, notes)
    caption = _caption_clubs(card, caption, notes)
    return card, caption, notes
