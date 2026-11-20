"""Texty zpráv Telegram bota: popisek videa, náhled popisku, výsledek publikace, tlačítka.

threads=True znamená, že je Threads nastavené (THREADS_USER_ID): náhled ukáže i text pro Threads
a výsledek řádek Threads. Bez něj se o Threads nepíše nic.
"""
from __future__ import annotations

from .caption import for_network
from .queue import Reel

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
