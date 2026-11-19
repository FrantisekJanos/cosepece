"""Rozdělení textu návrhu zákona nebo pozměňovacího návrhu na celky (část, článek, §, bod novely).

Text pochází z DOCX převodem „konec odstavce = nový řádek“, takže odstavec je jeden řádek
a číslování bývá na stejném řádku jako text. Čisté funkce, bez DB a sítě.

Celky na sebe navazují a pokrývají celý text: spojení jejich textů je původní text
(až na bílé znaky), `od_znaku` ukazuje na začátek celku v původním textu.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_CEL = 4000      # strop celku normativní části
MAX_VOLNY = 2000    # volný text a důvodová zpráva

# port PnText::normativePart; navíc volitelné „II.“ před nadpisem a „(Důvodová zpráva)“ za ním
RE_DUVODOVA = re.compile(
    r"^\s*(?:[IVX]+\.\s*)?(Odůvodnění|Zdůvodnění|O\s*d\s*ů\s*v\s*o\s*d\s*n\s*ě\s*n\s*í"
    r"|D\s*ů\s*v\s*o\s*d\s*o\s*v\s*á\s+z\s*p\s*r\s*á\s*v\s*a)\s*(?:\(\s*Důvodová\s+zpráva\s*\))?\s*:?\s*$",
    re.M | re.I,
)

# nadpisy jsou samostatný řádek, smí začínat uvozovkou (nové znění vkládané novelou)
RE_CAST = re.compile(r"^„?ČÁST\s+([^\W\d_]+|\d+)$")
RE_CLANEK = re.compile(r"^„?Čl\.\s*(?:SEQ\b.*?MERGEFORMAT\s+)?([IVXLCDM]+|\d+[a-z]?)$")
RE_PARAGRAF = re.compile(r"^„?§\s*(\d+[a-z]*)$")
# bod novely: „15. V § …“, číslo může stát i samo na řádku a text pokračuje dalším řádkem
RE_BOD = re.compile(r"^„?(\d+)\.(?:\s+(.*))?$")
RE_BOD_ZACATEK = re.compile(
    r"^(?:(?:V|Ve|Za|Na|Před|Pod)\s|Dosavadní|Poznámk|Slova\b|Slovo\b|Věta\b|Část\b|Příloha"
    r"|Název\b|Nadpis\b|Text\b|Odkaz\b)"
)
# pojistka proti číslovaným výčtům v běžném textu („1. V případě, že …“): bod novely něco mění
RE_BOD_SLOVESO = re.compile(
    r"\b(?:zní|znějí|zrušuj\w*|nahrazuj\w*|vklád\w*|doplňuj\w*|vypouští|vypouštějí|označuj\w*"
    r"|přečísl\w*|mění|přesouv\w*)\b"
)
# „Zákon č. 586/1992 Sb., … se mění takto:“, „V zákoně č. …“, „Změna zákona č. …“
# a jednorázová změna „V § 7 odst. 2 zákona č. 110/2006 Sb. se …“ (číslo mimo citovaný text)
RE_MENENY_ZAKON = re.compile(
    r"^„?\s*(?:(?:Zákon|V\s+zákoně|Změna\s+zákona)\s+|V\s+§[^„]*?\bzákona\s+)č\.\s*(\d+/\d{4})\s*Sb\."
)

UROVEN = {"cast": 0, "clanek": 1, "bod": 2, "paragraf": 3}


@dataclass(frozen=True)
class Cel:
    poradi: int
    typ: str          # cast | clanek | paragraf | bod | odstavec_volny | duvodova_zprava
    oznaceni: str     # "ČÁST PRVNÍ", "Čl. I", "§ 17c", "bod 15", "odst. 3" u volného textu pořadové
    cesta: str        # "ČÁST PRVNÍ / Čl. I / bod 15"; u děleného celku navíc „(1/3)“
    meni_zakon: str | None   # "586/1992", pokud celek nebo nadřazený celek uvádí měněný zákon
    text: str
    od_znaku: int     # posun začátku celku v původním textu


def normativni_a_duvodova(text: str) -> tuple[str, str]:
    """Rozdělí text na normativní část a odůvodnění (důvodovou zprávu) od prvního nadpisu."""
    m = RE_DUVODOVA.search(text)
    if not m:
        return text, ""
    return text[: m.start()], text[m.start():]


@dataclass
class _Usek:
    """Souvislý úsek textu jednoho druhu (nadpis a řádky pod ním, volný text, odůvodnění)."""
    typ: str
    oznaceni: str
    cesta: str
    meni_zakon: str | None
    start: int
    end: int


@dataclass
class _Uroven:
    typ: str
    oznaceni: str
    v_uvozovkach: bool       # vnořeno do bodu PN (nové znění v uvozovkách)
    citovany: bool           # nadpis začíná „ nebo leží v neuzavřených uvozovkách
    meni_zakon: str | None = None
    cislo: int | None = None


def _radky(text: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(0)) for m in re.finditer(r"[^\n]*\n|[^\n]+$", text)]


def _nadpis(radky: list[tuple[int, str]], i: int) -> tuple[str, str, int | None] | None:
    """Pozná nadpis celku na řádku i: (typ, označení, číslo bodu)."""
    s = radky[i][1].strip()
    if m := RE_CAST.match(s):
        return "cast", "ČÁST " + m.group(1).upper(), None
    if m := RE_CLANEK.match(s):
        return "clanek", "Čl. " + m.group(1), None
    if m := RE_PARAGRAF.match(s):
        return "paragraf", "§ " + m.group(1), None
    if m := RE_BOD.match(s):
        zbytek = (m.group(2) or "").strip()
        if not zbytek:
            # číslo samo na řádku, text bodu je na dalším neprázdném řádku
            j = i + 1
            while j < len(radky) and not radky[j][1].strip():
                j += 1
            zbytek = radky[j][1].strip() if j < len(radky) else ""
        if RE_BOD_ZACATEK.match(zbytek) and RE_BOD_SLOVESO.search(zbytek):
            return "bod", f"bod {m.group(1)}", int(m.group(1))
    return None


def _meni_zakon(radky: list[str]) -> str | None:
    """Měněný zákon z prvních řádků celku („Zákon č. 586/1992 Sb., … se mění takto:“)."""
    for i, r in enumerate(radky[:4]):
        s = r.strip()
        if i == 0:
            s = re.sub(r"^„?\d+\.\s*", "", s)   # bod novely: bez čísla
        if m := RE_MENENY_ZAKON.match(s):
            return m.group(1)
    return None


def _useky(text: str) -> list[_Usek]:
    radky = _radky(text)
    nadpisy_duv = [m.start() for m in RE_DUVODOVA.finditer(text)]
    # PN s odůvodněním za každým bodem: po odůvodnění se další bod vrací do normativní části
    stridave = len(nadpisy_duv) >= 2
    zacatek_duv = nadpisy_duv[0] if nadpisy_duv else len(text)

    useky: list[_Usek] = []
    zasobnik: list[_Uroven] = []
    hloubka = 0                # počet otevřených uvozovek „ … “
    posledni_bod: int | None = None
    v_duvodove = False
    radky_useku: list[str] = []

    def uzavri(end: int) -> None:
        if not useky:
            return
        u = useky[-1]
        u.end = end
        if u.typ in UROVEN and zasobnik:
            mz = _meni_zakon(radky_useku)
            if mz:
                zasobnik[-1].meni_zakon = mz
                u.meni_zakon = mz

    def novy(typ: str, oznaceni: str, start: int) -> None:
        nonlocal radky_useku
        mz = next((z.meni_zakon for z in reversed(zasobnik) if z.meni_zakon), None)
        cesta = " / ".join(z.oznaceni for z in zasobnik) if typ in UROVEN else oznaceni
        if typ == "duvodova_zprava" and zasobnik and stridave:
            cesta = " / ".join(z.oznaceni for z in zasobnik) + " / " + oznaceni
        useky.append(_Usek(typ, oznaceni, cesta, mz if typ in UROVEN else None, start, start))
        radky_useku = []

    for i, (start, radek) in enumerate(radky):
        hloubka_pred = hloubka
        hloubka = max(0, hloubka + radek.count("„") - radek.count("“"))

        je_duv = RE_DUVODOVA.match(radek.rstrip("\n")) is not None and start >= zacatek_duv
        if je_duv:
            v_duvodove = True
            uzavri(start)
            novy("duvodova_zprava", "důvodová zpráva", start)
            radky_useku.append(radek)
            continue

        nadpis = _nadpis(radky, i)
        if v_duvodove and not (stridave and nadpis and nadpis[0] == "bod"):
            if not useky or useky[-1].typ != "duvodova_zprava":
                uzavri(start)
                novy("duvodova_zprava", "důvodová zpráva", start)
            radky_useku.append(radek)
            continue
        v_duvodove = False

        if nadpis is None:
            if not useky or (useky[-1].typ == "duvodova_zprava"):
                uzavri(start)
                novy("odstavec_volny", "", start)
            radky_useku.append(radek)
            continue

        typ, oznaceni, cislo = nadpis
        s = radek.strip()
        # uvozovky (nové znění vkládané bodem) rozhodují jen v PN, kde hlavní strukturu tvoří body:
        # „ČÁST …“ nebo „Čl. …“ uvnitř bodu PN patří pod tento bod, další bod PN je zase nahoře.
        # V návrhu zákona stačí úrovně (§ pod bodem, bod pod článkem). Bod v uvozovkách
        # („14. V § 5 …“ jako nové znění bodu tisku) hlavní strukturu PN netvoří.
        koren = zasobnik[0] if zasobnik else None
        v_uvoz = (s.startswith("„") or hloubka_pred > 0) and koren is not None \
            and koren.typ == "bod" and not koren.v_uvozovkach and not koren.citovany
        if v_uvoz and typ == "bod" and not s.startswith("„") and posledni_bod is not None \
                and cislo == posledni_bod + 1:
            # neuzavřená uvozovka v dokumentu: navazující číslo bodu PN ji ukončuje
            v_uvoz = False
            hloubka = max(0, radek.count("„") - radek.count("“"))

        uzavri(start)
        uroven = UROVEN[typ]
        if not v_uvoz:
            while zasobnik and zasobnik[-1].v_uvozovkach:
                zasobnik.pop()
        while zasobnik and (zasobnik[-1].v_uvozovkach or not v_uvoz) \
                and UROVEN[zasobnik[-1].typ] >= uroven:
            zasobnik.pop()
        zasobnik.append(_Uroven(typ, oznaceni, v_uvoz, s.startswith("„") or hloubka_pred > 0, cislo=cislo))
        if typ == "bod" and not v_uvoz:
            posledni_bod = cislo
        novy(typ, oznaceni, start)
        radky_useku.append(radek)

    uzavri(len(text))
    return useky


def _kusy(text: str, start: int, end: int, limit: int) -> list[tuple[int, int]]:
    """Rozdělí úsek po řádcích na kusy do `limit` znaků; delší řádek dělí na mezeře."""
    kusy: list[tuple[int, int]] = []
    od = start
    for m in re.finditer(r"[^\n]*\n|[^\n]+$", text[start:end]):
        r_od, r_do = start + m.start(), start + m.end()
        if r_do - od <= limit:
            continue
        if r_od > od and r_do - r_od <= limit:
            # řádek se vejde do nového kusu
            kusy.append((od, r_od))
            od = r_od
            continue
        # dlouhý řádek: doplní rozpracovaný kus a zbytek dělí na mezerách
        while r_do - od > limit:
            mezera = max(text.rfind(" ", max(od, r_od), od + limit), text.rfind("	", max(od, r_od), od + limit))
            if mezera >= max(od, r_od) and mezera + 1 > od:
                rez = mezera + 1
            elif r_od > od:
                rez = r_od
            else:
                rez = od + limit
            kusy.append((od, rez))
            od = rez
    if od < end:
        kusy.append((od, end))
    return kusy


def rozdel(text: str) -> list[Cel]:
    """Rozdělí text na celky: nejmenší strukturní jednotky do 4 000 znaků, volný text
    a důvodovou zprávu po odstavcích do ~2 000 znaků."""
    cely: list[Cel] = []
    cislo_volny = 0
    cislo_duv = 0
    for u in _useky(text):
        strukturni = u.typ in UROVEN
        if strukturni and len(text[u.start:u.end].strip()) <= MAX_CEL:
            kusy = [(u.start, u.end)]
        else:
            kusy = _kusy(text, u.start, u.end, MAX_CEL if strukturni else MAX_VOLNY)
        kusy = [(a, b) for a, b in kusy if text[a:b].strip()]
        for k, (a, b) in enumerate(kusy, 1):
            usek = text[a:b]
            oznaceni, cesta = u.oznaceni, u.cesta
            if strukturni:
                if len(kusy) > 1:
                    cesta = f"{cesta} ({k}/{len(kusy)})"
            elif u.typ == "odstavec_volny":
                cislo_volny += 1
                oznaceni = cesta = f"odst. {cislo_volny}"
            else:
                cislo_duv += 1
                oznaceni = f"důvodová zpráva {cislo_duv}"
                cesta = u.cesta
            cely.append(Cel(
                poradi=len(cely),
                typ=u.typ,
                oznaceni=oznaceni,
                cesta=cesta,
                meni_zakon=u.meni_zakon,
                text=usek.strip(),
                od_znaku=a + len(usek) - len(usek.lstrip()),
            ))
    return cely
