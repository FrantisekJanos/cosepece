"""Hlasování Sněmovny k tiskům: nástroj hlasovani_k_tisku.

Data jsou v tabulkách PHP webu `hlasovani` a `hlasovani_kluby` (app/Votes.php je každou hodinu přepíše celé,
jen aktuální období); MCP je jen čte. V testovací DB nebo u staršího webu tabulky chybět mohou, pak nástroj
vrátí čitelnou chybu. PN se s hlasováním nepárují: data číslo ani písmeno PN neobsahují.
"""
from __future__ import annotations

from ..tools import OBDOBI, PSP, _limit, _next_offset, _offset, _one, _rows, _tisk_urls

# strop na stránku: 80 nejhorších řádků (s kluby asi 610 znaků) + hlavička vyjde pod 54 000 znaků,
# rezerva pod limitem odpovědi 60 000 (test_nejhorsi_pripad_se_vejde_pod_54000_znaku)
MAX_LIMIT_HLASOVANI = 80
KLUBY_SLOUPCE = ["pro", "proti", "zdrzel", "nepritomen"]
VYSLEDEK = {"A": "přijato", "R": "zamítnuto"}
TABULKY = ("tisky", "kroky", "hlasovani", "hlasovani_kluby")

# sloupce jednoho hlasování; krok = stav kroku historie tisku (jen u hlasování s příznakem krok = 1)
SELECT_HLASOVANI = """
    SELECT h.id_hlasovani, h.schuze, h.cislo, h.datum, h.vysledek, h.pro, h.proti, h.zdrzel, h.nehlasoval,
           h.prihlaseno, h.kvorum,
           CASE WHEN h.krok = 1 THEN (SELECT k.stav FROM kroky k WHERE k.id_hlasovani = h.id_hlasovani
                                      ORDER BY k.id_hist LIMIT 1) END AS krok
    FROM hlasovani h"""


def _over_tabulky(conn) -> None:
    """Chybějící tabulka webu = ValueError s čitelnou zprávou (místo chyby databáze)."""
    chybi = [r["t"] for r in _rows(conn, "SELECT t FROM unnest(%s::text[]) AS t WHERE to_regclass(t) IS NULL",
                                   (list(TABULKY),))]
    if chybi:
        slovo = "tabulka" if len(chybi) == 1 else "tabulky"
        raise ValueError(f"Hlasování nejsou v databázi k dispozici: chybí {slovo} {', '.join(chybi)} "
                         "(data plní web cosepece.cz). Zkuste to později.")


def _kluby(conn, ids: list[int]) -> dict[int, dict[str, list]]:
    """Kluby k hlasováním kompaktně: {id_hlasovani: {klub: [pro, proti, zdrzel, nepritomen]}}.

    Kluby od největšího (součet sloupců = počet členů klubu), při shodě abecedně."""
    out: dict[int, dict[str, list]] = {}
    if not ids:
        return out
    for r in _rows(conn, """SELECT id_hlasovani, klub, pro, proti, zdrzel, nepritomen FROM hlasovani_kluby
                            WHERE id_hlasovani = ANY(%s)
                            ORDER BY id_hlasovani,
                                     coalesce(pro, 0) + coalesce(proti, 0) + coalesce(zdrzel, 0)
                                     + coalesce(nepritomen, 0) DESC, klub""", (ids,)):
        out.setdefault(r["id_hlasovani"], {})[r["klub"]] = [r[s] for s in KLUBY_SLOUPCE]
    return out


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
    t = _one(conn, "SELECT nazev FROM tisky WHERE ct = %s ORDER BY id_tisk LIMIT 1", (ct,))
    # nazev z hlasování jen náhradou za tisk, který web nesleduje; min() v collation C dá přednost variantě
    # bez „**“ (na konci i uprostřed místo mezery)
    stat = _one(conn, 'SELECT count(*) AS n, min(nazev COLLATE "C") AS nazev FROM hlasovani WHERE ct = %s', (ct,))
    if not t and not stat["n"]:
        raise ValueError(f"Tisk {ct} v {OBDOBI}. období nesleduji a hlasování k němu nemám. "
                         "Zkuste seznam_tisku(q=…).")
    off = _offset(offset)
    rows = _rows(conn, f"""{SELECT_HLASOVANI} WHERE h.ct = %s
                           ORDER BY h.datum, h.cislo, h.id_hlasovani LIMIT %s OFFSET %s""",
                 (ct, min(_limit(limit), MAX_LIMIT_HLASOVANI), off))
    kl = _kluby(conn, [r["id_hlasovani"] for r in rows])
    radky = [_radek_hlasovani(r, kl.get(r["id_hlasovani"])) for r in rows]
    return _odpoved(ct, t["nazev"] if t else stat["nazev"], stat["n"], off, radky)


def register(server, call, annotations) -> None:
    @server.tool(annotations=annotations)
    def hlasovani_k_tisku(ct: int, limit: int = 50, offset: int = 0) -> dict:
        """Hlasování Sněmovny k tisku chronologicky: schůze, číslo, datum, výsledek (přijato/zamítnuto), pro, proti,
        zdrzel, nehlasoval, prihlaseno, kvorum, url_psp a kluby {klub: [pro, proti, zdrzel, nepritomen]} (zdrzel klubu
        zahrnuje i nehlasoval). Jsou tu i procedurální hlasování (zamítnutí, vrácení, lhůty, čas hlasování); krok
        (stav tisku) jen u hlasování z historie tisku. Data mohou být neúplná, zmatečná hlasování nejsou označena.
        Stránkování přes offset: celkem, dalsi_offset (limit nejvýš 80). Příklad: hlasovani_k_tisku(231)."""
        return call("hlasovani_k_tisku", _hlasovani_k_tisku, ct, limit, offset)
