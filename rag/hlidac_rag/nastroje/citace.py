"""Nástroj over_citaci: zazněla citace opravdu v projevu, nebo stojí v textu návrhu zákona či PN?

Brání tomu, aby model přisoudil výrok špatnému řečníkovi nebo citoval zákon zpaměti. Porovnání dělá
hlidac_rag.citace_shoda.najdi_citaci; tady se vybere prohledávaný text (projev, nebo celky textu tisku či PN)
a nalezené místo se převede na celek a pozici v jeho textu.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from itertools import accumulate

from ..citace_shoda import najdi_citaci
from ..tools import NOTE_UNAUTH, _dokument, _jeden_celek, _one, _parse_key, _rows

MAX_CITACE = 2000
OKOLI = 150                 # kontext: kolik znaků před shodou a za ní
# krajní mezery a uvozovky, které modely kolem citace přidávají
_KRAJNI = re.compile("^[\\s\"'„“”»«‚‘’]+|[\\s\"'„“”»«‚‘’]+$")
_KUS = re.compile(r"\s*\(\d+/\d+\)$")      # „(k/n)“ na konci cesty kusu dlouhého celku (jako tools._jeden_celek)
_ZNENI = {"puvodni": "předložené znění", "schvalene": "znění schválené Sněmovnou"}
_SLOUPCE_CELKU = "poradi, typ, oznaceni, cesta, text"


def register(server, call, annotations):
    @server.tool(annotations=annotations)
    def over_citaci(citace: str, klic: str | None = None, ct: int | None = None, pn: int | None = None,
                    oznaceni: str | None = None, poradi: int | None = None, verze: str | None = None) -> dict:
        """Ověří doslovnou citaci: zazněla v projevu (klic jako u get_projev), nebo stojí v textu návrhu zákona (ct)
        či PN (pn)? Volejte před každou doslovnou citací projevu nebo textu zákona či PN, hlavně když výrok
        přisuzujete řečníkovi. Zdroj právě jeden. U textu volitelně oznaceni nebo poradi celku a verze jako
        u get_cel; bez nich se hledá v celém dokumentu včetně důvodové zprávy.
        shoda: presne = citujte; priblizne = citujte přesné znění z nalezeny_text, ne vlastní citaci, a ověřte, že
        význam sedí (slovo s opačným významem, např. zvýšit/snížit, nástroj nepozná); nenalezeno = necitujte
        a výrok nepřisuzujte. Rozhoduje shoda, ne skore (i nenalezeno může mít vysoké skóre, např. budeme/nebudeme).
        Vrací od_znaku/do_znaku (u textu od začátku celku poradi, jako offset_znaku v get_cel; přesahuje-li shoda
        do dalšího celku, do_poradi = celek, kde končí), kontext (±150 znaků), u projevu recnik, klub, datum,
        autorizovano, url;
        u textu oznaceni, cesta, verze, url_docx, url_psp. Citace 3 slova až 2 000 znaků (delší rozdělte).
        Příklad: over_citaci(citace="…", ct=221, oznaceni="Čl. VI"), over_citaci(citace="…", klic="10/34/633/1")."""
        return call("over_citaci", _over_citaci, citace, klic, ct, pn, oznaceni, poradi, verze)


def _over_citaci(conn, citace: str, klic: str | None = None, ct: int | None = None, pn: int | None = None,
                 oznaceni: str | None = None, poradi: int | None = None, verze: str | None = None) -> dict:
    """Ověří citaci v projevu (klic) nebo v celcích textu tisku či PN; parametry se kontrolují před dotazem do DB."""
    citace = _KRAJNI.sub("", citace or "")
    if len(citace) > MAX_CITACE:
        limit = f"{MAX_CITACE:,}".replace(",", " ")
        raise ValueError(f"Citace má {len(citace)} znaků, nejvýš {limit}; rozdělte ji na kratší úseky "
                         "a ověřte každý zvlášť.")
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
    r = _one(conn, """SELECT recnik, klub, datum, autorizovano, url, text FROM steno.projev
                      WHERE obdobi=%s AND schuze=%s AND turn=%s AND aname=%s""", (o, s, t, a))
    if not r:
        raise ValueError(f"Projev {klic} neznám.")
    text = r.pop("text") or ""
    out = {**_shoda(text, citace), "klic": f"{o}/{s}/{t}/{a}", **r}
    if not r["autorizovano"]:
        out["poznamka"] = NOTE_UNAUTH
    return out


def _v_textu(conn, citace: str, ct: int | None, pn: int | None, oznaceni: str | None, poradi: int | None,
             verze: str | None) -> dict:
    """Citace v celcích textu: celky rozsahu se spojí "\\n" (normalizace ho smaže) a hledá se jedním voláním."""
    dok = _dokument(conn, ct, pn, verze)
    celky = _celky(conn, dok["id"], f"pn={pn}" if pn is not None else f"ct={ct}", oznaceni, poradi)
    v = _shoda("\n".join(c["text"] for c in celky), citace)
    cel = dict.fromkeys(("poradi", "typ", "oznaceni", "cesta"))
    do_poradi = None
    if v["od_znaku"] is not None:
        # začátek každého celku ve spojeném textu; pozice se přepočtou na celek, ve kterém shoda začíná
        zacatky = list(accumulate((len(c["text"]) + 1 for c in celky[:-1]), initial=0))
        k = bisect_right(zacatky, v["od_znaku"]) - 1
        konec = bisect_right(zacatky, v["do_znaku"] - 1) - 1
        cel = {x: celky[k][x] for x in cel}
        v["od_znaku"] -= zacatky[k]
        v["do_znaku"] -= zacatky[k]
        if konec != k:
            do_poradi = celky[konec]["poradi"]
    out = {**v, **cel, "ct": dok["ct"], "pn": dok["pn"], "verze": dok["verze"],
           "dostupne_verze": dok["dostupne_verze"], "url_docx": dok["url_docx"], "url_psp": dok["url_psp"]}
    if do_poradi is not None:
        out["do_poradi"] = do_poradi
    jine = [x for x in dok["dostupne_verze"] if x != dok["verze"]]
    if out["shoda"] == "nenalezeno" and jine:
        out["poznamka"] = (f"V tomto znění ({_ZNENI[dok['verze']]}) citace není, "
                           f'zkuste verze="{jine[0]}" ({_ZNENI[jine[0]]}).')
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
    dalsi = _rows(conn, "SELECT poradi, cesta FROM texty.cel WHERE dokument_id = %s AND poradi >= %s ORDER BY poradi",
                  (dokument_id, int(poradi)))
    if not dalsi or dalsi[0]["poradi"] != int(poradi):
        raise ValueError(f"Celek s poradi {poradi} v textu není. Pořadí celků vypíše osnova_textu({kde}).")
    posledni = dalsi[_rozsah(dalsi) - 1]["poradi"]
    return _rows(conn, f"""SELECT {_SLOUPCE_CELKU} FROM texty.cel
                           WHERE dokument_id = %s AND poradi BETWEEN %s AND %s ORDER BY poradi""",
                 (dokument_id, int(poradi), posledni))


def _rozsah(radky: list[dict]) -> int:
    """Kolik řádků od výchozího (radky[0]) patří k jeho celku.

    Patří k němu řádky hned za ním se stejnou cestou bez „(k/n)“ (kusy, pokračování za citovaným zněním)
    nebo s cestou pod ní („Čl. I / § 17c“ pod „Čl. I“, články pod „ČÁST PRVNÍ“); první jiný řádek rozsah končí."""
    zaklad = _KUS.sub("", radky[0]["cesta"])
    predpona = zaklad + " / "
    n = 1
    for r in radky[1:]:
        cesta = _KUS.sub("", r["cesta"])
        if cesta != zaklad and not cesta.startswith(predpona):
            break
        n += 1
    return n
