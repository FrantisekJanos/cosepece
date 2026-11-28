"""Agentní smyčka jednoho tahu (spec 8.2, 11.1): volání modelu, nástroje, stropy kol, ceny a výsledků.

`tah` je asynchronní generátor událostí pro službu: `stav` (co se děje), `text` (kousek odpovědi),
`volani_llm` (usage a cena jednoho volání), `volani_nastroje`, a vždy jako poslední `konec`
{text, stav, cena_usd, kol, model}. Výjimka z modelu ani z nástroje do volajícího nepropadne: skončí
stavem `chyba` a cenou volání, která proběhla.

Stavy: `ok`; `strop` (strop kol, ceny tahu nebo rozpočtu výsledků, poslední odpověď bez nástrojů, nebo
odpověď useknutá na `max_tokens`); `odmitnuto` (stop_reason `refusal`); `chyba` (`Pretizeno` i jiná chyba);
`preruseno` (`prerusit` nastavený, tah skončí po rozběhnutém kole). `pause_turn` (jen serverové nástroje,
které tah nemá) je `chyba`.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from .cenik import CENY, Cena, cena_usd
from .config import Nastaveni
from .historie import ZNAKU_NA_TOKEN, odhad_tokenu
from .llm import LLM, Odpoved, Pretizeno, Usage
from .nastroje import SCHEMATA, Polozka, Vysledek
from .prompt import SYSTEM
from .registr import Registr, Zdroj

log = logging.getLogger("hlidac_rag.chat.smycka")

SOUBEH_NASTROJU = 2            # spec 8.2: pevně nejvýš 2 souběžná volání nástrojů v kole
POKYN_STROP = "Další nástroje už nevolej. Odpověz z toho, co máš, a řekni, co jsi nestihl prohledat."
ROZPOCET_VYCERPAN = ("Rozpočet výsledků nástrojů pro tuto odpověď je vyčerpaný, výsledek se nevrátil. "
                     "Odpověz z toho, co už máš.")
ZKRACENO_ROZPOCTEM = "\n[zkráceno: rozpočet výsledků nástrojů pro tuto odpověď je vyčerpaný]"
MIN_ZBYTEK_TOKENU = 50         # menší zbytek rozpočtu už nemá smysl plnit useknutým výsledkem
NASTROJ_SELHAL = "Nástroj {nazev} selhal na chybě serveru. Zkuste jiný dotaz nebo jiný nástroj."

_POPIS = {
    "get_tisk": "detail tisku", "seznam_tisku": "seznam tisků", "tisky_menici_zakon": "tisky měnící zákon",
    "tisk_podle_sbirky": "tisk podle Sbírky", "pn_k_tisku": "pozměňovací návrhy", "get_pn": "pozměňovací návrh",
    "osnova_textu": "osnova textu", "get_cel": "text návrhu", "hledej_v_textech": "hledání v textech",
    "rozprava": "rozprava", "projevy_k_tisku": "projevy k tisku", "projevy_recnika": "projevy řečníka",
    "get_projev": "projev", "hledej_v_projevech": "hledání v projevech", "najdi_osobu": "osoba",
    "hlasovani_k_tisku": "hlasování k tisku", "get_hlasovani": "hlasování", "upozorneni": "upozornění",
    "pripravovane": "připravované zákony", "nacti_zdroj": "zdroj konverzace",
}


@dataclass
class Udalost:
    typ: str
    data: dict


def _ocen(model: str, vstup: int, vystup: int, cteni: int, zapis: int, cenik: dict[str, Cena] | None) -> float:
    """Cena podle ceníku. Model mimo ceník je chyba konfigurace: zaloguje se a ocení nejdražší cenou
    z ceníku, ať se rozpočet nikdy nepodhodnotí."""
    try:
        return cena_usd(model, vstup, vystup, cteni, zapis, cenik)
    except KeyError:
        log.error("model %s není v ceníku, cena podle nejdražšího modelu", model)
        ceny = cenik if cenik is not None else CENY
        return max(cena_usd(m, vstup, vystup, cteni, zapis, ceny) for m in ceny)


def _cena(odp: Odpoved, cenik: dict[str, Cena] | None) -> float:
    """Cena volání podle modelu, který odpověděl."""
    u = odp.usage
    return _ocen(odp.model, u.vstup, u.vystup, u.cache_cteni, u.cache_zapis, cenik)


def _odhad_volani(usage: Usage, novych_tokenu: int, model: str, n: Nastaveni,
                  cenik: dict[str, Cena] | None) -> float:
    """Odhad dalšího volání: celý vstup posledního volání jako čtení cache, jeho výstup a nové výsledky nástrojů
    (s pokynem) jako zápis do cache a plných max_tokenu_odpovedi výstupu."""
    cteni = usage.vstup + usage.cache_cteni + usage.cache_zapis
    zapis = usage.vystup + novych_tokenu
    return _ocen(model, 0, n.max_tokenu_odpovedi, cteni, zapis, cenik)


def _int(s: str) -> int | None:
    return int(s) if s.isdigit() else None


def preloz_zdroj(z: Zdroj) -> tuple[str, dict] | None:
    """Kanonický klíč zdroje (spec 9.1) → volání nástroje, které zdroj znovu načte; neznámý tvar = None.

    Celek textu se načítá přes poradi z meta, je-li (klíč s označením není jednoznačný, např. „bod 1“ ve dvou
    článcích); jinak podle označení z klíče."""
    druh, _, zbytek = z.klic.partition(":")
    if druh == "tisk" and _int(zbytek) is not None:
        return "get_tisk", {"ct": int(zbytek)}
    if druh == "pn" and _int(zbytek) is not None:
        return "get_pn", {"cislo": int(zbytek)}
    if druh == "cel":
        dok, _, zbytek = zbytek.partition(":")
        if dok == "tisk":
            ct, verze, oznaceni = (zbytek.split(":", 2) + ["", ""])[:3]
            if _int(ct) is None:
                return None
            args: dict = {"ct": int(ct)}
            if verze in ("puvodni", "schvalene"):
                args["verze"] = verze
        elif dok == "pn":
            pn, _, oznaceni = zbytek.partition(":")
            if _int(pn) is None:
                return None
            args = {"pn": int(pn)}
        else:
            return None
        poradi = z.meta.get("poradi")
        if isinstance(poradi, int):
            args["poradi"] = poradi
        elif oznaceni.startswith("poradi ") and _int(oznaceni[7:]) is not None:
            args["poradi"] = int(oznaceni[7:])
        elif oznaceni:
            args["oznaceni"] = oznaceni
        else:
            return None
        return "get_cel", args
    if druh == "projev" and zbytek:
        return "get_projev", {"klic": zbytek}
    if druh == "hlas" and _int(zbytek) is not None:
        return "get_hlasovani", {"id_hlasovani": int(zbytek)}
    if druh == "zakon" and zbytek:
        return "tisky_menici_zakon", {"sb": zbytek}
    if druh == "rozbor":
        typ, _, cislo = zbytek.partition(":")
        if _int(cislo) is None:
            return None
        if typ == "tisk":
            return "get_tisk", {"ct": int(cislo), "vcetne": ["rozbor"]}
        if typ == "pn":
            return "get_pn", {"cislo": int(cislo)}
        return None
    if druh == "upoz":
        casti = zbytek.split(":")
        ct = _int(casti[1]) if len(casti) > 1 else None
        return "upozorneni", ({"ct": ct} if ct else {})
    if druh == "prip" and zbytek:
        return "pripravovane", {"pid": zbytek}
    return None


async def _proved(blok: dict, spust_nastroj: Callable[[str, dict], Awaitable[Vysledek]], registr: Registr,
                  sem: asyncio.Semaphore) -> tuple[str | None, dict | None, Vysledek, int]:
    """(skutečný nástroj, jeho argumenty, výsledek, ms) pro jeden blok tool_use; nikdy nevyhodí výjimku."""
    nazev, vstup = blok.get("name", ""), blok.get("input") or {}
    if nazev == "nacti_zdroj":
        n = vstup.get("n")
        z = registr.podle_n(n) if isinstance(n, int) else None
        preklad = preloz_zdroj(z) if z is not None else None
        if preklad is None:
            return None, None, Vysledek(f"Zdroj [{n}] v seznamu zdrojů konverzace není.", [], True), 0
        nazev, vstup = preklad
    async with sem:
        zacatek = time.monotonic()
        try:
            v = await spust_nastroj(nazev, vstup)
        except Exception:
            log.exception("nástroj %s selhal", nazev)
            v = Vysledek(NASTROJ_SELHAL.format(nazev=nazev), [], True)
        return nazev, vstup, v, int((time.monotonic() - zacatek) * 1000)


_CISLA = "Čísla zdrojů pro citaci: "


def _cisla(registr: Registr, polozky: list[Polozka]) -> tuple[str, list[int]]:
    """Položky do registru; řádek s čísly [N], ať je model může citovat."""
    cisla = [registr.pridej(p) for p in polozky]
    if not cisla:
        return "", []
    return _CISLA + ", ".join(f"[{c}] {p.klic}" for c, p in zip(cisla, polozky)) + "\n", cisla


def _do_rozpoctu(registr: Registr, v: Vysledek, zbyva: int) -> tuple[str, list[int], bool]:
    """(obsah pro model, čísla zdrojů, oříznuto) do zbývajícího rozpočtu výsledků v tokenech.

    Celý výsledek: do registru všechny položky. Oříznutý: jen položky, jejichž klíč zůstal v ponechaném textu
    (hlavička adaptéru je na začátku), ať model necituje nic, co nedostal. Řádek s čísly má rezervu
    s pětimístnými čísly, takže se vždy vejde. Když se nevejde ani kus, vrátí ROZPOCET_VYCERPAN."""
    rezerva = (len(_CISLA) + sum(len(f"[99999] {p.klic}, ") for p in v.polozky) + 1) if v.polozky else 0
    if math.ceil((len(v.text) + rezerva) / ZNAKU_NA_TOKEN) <= zbyva:
        hlavicka, cisla = _cisla(registr, v.polozky)
        return hlavicka + v.text, cisla, False
    k = math.floor(zbyva * ZNAKU_NA_TOKEN) - rezerva - len(ZKRACENO_ROZPOCTEM)
    if k < MIN_ZBYTEK_TOKENU * ZNAKU_NA_TOKEN:
        return ROZPOCET_VYCERPAN, [], False
    ponechano = v.text[:k]
    hlavicka, cisla = _cisla(registr, [p for p in v.polozky if p.klic in ponechano])
    return hlavicka + ponechano + ZKRACENO_ROZPOCTEM, cisla, True


async def _volani(llm: LLM, n: Nastaveni, model: str, zpravy: list[dict], tool_choice: dict | None,
                  oddelit: bool) -> AsyncIterator[tuple[str, object]]:
    """Jedno volání modelu: ("text", kousek) průběžně, nakonec ("odpoved", Odpoved). Text z `na_text`
    jde přes frontu, ať ho generátor může vydávat během streamu. `oddelit` = před prvním kouskem odstavec."""
    fronta: asyncio.Queue[str] = asyncio.Queue()
    prvni = True

    async def na_text(t: str) -> None:
        nonlocal prvni
        if not t:
            return
        if prvni and oddelit:
            fronta.put_nowait("\n\n")
        prvni = False
        fronta.put_nowait(t)

    uloha = asyncio.ensure_future(llm.volej(model=model, effort=n.effort, system=SYSTEM, tools=SCHEMATA,
                                            messages=zpravy, max_tokens=n.max_tokenu_odpovedi, na_text=na_text,
                                            tool_choice=tool_choice))
    try:
        while not uloha.done() or not fronta.empty():
            if not fronta.empty():
                yield "text", fronta.get_nowait()
                continue
            cteni = asyncio.ensure_future(fronta.get())
            await asyncio.wait({uloha, cteni}, return_when=asyncio.FIRST_COMPLETED)
            if cteni.done():
                yield "text", cteni.result()
            else:
                cteni.cancel()
        yield "odpoved", uloha.result()
    finally:
        if not uloha.done():
            uloha.cancel()


async def tah(llm: LLM, spust_nastroj: Callable[[str, dict], Awaitable[Vysledek]], messages: list[dict],
              registr: Registr, n: Nastaveni, model: str, prerusit: asyncio.Event,
              cenik: dict[str, Cena] | None = None) -> AsyncIterator[Udalost]:
    """Jeden tah konverzace nad `messages` (z historie.sestav); seznam volajícího se nemění."""
    zpravy = list(messages)
    cena, kol, zbyva_vysledku = 0.0, 0, n.rozpocet_vysledku_tokenu
    text: list[str] = []
    posledni_model, stav, posledni = model, "ok", False

    def konec() -> Udalost:
        return Udalost("konec", {"text": "".join(text), "stav": stav, "cena_usd": cena, "kol": kol,
                                 "model": posledni_model})

    if prerusit.is_set():
        stav = "preruseno"
        yield konec()
        return
    yield Udalost("stav", {"text": "Přemýšlím…"})
    sem = asyncio.Semaphore(SOUBEH_NASTROJU)
    while True:
        odp: Odpoved | None = None
        volani = _volani(llm, n, model, zpravy, {"type": "none"} if posledni else None, bool(text))
        try:
            async for druh, hodnota in volani:
                if druh == "text":
                    text.append(hodnota)
                    yield Udalost("text", {"text": hodnota})
                else:
                    odp = hodnota
        except Pretizeno as e:
            log.warning("model přetížený: %s", e)
            stav = "chyba"
            break
        except Exception:
            log.exception("volání modelu selhalo")
            stav = "chyba"
            break
        finally:
            await volani.aclose()     # zavřený tah (odpojený klient) zruší i rozběhnuté volání modelu
        cena_volani = _cena(odp, cenik)
        cena += cena_volani
        posledni_model = odp.model
        u = odp.usage
        yield Udalost("volani_llm", {"model": odp.model, "stop_reason": odp.stop_reason, "vstup": u.vstup,
                                     "vystup": u.vystup, "cache_cteni": u.cache_cteni, "cache_zapis": u.cache_zapis,
                                     "cena_usd": cena_volani, "ms": odp.ms, "kolo": kol + 1})
        if odp.stop_reason == "refusal":
            stav = "odmitnuto"
            break
        if posledni or odp.stop_reason == "max_tokens":
            stav = "strop"      # tool_use v posledním volání se nevykoná
            break
        if odp.stop_reason == "pause_turn":
            # nastává jen u serverových nástrojů, které tah nemá; pokračování by bylo prefillem (400)
            log.error("neočekávaný stop_reason pause_turn")
            stav = "chyba"
            break
        bloky = [b for b in odp.content if b.get("type") == "tool_use"]
        if odp.stop_reason != "tool_use" or not bloky:
            break

        zpravy.append({"role": "assistant", "content": odp.content})
        yield Udalost("stav", {"text": "Hledám v datech: " + ", ".join(
            dict.fromkeys(_POPIS.get(b.get("name"), "data") for b in bloky)) + "…"})
        hotove = await asyncio.gather(*(_proved(b, spust_nastroj, registr, sem) for b in bloky))
        vysledky: list[dict] = []
        for blok, (nastroj, argumenty, v, ms) in zip(bloky, hotove):
            orezano, cisla = False, []
            if v.chyba:
                obsah, chyba = v.text, True
            elif zbyva_vysledku < MIN_ZBYTEK_TOKENU:
                obsah, chyba = ROZPOCET_VYCERPAN, True
            else:
                obsah, cisla, orezano = _do_rozpoctu(registr, v, zbyva_vysledku)
                chyba = obsah == ROZPOCET_VYCERPAN
                if not chyba:
                    zbyva_vysledku -= odhad_tokenu(obsah)
            vysledek = {"type": "tool_result", "tool_use_id": blok["id"], "content": obsah}
            if chyba:
                vysledek["is_error"] = True
            vysledky.append(vysledek)
            yield Udalost("volani_nastroje", {"nazev": blok.get("name"), "argumenty": blok.get("input") or {},
                                              "nastroj": nastroj, "argumenty_nastroje": argumenty, "chyba": chyba,
                                              "ms": ms, "tokenu": odhad_tokenu(obsah), "orezano": orezano,
                                              "polozky": cisla})
        kol += 1
        if prerusit.is_set():
            stav = "preruseno"
            break
        # Strop ceny: další kolo s nástroji jen když se vejde ono i závěrečná odpověď bez nástrojů (2× odhad),
        # jinak by tah přetekl přes strop_tahu a den by přestal být tvrdý (spec 11.1).
        novych = sum(odhad_tokenu(b["content"]) for b in vysledky) + odhad_tokenu(POKYN_STROP)
        odhad = _odhad_volani(u, novych, model, n, cenik)
        if kol >= n.max_kol or cena + 2 * odhad > n.strop_tahu or zbyva_vysledku < MIN_ZBYTEK_TOKENU:
            vysledky.append({"type": "text", "text": POKYN_STROP})
            posledni = True
        zpravy.append({"role": "user", "content": vysledky})
    yield konec()
