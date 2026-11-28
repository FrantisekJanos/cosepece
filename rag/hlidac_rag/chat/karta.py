"""Karta kontextu obrazovky pro model (spec 5.4): co je na stránce, na které se uživatel ptá.

Karta se skládá jen z dat v databázi podle ověřeného `Kontext` (kontext.over), nikdy z textu stránky. Vejde se
do `max_tokenu` (odhad historie.odhad_tokenu): nejdřív se zkracuje seznam PN (tisky u zákona) a řádek o počtu řekne,
kolik karta ukazuje a jakým nástrojem se dostat k ostatním. Položky karty jdou do registru zdrojů. Karty se
v procesu cachují 5 minut. Chyba databáze nebo neexistující entita = bez karty (None), tah pokračuje.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

import psycopg

from .. import tools
from ..nastroje.hlasovani import _get_hlasovani, _hlasovani_k_tisku
from .historie import ZNAKU_NA_TOKEN, odhad_tokenu
from .kontext import Kontext
from .nastroje import Polozka, _datum, _hlas, _jednoradkove, _pn, _rozbor, _tisk, _zakon

log = logging.getLogger("hlidac_rag.chat.karta")

CACHE_S = 300
MAX_CACHE = 1000
PN_NA_KARTE = 15
TISKU_NA_KARTE = 10
OSNOVA_NA_KARTE = 12
DUVODU_SKORE = 5
ZKRACENO = "[karta zkrácena, další údaje přes nástroje]"
VYSLEDEK_NEZNAMY = "údaj teď nemám"
# výsledek hlasování v pn_vysledek.hlasovani (app/PnOutcome.php): A přijato, jinak nepřijato
VYSLEDEK_HLASOVANI = {"A": "přijato", "N": "nepřijato", "R": "nepřijato"}

VETY_STRANEK = {
    "prehled": "Uživatel je na titulní stránce (přehled nálezů).",
    "seznam_tisku": "Uživatel je na stránce se seznamem tisků.",
    "zakony": "Uživatel je na stránce s přehledem zákonů.",
    "hledani": "Uživatel je na stránce hledání.",
    "upozorneni": "Uživatel je na stránce upozornění.",
    "pripravovane": "Uživatel je na stránce připravovaných zákonů.",
    "staticka": "Uživatel je na informační stránce webu.",
}


def _ma_oduvodneni(conn, pn: int) -> bool:
    """Text PN má odůvodnění (celek typu duvodova_zprava, texty/parser.py)."""
    if not tools._texty_ok(conn):
        return False
    return bool(tools._one(conn, """SELECT EXISTS (SELECT 1 FROM texty.cel c JOIN texty.dokument d ON d.id = c.dokument_id
                                                   WHERE d.druh = 'pn' AND d.cislo_pn = %s
                                                     AND c.typ = 'duvodova_zprava') AS ok""", (pn,))["ok"])


# čtecí funkce (conn první argument); test je podstrkává přes monkeypatch.setitem
_FUNKCE: dict[str, Callable] = {
    "get_tisk": tools.get_tisk, "pn_k_tisku": tools.pn_k_tisku, "get_pn": tools.get_pn,
    "osnova_textu": tools.osnova_textu, "ma_oduvodneni": _ma_oduvodneni,
    "hlasovani_k_tisku": _hlasovani_k_tisku, "get_hlasovani": _get_hlasovani,
    "tisky_menici_zakon": tools.tisky_menici_zakon, "pripravovane": tools.pripravovane,
}

_cache: dict[tuple[Kontext, int], tuple[float, str | None, list[Polozka]]] = {}


def _cas() -> float:
    return time.monotonic()


def vycisti_cache() -> None:
    _cache.clear()


def _z(n: int) -> str:
    """Předložka před číslovkou v 2. pádě: „ze 40“ (čtyřiceti), „z 25“ (dvaceti pěti), „ze 132“ (sto třiceti…)."""
    s = str(n)
    if len(s) == 2 and s[0] == "1":
        return "ze" if s in ("13", "14", "16", "17") else "z"
    if len(s) == 3 and s[0] == "1":
        return "ze"   # sto…
    return "ze" if s[0] in "3467" else "z"


def _sloz(hlavni: list[str], seznam: list[str], hlavicka: Callable[[int], str] | None,
          max_tokenu: int) -> tuple[str, int, int]:
    """Text karty do max_tokenu (>= 1), počet ukázaných položek seznamu a počet zachovaných řádků `hlavni`.
    Zkracuje se seznam od konce; když se karta nevejde ani bez něj, odříznou se poslední řádky a karta to řekne."""
    for k in range(len(seznam), -1, -1):
        radky = hlavni + ([hlavicka(k)] if hlavicka else []) + seznam[:k]
        text = "\n".join(radky)
        if odhad_tokenu(text) <= max_tokenu:
            return text, k, len(hlavni)
    radky = list(hlavni)
    while len(radky) > 1 and odhad_tokenu("\n".join(radky + [ZKRACENO])) > max_tokenu:
        radky.pop()
    zachovano = len(radky)
    text = "\n".join(radky + [ZKRACENO])
    if odhad_tokenu(text) > max_tokenu:   # jediný řádek delší než celá karta (malý strop)
        text = text[:max(int(max_tokenu * ZNAKU_NA_TOKEN) - 1, 0)] + "…"
        while len(text) > 1 and odhad_tokenu(text) > max_tokenu:
            text = text[:-2] + "…"
    return text, 0, zachovano


def _sloz_sekce(sekce: list[tuple[list[str], list[Polozka]]], seznam: list[str],
                hlavicka: Callable[[int], str] | None, max_tokenu: int) -> tuple[str, int, list[Polozka]]:
    """Jako _sloz nad částmi karty (řádky, položky). Položky části, z níž ořez nenechal ani první řádek, vypadnou:
    do registru jde jen to, co model v kartě vidí."""
    hlavni = [r for radky, _ in sekce for r in radky]
    text, ukazano, zachovano = _sloz(hlavni, seznam, hlavicka, max_tokenu)
    polozky, od = [], 0
    for radky, p in sekce:
        if radky and od < zachovano:
            polozky += p
        od += len(radky)
    return text, ukazano, polozky


def _t(hodnota, n: int = 40) -> str:
    """Pole z databáze na jeden řádek a nejvýš n znaků (i čísla a krátké kódy: data nejsou pod kontrolou karty)."""
    return _jednoradkove("" if hodnota is None else str(hodnota), n)   # 0 je číslo, ne prázdná hodnota


def _hlavicka_seznamu(celkem: int, co: str, jednotka: str, nastroj: str, prazdne: str) -> Callable[[int], str]:
    """Řádek nad seznamem: kolik položek je celkem, kolik karta ukazuje a jak se dostat k ostatním."""
    def hlavicka(k: int) -> str:
        if not celkem:
            return prazdne
        if k >= celkem:
            return f"{co}: {celkem} (všechny níže)."
        return f"{co}: {celkem}; karta ukazuje {k} {_z(celkem)} {celkem}{jednotka}, ostatní přes `{nastroj}`."
    return hlavicka


def _veta(casti: list[str]) -> str:
    """Neprázdné části spojené středníkem, velké první písmeno (zbytek beze změny) a tečka."""
    s = "; ".join(x for x in casti if x)
    if not s:
        return ""
    return s[:1].upper() + s[1:] + ("" if s.endswith(".") else ".")


def _vysledek_pn(v: dict | None, detail: bool) -> str:
    if not v:
        return VYSLEDEK_NEZNAMY
    out = f"{_t(v.get('stav'), 20)} ({_t(v.get('popis') or v.get('stav'), 60)})"
    pismena = [_t(p.get("pismeno"), 10) for p in v.get("pismena") or [] if isinstance(p, dict) and p.get("pismeno")]
    if pismena:
        out += f"; písmena v souhrnu: {', '.join(pismena[:20])}"
    if detail:
        hl = [f"č. {_t(h.get('cislo'), 10)} ({_t(VYSLEDEK_HLASOVANI.get(h.get('vysledek'), h.get('vysledek')), 20)})"
              for h in v.get("hlasovani") or [] if isinstance(h, dict)]
        if hl:
            out += f"; hlasování: {', '.join(hl[:10])}"
        if v.get("stanovisko"):
            out += f"; stanovisko výboru: {_t(v['stanovisko'])}"
    return out


# ---------------------------------------------------------------- části karty

def _radky_tisku(conn, ct: int) -> tuple[list[str], list[Polozka]]:
    t = _FUNKCE["get_tisk"](conn, ct, vcetne=["zakony", "kroky", "upozorneni"])
    radky = [f"Kontext obrazovky: uživatel je na stránce tisku {ct} na cosepece.cz.",
             f"Tisk {ct}: {_jednoradkove(t.get('nazev'), 200)}"]
    if t.get("poznamka"):
        radky.append(_jednoradkove(t["poznamka"]))
    zaklad = [f"typ návrhu {_t(t['typ_navrhu'])}" if t.get("typ_navrhu") else "",
              f"předkladatel {_jednoradkove(t['navrhovatel'], 120)}" if t.get("navrhovatel") else "",
              f"předloženo {_datum(t['predlozeno'])}" if t.get("predlozeno") else ""]
    if any(zaklad):
        radky.append(_veta(zaklad))
    faze = f"Fáze: {_jednoradkove(t['faze'], 60)}" if t.get("faze") else ""
    kroky = t.get("kroky") or []
    if kroky:
        k = kroky[-1]
        krok = " / ".join(x for x in (k.get("stav"), k.get("akce")) if x)
        faze = (faze + "; " if faze else "") + f"poslední krok {_datum(k.get('datum'))}: {_jednoradkove(krok, 120)}"
    if faze:
        radky.append(faze + ".")
    if t.get("score") is not None:
        duvody = sorted((d for d in t.get("reasons") or [] if isinstance(d, dict)),
                        key=lambda d: -(d.get("points") or 0))[:DUVODU_SKORE]
        skore = f"Skóre webu {_t(t['score'], 5)}/100 (hodnotí postup, ne obsah)"
        if duvody:
            skore += "; důvody: " + "; ".join(f"{_jednoradkove(d.get('label'), 90)} (+{_t(d.get('points'), 5)})" for d in duvody)
        radky.append(skore + ".")
    zakony = t.get("zakony") or []
    z_pn = [z for z in t.get("zakony_z_pn") or [] if z not in zakony]
    if zakony or z_pn:
        radky.append(_veta(["Mění zákony: " + ", ".join(f"{_t(z, 15)} Sb." for z in zakony[:15]) if zakony else "",
                            "PN mění i: " + ", ".join(f"{_t(z, 15)} Sb." for z in z_pn[:10]) if z_pn else ""]))
    if t.get("sbirka"):
        radky.append(f"Vyhlášen ve Sbírce zákonů jako {_t(t['sbirka'], 20)}.")
    if "upozorneni" in t:
        radky.append(f"Upozornění webu k tisku: {len(t['upozorneni'])}.")
    return radky, [_tisk(t)]


def _radky_posledniho_hlasovani(conn, ct: int) -> tuple[list[str], list[Polozka]]:
    try:
        celkem = _FUNKCE["hlasovani_k_tisku"](conn, ct, limit=1)["celkem"]
        if not celkem:
            return ["Hlasování k tisku: žádné v datech."], []
        h = _FUNKCE["hlasovani_k_tisku"](conn, ct, limit=1, offset=celkem - 1)["hlasovani"][0]
    except (ValueError, IndexError, KeyError):
        return [], []     # tabulky hlasování chybí (čitelná ValueError) nebo neúplná odpověď
    radek = (f"Hlasování k tisku: {_t(celkem, 10)}; poslední č. {_t(h.get('cislo'), 10)} "
             f"({_t(_datum(h.get('datum')), 20)}): {_t(h.get('vysledek'), 20)}, pro {_t(h.get('pro'), 5)}, "
             f"proti {_t(h.get('proti'), 5)}, zdrželo se {_t(h.get('zdrzel'), 5)}")
    if h.get("krok"):
        radek += f"; krok: {_jednoradkove(h['krok'], 100)}"
    return [radek + ". Přehled přes `hlasovani_k_tisku`."], [_hlas({**h, "ct": ct})]


def _radky_pn(conn, pn: int) -> tuple[list[str], list[Polozka]]:
    d = _FUNKCE["get_pn"](conn, pn)
    autor = _jednoradkove(d.get("osoba"), 80) + (f" ({_t(d['klub'], 20)})" if d.get("klub") else "")
    radky = [f"Zaostřeno na PN {_t(d['cislo'], 10)} k tisku {_t(d.get('ct'), 10)}: předkladatel {autor}"
             + (f", podán {_t(_datum(d['cas']), 20)}" if d.get("cas") else "") + ".",
             f"Výsledek ve 3. čtení: {_vysledek_pn(d.get('vysledek'), detail=True)}."]
    if d.get("laws"):
        radky.append(_veta(["PN mění zákony: " + ", ".join(f"{_t(z, 15)} Sb." for z in str(d["laws"]).split(",")[:15] if z)]))
    polozky = [_pn(d, text_dostupny=bool(d.get("osnova_dostupna")))]
    ai = d.get("ai")
    if isinstance(ai, dict) and (ai.get("titulek") or ai.get("shrnuti")):
        rozbor = ". ".join(_jednoradkove(x, 220) for x in (ai.get("titulek"), ai.get("shrnuti")) if x)
        if d.get("ai_score") is not None:
            rozbor += f" Závažnost {_t(d['ai_score'], 5)}/10."
        radky.append(f"Rozbor PN (odvozený text AI, ne fakt; ověřuj v textu PN): {rozbor}")
        polozky.append(_rozbor("pn", d["cislo"]))
    praxe = d.get("v_praxi")
    if isinstance(praxe, dict) and praxe.get("co_se_meni"):
        radky.append(f"V praxi (odvozený text AI): {_jednoradkove(praxe['co_se_meni'], 220)}")
    if d.get("osnova_dostupna"):
        try:
            o = _FUNKCE["osnova_textu"](conn, pn=pn, limit=OSNOVA_NA_KARTE)
            celky = [_jednoradkove(c.get("oznaceni") or c.get("cesta"), 40) for c in o.get("celky") or []]
            radky.append(f"Osnova textu PN ({_t(o.get('celkem'), 10)} celků; celé přes `osnova_textu` a `get_cel`): "
                         + "; ".join(c for c in celky if c) + ".")
        except ValueError:
            pass
        oduvodneni = _FUNKCE["ma_oduvodneni"](conn, pn)
        radky.append(f"Příznaky: {'má odůvodnění' if oduvodneni else 'bez odůvodnění v textu'}.")
    else:
        radky.append("Příznaky: text PN zatím nemáme (dokument na psp.cz existuje).")
    return radky, polozky


def _radky_hlasovani(conn, id_hlasovani: int) -> tuple[list[str], list[Polozka]]:
    try:
        h = _FUNKCE["get_hlasovani"](conn, id_hlasovani)
    except ValueError:
        return [], []
    pole = {k: _t(h.get(k), 10) for k in ("cislo", "schuze", "pro", "proti", "zdrzel", "nehlasoval", "prihlaseno",
                                          "kvorum")}
    radky = [f"Zaostřeno na hlasování č. {pole['cislo']} ({pole['schuze']}. schůze, {_t(_datum(h.get('datum')), 20)}): "
             f"{_t(h.get('vysledek'), 20)}; pro {pole['pro']}, proti {pole['proti']}, zdrželo se {pole['zdrzel']}, "
             f"nehlasovalo {pole['nehlasoval']}; přihlášeno {pole['prihlaseno']}, kvórum {pole['kvorum']}."]
    if h.get("krok"):
        radky.append(f"Krok historie tisku: {_jednoradkove(h['krok'], 100)}.")
    kluby = [k for k in h.get("kluby") or [] if isinstance(k, dict)]
    if kluby:
        radky.append("Kluby (pro/proti/zdržel se/nepřítomen): " + ", ".join(
            f"{_t(k.get('klub'), 20)} " + "/".join(_t(k.get(s), 5) for s in ("pro", "proti", "zdrzel", "nepritomen"))
            for k in kluby[:20]) + ".")
    return radky, [_hlas(h)]


# ---------------------------------------------------------------- karty podle typu stránky

def _karta_tisku(conn, k: Kontext, max_tokenu: int) -> tuple[str, list[Polozka]]:
    sekce = [_radky_tisku(conn, k.ct)]
    if k.pn:
        sekce.append(_radky_pn(conn, k.pn))
    sekce.append(_radky_hlasovani(conn, k.hlasovani) if k.hlasovani else _radky_posledniho_hlasovani(conn, k.ct))
    d = _FUNKCE["pn_k_tisku"](conn, k.ct, limit=PN_NA_KARTE)
    pn = d.get("pn") or []
    seznam = [f"- PN {_t(r['cislo'], 10)} · {_jednoradkove(r.get('osoba'), 60)}"
              + (f" ({_t(r['klub'], 20)})" if r.get("klub") else "")
              + f" · výsledek: {_vysledek_pn(r.get('vysledek'), detail=False)}"
              + (f" · titulek AI: {_jednoradkove(r['titulek'], 90)}" if r.get("titulek") else "") for r in pn]
    hlavicka = _hlavicka_seznamu(d.get("celkem") or 0, "Pozměňovací návrhy", " PN", "pn_k_tisku",
                                 "Pozměňovací návrhy: žádné v datech.")
    text, ukazano, polozky = _sloz_sekce(sekce, seznam, hlavicka, max_tokenu)
    return text, polozky + [_pn(r, ct=k.ct) for r in pn[:ukazano]]


def _karta_zakona(conn, k: Kontext, max_tokenu: int) -> tuple[str, list[Polozka]]:
    d = _FUNKCE["tisky_menici_zakon"](conn, k.z, limit=TISKU_NA_KARTE)
    tisky = d.get("tisky") or []
    hlavni = [f"Kontext obrazovky: uživatel je na stránce zákona {k.z} Sb. na cosepece.cz."]
    seznam = [f"- tisk {_t(r['ct'], 10)}: {_jednoradkove(r.get('nazev'), 140)}"
              + (f" · fáze {_t(r['faze'], 60)}" if r.get("faze") else "")
              + (f" · skóre {_t(r['score'], 5)}" if r.get("score") is not None else "") for r in tisky]
    hlavicka = _hlavicka_seznamu(d.get("celkem") or 0, "Tisky, které zákon mění", "", "tisky_menici_zakon",
                                 f"Žádný tisk 10. období, který web sleduje, zákon {k.z} Sb. podle dat nemění.")
    text, ukazano, _ = _sloz(hlavni, seznam, hlavicka, max_tokenu)
    return text, [_zakon(k.z)] + [_tisk(r) for r in tisky[:ukazano]]


def _karta_pripravovaneho(conn, k: Kontext, max_tokenu: int) -> tuple[str, list[Polozka]]:
    materialy = _FUNKCE["pripravovane"](conn, pid=k.pid, limit=1).get("materialy") or []
    if not materialy:
        return VETY_STRANEK["pripravovane"], []
    r = materialy[0]
    udaje = [f"resort {_jednoradkove(r['resort'], 80)}" if r.get("resort") else "",
             f"fáze {_t(r['faze'], 20)}" if r.get("faze") else "", f"stav {_jednoradkove(r['stav'], 80)}" if r.get("stav") else "",
             f"připomínky do {_datum(r['pripominky_do'])}" if r.get("pripominky_do") else ""]
    hlavni = [f"Kontext obrazovky: uživatel je na stránce připravovaných zákonů u materiálu {_t(r['pid'], 40)}.",
              f"Připravovaný zákon (u vlády, ještě ne ve Sněmovně): {_jednoradkove(r.get('nazev'), 200)}.",
              _veta(udaje)]
    text, _, _ = _sloz([r for r in hlavni if r], [], None, max_tokenu)
    polozka = Polozka(f"prip:{r['pid']}", "pripravovane", f"Připravovaný zákon: {_jednoradkove(r.get('nazev'), 80)}", None,
                      {"url": r.get("url"), "faze": r.get("faze")})
    return text, [polozka]


def karta_kontextu(conn, k: Kontext, max_tokenu: int) -> tuple[str | None, list[Polozka]]:
    """Karta ověřeného kontextu (kontext.over) a její položky pro registr; nikdy nevyhodí.

    Neznámá stránka (`jina`) = bez karty. Seznamy a statické stránky = jen název stránky (bez databáze). Tisk,
    zákon a připravovaný materiál z databáze, cache 5 minut. Chyba databáze, neexistující entita nebo strop
    max_tokenu <= 0 = (None, [])."""
    if k.typ == "jina" or max_tokenu <= 0:
        return None, []
    if k.typ not in ("tisk", "zakon") and not (k.typ == "pripravovane" and k.pid):
        veta = VETY_STRANEK.get(k.typ)
        return (veta, []) if veta and odhad_tokenu(veta) <= max_tokenu else (None, [])
    klic = (Kontext(k.typ, ct=k.ct, pn=k.pn, hlasovani=k.hlasovani, z=k.z, pid=k.pid), max_tokenu)
    ted = _cas()
    ulozeno = _cache.get(klic)
    if ulozeno and ted - ulozeno[0] < CACHE_S:
        return ulozeno[1], list(ulozeno[2])
    try:
        if k.typ == "tisk" and k.ct:
            text, polozky = _karta_tisku(conn, k, max_tokenu)
        elif k.typ == "zakon" and k.z:
            text, polozky = _karta_zakona(conn, k, max_tokenu)
        elif k.typ == "pripravovane":
            text, polozky = _karta_pripravovaneho(conn, k, max_tokenu)
        else:
            return None, []
    except ValueError:
        return None, []      # entita mezitím zmizela (hodinový přepis tabulek webu)
    except psycopg.Error:
        try:
            conn.rollback()
        except (psycopg.Error, AttributeError):
            pass
        log.warning("karta kontextu %s selhala na chybě databáze", k, exc_info=True)
        return None, []
    except Exception:      # rozbité spojení nebo nečekaný tvar dat: tah pokračuje bez karty
        log.exception("karta kontextu %s selhala", k)
        return None, []
    if len(_cache) >= MAX_CACHE:
        for kl in [kl for kl, v in _cache.items() if ted - v[0] >= CACHE_S]:
            del _cache[kl]
        if len(_cache) >= MAX_CACHE:
            _cache.clear()
    _cache[klic] = (ted, text, list(polozky))
    return text, polozky
