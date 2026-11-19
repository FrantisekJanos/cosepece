"""Nástroj over_citaci: zazněla citace opravdu v projevu, nebo stojí v textu návrhu zákona či PN?

Brání tomu, aby model přisoudil výrok špatnému řečníkovi nebo citoval zákon zpaměti. Porovnání dělá
hlidac_rag.citace_shoda.najdi_citaci; tady se vybere prohledávaný text (projev, nebo celky textu tisku či PN)
a nalezené místo se převede na celek a pozici v jeho textu.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from itertools import accumulate

from ..citace_shoda import UVOZOVKY, najdi_citaci, zkontroluj_delku
from ..tools import NOTE_UNAUTH, _dokument, _jeden_celek, _one, _parse_key, _rows

MAX_CITACE = 2000
MAX_VSTUP = 4 * MAX_CITACE  # delší vstup se odmítne hned, ještě před ořezem okrajů
OKOLI = 150                 # kontext: kolik znaků před shodou a za ní
# krajní výpustky, které modely kolem citace přidávají (delší dřív, ať „[…]“ neskončí jako „[“)
_VYPUSTKY = ("[…]", "[...]", "(…)", "(...)", "…", "...")
_KUS = re.compile(r"\s*\(\d+/\d+\)$")      # „(k/n)“ na konci cesty kusu dlouhého celku (jako tools._jeden_celek)
_ZNENI = {"puvodni": "předložené znění", "schvalene": "znění schválené Sněmovnou"}
_SLOUPCE_CELKU = "poradi, typ, oznaceni, cesta, text"
NOTE_ZRUSENO = ("Projev v aktuální verzi stenozáznamu na psp.cz chybí (byl zrušen nebo přesunut); "
                "než ho budete citovat, ověřte ho na url.")


def register(server, call, annotations):
    @server.tool(annotations=annotations)
    def over_citaci(citace: str, klic: str | None = None, ct: int | None = None, pn: int | None = None,
                    oznaceni: str | None = None, poradi: int | None = None, verze: str | None = None) -> dict:
        """Ověří doslovnou citaci: zazněla v projevu (klic jako u get_projev), nebo stojí v textu návrhu zákona (ct)
        či PN (pn)? Volejte před každou doslovnou citací projevu nebo textu zákona či PN, hlavně když výrok
        přisuzujete řečníkovi. Zdroj právě jeden. U textu volitelně oznaceni nebo poradi celku a verze jako
        u get_cel (hledá se v celku včetně jeho kusů a vnořených celků); bez nich v celém dokumentu včetně
        důvodové zprávy.
        shoda: presne = citace sedí; priblizne = liší se drobnostmi (diakritika, interpunkce, koncovka, vynechané
        slovo), ověřte, že význam sedí (slovo s opačným významem jako zvýšit/snížit nástroj nepozná); nenalezeno =
        necitujte a výrok nepřisuzujte. Citujte vždy nalezeny_text, ne vlastní citaci, i u presne (presne toleruje
        velikost písmen, druh uvozovek a pomlček a mezery). Rozhoduje shoda, ne skore (i nenalezeno může mít
        vysoké skóre: budeme/nebudeme, jiné číslo).
        U textu vrací poradi, typ, oznaceni a cesta celku, kde shoda začíná (pro oznaceni="Čl. I" třeba „§ 17c“);
        od_znaku/do_znaku jsou posuny v textu tohoto celku jako get_cel(offset_znaku), ne pozice v celém
        dokumentu; přesahuje-li shoda do dalšího celku, do_poradi = celek, kde končí, a do_znaku_v_do_poradi =
        konec v jeho textu. Dále kontext (±150 znaků), u projevu recnik, klub, datum, autorizovano, url
        (a zruseno s poznamkou, když projev na psp.cz už chybí); u textu verze, url_docx, url_psp.
        Citace 3 slova až 2 000 znaků (delší rozdělte); krajní uvozovky a výpustky (…, […]) se ořežou.
        Příklad: over_citaci(citace="…", ct=221, oznaceni="Čl. VI"), over_citaci(citace="…", klic="10/34/633/1")."""
        return call("over_citaci", _over_citaci, citace, klic, ct, pn, oznaceni, poradi, verze)


def _over_citaci(conn, citace: str, klic: str | None = None, ct: int | None = None, pn: int | None = None,
                 oznaceni: str | None = None, poradi: int | None = None, verze: str | None = None) -> dict:
    """Ověří citaci v projevu (klic) nebo v celcích textu tisku či PN; vstup se kontroluje před dotazem do DB."""
    citace = citace or ""
    if len(citace) > MAX_VSTUP:            # dřív než ořez okrajů, ať obří vstup nic nestojí
        raise ValueError(_prilis_dlouha(len(citace)))
    citace = _orizni(citace)
    if len(citace) > MAX_CITACE:
        raise ValueError(_prilis_dlouha(len(citace)))
    zkontroluj_delku(citace)
    if (oznaceni is not None or poradi is not None) and ct is None and pn is None:
        raise ValueError("oznaceni a poradi určují celek textu, zadejte k nim ct (tisk) nebo pn (pozměňovací návrh).")
    if (klic is None) == (ct is None and pn is None):
        raise ValueError("Zadejte právě jeden zdroj citace: klic projevu, nebo ct (tisk) či pn (pozměňovací návrh).")
    if ct is not None and pn is not None:
        raise ValueError("Zadejte právě jedno z ct (sněmovní tisk) nebo pn (číslo pozměňovacího návrhu).")
    if klic is not None:
        if verze is not None:
            raise ValueError("verze patří k textu tisku (ct), u projevu ji vynechte.")
        return _v_projevu(conn, klic, citace)
    if oznaceni is not None and poradi is not None:
        raise ValueError('Zadejte oznaceni, nebo poradi celku (např. oznaceni="Čl. VI"), ne obojí.')
    return _v_textu(conn, citace, ct, pn, oznaceni, poradi, verze)


def _prilis_dlouha(delka: int) -> str:
    limit = f"{MAX_CITACE:,}".replace(",", " ")
    return f"Citace má {delka} znaků, nejvýš {limit}; rozdělte ji na kratší úseky a ověřte každý zvlášť."


def _orizni(citace: str) -> str:
    """Citace bez krajních mezer, uvozovek (citace_shoda.UVOZOVKY) a výpustek („…“, „...“, „[…]“, „(…)“),
    i opakovaných a střídavých. Výpustka uprostřed zůstane. Lineárně: indexy se jen posouvají, řez je jeden."""
    a, b = 0, len(citace)
    while True:
        while a < b and (citace[a].isspace() or citace[a] in UVOZOVKY):
            a += 1
        while a < b and (citace[b - 1].isspace() or citace[b - 1] in UVOZOVKY):
            b -= 1
        zacatek = next((v for v in _VYPUSTKY if citace.startswith(v, a, b)), None)
        if zacatek:
            a += len(zacatek)
        konec = next((v for v in _VYPUSTKY if citace.endswith(v, a, b)), None)
        if konec:
            b -= len(konec)
        if not zacatek and not konec:
            return citace[a:b]


def _shoda(text: str, citace: str) -> dict:
    """Výsledek najdi_citaci doplněný o kontext kolem nalezeného místa."""
    v = najdi_citaci(text, citace)
    v["kontext"] = _kontext(text, v["od_znaku"], v["do_znaku"])
    return v


def _kontext(text: str, od: int | None, do: int | None) -> str | None:
    """Výsek ±OKOLI znaků kolem shody; „… “ / „ …“ = text pokračuje (ořez); bez kandidáta None."""
    if od is None:
        return None
    a, b = max(0, od - OKOLI), min(len(text), do + OKOLI)
    return ("… " if a else "") + text[a:b] + (" …" if b < len(text) else "")


def _v_projevu(conn, klic: str, citace: str) -> dict:
    o, s, t, a = _parse_key(klic)
    r = _one(conn, """SELECT recnik, klub, datum, autorizovano, url, zruseno, text FROM steno.projev
                      WHERE obdobi=%s AND schuze=%s AND turn=%s AND aname=%s""", (o, s, t, a))
    if not r:
        raise ValueError(f"Projev {klic} neznám.")
    text = r.pop("text") or ""
    zruseno = r.pop("zruseno")
    out = {**_shoda(text, citace), "klic": f"{o}/{s}/{t}/{a}", **r}
    poznamky = [] if r["autorizovano"] else [NOTE_UNAUTH]
    if zruseno is not None:
        # projev se neodmítá (get_projev ho vrací také), jen se varuje
        out["zruseno"] = zruseno
        poznamky.append(NOTE_ZRUSENO)
    if poznamky:
        out["poznamka"] = " ".join(poznamky)
    return out


def _v_textu(conn, citace: str, ct: int | None, pn: int | None, oznaceni: str | None, poradi: int | None,
             verze: str | None) -> dict:
    """Citace v celcích textu: celky rozsahu se spojí "\\n" (normalizace ho smaže) a hledá se jedním voláním."""
    dok = _dokument(conn, ct, pn, verze)
    celky = _celky(conn, dok["id"], f"pn={pn}" if pn is not None else f"ct={ct}", oznaceni, poradi)
    v = _shoda("\n".join(c["text"] for c in celky), citace)
    cel = dict.fromkeys(("poradi", "typ", "oznaceni", "cesta"))
    presah = {}
    if v["od_znaku"] is not None:
        # začátek každého celku ve spojeném textu; pozice se přepočtou na celek, ve kterém shoda začíná
        zacatky = list(accumulate((len(c["text"]) + 1 for c in celky[:-1]), initial=0))
        k = bisect_right(zacatky, v["od_znaku"]) - 1
        konec = bisect_right(zacatky, v["do_znaku"] - 1) - 1
        cel = {x: celky[k][x] for x in cel}
        if konec != k:
            presah = {"do_poradi": celky[konec]["poradi"], "do_znaku_v_do_poradi": v["do_znaku"] - zacatky[konec]}
        v["od_znaku"] -= zacatky[k]
        v["do_znaku"] -= zacatky[k]
    out = {**v, **cel, "ct": dok["ct"], "pn": dok["pn"], "verze": dok["verze"],
           "dostupne_verze": dok["dostupne_verze"], "url_docx": dok["url_docx"], "url_psp": dok["url_psp"], **presah}
    if out["shoda"] == "nenalezeno":
        rady = []
        if oznaceni is not None or poradi is not None:
            rady.append("Ve vybraném celku citace není; zopakujte volání bez oznaceni a poradi, "
                        "hledá se pak v celém dokumentu.")
        jine = [x for x in dok["dostupne_verze"] if x != dok["verze"]]
        if jine:
            rady.append(f"V tomto znění ({_ZNENI[dok['verze']]}) citace není, "
                        f'zkuste verze="{jine[0]}" ({_ZNENI[jine[0]]}).')
        if rady:
            out["poznamka"] = " ".join(rady)
    return out


def _celky(conn, dokument_id: int, kde: str, oznaceni: str | None, poradi: int | None) -> list[dict]:
    """Celky, ve kterých se citace hledá, v pořadí poradi (s textem).

    Bez oznaceni a poradi celý dokument. Jinak výchozí celek (oznaceni stejně jako v get_cel) a všechny
    bezprostředně následující řádky, které k němu patří (_rozsah)."""
    if oznaceni is None and poradi is None:
        return _rows(conn, f"SELECT {_SLOUPCE_CELKU} FROM texty.cel WHERE dokument_id = %s ORDER BY poradi",
                     (dokument_id,))
    if oznaceni is not None:
        shody = _rows(conn, """SELECT poradi, cesta FROM texty.cel
                               WHERE dokument_id = %s
                                 AND lower(regexp_replace(oznaceni, '\\s+', '', 'g')) = lower(%s)
                               ORDER BY poradi""", (dokument_id, re.sub(r"\s+", "", oznaceni)))
        if not shody:
            raise ValueError(f"Celek „{oznaceni}“ v textu nenalezen. Označení celků vypíše osnova_textu({kde}).")
        if not _jeden_celek(conn, dokument_id, shody):
            vycet = "; ".join(f"poradi {s['poradi']} ({s['cesta']})" for s in shody)
            raise ValueError(f"Označení „{oznaceni}“ je v textu vícekrát, zopakujte dotaz s poradi: {vycet}")
        poradi = shody[0]["poradi"]
    dalsi = _rows(conn, """SELECT poradi, typ, oznaceni, cesta FROM texty.cel
                           WHERE dokument_id = %s AND poradi >= %s ORDER BY poradi""", (dokument_id, int(poradi)))
    if not dalsi or dalsi[0]["poradi"] != int(poradi):
        raise ValueError(f"Celek s poradi {poradi} v textu není. Pořadí celků vypíše osnova_textu({kde}).")
    posledni = dalsi[_rozsah(dalsi) - 1]["poradi"]
    return _rows(conn, f"""SELECT {_SLOUPCE_CELKU} FROM texty.cel
                           WHERE dokument_id = %s AND poradi BETWEEN %s AND %s ORDER BY poradi""",
                 (dokument_id, int(poradi), posledni))


def _rozsah(radky: list[dict]) -> int:
    """Kolik řádků od výchozího (radky[0]) patří k jeho celku; první jiný řádek rozsah končí.

    Patří k němu (a) kusy a pokračování za citovaným zněním: stejná cesta bez „(k/n)“ a stejné označení
    (bez mezer a velikosti písmen jako v get_cel), (b) vnořené celky: cesta pod ní („Čl. I / § 17c“ pod
    „Čl. I“, články pod „ČÁST PRVNÍ“), ale ne důvodová zpráva. Kusy důvodové zprávy mají všechny cestu
    „důvodová zpráva“ a liší se jen označením, odůvodnění bodu PN má cestu „bod 1 / důvodová zpráva“."""
    zaklad = _KUS.sub("", radky[0]["cesta"])
    oznaceni = _bez_mezer(radky[0]["oznaceni"])
    predpona = zaklad + " / "
    n = 1
    for r in radky[1:]:
        cesta = _KUS.sub("", r["cesta"])
        kus = cesta == zaklad and _bez_mezer(r["oznaceni"]) == oznaceni
        vnoreny = cesta.startswith(predpona) and r["typ"] != "duvodova_zprava"
        if not (kus or vnoreny):
            break
        n += 1
    return n


def _bez_mezer(oznaceni: str | None) -> str:
    return re.sub(r"\s+", "", oznaceni or "").lower()
