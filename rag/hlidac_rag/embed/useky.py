"""Dělení textu projevu nebo celku na úseky pro embeddingy a hlavičky úseků.

Úsek je souvislý kus původního textu (offsety od_znaku/do_znaku, text[od:do] == telo). Krátký text je jeden úsek,
delší se skládá z odstavců do limitu, odstavec delší než limit z vět, věta delší než limit se tvrdě řeže (na mezeře,
když nějaká je). Sousední úseky se překrývají zhruba o `prekryv` znaků (začátek zarovnaný na začátek slova), ať
myšlenka na hranici úseku nezmizí. Hlavička (řečník, datum, tisk…) se embeduje spolu s tělem, ale neukazuje se.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date

_ODDELOVAC_ODSTAVCU = re.compile(r"\n\s*\n")
_KONEC_VETY = re.compile(r"[.!?…](?=\s)")


@dataclass
class Usek:
    poradi: int
    od_znaku: int
    do_znaku: int
    telo: str


def _orizni(text: str, a: int, b: int) -> tuple[int, int] | None:
    """Rozsah [a, b) bez bílých znaků na krajích; prázdný = None."""
    while a < b and text[a].isspace():
        a += 1
    while b > a and text[b - 1].isspace():
        b -= 1
    return (a, b) if a < b else None


def _odstavce(text: str) -> list[tuple[int, int]]:
    out, pos = [], 0
    for m in _ODDELOVAC_ODSTAVCU.finditer(text):
        r = _orizni(text, pos, m.start())
        if r:
            out.append(r)
        pos = m.end()
    r = _orizni(text, pos, len(text))
    if r:
        out.append(r)
    return out


def _vety(text: str, a: int, b: int) -> list[tuple[int, int]]:
    out, pos = [], a
    for m in _KONEC_VETY.finditer(text, a, b):
        r = _orizni(text, pos, m.end())
        if r:
            out.append(r)
        pos = m.end()
    r = _orizni(text, pos, b)
    if r:
        out.append(r)
    return out


def _tvrde(text: str, a: int, b: int, kus: int) -> list[tuple[int, int]]:
    """Rozsah delší než kus po kusech nejvýš `kus` znaků, řez na poslední mezeře v kusu, když nějaká je."""
    out = []
    while b - a > kus:
        konec = a + kus
        mezera = text.rfind(" ", a + kus // 2, konec)
        if mezera != -1:
            konec = mezera
        r = _orizni(text, a, konec)
        if r:
            out.append(r)
        a = konec
    r = _orizni(text, a, b)
    if r:
        out.append(r)
    return out


def _jednotky(text: str, max_znaku: int, prekryv: int) -> list[tuple[int, int]]:
    """Nedělitelné kusy textu (odstavce, věty, tvrdé řezy), každý nejvýš max_znaku - prekryv znaků,
    aby se k němu vešel překryv s předchozím úsekem."""
    strop = max(1, max_znaku - prekryv)
    out = []
    for a, b in _odstavce(text):
        if b - a <= strop:
            out.append((a, b))
            continue
        for va, vb in _vety(text, a, b):
            out.extend([(va, vb)] if vb - va <= strop else _tvrde(text, va, vb, strop))
    return out


def _zacatek_slova(text: str, s: int, hranice: int) -> int:
    """První začátek slova od pozice s (nejvýš do hranice); když v okolí žádný není, zůstane s."""
    if s <= 0 or text[s - 1].isspace():
        return max(s, 0)
    m = re.compile(r"\s+").search(text, s, hranice)
    return m.end() if m else s


def rozdel(text: str, max_znaku: int = 2400, prekryv: int = 300) -> list[Usek]:
    """Úseky textu do max_znaku znaků s překryvem; prázdný text = žádný úsek."""
    cely = _orizni(text, 0, len(text))
    if not cely:
        return []
    if cely[1] - cely[0] <= max_znaku:
        return [Usek(0, cely[0], cely[1], text[cely[0]:cely[1]])]
    jednotky = _jednotky(text, max_znaku, prekryv)
    useky: list[Usek] = []
    i = 0
    while i < len(jednotky):
        if useky:
            # překryv: začátek kus před koncem předchozího úseku, zarovnaný na začátek slova
            konec_pred = useky[-1].do_znaku
            s = _zacatek_slova(text, max(konec_pred - prekryv, useky[-1].od_znaku + 1), konec_pred)
            s = max(s, jednotky[i][1] - max_znaku)
            r = _orizni(text, s, jednotky[i][1])
            s = r[0] if r else jednotky[i][0]
        else:
            s = jednotky[0][0]
        j = i
        while j + 1 < len(jednotky) and jednotky[j + 1][1] - s <= max_znaku:
            j += 1
        konec = jednotky[j][1]
        useky.append(Usek(len(useky), s, konec, text[s:konec]))
        i = j + 1
    return useky


def _datum(d) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d[:10])
        except ValueError:
            return d
    return f"{d.day}. {d.month}. {d.year}"


def hlavicka_projevu(r: dict) -> str:
    """Hlavička projevu: řečník (s rolí, není-li ve jméně), klub, datum, schůze, bod a tisky z projev_tisk."""
    recnik = (r.get("recnik") or "").strip()
    role = (r.get("role") or "").strip()
    kdo = recnik
    if role and role.lower() not in recnik.lower():
        kdo = f"{recnik}, {role}" if recnik else role
    if r.get("klub"):
        kdo += f" ({r['klub']})"
    casti = [kdo or "Neznámý řečník"]
    kdy = ", ".join(x for x in (_datum(r.get("datum")),
                                f"{r['schuze']}. schůze" if r.get("schuze") else "") if x)
    if kdy:
        casti.append(kdy)
    if r.get("bod_nazev"):
        casti.append(f"Bod: {r['bod_nazev'].strip()}")
    tisky = sorted(set(r.get("tisky") or []))
    if tisky:
        casti.append(("tisk " if len(tisky) == 1 else "tisky ") + ", ".join(str(t) for t in tisky))
    return "Projev v Poslanecké sněmovně. " + ". ".join(casti) + "."


def hlavicka_celku(r: dict) -> str:
    """Hlavička celku textu: tisk nebo PN, název tisku, cesta celku a měněný zákon."""
    if r.get("druh") == "pn":
        kdo = f"Pozměňovací návrh {r.get('pn')} k tisku {r.get('ct')}"
    else:
        kdo = f"Tisk {r.get('ct')}"
    casti = [kdo + (f": {r['nazev_tisku'].strip()}" if r.get("nazev_tisku") else "")]
    if r.get("cesta"):
        casti.append(r["cesta"])
    if r.get("meni_zakon"):
        casti.append(f"Mění zákon č. {r['meni_zakon']} Sb.")
    return ". ".join(casti)


def text_hash(hlavicka: str, telo: str) -> str:
    """Otisk vstupu embeddingu (hlavička + tělo), sha256 hex."""
    return hashlib.sha256(f"{hlavicka}\n\n{telo}".encode("utf-8")).hexdigest()
