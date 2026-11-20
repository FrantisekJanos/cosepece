"""Texty zpráv Telegram bota: popisek videa, náhled popisku, výsledek publikace, tlačítka."""
from __future__ import annotations

from .caption import for_network
from .queue import Reel

TEXT_LIMIT = 4096
VIDEO_CAPTION_LIMIT = 1024
NO_LINK = "odkaz se nepodařilo načíst"


def video_caption(reel: Reel) -> str:
    parts = [f"#{reel.id}"]
    if reel.ct is not None:
        parts.append(f"tisk {reel.ct}")
    if reel.format:
        parts.append(reel.format)
    return " · ".join(parts)[:VIDEO_CAPTION_LIMIT]


def _fb_block(reel: Reel) -> str:
    return f"Náhled #{reel.id}\n\nFacebook:\n{for_network(reel.caption, reel.ct, 'fb')}"


def _ig_block(reel: Reel) -> str:
    ig = for_network(reel.caption, reel.ct, "ig")
    if ig.startswith(reel.caption):
        ending = ig[len(reel.caption):].strip()
        return f"Instagram: stejný text, jen místo odkazu končí:\n{ending}"
    return f"Instagram:\n{ig}"


def preview_text(reel: Reel) -> str:
    """Přesně to, co půjde ven: celý text pro FB a pod ním konec, kterým se liší IG."""
    return f"{_fb_block(reel)}\n\n{_ig_block(reel)}"


def preview_parts(reel: Reel) -> list[str]:
    """Náhled rozdělený na zprávy do 4 096 znaků (obvykle jedna, u dlouhého popisku FB a IG zvlášť)."""
    text = preview_text(reel)
    if len(text) <= TEXT_LIMIT:
        return [text]
    parts = []
    for block in (_fb_block(reel), _ig_block(reel)):
        # pojistka pro popisek delší než limit zprávy (enqueue ho nepustí, ale DB ano)
        parts.extend(block[i:i + TEXT_LIMIT] for i in range(0, len(block), TEXT_LIMIT))
    return parts


def approval_buttons(reel_id: int) -> list[tuple[str, str]]:
    return [("Schválit", f"ok:{reel_id}"), ("Upravit popisek", f"edit:{reel_id}"),
            ("Zamítnout", f"no:{reel_id}")]


def retry_buttons(reel_id: int) -> list[tuple[str, str]]:
    return [("Zkusit znovu", f"retry:{reel_id}")]


def result_text(reel: Reel) -> str:
    if reel.status == "published":
        return (f"#{reel.id} zveřejněno.\n"
                f"Facebook: {reel.fb_permalink or NO_LINK}\n"
                f"Instagram: {reel.ig_permalink or NO_LINK}")
    if reel.status == "unknown":
        head = f"#{reel.id}: výsledek zveřejnění je nejistý. Zkusit znovu nejdřív dohledá, co už na sítích je."
    elif reel.status == "failed":
        head = f"#{reel.id}: zveřejnění selhalo."
    else:
        head = f"#{reel.id}: stav {reel.status}."
    return f"{head}\nChyba: {reel.error}" if reel.error else head
