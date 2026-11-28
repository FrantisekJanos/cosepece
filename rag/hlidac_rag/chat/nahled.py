"""Náhledy zdrojů a odkazy na originál (spec 6 a 12).

Náhled staví jen z dat, která jsou v databázi webu; z psp.cz se nic nestahuje (robots.txt zakazuje `orig2.sqw`,
`hlasy.sqw` a další). Na psp.cz vedou jen odkazy, na které klikne čtenář: stránka tisku (`historie.sqw`), stránka
dokumentu PN (`sd.sqw`), stránka hlasování (`hlasy.sqw?g=`), stenoprotokol s kotvou z DB a „Word na psp.cz“
z `docx_url`, který ingest uložil z odkazu na stránce dokumentu. Odkaz na stranu PDF se nedělá.

Klíč zdroje se nejdřív ověří regulárním výrazem na typy ze spec 9.1 (`platny_klic`), teprve potom se sáhne do DB.
Texty z DB se vracejí beze změny jako data (JSON, klient je vykreslí přes `textContent`); HTML tu nevzniká.
"""
from __future__ import annotations

import logging
import re

from .. import tools
from ..nastroje.hlasovani import SELECT_HLASOVANI, VYSLEDEK, _kluby_radky, _over_tabulky

log = logging.getLogger("hlidac_rag.chat.nahled")
OBDOBI = tools.OBDOBI
PSP = tools.PSP
ZDROJ = "Poslanecká sněmovna PČR, psp.cz"
# domény, na které smí vést odkaz z odpovědi i náhledu (spec 12)
WHITELIST_DOMEN = ("cosepece.cz", "psp.cz", "senat.cz", "e-sbirka.cz", "zakonyprolidi.cz", "odok.gov.cz", "vlada.cz")
MAX_PROJEV_ZNAKU = 12000
MAX_CEL_ZNAKU = 8000
PREDSTIH_ZV = 1000          # u dlouhého projevu se okno textu otevře kus před zvýrazněním
MAX_TISKU_ZAKONA = 10
TABULKA_RADKU = 6           # tolik krátkých řádků po sobě = buňky tabulky
TABULKA_BUNKA = 30

_C = r"[0-9]"
_OZNACENI = r"[^\x00-\x1f\x7f]{1,80}"
_KLIC = re.compile(
    rf"(?:tisk:(?P<tisk>{_C}{{1,6}})"
    rf"|pn:(?P<pn>{_C}{{1,7}})"
    rf"|cel:tisk:(?P<cel_ct>{_C}{{1,6}}):(?P<verze>puvodni|schvalene):(?P<ozn_tisk>{_OZNACENI})"
    rf"|cel:pn:(?P<cel_pn>{_C}{{1,7}}):(?P<ozn_pn>{_OZNACENI})"
    rf"|projev:(?P<projev>{_C}{{1,3}}/{_C}{{1,5}}/{_C}{{1,7}}/{_C}{{1,5}})"
    rf"|hlas:(?P<hlas>{_C}{{1,9}})"
    rf"|zakon:(?P<zakon>{_C}{{1,4}}/{_C}{{4}})"
    rf"|rozbor:(?P<rozbor_druh>tisk|pn):(?P<rozbor_cislo>{_C}{{1,7}}))")
_PORADI = re.compile(rf"poradi ({_C}{{1,6}})")
_DOCX = re.compile(rf"https://www\.psp\.cz/sqw/text/orig2\.sqw\?idd={_C}{{1,12}}")
_STENO = re.compile(r"https://www\.psp\.cz/[A-Za-z0-9/_.\-]{1,200}(?:#[A-Za-z0-9_\-]{1,40})?")
_ZAVORKA_CASTI = re.compile(r"\s*\(\d+/\d+\)$")
_DATUM = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

STAV_PN = {"prijat": "přijat", "castecne": "přijat částečně", "zamitnut": "zamítnut",
           "nehlasovatelny": "o návrhu se nehlasuje", "nezaznel": "při 3. čtení nezazněl",
           "ceka": "čeká na 3. čtení", "neurceno": "neurčeno"}


# ---------------------------------------------------------------- klíč a odkazy

def _rozloz(klic) -> dict | None:
    """Klíč zdroje → {typ, ...}; neplatný tvar nebo neznámý typ = None (bez dotazu do DB)."""
    if not isinstance(klic, str) or len(klic) > 160:
        return None
    m = _KLIC.fullmatch(klic)
    if m is None:
        return None
    g = m.groupdict()
    if g["tisk"]:
        return {"typ": "tisk", "ct": int(g["tisk"])}
    if g["pn"]:
        return {"typ": "pn", "pn": int(g["pn"])}
    if g["cel_ct"]:
        return {"typ": "cel", "druh": "tisk", "ct": int(g["cel_ct"]), "verze": g["verze"], "oznaceni": g["ozn_tisk"]}
    if g["cel_pn"]:
        return {"typ": "cel", "druh": "pn", "pn": int(g["cel_pn"]), "verze": None, "oznaceni": g["ozn_pn"]}
    if g["projev"]:
        return {"typ": "projev", "klic": g["projev"]}
    if g["hlas"]:
        return {"typ": "hlas", "id": int(g["hlas"])}
    if g["zakon"]:
        return {"typ": "zakon", "sb": g["zakon"]}
    return {"typ": "rozbor", "druh": g["rozbor_druh"], "cislo": int(g["rozbor_cislo"])}


def platny_klic(klic) -> bool:
    return _rozloz(klic) is not None


def klic_nahledu(klic: str, meta: dict | None) -> str:
    """Klíč pro `GET /chat-api/nahled`: u celku se známým pořadím se označení nahradí `poradi N`, protože
    označení („bod 1“) nemusí být v dokumentu jednoznačné."""
    r = _rozloz(klic)
    poradi = (meta or {}).get("poradi")
    if r is None or r["typ"] != "cel" or not isinstance(poradi, int) or isinstance(poradi, bool) or poradi < 0:
        return klic
    if r["druh"] == "tisk":
        return f"cel:tisk:{r['ct']}:{r['verze']}:poradi {poradi}"
    return f"cel:pn:{r['pn']}:poradi {poradi}"


def povoleny_odkaz(url) -> bool:
    """https a doména (nebo její subdoména) z whitelistu spec 12."""
    m = re.fullmatch(r"https://([a-z0-9.\-]+)(?:[/?#].*)?", url) if isinstance(url, str) else None
    if m is None or "orig2" in url:
        return False
    host = m.group(1)
    return any(host == d or host.endswith("." + d) for d in WHITELIST_DOMEN)


def _cislo(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 10**9 else None


def _docx(url) -> str | None:
    return url if isinstance(url, str) and _DOCX.fullmatch(url) else None


def _steno(url) -> str | None:
    return url if isinstance(url, str) and _STENO.fullmatch(url) and povoleny_odkaz(url) else None


def _web_tisk(ct: int | None, kotva: str = "") -> str | None:
    return f"/tisk/{ct}{kotva}" if ct else None


def odkazy(klic: str, meta: dict | None) -> dict:
    """Odkazy zdroje: `web` (adresa webu od kořene), `psp` (stránka na psp.cz) a `docx` („Word na psp.cz“, jen
    z `docx_url` v datech). Hodnota je None, když ji data neumožňují. Nikdy PDF, kopie ani `#page`."""
    meta = meta if isinstance(meta, dict) else {}
    out: dict = {"web": None, "psp": None, "docx": None}
    r = _rozloz(klic)
    if r is None:
        return out
    typ, ct = r["typ"], _cislo(meta.get("ct"))
    if typ == "tisk" or (typ == "cel" and r["druh"] == "tisk"):
        ct = r["ct"]
        out["web"], out["psp"] = f"/tisk/{ct}", f"{PSP}/sqw/historie.sqw?o={OBDOBI}&t={ct}"
    elif typ == "pn" or (typ == "cel" and r["druh"] == "pn") or (typ == "rozbor" and r["druh"] == "pn"):
        pn = r["pn"] if typ != "rozbor" else r["cislo"]
        out["web"] = _web_tisk(ct, f"#pn-{pn}")
        out["psp"] = f"{PSP}/sqw/sd.sqw?cd={pn}&o={OBDOBI}"
    elif typ == "rozbor":
        out["web"], out["psp"] = f"/tisk/{r['cislo']}", f"{PSP}/sqw/historie.sqw?o={OBDOBI}&t={r['cislo']}"
    elif typ == "projev":
        out["psp"] = _steno(meta.get("url"))
    elif typ == "hlas":
        out["web"] = _web_tisk(ct)
        out["psp"] = f"{PSP}/sqw/hlasy.sqw?g={r['id']}"
    elif typ == "zakon":
        out["web"] = "/zakon/" + r["sb"]
    if typ in ("tisk", "pn", "cel"):
        out["docx"] = _docx(meta.get("docx_url") or meta.get("url_docx"))
    return out


# ---------------------------------------------------------------- stavba náhledu

def _vysledek(typ: str, titulek: str, text: str, *, cesta=None, zv=None, znacky=None, predchozi=None, dalsi=None,
              odkazy_=None, tabulka=False, kluby=None, verze=None, zkraceno=False, projev=None) -> dict:
    return {"typ": typ, "titulek": titulek, "cesta": list(cesta or []), "text": text, "zv": zv,
            "znacky": list(znacky or []), "predchozi": predchozi, "dalsi": dalsi,
            "odkazy": odkazy_ or {"web": None, "psp": None, "docx": None}, "tabulka": tabulka, "kluby": kluby,
            "verze": verze, "zkraceno": zkraceno, "projev": projev, "zdroj": ZDROJ}


def _zvyrazneni(zv, delka: int, posun: int = 0) -> list[int] | None:
    """Zvýraznění [od, do] v textu délky `delka` (offsety zadané vůči celému textu, `posun` = začátek okna)."""
    if not zv or len(zv) != 2 or not all(isinstance(x, int) and not isinstance(x, bool) for x in zv):
        return None
    od, do = zv[0] - posun, zv[1] - posun
    return [od, do] if 0 <= od < do <= delka else None


def ma_tabulku(text: str) -> bool:
    """DOCX se převádí po odstavcích a buňky tabulky jsou každá na vlastním řádku (značky tabulky v textu nejsou):
    tabulku prozradí tabulátor, nebo řada krátkých řádků po sobě bez prázdného řádku mezi nimi."""
    if "\t" in text:
        return True
    beh = 0
    for radek in text.split("\n"):
        r = radek.strip()
        beh = beh + 1 if 0 < len(r) <= TABULKA_BUNKA else 0
        if beh >= TABULKA_RADKU:
            return True
    return False


def _datum(s) -> str:
    m = _DATUM.match(str(s or ""))
    return f"{int(m.group(3))}. {int(m.group(2))}. {m.group(1)}" if m else str(s or "")


def _nahled_tisk(conn, r: dict, zv) -> dict | None:
    row = tools._one(conn, """SELECT ct, nazev, uplny_nazev, navrhovatel, faze, predlozeno FROM tisky
                              WHERE ct = %s ORDER BY id_tisk LIMIT 1""", (r["ct"],))
    if row is None:
        return None
    radky = [row["uplny_nazev"] or row["nazev"]]
    if row["navrhovatel"]:
        radky.append(f"Předkladatel: {row['navrhovatel']}")
    if row["faze"]:
        radky.append(f"Fáze: {row['faze']}")
    if row["predlozeno"]:
        radky.append(f"Předloženo: {_datum(row['predlozeno'])}")
    return _vysledek("tisk", f"Tisk {r['ct']}", "\n".join(x for x in radky if x),
                     odkazy_=odkazy(f"tisk:{r['ct']}", {}))


def _nahled_pn(conn, r: dict, zv) -> dict | None:
    row = tools._one(conn, """SELECT p.cislo, p.ct, p.osoba, p.klub, p.cas, v.stav, t.docx_url
                              FROM pn p LEFT JOIN pn_text t ON t.id_dokument = p.id_dokument
                                        LEFT JOIN pn_vysledek v ON v.ct = p.ct AND v.cislo = p.cislo
                              WHERE p.cislo = %s""", (r["pn"],))
    if row is None:
        return None
    autor = " ".join(x for x in (row["osoba"], f"({row['klub']})" if row["klub"] else "") if x)
    radky = [f"Autor: {autor}" if autor else "", f"Podáno: {_datum(row['cas'])}" if row["cas"] else "",
             f"Výsledek: {STAV_PN.get(row['stav'], row['stav'])}" if row["stav"] else ""]
    return _vysledek("pn", f"PN {r['pn']} (tisk {row['ct']})", "\n".join(x for x in radky if x),
                     cesta=[f"Tisk {row['ct']}"],
                     odkazy_=odkazy(f"pn:{r['pn']}", {"ct": row["ct"], "docx_url": row["docx_url"]}))


def _nahled_cel(conn, r: dict, zv) -> dict | None:
    kw: dict = {"max_znaku": MAX_CEL_ZNAKU}
    kw["ct" if r["druh"] == "tisk" else "pn"] = r["ct"] if r["druh"] == "tisk" else r["pn"]
    if r["verze"]:
        kw["verze"] = r["verze"]
    m = _PORADI.fullmatch(r["oznaceni"])
    if m:
        kw["poradi"] = int(m.group(1))
    else:
        kw["oznaceni"] = r["oznaceni"]
    d = tools.get_cel(conn, **kw)
    dok, c = d["dokument"], d["cel"]
    text = c["text"] or ""
    cesta = [s for s in c["cesta"].split(" / ") if s]
    if cesta:
        cesta[-1] = _ZAVORKA_CASTI.sub("", cesta[-1])
    if dok["druh"] == "pn":
        zaklad, titulek = f"cel:pn:{dok['pn']}:", f"PN {dok['pn']}, {c['oznaceni']}"
        meta = {"ct": dok.get("ct")}
        klic_odkazu = f"pn:{dok['pn']}"
    else:
        zaklad, titulek = f"cel:tisk:{dok['ct']}:{dok['verze']}:", f"Tisk {dok['ct']}, {c['oznaceni']}"
        meta = {}
        klic_odkazu = f"tisk:{dok['ct']}"
    meta["docx_url"] = dok.get("url_docx")
    odk = odkazy(klic_odkazu, meta)
    return _vysledek(
        "cel", titulek, text, cesta=cesta, zv=_zvyrazneni(zv, len(text)),
        predchozi=f"{zaklad}poradi {d['predchozi_poradi']}" if d.get("predchozi_poradi") is not None else None,
        dalsi=f"{zaklad}poradi {d['dalsi_poradi']}" if d.get("dalsi_poradi") is not None else None,
        odkazy_=odk, tabulka=ma_tabulku(text), verze=dok.get("verze") if dok["druh"] == "tisk" else None,
        zkraceno=bool(c.get("pokracuje")))


def _nahled_projev(conn, r: dict, zv) -> dict | None:
    posun = 0
    if zv and len(zv) == 2 and isinstance(zv[1], int) and zv[1] > MAX_PROJEV_ZNAKU and isinstance(zv[0], int):
        posun = max(0, zv[0] - PREDSTIH_ZV)
    d = tools.get_projev(conn, r["klic"], max_znaku=MAX_PROJEV_ZNAKU, offset_znaku=posun)
    text = d["text"] or ""
    kdo = " ".join(" ".join(str(d.get("recnik") or "").split()).split())
    if d.get("klub"):
        kdo += f" ({d['klub']})"
    datum = d.get("datum")
    schuze = r["klic"].split("/")[1]
    cesta = [f"{schuze}. schůze"] + ([" ".join(str(d["bod"]).split())] if d.get("bod") else [])
    klic = f"projev:{r['klic']}"
    url = _steno(d.get("url"))
    return _vysledek(
        "projev", f"{kdo}, {_datum(datum)}" if datum else kdo, text, cesta=cesta, zv=_zvyrazneni(zv, len(text), posun),
        znacky=[] if d.get("autorizovano") else ["neautorizovano"], odkazy_=odkazy(klic, {"url": url}),
        zkraceno=bool(d.get("pokracuje")) or d.get("offset_znaku", 0) > 0,
        # metadata ve tvaru `zaznelo_v` z ověření citací (citace._zaznelo_v)
        projev={"recnik": kdo, "klic": klic, "datum": datum, "url": url})


def _nahled_hlas(conn, r: dict, zv) -> dict | None:
    _over_tabulky(conn)
    h = tools._one(conn, f"{SELECT_HLASOVANI} WHERE h.id_hlasovani = %s", (r["id"],))
    if h is None:
        return None
    kluby = [{k: v for k, v in kr.items() if k != "id_hlasovani"} for kr in _kluby_radky(conn, [r["id"]])]
    vysledek = VYSLEDEK.get(h["vysledek"], "neznámý")
    radky = [f"{vysledek[:1].upper()}{vysledek[1:]}: pro {h['pro']}, proti {h['proti']}, zdrželo se {h['zdrzel']}, "
             f"nehlasovalo {h['nehlasoval']}.", f"Přihlášeno {h['prihlaseno']}, kvórum {h['kvorum']}."]
    if h.get("nazev_hlasovani"):
        radky.append(f"Bod: {h['nazev_hlasovani']}")
    ct = h["ct"] if h["ct"] else None
    return _vysledek(
        "hlasovani", f"Hlasování č. {h['cislo']}, {_datum(h['datum'])}", "\n".join(radky),
        cesta=[f"{h['schuze']}. schůze"] + ([f"Tisk {ct}"] if ct else []),
        odkazy_=odkazy(f"hlas:{r['id']}", {"ct": ct}), kluby=kluby)


def _nahled_zakon(conn, r: dict, zv) -> dict | None:
    tisky = tools._rows(conn, """SELECT ct, nazev FROM tisky WHERE %s = ANY(string_to_array(zakony, ','))
                                 ORDER BY score DESC NULLS LAST, ct LIMIT %s""", (r["sb"], MAX_TISKU_ZAKONA))
    if not tisky:
        return None
    return _vysledek("zakon", f"Zákon {r['sb']} Sb.", "\n".join(f"Tisk {t['ct']}: {t['nazev']}" for t in tisky),
                     cesta=["Tisky, které zákon mění"], odkazy_=odkazy(f"zakon:{r['sb']}", {}))


def _nahled_rozbor(conn, r: dict, zv) -> dict | None:
    if r["druh"] == "tisk":
        row = tools._one(conn, "SELECT ai_json FROM tisk_text WHERE ct = %s", (r["cislo"],))
        meta, titulek = {}, f"Rozbor AI, tisk {r['cislo']}"
    else:
        row = tools._one(conn, "SELECT p.ct, t.ai_json FROM pn p JOIN pn_text t ON t.id_dokument = p.id_dokument "
                               "WHERE p.cislo = %s", (r["cislo"],))
        meta, titulek = {"ct": row and row["ct"]}, f"Rozbor AI, PN {r['cislo']}"
    ai = tools._json(row["ai_json"]) if row else None
    if not isinstance(ai, dict) or not (ai.get("titulek") or ai.get("shrnuti")):
        return None
    text = "\n".join(str(x) for x in (ai.get("titulek"), ai.get("shrnuti")) if x)
    return _vysledek("rozbor", titulek, text, znacky=["odvozeny_ai"],
                     odkazy_=odkazy(f"rozbor:{r['druh']}:{r['cislo']}", meta))


_STAVBY = {"tisk": _nahled_tisk, "pn": _nahled_pn, "cel": _nahled_cel, "projev": _nahled_projev,
           "hlas": _nahled_hlas, "zakon": _nahled_zakon, "rozbor": _nahled_rozbor}


def nahled(conn, klic: str, zv: tuple[int, int] | None = None) -> dict | None:
    """Náhled zdroje z DB; None = neplatný klíč, zdroj nebo jeho text v datech není. Chyba databáze se nechytá."""
    r = _rozloz(klic)
    if r is None:
        return None
    try:
        return _STAVBY[r["typ"]](conn, r, zv)
    except ValueError:      # nástroje hlásí chybějící dokument, text nebo tabulky čitelnou ValueError
        log.debug("náhled %s: zdroj nebo jeho text není v datech", klic, exc_info=True)
        return None
