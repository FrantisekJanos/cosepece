"""Telegram bot pro schvalování Reels (jediný vlastník v soukromém chatu).

Zásady: obsluhuje se jen chat REELS_TELEGRAM_CHAT_ID (cokoli jiného se zahodí bez volání API),
nic se nezveřejní bez tlačítka Schválit, ke schválení čeká nejvýš jedna jednotka (sada variant nebo
samostatné video, kromě /dalsi), offset Telegramu se ukládá po každé zpracované zprávě, takže restart žádné tlačítko nezopakuje.
Tokeny se nikam nevypisují.

Threads je volitelné: bez THREADS_USER_ID je threads=None a bot o Threads nepíše. Token Threads
drží reel_state (threads_token); při startu se naplní z THREADS_TOKEN, když tam ještě není,
a smyčka ho prodlužuje (nejvýš jeden pokus denně, když je starší než 7 dní).

Sady variant z generátoru (set_id): videa sady odejdou najednou, pod nimi jedna zpráva s tlačítky
Vybrat N a Hotovo, zbytek zamítnout (nevybrané položky sdílejí její telegram_msg_id). Vybrat N dá variantě
náhled se Schválit / Upravit / Zamítnout jako samostatnému videu (fáze 1) a zpráva sady přijde o její tlačítko;
ostatní varianty jdou vybrat dál. Hotovo (i dřívější noset) zamítne nevybrané varianty.
Hlídání dat (hlidani.py): každou hodinu výstraha při novém problému a zpráva „obnoveno“, v 8:00 Europe/Prague
denní souhrn, /stav na požádání.
Do Telegramu odejdou nejvýš 2 automatické sady denně (den podle Europe/Prague); sady ručních úloh (/video, /pn)
limit neomezují a nepočítají se do něj, /dalsi limit obchází. Čekání na limit bot ohlásí jednou za sadu. Smyčka
každou minutu převezme hotové sady z mezisložky (inbox) a každou hodinu hledá čerstvé nálezy (fresh).
"""
from __future__ import annotations

import dataclasses
import html
import json
import logging
import re
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import psycopg

from . import db, fresh, hlidani, inbox, jobs, publish, queue
from .enqueue import CAPTION_LIMIT
from .generator.formats import FORMATS
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
WATCH_INTERVAL = 3600  # s, jak často smyčka kontroluje čerstvost dat (hlidani.check)
SUMMARY_CHECK_INTERVAL = 300  # s, jak často smyčka zjišťuje, jestli už má odejít denní souhrn (8:00)
TELEGRAM_TEXT_LIMIT = 4000  # znaků zprávy (Telegram bere nejvýš 4096)
SETS_PER_DAY = 2  # automatických sad ke schválení za den (Europe/Prague); ruční sady a /dalsi limit obchází
LIMIT_NOTIFIED_KEY = "sets_limit_notified"  # reel_state: sada, u které už bot ohlásil čekání na limit
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
MAX_CT = 999_999  # nejvyšší číslo tisku, které /video přijme
VIDEO_USAGE = "Použití: /video <číslo tisku> [cesta|pn|zmena|rozprava]"
MAX_PN = 999_999  # nejvyšší číslo PN, které /pn přijme (větší by v PostgreSQL přeteklo integer)
PN_PER_VIDEO = 5  # nejvýš PN v jednom videu (/pn 1+2+…)
PN_ARGS = 10  # nejvýš samostatných zadání v jednom /pn
PN_LIST = 10  # nejvýš PN v seznamu /pn bez argumentu
PN_USAGE = ("Použití: /pn (PN za posledních 48 h), /pn <číslo PN> [další…] (každý PN vlastní video),\n"
            f"/pn <PN>+<PN> (jedno video z nejvýš {PN_PER_VIDEO} PN téhož tisku)")
WORKER_SEEN_KEY = "worker_seen_at"
HELP = ("Příkazy: /fronta (počty podle stavů a zadání), /dalsi (poslat další video ke schválení),\n"
        "/video <číslo tisku> [formát] (zadat video k tisku), /vyber (nechat vybrat tisk automaticky),\n"
        "/pn [číslo PN] (video k pozměňovacímu návrhu; bez čísla PN za posledních 48 h),\n"
        "/stav (čerstvost dat: synchronizace, stenoprotokoly, AI rozbor, PC s generátorem).\n"
        "Popisek upravíš tlačítkem Upravit popisek nebo odpovědí na video či náhled.\n"
        "Ze sady variant vyber jednu nebo víc tlačítky Vybrat, Hotovo zamítne zbylé.")
PICK_FIRST = "Nejdřív vyber variantu tlačítkem Vybrat, pak jde popisek upravit."
SET_GONE = "Sada už nečeká na výběr."
RETIRED = {"withdrawn": "#{} staženo: nález už není mezi hlavními",
           "expired": "#{} vypršelo (7 dní bez výběru)"}
RETIRED_REST = "#{} zbylé varianty vypršely"  # sada s výběrem: vyprší jen nevybrané varianty
LANDSCAPE_NOT_SENT = "Video na šířku ještě nebylo odesláno ke schválení."
LANDSCAPE_REJECTED = "Video na šířku Telegram odmítl. Vyměň ho přes reels-add-landscape, nebo položku zamítni."
LANDSCAPE_FAILED_KEY = "landscape_failed:{}"  # reel_state: sha256 šířky, kterou Telegram trvale odmítl


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


def _failed_mark(reel: Reel) -> str:
    """Hodnota landscape_failed:<id> pro aktuální šířku (bez otisku ""; zápis i porovnání stejně)."""
    return reel.video_landscape_sha256 or ""


def _num(n: int) -> str:
    """Číslo s mezerou mezi tisíci (2 120)."""
    return f"{n:,}".replace(",", " ")


def _pn_label(pns) -> str:
    return "PN " + "+".join(str(n) for n in pns)


def _job_what(ct: int | None, focus_pn) -> str:
    """Popis zadání do zpráv: „PN 2376+2377, tisk 300“, „tisk 300“, nebo „automatický výběr“."""
    if ct is None:
        return "automatický výběr"
    return f"{_pn_label(focus_pn)}, tisk {ct}" if focus_pn else f"tisk {ct}"


def _parse_pn_group(token: str) -> list[int] | None:
    """„2376“ nebo „2376+2377“ → seřazená čísla bez duplicit; None = neplatný zápis."""
    nums = []
    for part in token.split("+"):
        if not part.isdigit() or not 1 <= int(part) <= MAX_PN:
            return None
        nums.append(int(part))
    return sorted(set(nums))


def _pn_time(cas: str | None) -> str:
    """„2026-11-23 20:09:00“ → „23. 11. 20:09“ (pn.cas je text v pražském čase)."""
    try:
        t = datetime.strptime((cas or "")[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return cas or "?"
    return f"{t.day}. {t.month}. {t:%H:%M}"


def _ai_title(ai_json: str | None) -> str:
    try:
        ai = json.loads(ai_json) if ai_json else None
    except (ValueError, TypeError):
        return ""
    return str(ai.get("titulek") or "").strip() if isinstance(ai, dict) else ""


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
        self._next_notify = float("-inf")
        self._next_watch = float("-inf")
        self._next_summary = float("-inf")
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
        if action == "pn":  # tlačítka seznamu /pn: „pn:2376“ nebo „pn:2376+2377“
            self._answered = False
            try:
                self._pn_request(raw_id)
            finally:
                if not self._answered:
                    self._answer(cb_id)
            return
        try:
            reel_id = int(raw_id)
        except ValueError:
            self._try("answerCallbackQuery", self.tg.answer_callback, cb_id, "Neznámé tlačítko.")
            return
        handlers = {"ok": self._approve, "no": self._reject, "retry": self._retry, "edit": self._edit,
                    # noset: tlačítko zpráv sad odeslaných před výběrem více variant
                    "pick": self._pick, "doneset": self._reject_set, "noset": self._reject_set}
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
        if not queue.approve(self.conn, reel_id, _now()):
            reel = queue.get(self.conn, reel_id)
            if reel is not None and reel.status == "pending":
                if queue.get_state(self.conn, LANDSCAPE_FAILED_KEY.format(reel_id)) == _failed_mark(reel):
                    self._answer(cb_id, LANDSCAPE_REJECTED)
                    self._say(f"#{reel_id}: {LANDSCAPE_REJECTED}")
                    return
                # doplněné video na šířku ještě vlastník neviděl (nebo k němu nepřišel nový náhled)
                self._answer(cb_id, LANDSCAPE_NOT_SENT)
                self._say(f"#{reel_id}: {LANDSCAPE_NOT_SENT} Schválit půjde z nového náhledu.")
            else:
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
        """Čekající varianta sady, která sama ještě nebyla vybraná (jiné varianty sady vybrané být můžou)."""
        return (reel.set_id is not None and reel.status == "pending"
                and reel.id not in queue.picked(self.conn, reel.set_id))

    def _unpicked_items(self, set_id: int) -> list[Reel]:
        """Čekající nevybrané varianty sady (ty, které nabízí zpráva sady)."""
        picked = queue.picked(self.conn, set_id)
        return [r for r in queue.set_items(self.conn, set_id) if r.status == "pending" and r.id not in picked]

    def _pick(self, reel_id: int, cb_id, msg_id) -> None:
        reel = queue.get(self.conn, reel_id)
        # platí jen tlačítko aktuální zprávy sady (po /fronta má sada novou zprávu)
        if reel is None or not self._unpicked(reel) or msg_id != reel.telegram_msg_id:
            self._answer(cb_id, SET_GONE)
            return
        self._answer(cb_id, f"Vybrána varianta {reel.variant}")
        # nejdřív náhled; když se nepošle, nic se nemění a varianta dál čeká na výběr
        try:
            preview_id = self._send_preview(reel)
        except TelegramError as e:
            log.warning("náhled #%s se nepodařilo poslat: %s", reel.id, self._safe(e))
            self._say(f"Náhled varianty {reel.variant} se nepodařilo poslat, výběr neproběhl. Zkus to znovu.")
            return
        # ostatní varianty zůstávají pending a jdou vybrat dál; schválit jde jen vybraná
        queue.mark_picked(self.conn, reel.set_id, reel.id)
        queue.set_fields(self.conn, reel.id, telegram_msg_id=preview_id)
        # zpráva sady přijde o tlačítko vybrané varianty; po poslední i o Hotovo
        rest = [r for r in self._unpicked_items(reel.set_id) if r.telegram_msg_id == msg_id]
        if rest:
            self._try("editMessageReplyMarkup", self.tg.edit_buttons, self.chat_id, msg_id, set_buttons(rest))
        else:
            self._drop_buttons(msg_id)

    def _reject_set(self, set_id: int, cb_id, msg_id) -> None:
        """Hotovo (doneset, dřívější noset): zamítne nevybrané varianty; před výběrem celou sadu."""
        rest = self._unpicked_items(set_id)
        if not rest or msg_id != rest[0].telegram_msg_id:
            self._answer(cb_id, SET_GONE)
            return
        any_picked = bool(queue.picked(self.conn, set_id))
        for r in rest:
            queue.transition(self.conn, r.id, "pending", "rejected", decided_at=_now())
        self._answer(cb_id, "Zamítnuto")
        self._drop_buttons(msg_id)
        self._say(f"Sada #{set_id}: zbylé varianty zamítnuty." if any_picked else f"Sada #{set_id} zamítnuta.")
        self._safe_send_next()

    def _offer_set(self, set_id: int) -> bool:
        """Zpráva sady s tlačítky Vybrat pro nevybrané varianty; stará zpráva sady přijde o tlačítka.

        Když se zpráva nepošle, nic se nemění (položky zůstanou pending, /fronta to zkusí znovu).
        """
        pending = self._unpicked_items(set_id)
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
        elif command == "/video":
            self._cmd_video((text or "").split()[1:])
        elif command == "/vyber":
            self._add_job("auto_pick", None, None)
        elif command == "/pn":
            self._cmd_pn((text or "").strip().partition(" ")[2])
        elif command == "/stav":
            self._say(self._clip(hlidani.summary_text(self.conn, self.now())))
        else:
            self._say(HELP)

    def _cmd_video(self, args: list[str]) -> None:
        """/video <ct> [formát]: ruční zadání generátoru."""
        try:
            ct = int(args[0]) if args else 0
        except ValueError:
            ct = 0
        # horní mez: větší číslo by v PostgreSQL přeteklo integer a bot by neodpověděl
        if not 1 <= ct <= MAX_CT or len(args) > 2:
            self._say(VIDEO_USAGE)
            return
        hint = args[1].lower() if len(args) == 2 else None
        if hint is not None and hint not in FORMATS:
            self._say("Neznámý formát. Povolené: " + ", ".join(FORMATS) + ".")
            return
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM tisky WHERE ct = %s LIMIT 1", (ct,))
            known = cur.fetchone() is not None
        self.conn.commit()
        if not known:
            self._say(f"Tisk {ct} neznám.")
            return
        self._add_job("manual", ct, hint)

    def _add_job(self, source: str, ct: int | None, hint: str | None, focus_pn: list[int] | None = None) -> None:
        what = _job_what(ct, focus_pn)
        try:
            job_id = jobs.add(self.conn, source=source, ct=ct, format_hint=hint, focus_pn=focus_pn)
        except jobs.ActiveJobExists:
            self._say(f"{_pn_label(focus_pn)} (tisk {ct}) už ve frontě je." if focus_pn
                      else f"Tisk {ct} už ve frontě je.")
            return
        self._say(f"Zadání #{job_id} ({what}) je {jobs.position(self.conn, job_id)}. ve frontě.")

    def _cmd_pn(self, rest: str) -> None:
        """/pn: seznam PN za 48 h; /pn 2376 2377 = dvě úlohy; /pn 2376+2377 = jedno video z obou."""
        rest = re.sub(r"\s*\+\s*", "+", rest.strip())  # „2376 + 2377“ = „2376+2377“
        if not rest:
            self._pn_list()
            return
        tokens = rest.split()
        groups = [_parse_pn_group(t) for t in tokens]
        if len(tokens) > PN_ARGS or any(g is None for g in groups):
            self._say(PN_USAGE)
            return
        for token in tokens:
            self._pn_request(token)

    def _pn_request(self, token: str) -> None:
        """Jedno zadání z „2376“ nebo „2376+2377“ (příkaz i tlačítko): ověří PN a založí úlohu."""
        pns = _parse_pn_group(token)
        if pns is None:
            self._say(PN_USAGE)
            return
        if len(pns) > PN_PER_VIDEO:
            self._say(f"Do jednoho videa nejvýš {PN_PER_VIDEO} PN.")
            return
        with self.conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ON (cislo) cislo, ct FROM pn WHERE cislo = ANY(%s) ORDER BY cislo, ct",
                        (pns,))
            found = dict(cur.fetchall())
        self.conn.commit()
        unknown = [n for n in pns if n not in found]
        if unknown:
            self._say(f"PN {unknown[0]} zatím neznám (sync běží každou hodinu).")
            return
        tisky = sorted({found[n] for n in pns})
        if len(tisky) > 1:
            parts = [f"PN {n} (tisk {found[n]})" for n in pns]
            self._say(f"{', '.join(parts[:-1])} a {parts[-1]} patří k různým tiskům. "
                      "Jedno video jde jen z PN téhož tisku, zadej je zvlášť (mezerou místo +).")
            return
        self._add_job("manual", tisky[0], None, focus_pn=pns)

    def _pn_list(self) -> None:
        """PN podané za posledních 48 h (pn.cas je pražský čas), nejnovější první, s tlačítky pro zadání."""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT p.cislo, p.ct, p.osoba, p.cas, x.ai_json FROM pn p "
                "LEFT JOIN pn_text x ON x.id_dokument = p.id_dokument "
                "WHERE p.cas >= to_char((%s::timestamptz AT TIME ZONE 'Europe/Prague') - interval '48 hours', "
                "'YYYY-MM-DD HH24:MI:SS') ORDER BY p.cas DESC, p.cislo DESC LIMIT %s",
                (self.now(), PN_LIST))
            rows = cur.fetchall()
        self.conn.commit()
        if not rows:
            self._say("Za posledních 48 h žádný nový PN.")
            return
        lines, buttons = [], []
        by_ct: dict[int, list[int]] = {}
        for cislo, ct, osoba, cas, ai_json in rows:
            line = f"PN {cislo} · tisk {ct} · {osoba or 'autor neznámý'} · {_pn_time(cas)}"
            title = _ai_title(ai_json)
            lines.append(line + (f"\n{title} (shrnutí AI)" if title else ""))
            buttons.append((f"Video k PN {cislo}", f"pn:{cislo}"))
            by_ct.setdefault(ct, []).append(cislo)
        for ct, nums in by_ct.items():
            if len(nums) >= 2:
                # nejnovějších nejvýš PN_PER_VIDEO PN tisku (víc jedno video neunese)
                group = "+".join(str(n) for n in sorted(nums[:PN_PER_VIDEO]))
                buttons.append((f"Jedno video ze všech ({group})", f"pn:{group}"))
        self._say(self._clip("PN za posledních 48 h:\n\n" + "\n\n".join(lines)), buttons)

    def _worker_seen(self) -> str:
        seen = _parse_time(queue.get_state(self.conn, WORKER_SEEN_KEY))
        if seen is None:
            return "PC zatím nebylo vidět"
        # čas v Europe/Prague přes PostgreSQL (obraz nepotřebuje tzdata)
        with self.conn.cursor() as cur:
            cur.execute("SELECT to_char(%s::timestamptz AT TIME ZONE 'Europe/Prague', 'FMDD. FMMM. FMHH24:MI')",
                        (seen,))
            text = cur.fetchone()[0]
        self.conn.commit()
        return f"PC naposledy: {text}"

    def maybe_notify_failed(self) -> None:
        """Neúspěšná zadání generátoru se oznámí jednou, nejvýš za INGEST_INTERVAL s."""
        now = self.clock()
        if now < self._next_notify:
            return
        self._next_notify = now + INGEST_INTERVAL
        self._guard("hlášení neúspěšných zadání", self._notify_failed)

    def _notify_failed(self) -> None:
        for job in jobs.failed_unnotified(self.conn):
            error = self._scrub(job.error or "")[:300]
            what = _job_what(job.ct, job.focus_pn)
            if self._say(f"Zadání #{job.id} ({what}) se nepodařilo: {error}"):
                jobs.mark_notified(self.conn, job.id)

    def _show_queue(self) -> None:
        """Počty podle stavů; tlačítka, která se mohla ztratit (výpadek Telegramu, smazané zprávy), znovu.

        pending → nový náhled s tlačítky schválení; failed/unknown → Zkusit znovu; approved a dlouho
        běžící publishing jen textem (vyřeší je restart bota).
        """
        counts = queue.counts(self.conn)
        lines = [f"{s}: {counts[s]}" for s in queue.STATUSES if counts.get(s)]
        head = "Fronta:\n" + "\n".join(lines) if lines else "Fronta je prázdná."
        job_lines = [f"#{j.id} {j.source} {j.ct if j.ct is not None else '-'}"
                     f"{' ' + _pn_label(j.focus_pn) if j.focus_pn else ''} {j.status}"
                     for j in jobs.pending(self.conn)]
        if job_lines:
            head += "\n\nZadání:\n" + "\n".join(job_lines)
        self._say(head + "\n" + self._worker_seen())
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

        Pořadí zpráv: video 9:16, video na šířku (když ho položka má), náhled s tlačítky.
        Video, které Telegram odmítne (4xx) nebo jehož soubor nejde přečíst, se vyřadí (rejected,
        bez tlačítka, nikdy se z toho stavu nezveřejní) a zkusí se další, nejvýš SEND_ATTEMPTS za volání.
        Platí pro obě videa: jedno schválení publikuje obě. Výpadek sítě nebo 5xx/429 položku
        nevyřadí: výjimka jde dál, položka zůstane queued.
        """
        if not force and queue.pending_count(self.conn) > 0:
            return
        # denní limit platí jen pro automatické sady; samostatná videa (reels-enqueue) a sady ručních
        # úloh (/video) jdou dál
        sets_ok = force or queue.sets_sent_on(self.conn, queue.local_date(self.conn, self.now())) < SETS_PER_DAY
        for attempt in range(SEND_ATTEMPTS):
            reel = queue.next_queued(self.conn, sets=sets_ok)
            if reel is None:
                if force and attempt == 0:
                    self._say("Fronta je prázdná.")
                elif not sets_ok:
                    self._announce_limit()
                return
            if reel.set_id is not None:
                if self._send_set(reel.set_id):
                    return
                continue
            shape = "na výšku"
            try:
                video_id = self.tg.send_video(self.chat_id, reel.video_path, video_caption(reel))
                landscape_id = None
                if reel.video_landscape_path:
                    shape = "na šířku"
                    landscape_id = self.tg.send_video(self.chat_id, reel.video_landscape_path,
                                                      video_caption(reel, landscape=True))
            except TelegramError as e:
                if not e.rejected:
                    raise
                self._give_up(reel, e, shape)
                continue
            except OSError as e:
                self._give_up(reel, e, shape)
                continue
            preview_id = self._send_preview(reel)
            # otisk odeslané šířky: když ji reels-add-landscape mezitím vyměnil, schválení neprojde,
            # dokud _send_landscape_updates nepošle novou (queue.approve)
            queue.transition(self.conn, reel.id, "queued", "pending", sent_at=_now(),
                             telegram_video_msg_id=video_id, telegram_landscape_msg_id=landscape_id,
                             telegram_landscape_sha256=reel.video_landscape_sha256 if landscape_id else None,
                             telegram_msg_id=preview_id)
            return

    def _announce_limit(self) -> None:
        """Automatická sada čeká kvůli dennímu limitu: ohlásí se jednou za sadu (reel_state sets_limit_notified)."""
        waiting = queue.next_queued(self.conn)
        if waiting is None or waiting.set_id is None:
            return
        if queue.get_state(self.conn, LIMIT_NOTIFIED_KEY) == str(waiting.set_id):
            return
        if self._say(f"Sada #{waiting.set_id} čeká na zítřek: denní limit {SETS_PER_DAY} automatických sad "
                     "je vyčerpaný. Poslat hned: /dalsi"):
            queue.set_state(self.conn, LIMIT_NOTIFIED_KEY, str(waiting.set_id))

    def _send_landscape_updates(self) -> None:
        """Video na šířku doplněné k čekající položce: pošle ho a nový náhled s tlačítky.

        Id videa se uloží hned po odeslání, aby se video neposílalo v každé obrátce znovu. Náhled,
        který se nepošle, se dožene v další obrátce: Telegram čísluje zprávy v chatu vzestupně, takže
        náhled starší než video na šířku má menší message_id (telegram_msg_id < telegram_landscape_msg_id).
        Do té doby Schválit nepublikuje (queue.approve). Výpadek Telegramu (síť, 5xx, 429) se jen
        zaloguje a zkusí se znovu. Trvalou chybu (Telegram video odmítl, soubor nejde přečíst) bot
        ohlásí jednou a tutéž šířku (podle sha256) už nezkouší; nový soubor přes reels-add-landscape
        se zkusí znovu.
        """
        for reel in queue.by_status(self.conn, ("pending",)):
            if not reel.video_landscape_path:
                continue
            # varianta sady před výběrem: šířka (a náhled s tlačítky) až po Vybrat, jinak by nový
            # náhled přepsal telegram_msg_id zprávy sady
            if self._unpicked(reel):
                continue
            # odeslat, když šířka v Telegramu chybí, nebo tam je jiný soubor (výměna během send_next)
            if (reel.telegram_landscape_msg_id is None
                    or reel.telegram_landscape_sha256 != reel.video_landscape_sha256):
                failed_key = LANDSCAPE_FAILED_KEY.format(reel.id)
                if queue.get_state(self.conn, failed_key) == _failed_mark(reel):
                    continue
                try:
                    land_id = self.tg.send_video(self.chat_id, reel.video_landscape_path,
                                                 video_caption(reel, landscape=True))
                except (TelegramError, OSError) as e:
                    if isinstance(e, TelegramError) and not e.rejected:
                        log.warning("video na šířku #%s se nepodařilo poslat: %s", reel.id, self._safe(e))
                        continue
                    reason = self._safe(e)[:300]
                    log.warning("video na šířku #%s odmítnuto: %s", reel.id, reason)
                    queue.set_state(self.conn, failed_key, _failed_mark(reel))
                    what = "Telegram odmítl" if isinstance(e, TelegramError) else "nejde přečíst"
                    self._say(f"#{reel.id}: video na šířku {what} ({reason}). "
                              "Vyměň ho přes reels-add-landscape, nebo položku zamítni.")
                    continue
                if not land_id:
                    continue
                # zápis jen pro tentýž soubor; mezitím vyměněný (reels-add-landscape) se pošle v další obrátce
                if not queue.set_landscape_sent(self.conn, reel.id, land_id, reel.video_landscape_sha256):
                    continue
                queue.delete_state(self.conn, failed_key)
                reel = queue.get(self.conn, reel.id)
                if reel is None or reel.status != "pending":
                    continue
            if (reel.telegram_msg_id or 0) < reel.telegram_landscape_msg_id:
                self._resend_preview(reel)

    def _give_up(self, reel: Reel, exc: Exception, shape: str = "na výšku") -> None:
        """Vyřadí položku, jejíž video nejde poslat; shape říká, které (na výšku / na šířku)."""
        error = f"nelze poslat do Telegramu: {self._safe(exc)}"[:500]
        log.warning("#%s vyřazeno (video %s): %s", reel.id, shape, error)
        if queue.transition(self.conn, reel.id, "queued", "rejected", error=error):
            self._say(f"#{reel.id} vyřazeno, video {shape} {error}. Oprav video a zařaď ho znovu.")

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
            # u sady s výběrem vypršely jen nevybrané varianty, vybrané dál čekají na schválení
            text = RETIRED_REST if queue.picked(self.conn, set_id) else RETIRED.get(status, "#{} " + status)
            self._say(text.format(set_id))

    # --- hlídání čerstvosti dat ----------------------------------------------

    @staticmethod
    def _clip(text: str) -> str:
        return text if len(text) <= TELEGRAM_TEXT_LIMIT else text[:TELEGRAM_TEXT_LIMIT - 1] + "…"

    def maybe_watch(self) -> None:
        """Kontrola dat nejvýš jednou za WATCH_INTERVAL s (výstrahy a obnovení), denní souhrn po 8:00."""
        now = self.clock()
        if now >= self._next_watch:
            self._next_watch = now + WATCH_INTERVAL
            self._guard("hlídání dat", self._watch)
        if now >= self._next_summary:
            self._next_summary = now + SUMMARY_CHECK_INTERVAL
            self._guard("denní souhrn stavu dat", self._daily_summary)

    def _watch(self) -> None:
        new, recovered = hlidani.check(self.conn, self.now())
        text = hlidani.alert_text(new, recovered)
        if text:
            self._say(self._clip(text))

    def _daily_summary(self) -> None:
        day = hlidani.summary_due(self.conn, self.now())
        if day is None:
            return
        # souhrn se nezapíše jako odeslaný, když zpráva neodejde (další pokus za SUMMARY_CHECK_INTERVAL)
        if self._say(self._clip(hlidani.summary_text(self.conn, self.now()))):
            hlidani.mark_summary(self.conn, day)

    def maybe_send_next(self) -> None:
        """Nově zařazené video (i doplněné video na šířku) dorazí samo, nejvýš jednou za SEND_INTERVAL s."""
        now = self.clock()
        if now < self._next_send_check:
            return
        self._next_send_check = now + SEND_INTERVAL
        try:
            self._send_landscape_updates()
        except Exception as e:  # noqa: BLE001
            if self._db_lost(e):
                raise
            log.warning("odeslání videa na šířku selhalo: %s", self._safe(e))
            self._rollback()
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
                self.maybe_notify_failed()
                self.maybe_fresh()
                self.maybe_refresh_threads_token()
                self.maybe_watch()
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
