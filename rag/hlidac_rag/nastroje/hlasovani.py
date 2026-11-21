"""Hlasování Sněmovny k tiskům: nástroje hlasovani_k_tisku a get_hlasovani.

Data jsou v tabulkách PHP webu `hlasovani` a `hlasovani_kluby` (app/Votes.php je každou hodinu přepíše celé,
jen aktuální období) a `hlasovani_poslanci` (hlasy jednotlivých poslanců, jen přibývají); MCP je jen čte.
Hlasování, které web s tiskem nespároval (pořad schůze, jiný název), má ct 0: get_hlasovani ho vrátí bez tisku.
V testovací DB nebo u staršího webu tabulky chybět mohou, pak nástroj vrátí čitelnou chybu (hlasy poslanců
jen prázdný seznam). PN se s hlasováním nepárují: data číslo ani písmeno PN neobsahují.
"""
from __future__ import annotations

from ..tools import OBDOBI, PSP, _limit, _next_offset, _offset, _one, _rows, _tisk_urls

# strop na stránku: 70 nejhorších možných řádků podle schématu webu (krok „stav / akce“ 243 znaků, 10 klubů,
# asi 770 znaků na řádek) + hlavička vyjde pod 54 000 znaků, rezerva pod limitem odpovědi 60 000
# (test_nejhorsi_pripad_se_vejde_pod_54000_znaku)
MAX_LIMIT_HLASOVANI = 70
# offset se ořízne, ať obrovská hodnota nepřeteče číslo v databázi (výsledek je prázdná stránka s celkem)
MAX_OFFSET = 10**6
MAX_PROJEVU = 50
PROJEVY_TABULKA = "steno.projev"
KLUBY_SLOUPCE = ["pro", "proti", "zdrzel", "nepritomen"]
VYSLEDEK = {"A": "přijato", "R": "zamítnuto"}
# kód hlasu poslance z psp.cz (hl_poslanec)
HLAS = {"A": "pro", "B": "proti", "N": "proti", "C": "zdržel se", "F": "nehlasoval", "K": "zdržel se nebo nehlasoval",
        "@": "nepřihlášen", "M": "omluven", "W": "před složením slibu"}
OSOBY_TABULKA = "steno.osoba"
TABULKY = ("tisky", "kroky", "hlasovani", "hlasovani_kluby")

# sloupce jednoho hlasování; krok = „stav / akce“ kroku historie tisku (bez akce jen stav), jen u hlasování
# s příznakem krok = 1; samotný stav by přehlasování Senátu („Prezident / setrváno“) pletl s hlasováním o vetu
SELECT_HLASOVANI = """
    SELECT h.id_hlasovani, h.ct, h.schuze, h.cislo, h.datum, h.vysledek, h.pro, h.proti, h.zdrzel, h.nehlasoval,
           h.prihlaseno, h.kvorum, h.nazev AS nazev_hlasovani,
           CASE WHEN h.krok = 1 THEN (
               SELECT nullif(concat_ws(' / ', nullif(k.stav, ''), nullif(k.akce, '')), '')
               FROM kroky k WHERE k.id_hlasovani = h.id_hlasovani AND k.ct = h.ct ORDER BY k.id_hist LIMIT 1) END AS krok
    FROM hlasovani h"""


def _over_tabulky(conn) -> None:
    """Chybějící tabulka webu = ValueError s čitelnou zprávou (místo chyby databáze)."""
    chybi = [r["t"] for r in _rows(conn, "SELECT t FROM unnest(%s::text[]) AS t WHERE to_regclass(t) IS NULL",
                                   (list(TABULKY),))]
    if chybi:
        slovo = "tabulka" if len(chybi) == 1 else "tabulky"
        raise ValueError(f"Hlasování nejsou v databázi k dispozici: chybí {slovo} {', '.join(chybi)} "
                         "(data plní web cosepece.cz). Zkuste to později.")


def _kluby_radky(conn, ids: list[int]) -> list[dict]:
    """Řádky klubů k hlasováním: od největšího klubu (součet sloupců = počet členů), při shodě abecedně."""
    if not ids:
        return []
    return _rows(conn, """SELECT id_hlasovani, klub, pro, proti, zdrzel, nepritomen FROM hlasovani_kluby
                          WHERE id_hlasovani = ANY(%s)
                          ORDER BY id_hlasovani,
                                   coalesce(pro, 0) + coalesce(proti, 0) + coalesce(zdrzel, 0)
                                   + coalesce(nepritomen, 0) DESC, klub""", (ids,))


def _kluby(conn, ids: list[int]) -> dict[int, dict[str, list]]:
    """Kluby k hlasováním kompaktně: {id_hlasovani: {klub: [pro, proti, zdrzel, nepritomen]}}."""
    out: dict[int, dict[str, list]] = {}
    for r in _kluby_radky(conn, ids):
        out.setdefault(r["id_hlasovani"], {})[r["klub"]] = [r[s] for s in KLUBY_SLOUPCE]
    return out


def _stat_hlasovani(conn, ct: int) -> dict:
    """Počet hlasování k tisku a jejich název; nazev jen náhradou za tisk, který web nesleduje (min() v collation C
    dá přednost variantě bez „**“ na konci i uprostřed místo mezery)."""
    return _one(conn, 'SELECT count(*) AS n, min(nazev COLLATE "C") AS nazev FROM hlasovani WHERE ct = %s', (ct,))


def _tisk(conn, ct: int) -> dict | None:
    return _one(conn, "SELECT nazev FROM tisky WHERE ct = %s ORDER BY id_tisk LIMIT 1", (ct,))


def _nazev_tisku(t: dict | None, stat: dict) -> str | None:
    """Název z tisků webu; nemá-li ho (tisk chybí, NULL, prázdný), tak z hlasování."""
    return (t and t["nazev"]) or stat["nazev"]


def _radek_hlasovani(r: dict, kluby_hlasovani: dict[str, list] | None) -> dict:
    """Jeden řádek hlasování pro klienta z řádku SELECT_HLASOVANI a kompaktních klubů.

    vysledek bere text z dat (A/R), ne z kvóra: zmatečná hlasování mají A/R taky."""
    return {
        "id_hlasovani": r["id_hlasovani"], "schuze": r["schuze"], "cislo": r["cislo"], "datum": r["datum"],
        "vysledek": VYSLEDEK.get(r["vysledek"], "neznámý"),
        "pro": r["pro"], "proti": r["proti"], "zdrzel": r["zdrzel"], "nehlasoval": r["nehlasoval"],
        "prihlaseno": r["prihlaseno"], "kvorum": r["kvorum"], "krok": r["krok"],
        "url_psp": f"{PSP}/sqw/hlasy.sqw?g={r['id_hlasovani']}",
        "kluby": kluby_hlasovani or {},
    }


def _odpoved(ct: int, nazev: str | None, celkem: int, off: int, radky: list[dict]) -> dict:
    """Hlavička a stránka seznamu hlasování k tisku."""
    out = {"ct": ct, "nazev": nazev, **_tisk_urls(ct), "celkem": celkem, "offset": off,
           "dalsi_offset": _next_offset(celkem, off, len(radky)), "pocet": len(radky),
           "kluby_sloupce": KLUBY_SLOUPCE}
    if not celkem:
        out["poznamka"] = ("K tisku nemám žádné hlasování: zatím se o něm nehlasovalo, nebo se hlasování "
                           "s tiskem nepodařilo spárovat (data mohou být neúplná).")
    out["hlasovani"] = radky
    return out


def _hlasovani_k_tisku(conn, ct: int, limit: int = 50, offset: int = 0) -> dict:
    _over_tabulky(conn)
    t = _tisk(conn, ct)
    # ct 0 = hlasování, která web s tiskem nespároval; nejsou „hlasováními k tisku 0“
    stat = _stat_hlasovani(conn, ct) if ct > 0 else {"n": 0, "nazev": None}
    if not t and not stat["n"]:
        raise ValueError(f"Tisk {ct} v {OBDOBI}. období nesleduji a hlasování k němu nemám. "
                         "Zkuste seznam_tisku(q=…).")
    off = min(_offset(offset), MAX_OFFSET)
    rows = _rows(conn, f"""{SELECT_HLASOVANI} WHERE h.ct = %s
                           ORDER BY h.datum, h.cislo, h.id_hlasovani LIMIT %s OFFSET %s""",
                 (ct, min(_limit(limit), MAX_LIMIT_HLASOVANI), off))
    kl = _kluby(conn, [r["id_hlasovani"] for r in rows])
    radky = [_radek_hlasovani(r, kl.get(r["id_hlasovani"])) for r in rows]
    return _odpoved(ct, _nazev_tisku(t, stat), stat["n"], off, radky)


def _projevy(conn, id_hlasovani: int, schuze: int | None) -> dict:
    """Projevy, v jejichž poli hlasovani je toto id (klíče pro get_projev); bez steno.projev prázdně s poznámkou.

    Nejdřív projevy ze schůze hlasování (schůze se překrývají, čísla nejdou časově po sobě), pak podle data."""
    if not _one(conn, "SELECT to_regclass(%s) IS NOT NULL AS ok", (PROJEVY_TABULKA,))["ok"]:
        return {"projevy": [], "projevu_celkem": 0,
                "poznamka": "Stenozáznamy nejsou v databázi k dispozici, projevy nelze přiřadit."}
    kde = f"FROM {PROJEVY_TABULKA} WHERE obdobi = %s AND zruseno IS NULL AND %s = ANY(hlasovani)"
    celkem = _one(conn, f"SELECT count(*) AS n {kde}", (OBDOBI, id_hlasovani))["n"]
    rows = _rows(conn, f"SELECT schuze, turn, aname, datum, recnik {kde} ORDER BY (schuze = %s) DESC, datum, schuze, poradi, id LIMIT %s",
                 (OBDOBI, id_hlasovani, schuze, MAX_PROJEVU))
    projevy = [{"klic": f"{OBDOBI}/{r['schuze']}/{r['turn']}/{r['aname']}", "datum": r["datum"],
                "recnik": r["recnik"]} for r in rows]
    return {"projevy": projevy, "projevu_celkem": celkem}


def _poslanci(conn, id_hlasovani: int) -> list[dict]:
    """Hlasy jednotlivých poslanců: id_osoba, jméno (ze steno.osoba, jinak None), klub ke dni hlasování, hlas textem.
    Řazeno podle klubu a příjmení; 200 řádků vyjde asi na 20 000 znaků (test_hlasy_poslancu_nejhorsi_pripad)."""
    if not _one(conn, "SELECT to_regclass('hlasovani_poslanci') IS NOT NULL AS ok")["ok"]:
        return []
    osoby = _one(conn, "SELECT to_regclass(%s) IS NOT NULL AS ok", (OSOBY_TABULKA,))["ok"]
    jmeno = "nullif(concat_ws(' ', o.jmeno, o.prijmeni), '')" if osoby else "NULL"
    join = f"LEFT JOIN {OSOBY_TABULKA} o ON o.id_osoba = p.id_osoba" if osoby else ""
    prijmeni = "o.prijmeni" if osoby else "NULL"
    rows = _rows(conn, f"""SELECT p.id_osoba, {jmeno} AS jmeno, p.klub, p.hlas FROM hlasovani_poslanci p {join}
                            WHERE p.id_hlasovani = %s
                            ORDER BY p.klub NULLS LAST, {prijmeni} NULLS LAST, p.id_osoba""", (id_hlasovani,))
    return [{"id_osoba": r["id_osoba"], "jmeno": r["jmeno"], "klub": r["klub"], "hlas": HLAS.get(r["hlas"], r["hlas"])}
            for r in rows]


def _get_hlasovani(conn, id_hlasovani: int) -> dict:
    _over_tabulky(conn)
    r = _one(conn, f"{SELECT_HLASOVANI} WHERE h.id_hlasovani = %s", (id_hlasovani,))
    if not r:
        raise ValueError(f"Hlasování {id_hlasovani} neznám. Seznam hlasování k tisku vrací hlasovani_k_tisku(ct).")
    ct = r["ct"]
    out = _radek_hlasovani(r, None)
    # v detailu kluby v plné podobě (seznam objektů), ne kompaktně jako v seznamu k tisku
    out["kluby"] = [{k: v for k, v in kr.items() if k != "id_hlasovani"}
                    for kr in _kluby_radky(conn, [id_hlasovani])]
    if ct:
        tisk_info = {"ct": ct, "nazev": _nazev_tisku(_tisk(conn, ct), _stat_hlasovani(conn, ct)),
                     "url_web": _tisk_urls(ct)["url_web"]}
    else:
        tisk_info = {"ct": None, "nazev": r["nazev_hlasovani"], "url_web": None,
                     "poznamka": "Hlasování se nepodařilo spárovat s tiskem (např. pořad schůze nebo jiný název "
                                 "bodu); nazev je popis hlasování z psp.cz."}
    return {"id_hlasovani": out.pop("id_hlasovani"), **tisk_info, **out,
            **_projevy(conn, id_hlasovani, r["schuze"]), "poslanci": _poslanci(conn, id_hlasovani)}


def register(server, call, annotations) -> None:
    @server.tool(annotations=annotations)
    def hlasovani_k_tisku(ct: int, limit: int = 50, offset: int = 0) -> dict:
        """Hlasování Sněmovny k tisku chronologicky: schůze, číslo, datum, výsledek (přijato/zamítnuto), pro, proti,
        zdrzel, nehlasoval, prihlaseno, kvorum, url_psp a kluby {klub: [pro, proti, zdrzel, nepritomen]} (zdrzel klubu
        zahrnuje i nehlasoval). Jsou tu i procedurální hlasování (zamítnutí, vrácení, lhůty, čas hlasování); krok
        („Prezident / setrváno“) jen u hlasování z historie tisku. Data mohou být neúplná, zmatečná hlasování nejsou
        označena. Stránkování přes offset: celkem, dalsi_offset (limit nejvýš 70). Příklad: hlasovani_k_tisku(231)."""
        return call("hlasovani_k_tisku", _hlasovani_k_tisku, ct, limit, offset)

    @server.tool(annotations=annotations)
    def get_hlasovani(id_hlasovani: int) -> dict:
        """Detail jednoho hlasování: výsledek, počty, tisk (ct, nazev, url_web), všechny kluby jako seznam
        {klub, pro, proti, zdrzel, nepritomen} (zdrzel klubu zahrnuje i nehlasoval), projevy (klic pro get_projev),
        v jejichž textu je hlasování zmíněno, a poslanci: hlas každého poslance [{id_osoba, jmeno, klub, hlas}]
        (hlas: pro, proti, zdržel se, nehlasoval, nepřihlášen, omluven…; klub ke dni hlasování). Hlasování, které
        se nepodařilo spárovat s tiskem, má ct null a poznámku. Id hlasování vezměte z hlasovani_k_tisku(ct).
        Příklad: get_hlasovani(88876)."""
        return call("get_hlasovani", _get_hlasovani, id_hlasovani)
