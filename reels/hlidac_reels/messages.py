"""Texty zpráv Telegram bota: popisek videa, náhled popisku, výsledek publikace, tlačítka.

threads=True znamená, že je Threads nastavené (THREADS_USER_ID): náhled ukáže i text pro Threads
a výsledek řádek Threads. Bez něj se o Threads nepíše nic.
"""
from __future__ import annotations

from .caption import for_network
from .queue import AUTO_HOLD, Reel

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


def set_text(items: list[Reel]) -> str:
    """Zpráva sady nad tlačítky Vybrat: tisk a z každé nevybrané varianty první 2 řádky popisku."""
    first = items[0]
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
