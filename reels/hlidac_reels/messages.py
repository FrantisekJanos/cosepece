"""Texty zpráv Telegram bota: popisek videa, náhled popisku, výsledek publikace, tlačítka.

threads=True znamená, že je Threads nastavené (THREADS_USER_ID): náhled ukáže i text pro Threads
a výsledek řádek Threads. Bez něj se o Threads nepíše nic.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from .caption import for_network
from .jobs import lease_expired
from .queue import (AUTO_HOLD, AUTO_SET_MAX, AUTO_SOURCE, HELD_MAX, PICKED_MAX, REQUESTED_SET_MAX,
                    UNPICKED_AFTER_DECISION, Expired, Reel, Unit)

TEXT_LIMIT = 4096
VIDEO_CAPTION_LIMIT = 1024
NO_LINK = "odkaz se nepodařilo načíst"
THREADS_PREFIX = "Threads: "  # publish.run tak označí chybu Threads v reel.error
REASON_LIMIT = 300
LANDSCAPE_NOTE = "na šířku (Threads)"


def video_caption(reel: Reel, landscape: bool = False) -> str:
    """Popisek videa v Telegramu; landscape=True u videa na šířku (dostane ho jen Threads)."""
    parts = [f"#{reel.id}"]
    if reel.ct is not None:
        parts.append(f"tisk {reel.ct}")
    if reel.format:
        parts.append(reel.format)
    if landscape:
        parts.append(LANDSCAPE_NOTE)
    return " · ".join(parts)[:VIDEO_CAPTION_LIMIT]


def _variant_label(reel: Reel) -> str:
    """v2, nebo v3 s formátem (template_v3)."""
    if reel.template == "template_v3":
        return f"v3 {reel.format}" if reel.format else "v3"
    return "v2"


def set_video_caption(reel: Reel) -> str:
    return f"#{reel.set_id} · varianta {reel.variant} · {_variant_label(reel)}"[:VIDEO_CAPTION_LIMIT]


def remaining_text(set_id: int, variants: list[int]) -> str:
    """Úvod zprávy sady po rozhodnutí o vybraných variantách: co ještě čeká na výběr."""
    if len(variants) == 1:
        return f"Sada #{set_id}: zbývá varianta {variants[0]}. Vybrat, nebo Hotovo."
    return f"Sada #{set_id}: zbývají varianty {', '.join(str(v) for v in variants)}. Vybrat, nebo Hotovo."


def set_text(items: list[Reel], remaining: bool = False, skipped: list[dict] | None = None) -> str:
    """Zpráva sady nad tlačítky Vybrat: tisk a z každé nevybrané varianty první 2 řádky popisku.

    remaining=True: úvod „zbývá varianta N“ (po rozhodnutí o vybraných variantách).
    skipped = nevzniklé varianty sady (jobs.skipped): na konci řádky „Varianta 3 (zmena) nevznikla: …“.
    """
    first = items[0]
    if remaining:
        head = remaining_text(first.set_id, [r.variant for r in items])
        if first.ct is not None:
            head += f"\nTisk {first.ct}"
    else:
        head = f"Sada #{first.set_id}" + (f" · tisk {first.ct}" if first.ct is not None else "")
        head += "\nVyber jednu nebo víc variant, Hotovo zbylé zamítne."
    blocks = []
    for r in items:
        lines = "\n".join(r.caption.strip().splitlines()[:2])
        blocks.append(f"{r.variant} ({_variant_label(r)}):\n{lines}")
    tail = [autopilot_skipped(skipped)] if skipped else []
    # dlouhé řádky se zkrátí rovnoměrně, aby se do zprávy vešly všechny varianty i nevzniklé varianty na konci
    room = (TEXT_LIMIT - len(head) - sum(len(t) for t in tail) - 2 * (len(blocks) + len(tail))) // max(len(blocks), 1)
    blocks = [b if len(b) <= room else b[:max(room - 1, 0)] + "…" for b in blocks]
    return "\n\n".join([head, *blocks, *tail])[:TEXT_LIMIT]


def set_buttons(items: list[Reel]) -> list[tuple[str, str]]:
    """Vybrat N pro každou nevybranou variantu a Hotovo (zamítne zbylé nevybrané, před výběrem všechny)."""
    picks = [(f"Vybrat {r.variant}", f"pick:{r.id}") for r in items]
    return picks + [("Hotovo, zbytek zamítnout", f"doneset:{items[0].set_id}")]


def _fb_block(reel: Reel) -> str:
    return f"Náhled #{reel.id}\n\nFacebook:\n{for_network(reel.caption, reel.ct, 'fb')}"


def _ig_block(reel: Reel) -> str:
    ig = for_network(reel.caption, reel.ct, "ig")
    if ig.startswith(reel.caption):
        ending = ig[len(reel.caption):].strip()
        return f"Instagram: stejný text, jen místo odkazu končí:\n{ending}"
    return f"Instagram:\n{ig}"


def _threads_block(reel: Reel) -> str:
    # vlastník vidí, které video Threads dostane (šířku, když je, jinak 9:16)
    shape = "na šířku" if reel.video_landscape_path else "na výšku"
    return f"Threads (video {shape}):\n{for_network(reel.caption, reel.ct, 'threads')}"


def _blocks(reel: Reel, threads: bool) -> list[str]:
    blocks = [_fb_block(reel), _ig_block(reel)]
    if threads:
        blocks.append(_threads_block(reel))
    return blocks


def preview_text(reel: Reel, threads: bool = False) -> str:
    """Přesně to, co půjde ven: celý text pro FB, pod ním konec, kterým se liší IG, a text pro Threads."""
    return "\n\n".join(_blocks(reel, threads))


def preview_parts(reel: Reel, threads: bool = False) -> list[str]:
    """Náhled rozdělený na zprávy do 4 096 znaků (obvykle jedna, u dlouhého popisku každá síť zvlášť)."""
    text = preview_text(reel, threads)
    if len(text) <= TEXT_LIMIT:
        return [text]
    parts = []
    for block in _blocks(reel, threads):
        # pojistka pro popisek delší než limit zprávy (enqueue ho nepustí, ale DB ano)
        parts.extend(block[i:i + TEXT_LIMIT] for i in range(0, len(block), TEXT_LIMIT))
    return parts


def approval_buttons(reel_id: int) -> list[tuple[str, str]]:
    return [("Schválit", f"ok:{reel_id}"), ("Upravit popisek", f"edit:{reel_id}"),
            ("Zamítnout", f"no:{reel_id}")]


def retry_buttons(reel_id: int) -> list[tuple[str, str]]:
    return [("Zkusit znovu", f"retry:{reel_id}")]


def interrupted_text(reel_id: int) -> str:
    """Schválená položka, u které publikace nezačala (pád, restart nebo uvíznutí za běhu)."""
    return f"#{reel_id}: zveřejnění bylo přerušeno před začátkem publikace."


def stuck_publishing_text(reel_id: int, restart: bool) -> str:
    """Publikace přerušená restartem nebo uvízlá déle než STALE_PUBLISHING: výsledek je nejistý."""
    why = "publikaci přerušil restart" if restart else "zveřejňování uvízlo"
    return f"#{reel_id}: {why}, výsledek je nejistý. Zkusit znovu nejdřív dohledá, co už na sítích je."


def _unit_label(reel: Reel) -> str:
    """Jednotka v textu zprávy: sada podle set_id, samostatné video podle id."""
    return f"Sada #{reel.set_id}" if reel.set_id is not None else f"#{reel.id}"


def held_text(reel: Reel) -> str:
    """Jednotku se opakovaně nepodařilo poslat do Telegramu (stav held)."""
    what = _unit_label(reel)
    where = f" (tisk {reel.ct})" if reel.ct is not None else ""
    error = (reel.send_error or "")[:REASON_LIMIT]
    reason = f"\nPoslední chyba: {error}" if error else ""
    return (f"{what}{where} se nepodařilo poslat ke schválení ani po {reel.send_attempts} pokusech.{reason}\n"
            "Zkusit znovu, nebo zahodit?")


def held_buttons(reel_id: int) -> list[tuple[str, str]]:
    return [("Zkusit znovu", f"sendretry:{reel_id}"), ("Zahodit", f"drop:{reel_id}")]


def held_dropped_text(reel: Reel) -> str:
    """Potvrzení Zahodit u jednotky, kterou nešlo poslat."""
    return f"Sada #{reel.set_id} zahozena." if reel.set_id is not None else f"#{reel.id} zahozeno."


def set_dropped_text(set_id: int) -> str:
    """Žádné video sady Telegram nepřijal (odmítnutí 4xx nebo nečitelný soubor)."""
    return f"Sada #{set_id} vyřazena: žádné její video nejde poslat do Telegramu."


def auto_waiting_text(set_id: int, limit: int, reason: str) -> str:
    """Automatická sada čeká: reason 'limit' = denní limit, jinak nerozhodnutá jiná automatická sada."""
    if reason == "limit":
        return (f"Sada #{set_id} čeká na zítřek: denní limit {limit} automatických sad je vyčerpaný. "
                "Poslat hned: /dalsi")
    return (f"Sada #{set_id} čeká, dokud nevybereš z předchozí automatické sady (nejdéle {AUTO_HOLD} h). "
            "Poslat hned: /dalsi")


def withdrawn_text(set_id: int) -> str:
    """Sada stažená, protože její tisk vypadl z hlavních nálezů (fresh.retire)."""
    return f"#{set_id} staženo: nález už není mezi hlavními"


def expired_text(e: Expired) -> str:
    """Jedna zpráva o vypršení jednotky (queue.expire); vypršení jen zamítá, nikdy neschvaluje."""
    ids = ", ".join(f"#{i}" for i in e.reel_ids)
    if e.kind == "unpicked":
        hours = int(UNPICKED_AFTER_DECISION.total_seconds() // 3600)
        return f"Sada #{e.set_id}: nevybrané varianty vypršely ({hours} h od posledního rozhodnutí)."
    if e.kind == "picked":
        what = "vybraná varianta" if len(e.reel_ids) == 1 else "vybrané varianty"
        verb = "vypršela" if len(e.reel_ids) == 1 else "vypršely"
        return f"Sada #{e.set_id}: {what} {ids} {verb} ({PICKED_MAX.days} dní bez schválení)."
    if e.kind == "held":
        if e.set_id is not None:
            return f"Sada #{e.set_id} zamítnuta: {HELD_MAX.days} dní se nepodařilo poslat ke schválení."
        return f"{ids} zamítnuto: {HELD_MAX.days} dní se nepodařilo poslat ke schválení."
    days = (AUTO_SET_MAX if e.kind == "auto_set" else REQUESTED_SET_MAX).days
    if e.set_id is not None:
        return f"Sada #{e.set_id} vypršela: {days} dní bez výběru."
    return f"{ids} vypršelo: {days} dní bez rozhodnutí."


def reminder_text(set_id: int) -> str:
    """Nedotčená automatická sada čeká AUTO_HOLD h (už nebrzdí další automatickou)."""
    return f"Sada #{set_id} čeká {AUTO_HOLD} h. Vybrat, nebo Hotovo."


# --- /fronta: přehled -----------------------------------------------------

QUEUE_EMPTY = "Fronta je prázdná."
RESEND_BUTTONS = [("Poslat tlačítka znovu", "resend:0")]
_MINE = ("approval", "untouched", "picked", "remaining")
_READY = ("next", "deferred", "limit", "blocked", "autopilot", "approved", "publishing")
_PROBLEMS = ("held", "failed", "unknown", "stale")
_PER_VARIANT = ("autopilot", "approved", "publishing", "stale", "failed", "unknown")
# proč úloha s odkladem čeká (reel_jobs.wait_reason); retry a neznámý důvod = jen „další pokus H:MM“
_WAIT_TEXT = {"ai_title": "čeká na rozbor AI", "env": "čeká po chybě prostředí",
              "limit": "čeká na limit předplatného"}


def _age(since: datetime, now: datetime) -> str:
    """Jak dlouho položka čeká: minuty do hodiny, hodiny do dvou dnů, pak dny."""
    mins = max(int((now - since).total_seconds() // 60), 0)
    if mins < 60:
        return f"{mins} min"
    if mins < 48 * 60:
        return f"{mins // 60} h"
    return f"{mins // (24 * 60)} d"


def _what(ct: int | None, focus_pn) -> str:
    """Předmět videa: PN, tisk, nebo automatický výběr (zadání bez tisku)."""
    if focus_pn:
        return "PN " + "+".join(str(n) for n in focus_pn)
    return f"tisk {ct}" if ct is not None else "automatický výběr"


def _variants(nums: list[int], one: str, many: str) -> str:
    return f"{one} {nums[0]}" if len(nums) == 1 else f"{many} {', '.join(str(n) for n in nums)}"


def _unit_line(u: Unit, now: datetime) -> str:
    """Řádek přehledu: číslo, předmět a na co jednotka čeká."""
    head = f"#{u.label} {_what(u.ct, u.focus_pn)}"
    if u.state in ("next", "limit", "blocked", "deferred") and u.source == AUTO_SOURCE:
        head += " (automatická)"
    # u schválených a publikovaných variant sady: které (čekající, odkládané a held se týkají celé sady)
    which = [_variants(u.variants, "varianta", "varianty")] if u.variants and u.state in _PER_VARIANT else []
    age = _age(u.since, now)
    if u.state == "approval":
        parts = ["čeká na schválení", age]
    elif u.state == "untouched":
        parts = ["nedotčená", age]
    elif u.state == "picked":
        verb = "čeká" if len(u.variants) == 1 else "čekají"
        parts = [f"{_variants(u.variants, 'vybraná varianta', 'vybrané varianty')} {verb} na schválení", age]
    elif u.state == "remaining":
        parts = [_variants(u.variants, "zbývá varianta", "zbývají varianty"), age, "Hotovo?"]
    elif u.state == "next":
        parts = ["na řadě"]
    elif u.state == "deferred":
        parts = [f"odloženo do {u.note} (chyba odeslání, pokus {u.attempts})"]
    elif u.state == "limit":
        parts = [f"čeká: denní limit {u.note}", "/dalsi"]
    elif u.state == "blocked":
        parts = [f"čeká na výběr z předchozí automatické sady (nejdéle {AUTO_HOLD} h)", "/dalsi"]
    elif u.state == "autopilot":
        parts = ["čeká na automatické zveřejnění"]
    elif u.state == "approved":
        parts = ["schváleno, čeká na zveřejnění", age]
    elif u.state == "publishing":
        parts = ["zveřejňuje se", age]
    elif u.state == "stale":
        parts = ["zveřejňování možná přerušeno, bot ho brzy převede na nejisté"]
    elif u.state == "held":
        error = (u.note or "")[:REASON_LIMIT]
        parts = [f"nejde doručit ({error + ', ' if error else ''}{u.attempts}×)", "Zkusit znovu / Zahodit"]
    elif u.state == "failed":
        error = (u.note or "")[:REASON_LIMIT]
        parts = [f"zveřejnění selhalo{': ' + error if error else ''}", "Zkusit znovu"]
    elif u.state == "unknown":
        parts = ["výsledek zveřejnění nejistý", "Zkusit znovu"]
    else:
        parts = [u.state]
    return " · ".join([head, *which, *parts])


def _hm_local(t: datetime) -> str:
    """H:MM v časové zóně hodnoty (queue_overview bez hm; bot předává jobs.hm, tedy Europe/Prague)."""
    return f"{t.hour}:{t.minute:02d}"


def _job_generating(j, now: datetime) -> str:
    """Řádek vydané úlohy: fáze (nebo „generuje se“), jak dlouho trvá, a PC neodpovídá u propadlého vydání."""
    stage = _STAGE_TEXT.get(j.stage, j.stage) if j.stage else "generuje se"
    parts = [f"#{j.id} {_what(j.ct, j.focus_pn)}", stage, _age(j.stage_at or j.claimed_at or j.created_at, now)]
    if lease_expired(j, now):
        parts.append("PC neodpovídá")
    return " · ".join(parts)


def _job_waiting(j, hm: Callable[[datetime], str]) -> str:
    """Řádek úlohy s odkladem (not_before v budoucnu): proč čeká a kdy je další pokus."""
    nxt = f"další pokus {hm(j.not_before)}"
    why = _WAIT_TEXT.get(j.wait_reason or "")
    return f"#{j.id} {_what(j.ct, j.focus_pn)} · " + (f"{why} ({nxt})" if why else nxt)


def queue_overview(units: list[Unit], jobs: list, worker_line: str, now: datetime | None = None,
                   hm: Callable[[datetime], str] | None = None) -> str:
    """Přehled /fronta (spec 4.2): sekce v pevném pořadí, prázdné se vynechají, poslední řádek stav PC.

    units z queue.open_units, jobs = aktivní zadání generátoru (jobs.pending, v pořadí výdeje), hm = čas H:MM
    pro „další pokus“ (bot: jobs.hm v Europe/Prague). Ve frontě zadání nejdřív úlohy připravené k výdeji
    (číslované podle pořadí výdeje), za nimi úlohy s odkladem; rozhoduje not_before, ne wait_reason (ten po
    návratu do fronty zůstává). Přehled delší než TEXT_LIMIT zkrátí _fit_overview tak, že Problémy a stav PC
    zůstanou celé.
    """
    now = now or datetime.now(timezone.utc)
    hm = hm or _hm_local
    mine = [u for u in units if u.state in _MINE]
    ready = [u for u in units if u.state in _READY]
    problems = [u for u in units if u.state in _PROBLEMS]
    generating = [j for j in jobs if j.status == "claimed"]
    queued = [j for j in jobs if j.status == "queued"]
    issuable, deferred = [], []
    for j in queued:
        (deferred if j.not_before is not None and j.not_before > now else issuable).append(j)
    sections: list[tuple[str, str, list[str]]] = []  # (klíč pro _TRIM_ORDER, nadpis, řádky položek)
    if mine:
        sections.append(("mine", f"Čeká na tebe ({len(mine)})", [_unit_line(u, now) for u in mine]))
    if ready:
        sections.append(("ready", f"Hotové, čekají na odeslání ({len(ready)})", [_unit_line(u, now) for u in ready]))
    if generating:
        sections.append(("generating", "Generuje se", [_job_generating(j, now) for j in generating]))
    if queued:
        sections.append(("queued", f"Ve frontě zadání ({len(queued)})",
                         [*(f"#{j.id} {_what(j.ct, j.focus_pn)} · {n}. v pořadí" for n, j in enumerate(issuable, 1)),
                          *(_job_waiting(j, hm) for j in deferred)]))
    if problems:
        sections.append(("problems", f"Problémy ({len(problems)})", [_unit_line(u, now) for u in problems]))
    if not sections:
        return f"{QUEUE_EMPTY}\n\n{worker_line}"
    return _fit_overview(sections, worker_line)


# pořadí, ve kterém dlouhý přehled ubírá řádky položek: Problémy až nakonec, stav PC nikdy
_TRIM_ORDER = ("ready", "queued", "mine", "generating", "problems")


def _overview_section(head: str, lines: list[str], keep: int) -> str:
    """Sekce přehledu s prvními keep řádky položek; zkrácená končí „… a další N“."""
    hidden = len(lines) - keep
    return "\n".join([head, *lines[:keep], *([f"… a další {hidden}"] if hidden else [])])


def _fit_overview(sections: list[tuple[str, str, list[str]]], worker_line: str) -> str:
    """Přehled /fronta do TEXT_LIMIT: ubírá řádky položek od konce sekcí v pořadí _TRIM_ORDER (Hotové, Ve frontě
    zadání, Čeká na tebe, Generuje se), až když nestačí, i konec Problémů; stav PC zůstane posledním řádkem."""
    texts = [_overview_section(head, lines, len(lines)) for _, head, lines in sections]
    total = sum(len(t) for t in texts) + 2 * len(texts) + len(worker_line)  # „\n\n“ před každou další částí
    for key in _TRIM_ORDER:
        for i, (k, head, lines) in enumerate(sections):
            if k != key:
                continue
            keep = len(lines)
            while total > TEXT_LIMIT and keep > 0:
                keep -= 1
                shorter = _overview_section(head, lines, keep)
                total += len(shorter) - len(texts[i])
                texts[i] = shorter
    body = "\n\n".join(texts)
    tail = "\n\n" + worker_line
    if len(body) + len(tail) > TEXT_LIMIT:
        # pojistka (samé nadpisy přes limit nevzniknou): uříznout tělo, stav PC zůstane na konci
        body = body[:max(TEXT_LIMIT - len(tail), 0)]
    return body + tail


NOTHING_READY = "Teď není co poslat: položky ve frontě jsou odložené po chybě odeslání, zkusím je znovu."


def _split_error(error: str | None) -> tuple[list[str], list[str]]:
    """Chyba z publish.run rozdělená na důvod Threads a ostatní chyby.

    publish.run dává chybu Threads vždy na konec ("<IG/FB>; Threads: …"), takže důvod Threads je
    celý zbytek textu od prefixu a smí obsahovat i "; ".
    """
    error = error or ""
    if error.startswith(THREADS_PREFIX):
        return [error[len(THREADS_PREFIX):]], []
    sep = "; " + THREADS_PREFIX
    i = error.find(sep)
    if i >= 0:
        return [error[i + len(sep):]], [error[:i]]
    return [], [error] if error else []


def _threads_line(reel: Reel, reasons: list[str]) -> str:
    if reel.threads_media_id:
        return f"Threads: {reel.threads_permalink or NO_LINK}"
    if not reasons:
        return "Threads: nepublikováno"
    word = "výsledek nejistý" if reel.status == "unknown" else "nepublikováno"
    return f"Threads: {word} – {'; '.join(reasons)[:REASON_LIMIT]}"


def result_text(reel: Reel, threads: bool = False) -> str:
    """Výsledek publikace: řádek za každou síť s odkazem; chybějící Threads s důvodem."""
    threads_reasons, other = _split_error(reel.error)
    if reel.status == "published":
        head = f"#{reel.id} zveřejněno."
    elif reel.status == "unknown":
        head = f"#{reel.id}: výsledek zveřejnění je nejistý. Zkusit znovu nejdřív dohledá, co už na sítích je."
    elif reel.status == "failed":
        head = f"#{reel.id}: zveřejnění selhalo."
    else:
        head = f"#{reel.id}: stav {reel.status}."
    lines = [head]
    if reel.status == "published" or reel.fb_video_id:
        lines.append(f"Facebook: {reel.fb_permalink or NO_LINK}")
    if reel.status == "published" or reel.ig_media_id:
        lines.append(f"Instagram: {reel.ig_permalink or NO_LINK}")
    if threads and (reel.status == "published" or reel.threads_media_id or threads_reasons):
        lines.append(_threads_line(reel, threads_reasons))
    elif threads_reasons:
        other = [THREADS_PREFIX + r for r in threads_reasons] + other  # Threads vypnuté, chyba z dřívějška
    if other:
        lines.append(f"Chyba: {'; '.join(other)}")
    return "\n".join(lines)


def autopilot_text(on: bool, to_approval: int = 0, jobs_to_approval: int = 0) -> str:
    """Odpověď na /autopilot: stav přepínače; to_approval = kolik videí autopilota vypnutí vrátilo ke schválení,
    jobs_to_approval = kolika rozpracovaným úlohám autopilota vypnutí smazalo příznak."""
    if on:
        return ("Autopilot zapnut: PN vybraná v /pn se zveřejní bez schválení (2 videa na PN). "
                "Vypnout: /autopilot off")
    text = "Autopilot vypnut: sady jdou ke schválení."
    if to_approval:
        text += f" Dosud nezveřejněná videa autopilota ({to_approval}) půjdou ke schválení."
    if jobs_to_approval:
        text += f" Rozpracované úlohy autopilota ({jobs_to_approval}) půjdou ke schválení i po novém zapnutí."
    return text


# poslední řádek odpovědi na zadání s autopilotem (pod stavovou zprávou)
AUTOPILOT_QUEUED = "Autopilot: po vygenerování se rovnou zveřejní."
AUTOPILOT_PN_LIST_HEADER = "Autopilot zapnut: vybrané PN se zveřejní bez schválení."


def autopilot_done(reel: Reel, focus_pn: list[int] | None) -> str:
    """Hlášení o zveřejnění položky autopilota s odkazy; síť bez odkazu se vynechá."""
    what = []
    if focus_pn:
        what.append("PN " + "+".join(str(n) for n in focus_pn))
    if reel.ct is not None:
        what.append(f"tisk {reel.ct}")
    what.append(f"varianta {reel.format or _variant_label(reel)}")
    links = [f"{name} {link}" for name, link in (("FB", reel.fb_permalink), ("IG", reel.ig_permalink),
                                                ("Threads", reel.threads_permalink)) if link]
    head = f"Autopilot zveřejnil #{reel.id} ({', '.join(what)})"
    return (f"{head}: {' · '.join(links)}" if links else head)[:TEXT_LIMIT]


def autopilot_skipped(skipped: list[dict]) -> str:
    """Varianty sady autopilota, které generátor nevytvořil (z manifestu {n, format, errors}); prázdné = nic."""
    lines = []
    for s in skipped:
        fmt = f" ({s['format']})" if s.get("format") else ""
        errors = s.get("errors") if isinstance(s.get("errors"), list) else []
        reason = "; ".join(str(e) for e in errors)[:REASON_LIMIT] or "důvod neuveden"
        lines.append(f"Varianta {s.get('n')}{fmt} nevznikla: {reason}")
    return "\n".join(lines)[:TEXT_LIMIT]


# --- události úloh a workeru (reel_events, bot.maybe_events) ---------------

FAILED_ERROR_LIMIT = 300  # znaků chyby ve zprávě o selhání
SUMMARY_ERROR_LIMIT = 200  # znaků chyby v řádku denního souhrnu
FRESH_MAX_DAYS = 7  # automatická úloha vyprší ve frontě po 7 dnech (ReelJobs::FRESH_MAX_DAYS)
OUTAGE_BUTTONS = 5  # nejvýš tlačítek Zkusit znovu ve zprávě po výpadku
PAUSE_REASONS = {"limit": "vyčerpaný limit předplatného", "login": "Claude Code není přihlášený",
                 "mcp": "nefunguje MCP server"}
WORKER_RESUMED = "PC s generátorem zase generuje."


def failed_text(job_id: int, what: str, error: str) -> str:
    """Selhání vyžádané úlohy (událost failed, urgency 1); what = popis zadání („tisk 221“)."""
    return f"Zadání #{job_id} ({what}) se nepodařilo: {error[:FAILED_ERROR_LIMIT]}"


def failed_buttons(job_id: int) -> list[tuple[str, str]]:
    return [("Zkusit znovu", f"jobretry:{job_id}"), ("Zahodit", f"jobcancel:{job_id}")]


def _pause_reason(reason: str | None, detail: str | None) -> str:
    """Důvod pozastavení workeru česky; neznámý nebo nečitelný důvod = ""."""
    reason = reason if isinstance(reason, str) else None
    detail = (detail if isinstance(detail, str) else "").strip()
    if reason == "nastroje":
        return f"chybí nástroje ({detail})" if detail else "chybí nástroje"
    if reason == "env":
        return f"chyba prostředí ({detail[:200]})" if detail else "chyba prostředí"
    return PAUSE_REASONS.get(reason or "", "")


def worker_paused_text(reason: str | None, detail: str | None, until_hm: str | None) -> str:
    """Worker se pozastavil (událost worker_paused): jedna zpráva pro všechny čekající úlohy."""
    why = _pause_reason(reason, detail)
    text = "PC s generátorem je pozastavené" + (f": {why}" if why else "")
    if until_hm:
        text += f" (do {until_hm})"
    return text + ". Zadání ve frontě počkají."


def worker_line(seen: str | None, state: dict | None = None, until_hm: str | None = None,
                stale: bool = False) -> str:
    """Řádek stavu PC (/fronta, stavová zpráva zadání): kdy bylo PC naposledy vidět a stav generátoru.

    seen = čas posledního heartbeat („20. 11. 14:05“), None = PC zatím nebylo vidět; state = reel_state.worker_state
    ({state, reason, detail}), důvod pozastavení stejnými slovy jako zpráva o pozastavení (worker_paused_text);
    until_hm = do kdy je pozastaveno. Neznámý stav = jen kdy bylo PC vidět. stale = heartbeat je starší než
    jobs.WORKER_STALE (rozhoduje bot): „PC neodpovídá od …“ bez stavu generátoru, ten už nemusí platit.
    """
    if stale and seen:
        return f"PC neodpovídá od {seen}"
    text = f"PC naposledy: {seen}" if seen else "PC zatím nebylo vidět"
    st = state.get("state") if isinstance(state, dict) else None
    if st == "running":
        return text + " · generátor běží"
    if st == "paused":
        why = _pause_reason(state.get("reason"), state.get("detail"))
        text += " · pozastaveno" + (f": {why}" if why else "")
        if until_hm:
            text += f" (do {until_hm})"
    return text


def silent_text(job_id: int, what: str, since_hm: str) -> str:
    """Vydaná úloha bez známky života (lease_until v minulosti), PC je asi vypnuté."""
    return f"PC neodpovídá od {since_hm}, zadání #{job_id} ({what}) počká."


def outage_text(done: int, failed: list[int], paused: bool) -> str:
    """Neodeslané naléhavé události po výpadku delším než events.OUTAGE v jedné zprávě; "" = není co hlásit."""
    parts = []
    if done:
        parts.append(f"hotové sady: {done}")
    if failed:
        parts.append("nepovedená zadání: " + ", ".join(f"#{i}" for i in failed))
    if paused:
        parts.append("PC se pozastavilo")
    if not parts:
        return ""
    return "Během výpadku: " + "; ".join(parts) + ". /fronta"


def outage_buttons(failed: list[int]) -> list[tuple[str, str]]:
    """Zkusit znovu u prvních OUTAGE_BUTTONS nepovedených zadání ze zprávy po výpadku."""
    return [(f"Zkusit znovu #{i}", f"jobretry:{i}") for i in failed[:OUTAGE_BUTTONS]]


def summary_event_line(kind: str, job_id: int, what: str, error: str) -> str | None:
    """Řádek denního souhrnu z události urgency 2 (selhání nebo vypršení automatické úlohy); None = druh bez řádku."""
    if kind == "failed":
        return f"Zadání #{job_id} ({what}, automatické) se nepodařilo: {error[:SUMMARY_ERROR_LIMIT]}"
    if kind == "expired":
        return f"Zadání #{job_id} ({what}) vypršelo: {FRESH_MAX_DAYS} dní ve frontě"
    return None


# --- stavová zpráva zadání (bot._announce_job, bot._update_status_messages) -----

STATUS_ERROR_LIMIT = 200  # znaků chyby v řádku stavové zprávy
# délka stavové zprávy: jako bot.TELEGRAM_TEXT_LIMIT (rezerva pod 4 096), jinak by bot._clip uřízl poslední řádky
STATUS_TEXT_LIMIT = 4000
STATUS_BEFORE_SHOWN = 3  # nejvýš zadání vypsaných v „před ním“
_STAGE_TEXT = {"model": "model píše varianty", "kontrola": "kontrola", "upload": "nahrávání videí"}
_STAGE_NOTE = ("oprava", "render")  # fáze, u kterých se ukazuje poznámka workeru („render: 2/3“)
_REQUEUE_TEXT = {"env": "chyba prostředí", "infra": "chyba sítě"}  # jiný druh (content od starého workeru) = „chyba“
ZRUS_USAGE = "Použití: /zrus <číslo zadání>"


def _variant_count(n: int) -> str:
    if n == 1:
        return "1 varianta"
    return f"{n} varianty" if 2 <= n <= 4 else f"{n} variant"


def _next_try(detail: dict) -> str:
    hm = str(detail.get("next_hm") or "").strip()
    return f"další pokus {hm}" if hm else ""


def _status_line(kind: str, d: dict) -> str | None:
    """Text řádku stavové zprávy za událost (bez času); None = druh, který se do zprávy nepíše."""
    if kind == "accepted":
        pos, before = d.get("position"), d.get("before")
        text = f"přijato, {pos}. ve frontě" if isinstance(pos, int) and pos > 0 else "přijato"
        before = [i for i in before if isinstance(i, int)] if isinstance(before, list) else []
        if before:
            shown = ", ".join(f"#{i}" for i in before[:STATUS_BEFORE_SHOWN])
            rest = len(before) - STATUS_BEFORE_SHOWN
            text += f" (před ním {shown}{f' a {rest} další' if rest > 0 else ''})"
        return text
    if kind == "claimed":
        attempt = d.get("attempt")
        return f"generuje se ({attempt}. pokus)" if isinstance(attempt, int) and attempt >= 2 else "generuje se"
    if kind == "stage":
        stage, note = str(d.get("stage") or ""), str(d.get("note") or "").strip()
        if stage in _STAGE_NOTE:
            return f"{stage}: {note}" if note else stage
        return _STAGE_TEXT.get(stage, stage or None)
    if kind == "waiting":
        nxt = _next_try(d)
        return f"čeká na rozbor AI ({nxt})" if nxt else "čeká na rozbor AI"
    if kind == "requeued":
        # bez next_hm (fail bez druhu: zpět do fronty bez odkladu) se zkusí hned, jak bude PC volné
        what = _REQUEUE_TEXT.get(d.get("kind"), "chyba")
        return f"{what}, {_next_try(d) or 'zkusí se znovu'}"
    if kind == "lease":
        nxt = _next_try(d)
        return f"PC nedokončilo vydání, {nxt}" if nxt else "PC nedokončilo vydání"
    if kind == "silent":
        return "PC neodpovídá"
    if kind == "done":
        n = d.get("variants")
        return f"hotovo: {_variant_count(n)}" if isinstance(n, int) and not isinstance(n, bool) and n > 0 else "hotovo"
    if kind == "failed":
        error = str(d.get("error") or "").strip()[:STATUS_ERROR_LIMIT]
        return f"nepodařilo se: {error}" if error else "nepodařilo se"
    if kind == "cancelled":
        return "zrušeno"
    if kind == "expired":
        return f"vypršelo ({FRESH_MAX_DAYS} dní ve frontě)"
    return None


def job_status_text(job_id: int, what: str, events: list, worker_line: str | None = None) -> str:
    """Stavová zpráva zadání (spec 4.1): hlavička, řádek „H:MM …“ za každou událost historie (events.for_job),
    stav PC jako poslední řádek, jen když je zadaný. Dlouhá historie se zkrátí od nejstarších řádků."""
    head = f"Zadání #{job_id} · {what}"
    lines = []
    for ev in events:
        line = _status_line(ev.kind, ev.detail if isinstance(ev.detail, dict) else {})
        if line:
            lines.append(f"{ev.hm} {line}")
    tail = [worker_line] if worker_line else []
    text = "\n".join([head, *lines, *tail])
    while len(text) > STATUS_TEXT_LIMIT and lines:
        lines = lines[1:]
        text = "\n".join([head, "…", *lines, *tail])
    return text[:STATUS_TEXT_LIMIT]


def job_cancel_buttons(job_id: int) -> list[tuple[str, str]]:
    """Tlačítko stavové zprávy aktivního zadání (a odpovědi na zadání s autopilotem)."""
    return [("Zrušit", f"jobcancel:{job_id}")]


def job_cancelled_text(job_id: int) -> str:
    return f"Zadání #{job_id} zrušeno."


def job_not_running_text(job_id: int, status: str | None) -> str:
    """/zrus u zadání, které nejde zrušit; status None = zadání neexistuje."""
    if status is None:
        return f"Zadání #{job_id} neznám."
    return f"Zadání #{job_id} už neběží (stav {status})."
