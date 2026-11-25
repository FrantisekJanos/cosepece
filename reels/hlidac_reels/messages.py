"""Texty zpráv Telegram bota: popisek videa, náhled popisku, výsledek publikace, tlačítka.

threads=True znamená, že je Threads nastavené (THREADS_USER_ID): náhled ukáže i text pro Threads
a výsledek řádek Threads. Bez něj se o Threads nepíše nic.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .caption import for_network
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


def set_text(items: list[Reel], remaining: bool = False) -> str:
    """Zpráva sady nad tlačítky Vybrat: tisk a z každé nevybrané varianty první 2 řádky popisku.

    remaining=True: úvod „zbývá varianta N“ (po rozhodnutí o vybraných variantách).
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
    # dlouhé řádky se zkrátí rovnoměrně, aby se do zprávy vešly všechny varianty
    room = (TEXT_LIMIT - len(head) - 2 * len(blocks)) // max(len(blocks), 1)
    blocks = [b if len(b) <= room else b[:max(room - 1, 0)] + "…" for b in blocks]
    return "\n\n".join([head, *blocks])[:TEXT_LIMIT]


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


def queue_overview(units: list[Unit], jobs: list, worker_line: str, now: datetime | None = None) -> str:
    """Přehled /fronta (spec 4.2): sekce v pevném pořadí, prázdné se vynechají, poslední řádek stav PC.

    units z queue.open_units, jobs = aktivní zadání generátoru (jobs.pending, v pořadí výdeje).
    """
    now = now or datetime.now(timezone.utc)
    mine = [u for u in units if u.state in _MINE]
    ready = [u for u in units if u.state in _READY]
    problems = [u for u in units if u.state in _PROBLEMS]
    generating = [j for j in jobs if j.status == "claimed"]
    waiting = [j for j in jobs if j.status == "queued"]
    blocks = []
    if mine:
        blocks.append([f"Čeká na tebe ({len(mine)})", *(_unit_line(u, now) for u in mine)])
    if ready:
        blocks.append([f"Hotové, čekají na odeslání ({len(ready)})", *(_unit_line(u, now) for u in ready)])
    if generating:
        blocks.append(["Generuje se", *(f"#{j.id} {_what(j.ct, j.focus_pn)} · generuje se · "
                                        f"{_age(j.claimed_at or j.created_at, now)}" for j in generating)])
    if waiting:
        blocks.append([f"Ve frontě zadání ({len(waiting)})",
                       *(f"#{j.id} {_what(j.ct, j.focus_pn)} · {n}. v pořadí" for n, j in enumerate(waiting, 1))])
    if problems:
        blocks.append([f"Problémy ({len(problems)})", *(_unit_line(u, now) for u in problems)])
    if not blocks:
        blocks.append([QUEUE_EMPTY])
    blocks.append([worker_line])
    text = "\n\n".join("\n".join(b) for b in blocks)
    if len(text) > TEXT_LIMIT:
        # dlouhý přehled: zkrátí se, stav PC zůstane na konci
        tail = "\n…\n\n" + worker_line
        text = text[:TEXT_LIMIT - len(tail)].rsplit("\n", 1)[0] + tail
    return text


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


AUTOPILOT_QUEUED = " Autopilot: po vygenerování se rovnou zveřejní."
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
    """Důvod pozastavení workeru česky; neznámý důvod = ""."""
    detail = (detail or "").strip()
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
