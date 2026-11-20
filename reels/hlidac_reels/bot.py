"""Telegram bot pro schvalování Reels (jediný vlastník v soukromém chatu).

Zásady: obsluhuje se jen chat REELS_TELEGRAM_CHAT_ID (cokoli jiného se zahodí bez volání API),
nic se nezveřejní bez tlačítka Schválit, ke schválení čeká nejvýš jedna jednotka (sada variant nebo
samostatné video, kromě /dalsi), offset Telegramu se ukládá po každé zpracované zprávě, takže restart žádné tlačítko nezopakuje.
Tokeny se nikam nevypisují.

Threads je volitelné: bez THREADS_USER_ID je threads=None a bot o Threads nepíše. Token Threads
drží reel_state (threads_token); při startu se naplní z THREADS_TOKEN, když tam ještě není,
a smyčka ho prodlužuje (nejvýš jeden pokus denně, když je starší než 7 dní).

Sady variant z generátoru (set_id): videa sady odejdou najednou, pod nimi jedna zpráva s tlačítky
Vybrat N a Zamítnout vše (všechny položky sdílejí její telegram_msg_id). Po výběru dostane vybraná
varianta náhled se Schválit / Upravit / Zamítnout jako samostatné video (fáze 1), ostatní se zamítnou.
Do Telegramu odejdou nejvýš 2 sady denně (den podle Europe/Prague), /dalsi limit obchází. Smyčka
každou minutu převezme hotové sady z mezisložky (inbox) a každou hodinu hledá čerstvé nálezy (fresh).
"""
from __future__ import annotations

import dataclasses
import html
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import psycopg

from . import db, fresh, inbox, publish, queue
from .enqueue import CAPTION_LIMIT
from .messages import (approval_buttons, preview_parts, preview_text, result_text,  # noqa: F401
                       retry_buttons, set_buttons, set_text, set_video_caption, video_caption)
from .meta import Meta
from .queue import Reel
from .telegram import Telegram, TelegramError
from .threads import Threads

log = logging.getLogger("hlidac_reels.bot")

ERROR_PAUSE = 10
SEND_ATTEMPTS = 3  # kolik neposlatelných videí se vyřadí v jednom send_next
SEND_INTERVAL = 50  # s, jak často smyčka sama zkusí poslat další položku
INGEST_INTERVAL = 60  # s, jak často smyčka převezme hotové sady z mezisložky
FRESH_INTERVAL = 3600  # s, jak často smyčka hledá čerstvé nálezy a stahuje staré sady
SETS_PER_DAY = 2  # sad odeslaných ke schválení za den (Europe/Prague), /dalsi limit obchází
STALE_PUBLISHING = timedelta(minutes=15)
DB_LOST_EXIT = 3
INTERRUPTED = "přerušeno před zveřejněním"
EDIT_HINT = "Uprav a pošli jako odpověď na tuto zprávu."
THREADS_TOKEN_KEY = "threads_token"
THREADS_REFRESHED_KEY = "threads_token_refreshed_at"
THREADS_TRIED_KEY = "threads_token_refresh_tried_at"
THREADS_REFRESH_AGE = timedelta(days=7)
THREADS_REFRESH_RETRY = timedelta(days=1)
PREVIEW_THREADS_KEY = "preview_has_threads"
HELP = ("Příkazy: /fronta (počty podle stavů), /dalsi (poslat další video ke schválení).\n"
        "Popisek upravíš tlačítkem Upravit popisek nebo odpovědí na video či náhled.\n"
        "Ze sady variant nejdřív vyber jednu tlačítkem Vybrat.")
PICK_FIRST = "Nejdřív vyber variantu tlačítkem Vybrat, pak jde popisek upravit."
SET_GONE = "Sada už nečeká na výběr."
RETIRED = {"withdrawn": "#{} staženo: nález už není mezi hlavními",
           "expired": "#{} vypršelo (7 dní bez výběru)"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str | None) -> datetime | None:
    """Čas z reel_state; neplatný = None, bez časové zóny = UTC (ručně zapsaná hodnota)."""
    try:
        t = datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
    return t.replace(tzinfo=timezone.utc) if t is not None and t.tzinfo is None else t


def threads_token(conn) -> str:
    """Aktuální token Threads z reel_state ("" = chybí); token_provider pro Threads."""
    return queue.get_state(conn, THREADS_TOKEN_KEY) or ""


def seed_threads_token(conn, env_token: str | None, now: datetime) -> bool:
    """Počáteční token z THREADS_TOKEN do reel_state, jen když tam žádný není (prodloužený se nepřepíše)."""
    if not env_token or threads_token(conn):
        return False
    queue.set_state(conn, THREADS_TOKEN_KEY, env_token)
    queue.set_state(conn, THREADS_REFRESHED_KEY, now.isoformat())
    return True


def _num(n: int) -> str:
    """Číslo s mezerou mezi tisíci (2 120)."""
    return f"{n:,}".replace(",", " ")


# --- bot --------------------------------------------------------------------

class Bot:
    def __init__(self, conn, tg: Telegram, meta: Meta, chat_id: int, media_dir: str, public_base: str,
                 secrets: tuple[str, ...] = (), threads: Threads | None = None,
                 threads_env_token: str | None = None, inbox_dir: str | None = None):
        self.conn = conn
        self.tg = tg
        self.meta = meta
        self.chat_id = int(chat_id)
        self.media_dir = media_dir
        self.public_base = public_base
        self._secrets = tuple(s for s in (*secrets, getattr(tg, "_token", None)) if s)
        self.clock: Callable[[], float] = time.monotonic
        self.now: Callable[[], datetime] = _now
        self._next_send_check = float("-inf")
        # None = převzetí sad z mezisložky vypnuté
        self.inbox_dir = inbox_dir
        self._next_ingest = float("-inf")
        self._next_fresh = float("-inf")
        # None = Threads není nastavené (bez THREADS_USER_ID)
        self.threads = threads
        self.threads_env_token = threads_env_token or None

    def _safe(self, exc: BaseException) -> str:
        """Popis výjimky pro log a zprávy bez tokenů."""
        return self._scrub(f"{type(exc).__name__}: {exc}")

    def _scrub(self, text: str, *extra: str) -> str:
        """Text bez známých tokenů (env, Telegram, Meta a tokeny, které viděl klient Threads)."""
        known = (*self._secrets, *extra, *getattr(self.threads, "_known", ()))
        for s in sorted({s for s in known if s}, key=len, reverse=True):
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
        handlers = {"ok": self._approve, "no": self._reject, "retry": self._retry, "edit": self._edit,
                    "pick": self._pick, "noset": self._reject_set}
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
        # varianta sady bez dokončeného výběru se schválit nesmí (i po pádu uprostřed výběru)
        reel = queue.get(self.conn, reel_id)
        if reel is not None and self._unpicked(reel):
            self._answer(cb_id, "Nejdřív vyber variantu.")
            return
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
        reel = fn(self.conn, self.meta, reel_id, media_dir=self.media_dir, public_base=self.public_base,
                  threads=self.threads)
        buttons = retry_buttons(reel_id) if reel.status in ("failed", "unknown") else None
        self._say(self._scrub(result_text(reel, threads=self.threads is not None)), buttons)
        self._safe_send_next()

    def _reject(self, reel_id: int, cb_id, msg_id) -> None:
        if not queue.transition(self.conn, reel_id, "pending", "rejected", decided_at=_now()):
            self._answer(cb_id, "Položka už nečeká na schválení.")
            return
        self._answer(cb_id, "Zamítnuto")
        self._drop_buttons(msg_id)
        self._say(f"#{reel_id} zamítnuto.")
        self._safe_send_next()

    def _edit(self, reel_id: int, cb_id, msg_id) -> None:
        reel = queue.get(self.conn, reel_id)
        if reel is None or reel.status != "pending":
            self._answer(cb_id, "Položka už nečeká na schválení.")
            return
        self._answer(cb_id)
        text = f"<pre>{html.escape(reel.caption, quote=False)}</pre>\n\n{EDIT_HINT}"
        new_id = self._say(text, parse_mode="HTML")
        if new_id:
            queue.set_state(self.conn, f"edit_msg:{new_id}", str(reel_id))

    # --- sady variant ----------------------------------------------------

    def _unpicked(self, reel: Reel) -> bool:
        """Čekající položka sady, z níž ještě nebyla vybraná varianta."""
        return (reel.set_id is not None and reel.status == "pending"
                and queue.picked(self.conn, reel.set_id) is None)

    def _pick(self, reel_id: int, cb_id, msg_id) -> None:
        reel = queue.get(self.conn, reel_id)
        # platí jen tlačítko aktuální zprávy sady (po /fronta má sada novou zprávu)
        if reel is None or not self._unpicked(reel) or msg_id != reel.telegram_msg_id:
            self._answer(cb_id, SET_GONE)
            return
        self._answer(cb_id, f"Vybrána varianta {reel.variant}")
        # nejdřív náhled; když se nepošle, nic se nemění a sada dál čeká na výběr
        try:
            preview_id = self._send_preview(reel)
        except TelegramError as e:
            log.warning("náhled #%s se nepodařilo poslat: %s", reel.id, self._safe(e))
            self._say(f"Náhled varianty {reel.variant} se nepodařilo poslat, výběr neproběhl. Zkus to znovu.")
            return
        # nejdřív zamítnout ostatní, výběr zapsat až potom: pád mezi kroky nenechá schválitelné jiné varianty
        for other in queue.set_items(self.conn, reel.set_id):
            if other.id != reel.id:
                queue.transition(self.conn, other.id, "pending", "rejected", decided_at=_now())
        queue.mark_picked(self.conn, reel.set_id, reel.id)
        queue.set_fields(self.conn, reel.id, telegram_msg_id=preview_id)
        self._drop_buttons(msg_id)

    def _reject_set(self, set_id: int, cb_id, msg_id) -> None:
        pending = [r for r in queue.set_items(self.conn, set_id) if r.status == "pending"]
        if not pending or not self._unpicked(pending[0]) or msg_id != pending[0].telegram_msg_id:
            self._answer(cb_id, SET_GONE)
            return
        for r in pending:
            queue.transition(self.conn, r.id, "pending", "rejected", decided_at=_now())
        self._answer(cb_id, "Zamítnuto")
        self._drop_buttons(msg_id)
        self._say(f"Sada #{set_id} zamítnuta.")
        self._safe_send_next()

    def _offer_set(self, set_id: int) -> bool:
        """Zpráva sady s tlačítky Vybrat pro čekající varianty; stará zpráva sady přijde o tlačítka.

        Když se zpráva nepošle, nic se nemění (položky zůstanou pending, /fronta to zkusí znovu).
        """
        pending = [r for r in queue.set_items(self.conn, set_id) if r.status == "pending"]
        if not pending:
            return False
        msg_id = self._try("zpráva sady", self.tg.send_text, self.chat_id, set_text(pending), set_buttons(pending))
        if not msg_id:
            return False
        for r in pending:
            queue.set_fields(self.conn, r.id, telegram_msg_id=msg_id)
        for old in {r.telegram_msg_id for r in pending if r.telegram_msg_id and r.telegram_msg_id != msg_id}:
            self._drop_buttons(old)
        return True

    def _send_set(self, set_id: int) -> bool:
        """Pošle videa sady a zprávu s tlačítky; False = žádné video sady nešlo poslat (sada vyřazena).

        Odmítnuté video (4xx, nečitelný soubor) se vyřadí a sada jde dál bez něj. Výpadek sítě nebo 5xx
        jde dál jako výjimka a celá sada zůstane queued (už poslaná videa se pak pošlou znovu).
        """
        sent: list[tuple[Reel, int]] = []
        for reel in queue.set_items(self.conn, set_id):
            if reel.status != "queued":
                continue
            try:
                video_id = self.tg.send_video(self.chat_id, reel.video_path, set_video_caption(reel))
            except TelegramError as e:
                if not e.rejected:
                    raise
                self._give_up(reel, e)
                continue
            except OSError as e:
                self._give_up(reel, e)
                continue
            sent.append((reel, video_id))
        if not sent:
            self._say(f"Sada #{set_id} vyřazena: žádné její video nejde poslat do Telegramu.")
            return False
        # pending hned po videích: když zpráva s tlačítky neodejde (výpadek, restart), /fronta ji pošle znovu
        at = self.now()
        for reel, video_id in sent:
            queue.transition(self.conn, reel.id, "queued", "pending", sent_at=at, telegram_video_msg_id=video_id)
        self._offer_set(set_id)
        return True

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
        """Počty podle stavů; tlačítka, která se mohla ztratit (výpadek Telegramu, smazané zprávy), znovu.

        pending → nový náhled s tlačítky schválení; failed/unknown → Zkusit znovu; approved a dlouho
        běžící publishing jen textem (vyřeší je restart bota).
        """
        counts = queue.counts(self.conn)
        lines = [f"{s}: {counts[s]}" for s in queue.STATUSES if counts.get(s)]
        self._say("Fronta:\n" + "\n".join(lines) if lines else "Fronta je prázdná.")
        stale_before = _now() - STALE_PUBLISHING
        offered: set[int] = set()
        for reel in queue.by_status(self.conn, ("pending", "failed", "unknown", "approved", "publishing")):
            head = f"{video_caption(reel)} · {reel.status}"
            if self._unpicked(reel):
                # sada bez výběru: znovu jedna zpráva s tlačítky Vybrat (i když se původní neposlala)
                if reel.set_id not in offered:
                    offered.add(reel.set_id)
                    self._offer_set(reel.set_id)
            elif reel.status == "pending":
                self._resend_preview(reel)
            elif reel.status == "approved":
                self._say(f"{head} · čeká na restart bota")
            elif reel.status == "publishing":
                started = reel.decided_at or reel.sent_at or reel.created_at
                if started < stale_before:
                    self._say(f"{head} · zveřejňování možná přerušeno — restart bota je převede na nejisté")
            else:
                error = (reel.error or "")[:300]
                self._say(head + (f"\nChyba: {error}" if error else ""), retry_buttons(reel.id))

    def _resend_preview(self, reel: Reel) -> bool:
        """Nový náhled čekající položky s tlačítky; starý náhled přijde o tlačítka (ukazoval jiný text).

        Když se nový náhled nepošle, nic se nemění a starý si tlačítka nechá.
        """
        preview_id = self._try("náhled", self._send_preview, reel)
        if not preview_id:
            return False
        queue.set_fields(self.conn, reel.id, telegram_msg_id=preview_id)
        if reel.telegram_msg_id and reel.telegram_msg_id != preview_id:
            self._drop_buttons(reel.telegram_msg_id)
        return True

    def _refresh_previews_for_threads(self) -> None:
        """Po zapnutí Threads: čekající položky dostanou nový náhled i s textem pro Threads.

        Starý náhled (bez Threads) přijde o tlačítka, aby Schválit nezveřejnil text, který vlastník
        neviděl. Proběhne jednou (reel_state preview_has_threads); když se některý náhled nepošle,
        zkusí se to při dalším startu znovu.
        """
        if self.threads is None or queue.get_state(self.conn, PREVIEW_THREADS_KEY) == "1":
            return
        ok = True
        for reel in queue.by_status(self.conn, ("pending",)):
            if self._unpicked(reel):
                continue  # sada bez výběru nemá náhled se Schválit
            ok = self._resend_preview(reel) and ok
        if ok:
            queue.set_state(self.conn, PREVIEW_THREADS_KEY, "1")

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
        if self._unpicked(reel):
            self._say(PICK_FIRST)
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
        # nejdřív nový náhled; když se nepošle, nic se nemění a starý náhled si nechá tlačítka
        try:
            preview_id = self._send_preview(dataclasses.replace(reel, caption=caption))
        except TelegramError as e:
            log.warning("náhled #%s se nepodařilo poslat: %s", reel.id, self._safe(e))
            self._say(f"Náhled #{reel.id} se nepodařilo poslat, popisek se nezměnil. Zkus to znovu.")
            return
        queue.set_fields(self.conn, reel.id, caption=caption, telegram_msg_id=preview_id)
        # starý náhled přijde o tlačítka až teď (ukazoval starý text)
        self._drop_buttons(reel.telegram_msg_id)

    # --- odeslání ke schválení -------------------------------------------

    def _send_preview(self, reel: Reel) -> int:
        """Pošle náhled popisku, tlačítka u poslední zprávy; vrací id zprávy s tlačítky."""
        parts = preview_parts(reel, threads=self.threads is not None)
        msg_id = 0
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            msg_id = self.tg.send_text(self.chat_id, part, approval_buttons(reel.id) if last else None)
            if not last:
                # i na první část náhledu jde odpovědět novým popiskem
                queue.set_state(self.conn, f"edit_msg:{msg_id}", str(reel.id))
        return msg_id

    def send_next(self, force: bool = False) -> None:
        """Pošle další položku fronty ke schválení (když nic nečeká, nebo s force).

        Video, které Telegram odmítne (4xx) nebo jehož soubor nejde přečíst, se vyřadí (rejected,
        bez tlačítka, nikdy se z toho stavu nezveřejní) a zkusí se další, nejvýš SEND_ATTEMPTS za volání.
        Výpadek sítě nebo 5xx/429 položku nevyřadí: výjimka jde dál, položka zůstane queued.
        """
        if not force and queue.pending_count(self.conn) > 0:
            return
        # denní limit platí jen pro sady; samostatná videa (reels-enqueue) jdou dál
        sets_ok = force or queue.sets_sent_on(self.conn, queue.local_date(self.conn, self.now())) < SETS_PER_DAY
        for attempt in range(SEND_ATTEMPTS):
            reel = queue.next_queued(self.conn, sets=sets_ok)
            if reel is None:
                if force and attempt == 0:
                    self._say("Fronta je prázdná.")
                return
            if reel.set_id is not None:
                if self._send_set(reel.set_id):
                    return
                continue
            try:
                video_id = self.tg.send_video(self.chat_id, reel.video_path, video_caption(reel))
            except TelegramError as e:
                if not e.rejected:
                    raise
                self._give_up(reel, e)
                continue
            except OSError as e:
                self._give_up(reel, e)
                continue
            preview_id = self._send_preview(reel)
            queue.transition(self.conn, reel.id, "queued", "pending", sent_at=_now(),
                             telegram_video_msg_id=video_id, telegram_msg_id=preview_id)
            return

    def _give_up(self, reel: Reel, exc: Exception) -> None:
        error = f"nelze poslat do Telegramu: {self._safe(exc)}"[:500]
        log.warning("#%s vyřazeno: %s", reel.id, error)
        if queue.transition(self.conn, reel.id, "queued", "rejected", error=error):
            self._say(f"#{reel.id} vyřazeno, video {error}. Oprav video a zařaď ho znovu.")

    def _safe_send_next(self) -> None:
        """send_next, jehož selhání (mimo ztrátu DB) se jen zaloguje; položka počká na další pokus."""
        try:
            self.send_next()
        except Exception as e:  # noqa: BLE001
            if self._db_lost(e):
                raise
            log.warning("odeslání další položky selhalo: %s", self._safe(e))
            self._rollback()

    def _guard(self, what: str, fn: Callable[[], object]) -> None:
        """Krok smyčky, jehož selhání (mimo ztrátu DB) se jen zaloguje."""
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            if self._db_lost(e):
                raise
            log.warning("%s selhalo: %s", what, self._safe(e))
            self._rollback()

    def maybe_ingest(self) -> None:
        """Hotové sady z mezisložky do fronty, nejvýš jednou za INGEST_INTERVAL s."""
        if self.inbox_dir is None:
            return
        now = self.clock()
        if now < self._next_ingest:
            return
        self._next_ingest = now + INGEST_INTERVAL
        self._guard("převzetí sad z mezisložky", self._ingest)

    def _ingest(self) -> None:
        results = inbox.ingest_all(self.conn, Path(self.inbox_dir), Path(self.media_dir))
        for r in results:
            if not r.ok:
                # úloha je už failed; zprávu o ní pošle oznámení neúspěšných úloh
                log.warning("sada úlohy %s nepřevzata", r.job_id)
        if any(r.ok for r in results):
            self._safe_send_next()

    def maybe_fresh(self) -> None:
        """Čerstvé nálezy a vypršení či stažení sad, nejvýš jednou za FRESH_INTERVAL s."""
        now = self.clock()
        if now < self._next_fresh:
            return
        self._next_fresh = now + FRESH_INTERVAL
        self._guard("hledání čerstvých nálezů", lambda: fresh.detect(self.conn, self.now()))
        self._guard("stažení starých sad", self._retire)

    def _retire(self) -> None:
        for set_id, status in fresh.retire(self.conn, self.now()):
            items = [r for r in queue.set_items(self.conn, set_id) if r.status == status]
            if not any(r.sent_at for r in items):
                # sada ještě nebyla v Telegramu: změna stavu bez zprávy
                log.info("sada %s ještě neodeslaná, nyní %s", set_id, status)
                continue
            for msg_id in {r.telegram_msg_id for r in items if r.telegram_msg_id}:
                self._drop_buttons(msg_id)
            self._say(RETIRED.get(status, "#{} " + status).format(set_id))

    def maybe_send_next(self) -> None:
        """Nově zařazené video dorazí samo: send_next nejvýš jednou za SEND_INTERVAL s."""
        now = self.clock()
        if now < self._next_send_check:
            return
        self._next_send_check = now + SEND_INTERVAL
        self._safe_send_next()

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

    # --- token Threads ---------------------------------------------------

    def maybe_refresh_threads_token(self) -> None:
        """Prodloužení tokenu Threads: když je starší než 7 dní, nejvýš jeden pokus za den.

        Čas pokusu se uloží před voláním API, takže selhání (i opakované restarty) znamená nejvýš
        jeden pokus a jednu zprávu v Telegramu denně. Při chybě zůstává dosavadní token.
        """
        if self.threads is None:
            return
        token = threads_token(self.conn)
        if not token:
            return
        now = self.now()
        refreshed = _parse_time(queue.get_state(self.conn, THREADS_REFRESHED_KEY))
        if refreshed is not None and now - refreshed < THREADS_REFRESH_AGE:
            return
        tried = _parse_time(queue.get_state(self.conn, THREADS_TRIED_KEY))
        if tried is not None and now - tried < THREADS_REFRESH_RETRY:
            return
        queue.set_state(self.conn, THREADS_TRIED_KEY, now.isoformat())
        try:
            new_token, expires_in = self.threads.refresh_token(token)
        except Exception as e:  # noqa: BLE001 – chyba prodloužení nesmí zastavit bota
            reason = self._scrub(getattr(e, "message", None) or type(e).__name__, token)[:300]
            log.warning("prodloužení tokenu Threads selhalo: %s", reason)
            self._say(f"Prodloužení tokenu Threads selhalo: {reason}\n"
                      "Platí dosavadní token, další pokus zítra. Když token vyprší, na Threads se přestane "
                      "zveřejňovat (FB a IG poběží dál).")
            return
        queue.set_state(self.conn, THREADS_TOKEN_KEY, new_token)
        queue.set_state(self.conn, THREADS_REFRESHED_KEY, now.isoformat())
        log.info("token Threads prodloužen, platnost %d dní", expires_in // 86400)

    def start(self) -> None:
        db.migrate(self.conn)
        if self.threads is not None and seed_threads_token(self.conn, self.threads_env_token, self.now()):
            log.info("token Threads převzat z THREADS_TOKEN")
        for reel_id in queue.recover_after_restart(self.conn):
            self._say(f"#{reel_id}: publikaci přerušil restart, výsledek je nejistý. "
                      "Zkusit znovu nejdřív dohledá, co už na sítích je.", retry_buttons(reel_id))
        # schválené, ale publikace nezačala (pád mezi schválením a publish.run): bez tlačítka by uvízly;
        # failed + Zkusit znovu je bezpečné, retry nejdřív dohledá, co už na sítích je
        for reel in queue.by_status(self.conn, ("approved",)):
            if queue.transition(self.conn, reel.id, "approved", "failed", error=INTERRUPTED):
                self._say(f"#{reel.id}: zveřejnění bylo přerušeno před začátkem publikace.",
                          retry_buttons(reel.id))
        self._refresh_previews_for_threads()
        self._clean_public()
        self._safe_send_next()

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
                self.maybe_send_next()
                self.maybe_ingest()
                self.maybe_fresh()
                self.maybe_refresh_threads_token()
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
    threads_env_token = env.get("THREADS_TOKEN") or ""
    meta = Meta(meta_token, env["META_PAGE_ID"], env["META_IG_USER_ID"],
                version=env.get("META_GRAPH_VERSION") or "v23.0")
    conn = db.connect()
    threads = None
    if env.get("THREADS_USER_ID"):
        # i bez platného tokenu: Threads pak viditelně selže (failed + Zkusit znovu), FB a IG vyjdou
        threads = Threads(env["THREADS_USER_ID"], lambda: threads_token(conn),
                          version=env.get("THREADS_GRAPH_VERSION") or "v1.0")
    bot = Bot(conn, Telegram(tg_token), meta, chat_id,
              env.get("REELS_MEDIA_DIR") or "/media", env["REELS_PUBLIC_BASE"],
              secrets=(meta_token, tg_token, threads_env_token), threads=threads,
              threads_env_token=threads_env_token, inbox_dir=env.get("REELS_INBOX_DIR") or "/inbox")
    log.info("bot běží, chat %s, Threads %s", chat_id, "zapnuté" if threads else "vypnuté")
    bot.loop()
