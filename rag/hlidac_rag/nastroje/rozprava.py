"""Nástroj rozprava: celá rozprava k tisku v pořadí, jak zazněla.

Vystoupení = nezrušené nepředsedající projevy tisku (steno.projev_tisk), řazené datum, schůze, pořadí a po filtrech
očíslované 1..N, v obou formátech stejně. Souhrn po řečnících a klubech je vždy přes celou filtrovanou rozpravu,
nezávisle na stránce. druh, cteni a reaguje_na_odhad jsou odvozené automaticky (rozprava_prepocet, tabulka kroky).
"""
from __future__ import annotations

import json

from ..psp.rozprava_pravidla import DRUHY, RE_FAKTICKA
from ..tools import _one, _rows, _speech_meta, _tisk_urls, unaccent_py

FORMATY = ("prehled", "plny")
CTENI = ("1", "2", "3", "senat", "prezident")
DRUHY_FILTRU = tuple(d for d in DRUHY if d != "predsedajici")   # předsedající nejsou vystoupení
MIN_ROZPOCET, MAX_ROZPOCET = 1000, 40000
STROP = 60000      # = mcp_server.MAX_ODPOVED; import by byl kruhový (mcp_server načítá tento modul)
REZERVA = 200      # na číslo dalsi_od_poradi a oddělovače řádků
OHLASENI = 300     # nejvýš znaků konce ohlášení faktické poznámky
POZNAMKA = ("druh, cteni a reaguje_na_odhad jsou odvozené automaticky z textu předsedajícího, řečníka a historie "
            "tisku, ověřujte je v plném textu.")

# čtení k datu projevu (tabulka kroky webu, datum je varchar(19)), poslední z kroků: 1. čtení kterýkoli krok
# „1. čtení…“ (u § 90/2 je jen „1. čtení (§ 90, odst. 2) - ob. rozprava | doporučen“, nikdy „zahájeno“),
# návrat do Sněmovny po Senátu či prezidentovi, 2. a 3. čtení jen jejich začátek = nejdřívější krok skutečného
# projednávání (akce zahájeno 238, projednávání ukončeno 71, ukončena 23; jako Scorer.php): „zahájeno“ bývá
# v historii datované až po „projednávání ukončeno“ (tisk 198), pozdější krok téhož čtení se nepočítá
_KROKY = r"""WITH s AS (
    SELECT left(datum, 10)::date AS den, datum, id_hist,
           CASE WHEN stav LIKE 'Sněmovna (Senát)%%' THEN 'senat'
                WHEN stav LIKE 'Sněmovna (Prezident)%%' THEN 'prezident' ELSE left(stav, 1) END AS cteni
    FROM kroky
    WHERE ct = %s AND datum ~ '^\d{4}-\d{2}-\d{2}'
      AND (stav ~ '^1\. čtení'
           OR (stav ~ '^[23]\. čtení' AND (akce IN ('zahájeno', 'projednávání ukončeno', 'ukončena')
                                           OR akce_id IN (238, 71, 23)))
           OR stav LIKE 'Sněmovna (Senát)%%' OR stav LIKE 'Sněmovna (Prezident)%%')),
k AS MATERIALIZED (
    SELECT den, datum, id_hist, cteni
    FROM (SELECT s.*, row_number() OVER (PARTITION BY cteni ORDER BY datum, id_hist) AS n FROM s) x
    WHERE cteni NOT IN ('2', '3') OR n = 1)
"""
_CTENI = "(SELECT k.cteni FROM k WHERE k.den <= p.datum ORDER BY k.datum DESC, k.id_hist DESC LIMIT 1)"
_VYSTOUPENI = """SELECT p.id, p.schuze, p.turn, p.aname, p.datum, p.id_osoba, p.recnik,
       nullif(concat_ws(' ', o.jmeno, o.prijmeni), '') AS jmeno, p.klub, p.druh, p.znaku, p.autorizovano,
       p.reaguje_na_odhad, left(p.text, 150) AS zacatek,
       ARRAY(SELECT n.cislo_pn FROM steno.projev_pn n WHERE n.projev_id = p.id ORDER BY n.cislo_pn) AS pn_zminene,
       {cteni} AS cteni
FROM steno.projev p LEFT JOIN steno.osoba o ON o.id_osoba = p.id_osoba
WHERE p.zruseno IS NULL AND NOT p.predsedajici
  AND p.id IN (SELECT projev_id FROM steno.projev_tisk WHERE ct = %s)
ORDER BY p.datum, p.schuze, p.poradi, p.id"""
# celé texty stránky; ohlaseni = konec bezprostředně předchozího nezrušeného projevu schůze,
# je-li to předsedající téhož dne
_TEXTY = f"""SELECT p.id, p.text, p.url,
       CASE WHEN q.predsedajici AND q.datum = p.datum THEN right(q.text, {OHLASENI}) END AS ohlaseni
FROM steno.projev p
LEFT JOIN LATERAL (SELECT q.predsedajici, q.datum, q.text FROM steno.projev q
                   WHERE q.obdobi = p.obdobi AND q.schuze = p.schuze AND q.zruseno IS NULL
                     AND (q.poradi, q.id) < (p.poradi, p.id)
                   ORDER BY q.poradi DESC, q.id DESC LIMIT 1) q ON true
WHERE p.id = ANY(%s)"""
_NAZEV_BODU = "SELECT nazev FROM steno.bod WHERE ct = %s AND nazev IS NOT NULL ORDER BY schuze, cislo LIMIT 1"


def _norm(s) -> str:
    return unaccent_py(str(s).strip().lower())


def _velikost(x) -> int:
    """Délka JSON stejně jako u stropu odpovědi v mcp_server._call."""
    return len(json.dumps(x, ensure_ascii=False, separators=(",", ":"), default=str))


def _kompaktni(d: dict) -> dict:
    """Bez polí s prázdnou hodnotou (None, "", [], {}, False)."""
    return {k: v for k, v in d.items() if v is not False and v not in (None, "", [], {})}


def rozprava(conn, ct: int, format: str = "prehled", osoby: list[int] | None = None, cteni: str | None = None,
             druh: list[str] | None = None, od_poradi: int = 0, max_znaku: int = 30000) -> dict:
    """Vystoupení k tisku v pořadí (přehled nebo plné texty) do rozpočtu max_znaku a úplný souhrn rozpravy."""
    fmt = _norm(format)
    if fmt not in FORMATY:
        raise ValueError(f"Neznámý format „{format}“. Platné: prehled (řádky se začátkem textu), plny (celé texty).")
    druhy = {_norm(d) for d in druh or []}
    if druhy - set(DRUHY_FILTRU):
        raise ValueError(f"Neznámý druh: {', '.join(sorted(druhy - set(DRUHY_FILTRU)))}. "
                         f"Platné: {', '.join(DRUHY_FILTRU)}.")
    faze = _norm(cteni) if cteni not in (None, "") else None
    if faze is not None and faze not in CTENI:
        raise ValueError(f"Neznámé cteni „{cteni}“. Platné: 1, 2, 3, senat (Sněmovna znovu po Senátu), "
                         "prezident (Sněmovna znovu po vetu prezidenta).")

    tab = _one(conn, "SELECT to_regclass('tisky') IS NOT NULL AS tisky, to_regclass('kroky') IS NOT NULL AS kroky")
    if faze and not tab["kroky"]:
        raise ValueError("Filtr cteni teď nejde použít: chybí historie tisků (tabulka kroky), čtení nelze určit. "
                         "Vynechte cteni.")
    if tab["kroky"]:
        vse = _rows(conn, _KROKY + _VYSTOUPENI.format(cteni=_CTENI), (ct, ct))
    else:
        vse = _rows(conn, _VYSTOUPENI.format(cteni="NULL::text"), (ct,))
    if not vse:
        raise ValueError(f"K tisku {ct} nejsou ve stenoprotokolech žádná vystoupení (rozprava se ještě nekonala, "
                         f"nebo tisk neznám). Ověřte get_tisk({ct}) nebo seznam_tisku(q=…).")
    osoby_ = set(osoby or [])
    vyber = [r for r in vse if (not osoby_ or r["id_osoba"] in osoby_) and (not druhy or r["druh"] in druhy)
             and (faze is None or r["cteni"] == faze)]
    if tab["tisky"]:
        nazev = _one(conn, f"SELECT coalesce((SELECT nazev FROM tisky WHERE ct = %s LIMIT 1), ({_NAZEV_BODU})) AS nazev",
                     (ct, ct))["nazev"]
    else:
        nazev = _one(conn, f"SELECT ({_NAZEV_BODU}) AS nazev", (ct,))["nazev"]

    start = max(1, int(od_poradi or 0))
    out = {"ct": ct, "nazev": nazev, **_tisk_urls(ct), "format": fmt, "celkem": len(vyber),
           "znaku_celkem": sum(r["znaku"] for r in vyber), "od_poradi": start, "dalsi_od_poradi": None,
           "poznamka": POZNAMKA, "souhrn": _souhrn(vyber), "vystoupeni": []}
    # rozpočet stránky: 1 000–40 000 a s hlavičkou a souhrnem pod stropem odpovědi serveru
    rozpocet = max(MIN_ROZPOCET, min(int(max_znaku), MAX_ROZPOCET, STROP - _velikost(out) - REZERVA))
    out["vystoupeni"] = _strana(conn, vyber, start, rozpocet, fmt == "plny")
    dalsi = start + len(out["vystoupeni"])
    out["dalsi_od_poradi"] = dalsi if dalsi <= len(vyber) else None
    return out


def _strana(conn, vyber: list[dict], start: int, rozpocet: int, plny: bool) -> list[dict]:
    """Vystoupení od pořadí start, dokud se řádky (JSON) vejdou do rozpočtu; vždy aspoň jedno.

    U plného formátu se texty načtou jen pro předvýběr podle znaku (text je částí řádku, víc se nevejde)."""
    kandidati = vyber[start - 1:]
    texty: dict = {}
    if plny and kandidati:
        soucet, n = 0, 0
        for r in kandidati:
            soucet += r["znaku"]
            if n and soucet > rozpocet:
                break
            n += 1
        kandidati = kandidati[:n]
        texty = {t["id"]: t for t in _rows(conn, _TEXTY, ([r["id"] for r in kandidati],))}
    strana, uzito = [], 0
    for poradi, r in enumerate(kandidati, start):
        radek = _radek(r, poradi, texty.get(r["id"], {}) if plny else None)
        n = _velikost(radek) + 1          # + oddělovač v seznamu
        if strana and uzito + n > rozpocet:
            break
        if n > rozpocet and plny:         # první vystoupení stránky je delší než rozpočet
            n = _zkrat(radek, rozpocet)
        strana.append(radek)
        uzito += n
    return strana


def _radek(r: dict, poradi: int, text: dict | None) -> dict:
    """Řádek vystoupení; text=None = přehled (začátek textu), jinak plný formát s textem, odkazem a ohlášením."""
    meta = _speech_meta({k: r[k] for k in ("schuze", "turn", "aname", "autorizovano")})
    radek = {"poradi": poradi, "klic": meta["klic"], "datum": r["datum"], "recnik": r["recnik"], "klub": r["klub"],
             "druh": r["druh"], "znaku": r["znaku"]}
    if text is None:
        radek["zacatek"] = r["zacatek"]
    radek.update(pn_zminene=r["pn_zminene"],
                 reaguje_na_odhad=r["reaguje_na_odhad"] if r["druh"] == "fakticka" else None,
                 cteni=r["cteni"], neautorizovano=not r["autorizovano"])
    if text is not None:
        radek.update(poznamka=meta.get("poznamka"), ohlaseni=_ohlaseni(text.get("ohlaseni")), url=text.get("url"),
                     text=text.get("text") or "")
    return _kompaktni(radek)


def _ohlaseni(konec: str | None) -> str | None:
    """Konec projevu předsedajícího (nejvýš OHLASENI znaků), jen když ohlašuje faktickou poznámku."""
    if not konec:
        return None
    if len(konec) >= OHLASENI and " " in konec:     # konec delšího textu: od prvního celého slova
        konec = "… " + konec.split(" ", 1)[1].lstrip()
    return konec if RE_FAKTICKA.search(konec) else None


def _zkrat(radek: dict, rozpocet: int) -> int:
    """Zkrátí text řádku tak, aby se řádek vešel do rozpočtu; zbytek textu přes get_projev. Vrátí velikost řádku."""
    text = radek.pop("text")
    radek["zkraceno"] = True
    delka = len(text)
    while True:
        radek["pokracovani"] = f'get_projev("{radek["klic"]}", offset_znaku={delka})'
        radek["text"] = text[:delka]
        n = _velikost(radek) + 1
        # ubrání k znaků zkrátí JSON aspoň o k, cyklus skončí po jednom až dvou krocích
        if n <= rozpocet or delka == 0:
            return n
        delka = max(0, delka - (n - rozpocet))


def _souhrn(vyber: list[dict]) -> dict:
    """Všichni řečníci (v pořadí prvního vystoupení) a kluby filtrované rozpravy s počty a znaky."""
    recnici: dict = {}
    kluby: dict = {}
    for r in vyber:
        kdo = r["id_osoba"] if r["id_osoba"] is not None else r["recnik"]
        if kdo not in recnici:
            recnici[kdo] = {"id_osoba": r["id_osoba"], "jmeno": r["jmeno"] or r["recnik"], "klub": r["klub"],
                            "vystoupeni": 0, "znaku": 0, "druhy": {}}
        s = recnici[kdo]
        s["vystoupeni"] += 1
        s["znaku"] += r["znaku"]
        if r["druh"]:
            s["druhy"][r["druh"]] = s["druhy"].get(r["druh"], 0) + 1
        k = kluby.setdefault(r["klub"], {"klub": r["klub"], "vystoupeni": 0, "znaku": 0, "recniku": set()})
        k["vystoupeni"] += 1
        k["znaku"] += r["znaku"]
        k["recniku"].add(kdo)
    poradi_klubu = sorted(kluby.values(), key=lambda k: (-k["vystoupeni"], k["klub"] is None, k["klub"] or ""))
    return {"recnici": [_kompaktni(s) for s in recnici.values()],
            "kluby": [dict(k, recniku=len(k["recniku"])) for k in poradi_klubu]}


def register(server, call, annotations):
    @server.tool(name="rozprava", annotations=annotations)
    def rozprava_nastroj(ct: int, format: str = "prehled", osoby: list[int] | None = None, cteni: str | None = None,
                         druh: list[str] | None = None, od_poradi: int = 0, max_znaku: int = 30000) -> dict:
        """Celá rozprava k tisku v pořadí, jak zazněla: kdo mluvil, v jaké roli (druh), na koho faktická poznámka
        nejspíš reagovala (reaguje_na_odhad), které PN zmínil (pn_zminene) a ve kterém čtení (cteni). Slouží ke čtení
        debaty; rejstřík projevů s filtry je projevy_k_tisku, jeden celý projev get_projev(klic). Předsedající se
        nevypisují. format "prehled" (výchozí, řádky s prvními 150 znaky) nebo "plny" (celé texty, u faktické
        i ohlášení předsedajícího). souhrn vyjmenuje všechny řečníky a kluby celé (filtrované) rozpravy.
        Filtry: osoby (id_osoba), druh (recnik, fakticka, prednostni, zpravodaj, predkladatel, ministr, proceduralni),
        cteni ("1", "2", "3", "senat", "prezident"). max_znaku = rozpočet stránky (1 000–40 000). Další stránka:
        od_poradi = dalsi_od_poradi se stejnými filtry (None = konec); poradi je v obou formátech stejné.
        Příklad: rozprava(76), pak rozprava(76, format="plny", od_poradi=41)."""
        return call("rozprava", rozprava, ct, format, osoby, cteni, druh, od_poradi, max_znaku)
