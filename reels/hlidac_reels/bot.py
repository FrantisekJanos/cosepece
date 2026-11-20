"""Telegram bot pro schvalování Reels (jediný vlastník v soukromém chatu).

Zásady: obsluhuje se jen chat REELS_TELEGRAM_CHAT_ID (cokoli jiného se zahodí bez volání API),
nic se nezveřejní bez tlačítka Schválit, ke schválení čeká nejvýš jedna položka (kromě /dalsi),
offset Telegramu se ukládá po každé zpracované zprávě, takže restart žádné tlačítko nezopakuje.
Tokeny se nikam nevypisují.
"""
from __future__ import annotations

import html
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import psycopg

from . import db, publish, queue
from .enqueue import CAPTION_LIMIT
from .messages import (approval_buttons, preview_parts, preview_text, result_text,  # noqa: F401
                       retry_buttons, video_caption)
from .meta import Meta
from .queue import Reel
from .telegram import Telegram

log = logging.getLogger("hlidac_reels.bot")

ERROR_PAUSE = 10
DB_LOST_EXIT = 3
INTERRUPTED = "přerušeno před zveřejněním"
EDIT_HINT = "Uprav a pošli jako odpověď na tuto zprávu."
HELP = ("Příkazy: /fronta (počty podle stavů), /dalsi (poslat další video ke schválení).\n"
        "Popisek upravíš tlačítkem Upravit popisek nebo odpovědí na video či náhled.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _num(n: int) -> str:
    """Číslo s mezerou mezi tisíci (2 120)."""
    return f"{n:,}".replace(",", " ")


# --- bot --------------------------------------------------------------------

class Bot:
    def __init__(self, conn, tg: Telegram, meta: Meta, chat_id: int, media_dir: str, public_base: str,
                 secrets: tuple[str, ...] = ()):
        self.conn = conn
        self.tg = tg
        self.meta = meta
        self.chat_id = int(chat_id)
        self.media_dir = media_dir
        self.public_base = public_base
        self._secrets = tuple(s for s in (*secrets, getattr(tg, "_token", None)) if s)

    def _safe(self, exc: BaseException) -> str:
        """Popis výjimky pro log a zprávy bez tokenů."""
        text = f"{type(exc).__name__}: {exc}"
        for s in self._secrets:
            text = text.replace(s, "***")
        return text

    def _try(self, what: str, fn: Callable, *args, **kw):
        """Volání Telegramu, jehož selhání nesmí zastavit práci (oznámení, tlačítka); chybu jen zaloguje."""
        try:
            return fn(*args, **kw)
        except Exception as e:  # noqa: BLE001
            log.warning("%s selhalo: %s", what, self._safe(e))
            return None

    def _say(self, text: str, buttons=None, **kw):
        return self._try("sendMessage", self.tg.send_text, self.chat_id, text, buttons, **kw)

    # --- vstup -----------------------------------------------------------

    def handle(self, update: dict) -> None:
        cq = update.get("callback_query")
        if isinstance(cq, dict):
            msg = cq.get("message") or {}
            chat = (msg.get("chat") or {}).get("id", (cq.get("from") or {}).get("id"))
            if chat != self.chat_id:
                return
            self._on_callback(cq, msg.get("message_id"))
            return
        msg = update.get("message")
        if isinstance(msg, dict):
            if (msg.get("chat") or {}).get("id") != self.chat_id:
                return
            self._on_message(msg)

    def _on_callback(self, cq: dict, msg_id: int | None) -> None:
        action, _, raw_id = str(cq.get("data") or "").partition(":")
        cb_id = cq.get("id")
        try:
            reel_id = int(raw_id)
        except ValueError:
            self._try("answerCallbackQuery", self.tg.answer_callback, cb_id, "Neznámé tlačítko.")
            return
        handlers = {"ok": self._approve, "no": self._reject, "retry": self._retry, "edit": self._edit}
        handler = handlers.get(action)
        if handler is None:
            self._try("answerCallbackQuery", self.tg.answer_callback, cb_id, "Neznámé tlačítko.")
            return
        self._answered = False
        try:
            handler(reel_id, cb_id, msg_id)
        finally:
            # i při chybě (např. DB) se tlačítko nesmí dál točit
            if not self._answered:
                self._answer(cb_id)

    def _answer(self, cb_id, text: str = "") -> None:
        self._answered = True
        self._try("answerCallbackQuery", self.tg.answer_callback, cb_id, text)

    def _drop_buttons(self, msg_id: int | None) -> None:
        if msg_id:
            self._try("editMessageReplyMarkup", self.tg.edit_buttons, self.chat_id, msg_id, None)

    # --- tlačítka --------------------------------------------------------

    def _approve(self, reel_id: int, cb_id, msg_id) -> None:
        # jediné místo, odkud se položka dostane k publikaci: přechod jen z pending
        if not queue.transition(self.conn, reel_id, "pending", "approved", decided_at=_now()):
            self._answer(cb_id, "Položka už nečeká na schválení.")
            return
        self._answer(cb_id, "Schváleno")
        self._drop_buttons(msg_id)
        self._publish(reel_id, publish.run)

    def _retry(self, reel_id: int, cb_id, msg_id) -> None:
        reel = queue.get(self.conn, reel_id)
        if reel is None or reel.status not in ("failed", "unknown"):
            self._answer(cb_id, "Není co opakovat.")
            return
        self._answer(cb_id, "Zkouším znovu")
        self._drop_buttons(msg_id)
        self._publish(reel_id, publish.retry)

    def _publish(self, reel_id: int, fn: Callable[..., Reel]) -> None:
        self._say("Zveřejňuji…")
        reel = fn(self.conn, self.meta, reel_id, media_dir=self.media_dir, public_base=self.public_base)
        buttons = retry_buttons(reel_id) if reel.status in ("failed", "unknown") else None
        self._say(result_text(reel), buttons)
        self.send_next()

    def _reject(self, reel_id: int, cb_id, msg_id) -> None:
        if not queue.transition(self.conn, reel_id, "pending", "rejected", decided_at=_now()):
            self._answer(cb_id, "Položka už nečeká na schválení.")
            return
        self._answer(cb_id, "Zamítnuto")
        self._drop_buttons(msg_id)
        self._say(f"#{reel_id} zamítnuto.")
        self.send_next()

    def _edit(self, reel_id: int, cb_id, msg_id) -> None:
        reel = queue.get(self.conn, reel_id)
        if reel is None or reel.status != "pending":
            self._answer(cb_id, "Položka už nečeká na schválení.")
            return
        self._answer(cb_id)
        text = f"<pre>{html.escape(reel.caption, quote=False)}</pre>\n\n{EDIT_HINT}"
        new_id = self.tg.send_text(self.chat_id, text, parse_mode="HTML")
        queue.set_state(self.conn, f"edit_msg:{new_id}", str(reel_id))

    # --- zprávy ----------------------------------------------------------

    def _on_message(self, msg: dict) -> None:
        text = msg.get("text")
        reply = msg.get("reply_to_message")
        if isinstance(reply, dict) and reply.get("message_id"):
            self._on_reply(int(reply["message_id"]), text)
            return
        command = (text or "").strip().split("@", 1)[0].split(" ", 1)[0]
        if command == "/fronta":
            self._show_queue()
        elif command == "/dalsi":
            self.send_next(force=True)
        else:
            self._say(HELP)

    def _show_queue(self) -> None:
        """Počty podle stavů a ke každé neúspěšné položce znovu tlačítko Zkusit znovu."""
        counts = queue.counts(self.conn)
        lines = [f"{s}: {counts[s]}" for s in queue.STATUSES if counts.get(s)]
        self._say("Fronta:\n" + "\n".join(lines) if lines else "Fronta je prázdná.")
        # tlačítko z oznámení se mohlo ztratit (výpadek Telegramu), /fronta ho vrátí
        for reel in queue.by_status(self.conn, ("failed", "unknown", "approved")):
            error = (reel.error or "")[:300]
            text = f"{video_caption(reel)} · {reel.status}" + (f"\nChyba: {error}" if error else "")
            self._say(text, retry_buttons(reel.id))

    def _reel_for_reply(self, target: int) -> Reel | None:
        mapped = queue.get_state(self.conn, f"edit_msg:{target}")
        if mapped:
            return queue.get(self.conn, int(mapped))
        return queue.by_telegram_msg(self.conn, target)

    def _on_reply(self, target: int, text: str | None) -> None:
        reel = self._reel_for_reply(target)
        if reel is None:
            self._say(HELP)
            return
        if reel.status != "pending":
            self._say(f"#{reel.id} už nečeká na schválení, popisek se nezměnil.")
            return
        caption = (text or "").strip()
        if not caption:
            self._say("Pošli nový popisek jako text.")
            return
        if len(caption) > CAPTION_LIMIT:
            self._say(f"Popisek má {_num(len(caption))} znaků, limit je {_num(CAPTION_LIMIT)}. "
                      "Popisek se nezměnil.")
            return
        queue.set_fields(self.conn, reel.id, caption=caption)
        # starý náhled přijde o tlačítka, aby se neschválil podle starého textu
        self._drop_buttons(reel.telegram_msg_id)
        reel = queue.get(self.conn, reel.id)
        preview_id = self._send_preview(reel)
        queue.set_fields(self.conn, reel.id, telegram_msg_id=preview_id)

    # --- odeslání ke schválení -------------------------------------------

    def _send_preview(self, reel: Reel) -> int:
        """Pošle náhled popisku, tlačítka u poslední zprávy; vrací id zprávy s tlačítky."""
        parts = preview_parts(reel)
        msg_id = 0
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            msg_id = self.tg.send_text(self.chat_id, part, approval_buttons(reel.id) if last else None)
            if not last:
                # i na první část náhledu jde odpovědět novým popiskem
                queue.set_state(self.conn, f"edit_msg:{msg_id}", str(reel.id))
        return msg_id

    def send_next(self, force: bool = False) -> None:
        """Pošle další položku fronty ke schválení (když nic nečeká, nebo s force)."""
        if not force and queue.pending_count(self.conn) > 0:
            return
        reel = queue.next_queued(self.conn)
        if reel is None:
            if force:
                self._say("Fronta je prázdná.")
            return
        video_id = self.tg.send_video(self.chat_id, reel.video_path, video_caption(reel))
        preview_id = self._send_preview(reel)
        queue.transition(self.conn, reel.id, "queued", "pending", sent_at=_now(),
                         telegram_video_msg_id=video_id, telegram_msg_id=preview_id)

    # --- smyčka ----------------------------------------------------------

    def _clean_public(self) -> None:
        """Smaže veřejné kopie videí, které zůstaly po přerušeném běhu."""
        public = Path(self.media_dir) / "public"
        if not public.is_dir():
            return
        for f in public.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                    log.info("smazána veřejná kopie po přerušeném běhu: %s", f.name)
                except OSError as e:
                    log.warning("nelze smazat %s: %s", f.name, type(e).__name__)

    def start(self) -> None:
        db.migrate(self.conn)
        for reel_id in queue.recover_after_restart(self.conn):
            self._say(f"#{reel_id}: publikaci přerušil restart, výsledek je nejistý. "
                      "Zkusit znovu nejdřív dohledá, co už na sítích je.", retry_buttons(reel_id))
        # schválené, ale publikace nezačala (pád mezi schválením a publish.run): bez tlačítka by uvízly;
        # failed + Zkusit znovu je bezpečné, retry nejdřív dohledá, co už na sítích je
        for reel in queue.by_status(self.conn, ("approved",)):
            if queue.transition(self.conn, reel.id, "approved", "failed", error=INTERRUPTED):
                self._say(f"#{reel.id}: zveřejnění bylo přerušeno před začátkem publikace.",
                          retry_buttons(reel.id))
        self._clean_public()
        self.send_next()

    def poll_once(self) -> None:
        offset = int(queue.get_state(self.conn, "tg_offset") or 0)
        for update in self.tg.get_updates(offset):
            try:
                self.handle(update)
            except Exception as e:  # noqa: BLE001 – jedna rozbitá zpráva nesmí zastavit bota
                if self._db_lost(e):
                    raise
                log.error("zpracování zprávy %s selhalo: %s", update.get("update_id"), self._safe(e))
                self._rollback()
            # posunout i po chybě: restart nesmí znovu zpracovat tlačítko
            queue.set_state(self.conn, "tg_offset", str(int(update["update_id"]) + 1))

    def _db_lost(self, exc: BaseException) -> bool:
        return isinstance(exc, psycopg.OperationalError) or bool(getattr(self.conn, "closed", False))

    def _rollback(self) -> None:
        # nepovedené čtení nesmí nechat spojení v přerušené transakci
        try:
            self.conn.rollback()
        except Exception:  # noqa: BLE001
            pass

    def loop(self, sleep: Callable[[float], None] = time.sleep) -> None:
        """Hlavní smyčka. Ztráta spojení s DB ukončí proces kódem 3 a Docker ho spustí znovu."""
        started = False
        while True:
            try:
                if not started:
                    self.start()
                    started = True
                self.poll_once()
            except Exception as e:  # noqa: BLE001
                if self._db_lost(e):
                    log.error("spojení s databází ztraceno, končím: %s", self._safe(e))
                    raise SystemExit(DB_LOST_EXIT) from None
                log.error("chyba %s: %s", "smyčky" if started else "při startu", self._safe(e))
                self._rollback()
                sleep(ERROR_PAUSE)


# --- spuštění ---------------------------------------------------------------

REQUIRED = ("META_SYSTEM_TOKEN", "META_PAGE_ID", "META_IG_USER_ID", "REELS_TELEGRAM_TOKEN",
            "REELS_TELEGRAM_CHAT_ID", "REELS_PUBLIC_BASE")


def _fail(msg: str) -> None:
    print(f"reels-bot: {msg}", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    env = os.environ
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        _fail("Chybí proměnné prostředí: " + ", ".join(missing))
    try:
        chat_id = int(env["REELS_TELEGRAM_CHAT_ID"])
    except ValueError:
        _fail("REELS_TELEGRAM_CHAT_ID musí být číslo (id chatu v Telegramu).")
    logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx loguje na úrovni INFO celé URL, v nichž je token bota
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    meta_token, tg_token = env["META_SYSTEM_TOKEN"], env["REELS_TELEGRAM_TOKEN"]
    meta = Meta(meta_token, env["META_PAGE_ID"], env["META_IG_USER_ID"],
                version=env.get("META_GRAPH_VERSION") or "v23.0")
    bot = Bot(db.connect(), Telegram(tg_token), meta, chat_id,
              env.get("REELS_MEDIA_DIR") or "/media", env["REELS_PUBLIC_BASE"],
              secrets=(meta_token, tg_token))
    log.info("bot běží, chat %s", chat_id)
    bot.loop()
