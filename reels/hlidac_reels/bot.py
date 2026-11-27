"""Telegram bot pro schvalování Reels (jediný vlastník v soukromém chatu).

Zásady: obsluhuje se jen chat REELS_TELEGRAM_CHAT_ID (cokoli jiného se zahodí bez volání API),
nic se nezveřejní bez tlačítka Schválit (kromě položek autopilota: maybe_autopublish je zveřejní
po jedné za SEND_INTERVAL a pošle odkazy), offset Telegramu se ukládá po každé zpracované zprávě, takže restart
žádné tlačítko nezopakuje. Doručení ke schválení řídí pravidla R1–R4 (send_next): vyžádaná jednotka (manual,
auto_pick, ukazka) odejde bez ohledu na nerozhodnuté položky jiných jednotek, automatická (fresh) podléhá limitu a
brzdě (viz níže); za cyklus nejvýš jedna jednotka. Co nejde poslat kvůli přechodné chybě, se odloží (1/5/15/60 min)
a po 5 pokusech čeká ve stavu held na Zkusit znovu / Zahodit. Videa a zpráva sady se ukládají hned po odeslání
(žádné duplicity), ztracené zprávy sady a náhledy doplní opravná smyčka. Druhá instance bota skončí (advisory lock).
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
Do Telegramu odejdou nejvýš 2 automatické sady denně (den podle Europe/Prague) a další automatická až po výběru
z předchozí (nejdéle AUTO_HOLD h); vyžádané sady (/video, /pn, /vyber) limit neomezují a nepočítají se do něj,
/dalsi limit obchází. Čekání automatické sady bot ohlásí jednou za sadu. Smyčka
každou minutu převezme hotové sady z mezisložky (inbox) a každou hodinu hledá čerstvé nálezy (fresh)
a uklízí stav (uklid: klíče reel_state 30 dní po konečném stavu, události po 90 dnech, složky zrušených úloh).
Selhání úloh a stav PC s generátorem hlásí bot z událostí (reel_events, maybe_events, každou minutu): převezme
selhání bez události (events.adopt_failed, např. z inboxu), u vydání bez známky života zapíše „PC neodpovídá“
a naléhavé události (urgency 1: selhání vyžádané úlohy s tlačítky Zkusit znovu / Zahodit, pozastavení a obnovení
workeru, PC neodpovídá) pošle po jedné v pořadí id. notified_at se zapíše až po odeslání, takže výpadek Telegramu
zprávu neztratí a nic neodejde dvakrát; po výpadku delším než events.OUTAGE odejde jedna souhrnná zpráva.
Selhání a vypršení automatických úloh (urgency 2) jdou jen do denního souhrnu.
Vyžádané zadání (/video, /vyber, /pn, Zkusit znovu) dostane jednu stavovou zprávu: pořadí, zadání před ním, stav PC
a tlačítko Zrušit. U zadání bez autopilota ji maybe_events podle událostí úlohy přepisuje (editMessageText, žádné nové
zprávy; job_status:<id> = poslední vykreslená událost), konečný stav bez tlačítek. Zrušit a /zrus <číslo> zruší
aktivní zadání (jediný zápis stavu úlohy z bota, jobs.cancel); Zahodit u zprávy o selhání jen odebere tlačítka.
Zkusit znovu založí nové zadání (nové id, vždy bez autopilota) a pošle mu novou stavovou zprávu; každé zadání
jde opakovat jen jednou (reel_state job_retried:<id>).
"""
from __future__ import annotations

import dataclasses
import hashlib
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

from . import db, events, fresh, hlidani, inbox, jobs, messages, publish, queue, uklid, xpost
from .enqueue import CAPTION_LIMIT
from .generator.formats import FORMATS
from .messages import (AUTOPILOT_PN_LIST_HEADER, AUTOPILOT_QUEUED, approval_buttons, autopilot_done,
                       autopilot_skipped, autopilot_text, preview_parts, preview_text, result_text,  # noqa: F401
                       interrupted_text, retry_buttons, set_buttons, set_text, set_video_caption,
                       stuck_publishing_text, video_caption)
from .meta import Meta
from .queue import Reel
from .telegram import Telegram, TelegramError
from .threads import Threads

log = logging.getLogger("hlidac_reels.bot")

ERROR_PAUSE = 10
SEND_INTERVAL = 50  # s, jak často smyčka sama zkusí poslat další položku
INGEST_INTERVAL = 60  # s, jak často smyčka převezme hotové sady z mezisložky
EVENTS_INTERVAL = 60  # s, jak často smyčka pošle události (selhání, stav PC) a hledá vydání bez známky života
FRESH_INTERVAL = 3600  # s, jak často smyčka hledá čerstvé nálezy a stahuje staré sady
WATCH_INTERVAL = 3600  # s, jak často smyčka kontroluje čerstvost dat (hlidani.check)
SUMMARY_CHECK_INTERVAL = 300  # s, jak často smyčka zjišťuje, jestli už má odejít denní souhrn (8:00)
ALIVE_INTERVAL = 60  # s, jak často smyčka zapíše známku života (reel_state.bot_seen_at)
# reel_state: čas posledního průchodu smyčkou bez chyby; PHP (Sync) hlásí stáří > 15 min do Sentry
BOT_SEEN_KEY = "bot_seen_at"
TELEGRAM_TEXT_LIMIT = 4000  # znaků zprávy (Telegram bere nejvýš 4096)
SETS_PER_DAY = 2  # automatických sad ke schválení za den (Europe/Prague); ruční sady a /dalsi limit obchází
LIMIT_NOTIFIED_KEY = "sets_limit_notified"  # reel_state: sada, u které už bot ohlásil čekání na limit
STALE_PUBLISHING = timedelta(minutes=15)
DB_LOST_EXIT = 3
INTERRUPTED = "přerušeno před zveřejněním"
# pg_try_advisory_lock: druhá instance bota by publikovala a odpovídala na tlačítka podruhé ("reel")
REELS_BOT_LOCK = 0x7265656C
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
PN_SCAN = 300  # /pnnew: kolik PN za 48 h projít, než se vyberou ty se shrnutím AI
PN_USAGE = ("Použití: /pn (PN za posledních 48 h), /pn <číslo PN> [další…] (každý PN vlastní video),\n"
            f"/pn <PN>+<PN> (jedno video z nejvýš {PN_PER_VIDEO} PN téhož tisku)")
WORKER_SEEN_KEY = "worker_seen_at"
WORKER_STATE_KEY = "worker_state"  # reel_state: JSON {state, reason, detail, until, at} z heartbeat workeru
# reel_state job_status:<id> = id poslední události vykreslené ve stavové zprávě zadání; bez klíče se zpráva
# nepřepisuje (smaže se po vykreslení konečného stavu)
JOB_STATUS_KEY = "job_status:"
FINAL_EVENTS = ("done", "failed", "cancelled", "expired")  # události konečného stavu úlohy
# konečný stav bez konečné události (server zapisuje stav a událost dvěma příkazy) se sleduje nejvýš takhle dlouho
STATUS_FINAL_WAIT = timedelta(minutes=10)
MAX_JOB_ID = 999_999_999  # nejvyšší číslo zadání, které /zrus přijme
# limit Meta: nejvýš 30 Reels na FB za klouzavých 24 h; autopilot drží rezervu a rozestup (všechny sítě
# jdou spolu, takže se tím řídí i IG a Threads). Po chybě limitu stojí autopilot FB_LIMIT_PAUSE.
FB_DAILY_CAP = 25
FB_MIN_GAP = timedelta(minutes=5)
FB_LIMIT_PAUSE = timedelta(hours=1)
FB_LIMIT_KEY = "fb_limit_until"  # reel_state: do kdy autopilot nezveřejňuje (ISO čas, UTC) po chybě limitu Meta
AUTOPILOT_KEY = "autopilot"  # reel_state: "1" = PN z /pn se zveřejní bez schválení
# reel_state autopilot_skipped:<set_id> = JSON nevzniklých variant sady autopilota; ohlásí se po její poslední položce
AUTOPILOT_SKIPPED_KEY = "autopilot_skipped:"
HELP = ("Příkazy: /fronta (přehled: na co která položka čeká), /dalsi (poslat další video ke schválení),\n"
        "/video <číslo tisku> [formát] (zadat video k tisku), /vyber (nechat vybrat tisk automaticky),\n"
        "/pn [číslo PN] (video k pozměňovacímu návrhu; bez čísla PN za posledních 48 h),\n"
        "/pnnew (PN za posledních 48 h, ke kterým video ještě není ani se nedělá),\n"
        "/zrus <číslo zadání> (zrušit zadání ve frontě nebo rozpracované),\n"
        "/stav (čerstvost dat a Reels: synchronizace, stenoprotokoly, AI rozbor, PC s generátorem,\n"
        "videa ke schválení, zprávy, které neodešly),\n"
        "/autopilot on|off (PN z /pn zveřejnit bez schválení; bez argumentu stav).\n"
        "Popisek upravíš tlačítkem Upravit popisek nebo odpovědí na video či náhled.\n"
        "Ze sady variant vyber jednu nebo víc tlačítky Vybrat, Hotovo zamítne zbylé.")
PICK_FIRST = "Nejdřív vyber variantu tlačítkem Vybrat, pak jde popisek upravit."
SET_GONE = "Sada už nečeká na výběr."
HELD_GONE = "Položka už nečeká na odeslání."
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


PN_EXCERPT = 200  # nejvýš znaků úryvku textu PN v seznamu /pn
PN_HEAD = 1500  # hlavička PN (předkladatelé, název tisku) se hledá jen na začátku textu
_PN_BODY = re.compile(r"(?:takto|tyto úpravy|následovně)\s*:\s*", re.IGNORECASE)
# odkaz na tisk na konci hlavičky: „(Sněmovní tisk č. 324/0).“, „(ST 324)“, „sněmovní tisk 324“
_PN_TISK = re.compile(r"\(?\b(?:sněmovní\w*\s+tisk\w*|ST)\s*(?:č\.\s*)?\d+(?:/\d+)?\)?\.?\s*", re.IGNORECASE)


def _videa(n: int) -> str:
    """Tvar slova video za číslovkou: 2–4 videa, jinak videí."""
    return "videa" if 2 <= n <= 4 else "videí"


def _pn_excerpt(text: str | None) -> str:
    """Úryvek textu PN bez hlavičky s předkladateli, nejvýš PN_EXCERPT znaků. Pro seznam /pn u PN, které ještě
    nemají rozbor AI. Začátek: za „… se mění takto:“ (i „provedou tyto úpravy:“), jinak za posledním odkazem
    na sněmovní tisk v hlavičce. Bez obojího prázdný řetězec."""
    flat = re.sub(r"\s+", " ", text or "").strip()
    head = flat[:PN_HEAD]
    marker = _PN_BODY.search(head)
    refs = [m for m in _PN_TISK.finditer(head) if marker is None or m.start() < marker.start()]
    gap = flat[refs[-1].end():marker.start()] if marker is not None and refs else ""
    if marker is not None and (not refs or (len(gap) <= 200 and not gap.startswith("V "))):
        start = marker.end()  # „(ST 324) Vládní návrh zákona … se mění takto:“
    elif refs:
        start = refs[-1].end()  # „… (Sněmovní tisk č. 324/0). V kapitole 329 …“ (věta textu začíná „V …“)
    else:
        return ""
    body = re.sub(r"^[\s/.,;:–-]*(?:Datum:\s*\d{1,2}\.\s*\d{1,2}\.\s*\d{4}\s*)?", "", flat[start:])
    body = re.sub(r"^(?:\d+\.|[IVX]+\.|[A-ZČŘŠŽ]\.|[a-z]\))\s*", "", body)
    if len(body) <= PN_EXCERPT:
        return body
    cut = body[:PN_EXCERPT].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:–-") + "…"


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
        self._next_autopublish = float("-inf")
        self.fb_min_gap = FB_MIN_GAP
        # None = převzetí sad z mezisložky vypnuté
        self.inbox_dir = inbox_dir
        self._next_ingest = float("-inf")
        self._next_fresh = float("-inf")
        self._next_events = float("-inf")
        self._next_watch = float("-inf")
        self._next_summary = float("-inf")
        self._next_alive = float("-inf")
        # reply_markup zprávy právě obsluhovaného tlačítka (Telegram ho posílá s každým callback_query)
        self._cb_markup: dict | None = None
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
        markup = (cq.get("message") or {}).get("reply_markup")
        self._cb_markup = markup if isinstance(markup, dict) else None
        if action in ("pn", "pnall"):  # seznam /pn: „pn:2376“, „pn:2376+2377“; „pnall:2376,2377“ = každé zvlášť
            self._answered = False
            try:
                if action == "pn":
                    self._pn_request(raw_id)
                else:
                    self._pn_each(raw_id)
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
                    "pick": self._pick, "doneset": self._reject_set, "noset": self._reject_set,
                    "sendretry": self._send_retry, "drop": self._drop_held,
                    "resend": self._resend_buttons,  # resend:0 pod přehledem /fronta
                    # zadání generátoru: id úlohy (reel_jobs), ne položky fronty
                    "jobcancel": self._job_cancel, "jobretry": self._job_retry,
                    # Reels na X přes složku Pro X (xpost.py)
                    "x": self._x_want, "xdone": self._x_done, "xskip": self._x_skip}
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
        # Schválit platí i pro video na šířku: to se znovu neschvaluje (rozhodnutí 2026-11-24)
        if not queue.approve(self.conn, reel_id, _now()):
            self._answer(cb_id, "Položka už nečeká na schválení.")
            return
        self._answer(cb_id, "Schváleno")
        self._drop_buttons(msg_id)
        try:
            self._publish(reel_id, publish.run)
        finally:
            # připomínka zbylé varianty nezávisí na výsledku publikace a jde hned po rozhodnutí,
            # dřív než cokoli dalšího (jinak by „zbývá varianta“ přišla pod videa jiné sady)
            if reel is not None:
                self._offer_remaining(reel.set_id)
        # tlačítko posune frontu jako ostatní rozhodnutí (Zamítnout, Hotovo); až po připomínce
        self._safe_send_next()

    def _retry(self, reel_id: int, cb_id, msg_id) -> None:
        reel = queue.get(self.conn, reel_id)
        if reel is None or reel.status not in ("failed", "unknown"):
            self._answer(cb_id, "Není co opakovat.")
            return
        self._answer(cb_id, "Zkouším znovu")
        self._drop_buttons(msg_id)
        self._publish(reel_id, publish.retry)
        self._safe_send_next()

    def _publish(self, reel_id: int, fn: Callable[..., Reel], auto: bool = False) -> Reel:
        """auto=True: položka autopilota, úspěch se ohlásí zprávou autopilota s odkazy."""
        self._say("Zveřejňuji…")
        try:
            reel = fn(self.conn, self.meta, reel_id, media_dir=self.media_dir, public_base=self.public_base,
                      threads=self.threads)
        except Exception as e:  # noqa: BLE001 – položka nesmí zůstat viset v approved/publishing
            if self._db_lost(e):
                raise
            reel = self._publish_crashed(reel_id, e)
        buttons = retry_buttons(reel_id) if reel.status in ("failed", "unknown") else None
        if reel.status == "published" and reel.set_id is not None:
            buttons = xpost.publish_buttons(reel_id)  # video ze sady je na PC: jde nabídnout na X
        if auto and reel.status == "published":
            text = autopilot_done(reel, jobs.focus_pn(self.conn, reel.set_id) if reel.set_id else None)
        else:
            text = result_text(reel, threads=self.threads is not None)
        self._say(self._scrub(text), buttons)
        # další jednotku fronty tady neposílat: _autopublish volá _publish v každém cyklu hned po
        # maybe_send_next a odešly by dvě jednotky za cyklus (R3); tlačítka posílají další sama
        return reel

    def _publish_crashed(self, reel_id: int, exc: Exception) -> Reel:
        """Výjimka uprostřed publikace: výsledek na sítích je nejistý → unknown (Zkusit znovu nejdřív dohledá)."""
        error = self._safe(exc)[:500]
        log.error("publikace #%s spadla: %s", reel_id, error)
        self._rollback()
        queue.transition(self.conn, reel_id, ("approved", "publishing"), "unknown", error=error)
        return queue.get(self.conn, reel_id)

    def _reject(self, reel_id: int, cb_id, msg_id) -> None:
        if not queue.transition(self.conn, reel_id, "pending", "rejected", decided_at=_now()):
            self._answer(cb_id, "Položka už nečeká na schválení.")
            return
        self._answer(cb_id, "Zamítnuto")
        self._drop_buttons(msg_id)
        self._say(f"#{reel_id} zamítnuto.")
        reel = queue.get(self.conn, reel_id)
        if reel is not None:
            self._offer_remaining(reel.set_id)
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

    def _held_unit(self, reel_id: int) -> list[Reel]:
        """Položky jednotky ve stavu held (celá sada, nebo samostatné video); prázdné = už nečeká."""
        reel = queue.get(self.conn, reel_id)
        if reel is None or reel.status != "held":
            return []
        if reel.set_id is None:
            return [reel]
        return [r for r in queue.set_items(self.conn, reel.set_id) if r.status == "held"]

    def _send_retry(self, reel_id: int, cb_id, msg_id) -> None:
        """Zkusit znovu u held: celá jednotka zpět do fronty s vynulovanými pokusy."""
        unit = self._held_unit(reel_id)
        if not unit:
            self._answer(cb_id, HELD_GONE)
            return
        for r in unit:
            queue.transition(self.conn, r.id, "held", "queued", send_attempts=0, next_send_at=None)
            queue.delete_state(self.conn, f"{queue.HELD_MSG_KEY}{r.id}")
        self._answer(cb_id, "Zkusím poslat znovu")
        self._drop_buttons(msg_id)
        self._safe_send_next()

    def _drop_held(self, reel_id: int, cb_id, msg_id) -> None:
        """Zahodit u held: celá jednotka do rejected (nikdy se z toho stavu nezveřejní)."""
        unit = self._held_unit(reel_id)
        if not unit:
            self._answer(cb_id, HELD_GONE)
            return
        for r in unit:
            queue.transition(self.conn, r.id, "held", "rejected", error="zahozeno", decided_at=_now())
            queue.delete_state(self.conn, f"{queue.HELD_MSG_KEY}{r.id}")
        self._answer(cb_id, "Zahozeno")
        self._drop_buttons(msg_id)
        self._say(messages.held_dropped_text(unit[0]))

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
        queue.set_fields(self.conn, reel.id, telegram_msg_id=preview_id, picked_at=_now())
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

    def _offer_remaining(self, set_id: int | None) -> None:
        """Po rozhodnutí o poslední vybrané variantě připomene nevybrané (zpráva „zbývá varianta N“).

        Nic se neděje, dokud nějaká vybraná varianta čeká na schválení, nebo když už nic nezbývá.
        Chyba odeslání se nepropaguje: položky zůstanou pending a doplní je /fronta.
        """
        if set_id is None:
            return
        try:
            picked = queue.picked(self.conn, set_id)
            items = queue.set_items(self.conn, set_id)
            if any(r.status == "pending" and r.id in picked for r in items):
                return
            self._offer_set(set_id, remaining=True)
        except Exception as e:  # noqa: BLE001 - rozhodnutí je hotové, připomínka nesmí shodit handler
            log.warning("připomínka zbylých variant sady #%s selhala: %s", set_id, self._safe(e))

    def _offer_set(self, set_id: int, remaining: bool = False, reminder: bool = False) -> bool:
        """Zpráva sady s tlačítky Vybrat pro nevybrané varianty; stará zpráva sady přijde o tlačítka.

        Když se zpráva nepošle, nic se nemění (položky zůstanou pending, zkusí to opravná smyčka a /fronta).
        Nevzniklé varianty (jobs.skipped) a varianty bez videa na šířku (reel_state set_warnings:) jsou jen
        v první zprávě sady (i když ji znovu posílá opravná smyčka nebo Poslat tlačítka znovu), zpráva „zbývá
        varianta“ ani připomínka (reminder) je neopakují.
        """
        pending = self._unpicked_items(set_id)
        if not pending:
            return False
        first = not (remaining or reminder)
        skipped = jobs.skipped(self.conn, set_id) if first else None
        warnings = self._set_warnings(set_id) if first else None
        # chyby nevzniklých variant a vadné šířky píše generátor: bez tokenů jako ostatní hlášení
        text = self._scrub(set_text(pending, remaining=remaining, skipped=skipped, reminder=reminder,
                                    warnings=warnings))
        msg_id = self._try("zpráva sady", self.tg.send_text, self.chat_id, text, set_buttons(pending))
        if not msg_id:
            return False
        for r in pending:
            queue.set_fields(self.conn, r.id, telegram_msg_id=msg_id)
        for old in {r.telegram_msg_id for r in pending if r.telegram_msg_id and r.telegram_msg_id != msg_id}:
            self._drop_buttons(old)
        return True

    def _set_warnings(self, set_id: int) -> dict | None:
        """Varianty sady bez videa na šířku z reel_state set_warnings:; chybějící nebo nečitelný JSON = None."""
        raw = queue.get_state(self.conn, f"{queue.SET_WARNINGS_KEY}{set_id}")
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            log.warning("nečitelný klíč %s%s", queue.SET_WARNINGS_KEY, set_id)
            return None

    def _send_set(self, set_id: int) -> bool:
        """Pošle videa sady a zprávu s tlačítky; False = žádné video sady nešlo poslat (sada vyřazena).

        Id každého videa se uloží hned po odeslání (položka zůstává queued), takže nový pokus po chybě
        nebo restartu pošle jen videa, která ještě v Telegramu nejsou. Odmítnuté video (4xx, nečitelný
        soubor) se vyřadí a sada jde dál bez něj. Přechodná chyba (síť, 5xx, 429) jde dál jako výjimka
        (send_next sadu odloží). Do pending jdou varianty až po odeslání všech videí sady.
        """
        dropped: list[str] = []  # hlášení vyřazených variant; když nezbude nic, nahradí je jedna zpráva o sadě
        for reel in queue.set_items(self.conn, set_id):
            if reel.status != "queued" or reel.telegram_video_msg_id:
                continue
            try:
                video_id = self.tg.send_video(self.chat_id, reel.video_path, set_video_caption(reel))
            except TelegramError as e:
                if not e.rejected:
                    # sada se odloží: už vyřazené varianty se ohlásí hned, jinak by o nich nikdo nevěděl
                    for text in dropped:
                        if text:
                            self._say(text)
                    raise
                dropped.append(self._give_up(reel, e, announce=False))
                continue
            except OSError as e:
                dropped.append(self._give_up(reel, e, announce=False))
                continue
            queue.mark_video_sent(self.conn, reel.id, video_id)
        sent = [r for r in queue.set_items(self.conn, set_id) if r.status == "queued" and r.telegram_video_msg_id]
        if not sent:
            self._say(messages.set_dropped_text(set_id))
            return False
        for text in dropped:
            if text:
                self._say(text)
        # pending bez zprávy sady: když zpráva s tlačítky neodejde, doplní ji opravná smyčka (_repair_messages)
        at = self.now()
        for reel in sent:
            queue.transition(self.conn, reel.id, "queued", "pending", sent_at=at)
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
        elif command == "/pnnew":
            self._pn_list(only_new=True)
        elif command == "/zrus":
            self._cmd_zrus((text or "").strip().partition(" ")[2])
        elif command == "/stav":
            # nález o pauze PC nese text chyby prostředí z workeru: bez tokenů jako zpráva o pauze
            self._say(self._clip(self._scrub(hlidani.summary_text(self.conn, self.now()))))
        elif command == "/autopilot":
            self._cmd_autopilot((text or "").strip().partition(" ")[2])
        else:
            self._say(HELP)

    def _autopilot_on(self) -> bool:
        return queue.get_state(self.conn, AUTOPILOT_KEY) == "1"

    def _cmd_autopilot(self, arg: str) -> None:
        """/autopilot on|off: přepne autopilota; bez argumentu (nebo s jiným) vypíše stav."""
        arg = arg.strip().lower()
        moved: list[int] = []
        held = 0
        if arg in ("on", "off"):
            queue.set_state(self.conn, AUTOPILOT_KEY, "1" if arg == "on" else "0")
        if arg == "off":
            # vypnutí platí hned i pro převzaté sady: nezveřejněné položky jdou ke schválení (send_next)
            moved = queue.autopilot_to_approval(self.conn)
            # i úlohy ve frontě nebo u workeru: jejich sada půjde ke schválení, i kdyby se autopilot
            # před převzetím znovu zapnul (jinak by se zveřejnila bez schválení)
            held = jobs.autopilot_off(self.conn)
        text = autopilot_text(self._autopilot_on(), len(moved), held)
        if arg == "off":
            # hlášení nevzniklých variant se pošle hned s odpovědí (autopilot je už nezveřejní)
            notes = [self._pop_skipped(key) for key in queue.state_keys(self.conn, AUTOPILOT_SKIPPED_KEY)]
            text = "\n\n".join([text, *(n for n in notes if n)])
        self._say(self._scrub(text)[:TELEGRAM_TEXT_LIMIT])
        if moved:
            self._safe_send_next()

    def _pop_skipped(self, key: str) -> str:
        """Text hlášení nevzniklých variant z klíče reel_state a klíč smaže (vadný obsah = prázdný text)."""
        raw = queue.get_state(self.conn, key)
        queue.delete_state(self.conn, key)
        try:
            return autopilot_skipped(json.loads(raw)) if raw else ""
        except (ValueError, TypeError, AttributeError):
            return ""

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

    def _add_job(self, source: str, ct: int | None, hint: str | None, focus_pn: list[int] | None = None,
                 autopilot: bool = False) -> None:
        try:
            job_id = jobs.add(self.conn, source=source, ct=ct, format_hint=hint, focus_pn=focus_pn,
                           autopilot=autopilot)
        except jobs.ActiveJobExists:
            self._say(self._duplicate_text(ct, focus_pn))
            return
        self._announce_job(job_id, _job_what(ct, focus_pn), autopilot)

    @staticmethod
    def _duplicate_text(ct: int | None, focus_pn) -> str:
        return f"{_pn_label(focus_pn)} (tisk {ct}) už ve frontě je." if focus_pn else f"Tisk {ct} už ve frontě je."

    def _announce_job(self, job_id: int, what: str, autopilot: bool) -> None:
        """Odpověď na nové zadání (příkaz i Zkusit znovu): událost accepted (pořadí a zadání před ním) a stavová
        zpráva s řádkem stavu PC a tlačítkem Zrušit.

        U úlohy bez autopilota se id zprávy uloží (status_msg_id) a průběh ji pak přepisuje
        (_update_status_messages); job_status:<id> = poslední vykreslená událost, aby se hned nepřepisovala.
        Úloha s autopilotem průběh do chatu nehlásí, Zrušit u odpovědi ale má.
        """
        ids = [j.id for j in jobs.pending(self.conn)]
        position = ids.index(job_id) + 1 if job_id in ids else 0
        before = ids[:position - 1] if position else []
        events.add(self.conn, "accepted", job_id=job_id, detail={"position": position, "before": before})
        history = events.for_job(self.conn, job_id)
        text = messages.job_status_text(job_id, what, history, self._worker_line())
        if autopilot:
            text += "\n" + AUTOPILOT_QUEUED
        msg_id = self._say(self._clip(self._scrub(text)), messages.job_cancel_buttons(job_id))
        if msg_id and not autopilot:
            jobs.set_status_msg(self.conn, job_id, msg_id)
            # poslední vykreslená událost (accepted; worker mohl úlohu mezitím převzít)
            queue.set_state(self.conn, f"{JOB_STATUS_KEY}{job_id}", str(history[-1].id))

    def _cancel_job(self, job_id: int) -> bool:
        """Zruší aktivní zadání (tlačítko i /zrus): událost cancelled a hned přepis stavové zprávy.

        False = zadání neexistuje nebo už je v konečném stavu (nic se nemění).
        """
        if not jobs.cancel(self.conn, job_id):
            return False
        events.add(self.conn, "cancelled", job_id=job_id)
        job = jobs.get(self.conn, job_id)
        if job is not None:
            # zrušení platí i bez přepisu; zpráva se jinak přepíše v dalším cyklu maybe_events
            self._guard("přepis stavové zprávy", lambda: self._render_status(job))
        return True

    def _job_cancel(self, job_id: int, cb_id, msg_id) -> None:
        """Zrušit (stavová zpráva, odpověď na zadání) a Zahodit (zpráva o selhání, úloha je už v konečném stavu:
        jen zmizí tlačítka, úloha se nemění).

        Zadání, které už neběží: zpráva o selhání (má i Zkusit znovu, jobretry:<id>) odpoví „Zahozeno.“, jiná
        zpráva (Zrušit u odpovědi na zadání s autopilotem po jeho skončení) „Zadání už neběží.“; obě přijdou
        o tlačítka.
        """
        if not self._cancel_job(job_id):
            failure = f"jobretry:{job_id}" in {data for _, data in self._cb_buttons()}
            self._answer(cb_id, "Zahozeno." if failure else "Zadání už neběží.")
            self._drop_buttons(msg_id)
            return
        self._answer(cb_id, "Zrušeno.")
        job = jobs.get(self.conn, job_id)
        # stavovou zprávu přepsal _cancel_job; jiná zpráva (odpověď s autopilotem) jen přijde o tlačítko
        if job is None or msg_id != job.status_msg_id:
            self._drop_buttons(msg_id)

    def _job_retry(self, job_id: int, cb_id, msg_id) -> None:
        """Zkusit znovu (zpráva o selhání, zpráva po výpadku): nové zadání se stavovou zprávou, stará zpráva
        přijde o tlačítka zadání. Aktivní zadání se neopakuje. Každé zadání jen jednou: další stisk (tlačítko, které
        se nepodařilo odebrat) jen odpoví „už se opakuje (#N)“ a tlačítka zadání odebere; stejné aktivní zadání =
        odpověď „už ve frontě je“. Obojí jen odpovědí na tlačítko, bez nové zprávy v chatu. Zadání skončené před víc
        než jobs.RETRY_MAX_AGE (úklid mezitím smaže klíč „už opakováno“) se neopakuje: odpověď „zadej ho znovu“
        a tlačítka zadání zmizí."""
        job = jobs.get(self.conn, job_id)
        if job is not None and job.status in jobs.ACTIVE:
            self._answer(cb_id, "Zadání ještě běží.")
            return
        try:
            new_id = jobs.retry(self.conn, job_id, now=self.now())
        except jobs.AlreadyRetried as e:
            self._answer(cb_id, f"Zadání už se opakuje (#{e.new_id})." if e.new_id else "Zadání už se opakuje.")
            self._drop_job_buttons(msg_id, job_id)
            return
        except jobs.RetryTooOld:
            self._answer(cb_id, f"Zadání #{job_id} je starší než {jobs.RETRY_MAX_AGE.days} dní, zadej ho znovu.")
            self._drop_job_buttons(msg_id, job_id)
            return
        except jobs.ActiveJobExists:
            self._answer(cb_id, self._duplicate_text(job.ct, job.focus_pn))
            return
        except ValueError:  # hotové nebo neexistující zadání
            self._answer(cb_id, "Není co opakovat.")
            self._drop_job_buttons(msg_id, job_id)
            return
        self._answer(cb_id, "Zkouším znovu")
        self._drop_job_buttons(msg_id, job_id)
        self._announce_job(new_id, _job_what(job.ct, job.focus_pn), autopilot=False)

    def _drop_job_buttons(self, msg_id: int | None, job_id: int) -> None:
        """Odebere ze zprávy tlačítka zadání job_id (jobretry, jobcancel), ostatní nechá.

        Zpráva po výpadku má Zkusit znovu u více zadání: po stisku zůstanou tlačítka ostatních. Zbylá tlačítka
        jsou z reply_markup stisknuté zprávy; bez něj, nebo když nic nezbude (zpráva o selhání jednoho zadání),
        zmizí všechna jako dřív.
        """
        own = {f"jobretry:{job_id}", f"jobcancel:{job_id}"}
        rest = [(text, data) for text, data in self._cb_buttons() if data not in own]
        if not msg_id:
            return
        if not rest:
            self._drop_buttons(msg_id)
            return
        self._try("editMessageReplyMarkup", self.tg.edit_buttons, self.chat_id, msg_id, rest)

    def _cb_buttons(self) -> list[tuple[str, str]]:
        """Tlačítka (text, callback_data) zprávy právě stisknutého tlačítka z jejího reply_markup; bez něj []."""
        rows = (self._cb_markup or {}).get("inline_keyboard")
        return [(str(b.get("text") or ""), b["callback_data"])
                for row in (rows if isinstance(rows, list) else []) if isinstance(row, list)
                for b in row if isinstance(b, dict) and isinstance(b.get("callback_data"), str)]

    def _cmd_zrus(self, arg: str) -> None:
        """/zrus <číslo zadání>: zruší zadání ve frontě nebo rozpracované („#12“ i „12“)."""
        arg = arg.strip().removeprefix("#")
        if not (arg.isascii() and arg.isdigit()) or not 1 <= int(arg) <= MAX_JOB_ID:
            self._say(messages.ZRUS_USAGE)
            return
        job_id = int(arg)
        if self._cancel_job(job_id):
            self._say(messages.job_cancelled_text(job_id))
            return
        job = jobs.get(self.conn, job_id)
        self._say(messages.job_not_running_text(job_id, job.status if job else None))

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

    def _pn_each(self, raw: str) -> None:
        """Tlačítko Všechny zvlášť („2376,2377“): ke každému PN samostatné zadání (vlastní video), jako /pn 2376 2377."""
        for token in raw.split(","):
            if token.strip().isdigit():
                self._pn_request(token.strip())

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
        # video k PN jen se shrnutím AI (titulek rozboru): bez něj by video nemělo o čem mluvit (25. 11.);
        # zadání se nezaloží, PN zůstane v /pnnew a zadá se, až rozbor dorazí
        with self.conn.cursor() as cur:
            cur.execute("SELECT p.cislo, x.ai_json FROM pn p LEFT JOIN pn_text x ON x.id_dokument = p.id_dokument "
                        "WHERE p.cislo = ANY(%s) AND p.ct = %s", (pns, tisky[0]))
            titled = {n for n, ai_json in cur.fetchall() if _ai_title(ai_json)}
        self.conn.commit()
        missing = [n for n in pns if n not in titled]
        if missing:
            verb = "nemá" if len(missing) == 1 else "nemají"
            self._say(f"{_pn_label(missing)} zatím {verb} shrnutí AI, video nezakládám. "
                      "Až rozbor dorazí, najdeš ho v /pnnew.")
            return
        # autopilot platí jen pro /pn (příkaz i tlačítko); /video a /vyber ho nenastavují
        self._add_job("manual", tisky[0], None, focus_pn=pns, autopilot=self._autopilot_on())

    def _pn_list(self, only_new: bool = False) -> None:
        """PN podané za posledních 48 h (pn.cas je pražský čas), nejnovější první, s tlačítky pro zadání.

        only_new (/pnnew): jen PN bez videa. PN má video, když je v zadání (i společném s jinými PN), které běží
        (queued, claimed), nebo je hotové a z jeho sady nezůstalo jen zamítnuté, propadlé či stažené (sada, která
        ještě nedorazila do fronty, se počítá jako video). Selhané a zrušené zadání video nemá. /pnnew navíc
        vynechá PN bez shrnutí AI (jen jejich počet na konci). V obou seznamech má tlačítko jen PN se shrnutím AI."""
        from .clubs import label as club_label  # import zde: horní blok importů mění souběžná větev
        new_only = (
            "AND NOT EXISTS (SELECT 1 FROM reel_jobs j WHERE p.cislo = ANY(j.focus_pn) AND ("
            "j.status IN ('queued', 'claimed') OR (j.status = 'done' AND NOT ("
            "EXISTS (SELECT 1 FROM reel_queue q WHERE q.set_id = j.id) AND NOT EXISTS (SELECT 1 FROM reel_queue q "
            "WHERE q.set_id = j.id AND q.status NOT IN ('rejected', 'expired', 'withdrawn')))))) "
        ) if only_new else ""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT p.cislo, p.ct, p.osoba, p.klub, p.cas, x.ai_json, left(x.text, 6000) FROM pn p "
                "LEFT JOIN pn_text x ON x.id_dokument = p.id_dokument "
                "WHERE p.cas >= to_char((%s::timestamptz AT TIME ZONE 'Europe/Prague') - interval '48 hours', "
                "'YYYY-MM-DD HH24:MI:SS') " + new_only + "ORDER BY p.cas DESC, p.cislo DESC LIMIT %s",
                (self.now(), PN_SCAN if only_new else PN_LIST))
            rows = cur.fetchall()
        self.conn.commit()
        waiting = 0
        if only_new:
            ready = [r for r in rows if _ai_title(r[5])]
            waiting = len(rows) - len(ready)
            rows = ready[:PN_LIST]
        tail = f"\n\nNa shrnutí AI čeká ještě PN bez videa: {waiting}." if waiting else ""
        if not rows:
            self._say(("Za posledních 48 h žádný PN se shrnutím AI bez videa." + tail) if only_new
                      else "Za posledních 48 h žádný nový PN.")
            return
        lines, buttons, nums = [], [], []
        for cislo, ct, osoba, klub, cas, ai_json, text in rows:
            # klub ke dni podání z dat psp.cz (pn.klub plní web); bez údaje se neuvádí
            club = club_label(klub)
            author = (osoba or "autor neznámý") + (f" ({club})" if club else "")
            line = f"PN {cislo} · tisk {ct} · {author} · {_pn_time(cas)}"
            title = _ai_title(ai_json)
            if title:
                line += f"\n{title} (shrnutí AI)"
            else:
                # rozbor AI běží na Macu se zpožděním; do té doby aspoň začátek textu návrhu
                excerpt = _pn_excerpt(text)
                line += f"\n„{excerpt}“ (text PN, rozbor AI zatím chybí)" if excerpt else "\n(rozbor AI zatím chybí)"
            lines.append(line)
            if title:  # tlačítko jen k PN se shrnutím AI
                buttons.append((f"Video k PN {cislo}", f"pn:{cislo}"))
                nums.append(str(cislo))
        # Všechny zvlášť: ke každému PN seznamu vlastní video (ne jedno společné). Jedno video z víc PN
        # zůstává jen příkazem /pn 2376+2377. callback_data má v Telegramu nejvýš 64 bajtů.
        while len(nums) >= 2 and len("pnall:" + ",".join(nums)) > 64:
            nums.pop()
        if len(nums) >= 2:
            buttons.append((f"Všechny zvlášť ({len(nums)} {_videa(len(nums))})", "pnall:" + ",".join(nums)))
        head = "PN bez videa za posledních 48 h:" if only_new else "PN za posledních 48 h:"
        if self._autopilot_on():
            head += "\n" + AUTOPILOT_PN_LIST_HEADER
        self._say(self._clip(head + "\n\n" + "\n\n".join(lines) + tail), buttons)

    def _worker_line(self) -> str:
        """Řádek stavu PC (/fronta, stavová zpráva zadání): kdy bylo PC naposledy vidět a stav generátoru
        z reel_state.worker_state (zapisuje server při heartbeat); nečitelný stav = jen kdy bylo PC vidět.

        Heartbeat starší než jobs.WORKER_STALE = „PC neodpovídá od …“ bez stavu generátoru. Výjimka stejná jako
        hlidani._reels_pc: pozastavený worker pošle heartbeat se stavem paused a `until` jednou a spí až do `until`,
        do `until` + WORKER_STALE proto zůstává „pozastaveno (do H:MM)“."""
        now = self.now()
        seen = _parse_time(queue.get_state(self.conn, WORKER_SEEN_KEY))
        seen_text = None
        if seen is not None:
            # čas v Europe/Prague přes PostgreSQL (obraz nepotřebuje tzdata)
            with self.conn.cursor() as cur:
                cur.execute("SELECT to_char(%s::timestamptz AT TIME ZONE 'Europe/Prague', 'FMDD. FMMM. FMHH24:MI')",
                            (seen,))
                seen_text = cur.fetchone()[0]
            self.conn.commit()
        try:
            state = json.loads(queue.get_state(self.conn, WORKER_STATE_KEY) or "null")
        except ValueError:
            state = None
        if not isinstance(state, dict):
            state = None
        until = state.get("until") if state is not None and state.get("state") == "paused" else None
        until = _parse_time(until) if isinstance(until, str) else None
        stale = (seen is not None and now - seen > jobs.WORKER_STALE
                 and not (until is not None and now <= until + jobs.WORKER_STALE))
        return messages.worker_line(seen_text, state, jobs.hm(self.conn, until) if until is not None else None,
                                    stale)

    # --- události úloh a workeru (reel_events) ---------------------------

    def maybe_events(self) -> None:
        """Události nejvýš jednou za EVENTS_INTERVAL s: převzetí selhání bez události, vydání bez známky života,
        odeslání naléhavých událostí, přepis stavových zpráv zadání. Každý krok zvlášť, chyba jednoho nezastaví
        ostatní."""
        now = self.clock()
        if now < self._next_events:
            return
        self._next_events = now + EVENTS_INTERVAL
        self._guard("převzetí selhání bez události", lambda: events.adopt_failed(self.conn))
        self._guard("kontrola vydání bez známky života", self._check_silent)
        self._guard("odeslání událostí", self._send_urgent)
        self._guard("přepis stavových zpráv", self._update_status_messages)
        self._guard("zprávy Pro X", self._x_notify)

    # --- Reels na X (složka Pro X na PC, xpost.py) -------------------------

    def _x_want(self, reel_id: int, cb_id, msg_id) -> None:
        state = xpost.want(self.conn, reel_id, self.now())
        if state == "wanted":
            self._answer(cb_id, "Na X: čekám na PC")
            self._drop_buttons(msg_id)
            self._say(xpost.wanted_text(reel_id), reply_to=msg_id)
            return
        self._answer(cb_id, {"waiting": "Na X: pořád čekám na PC", "ready": "Video už je ve složce Pro X.",
                                 "done": "Už je na X."}.get(
            state, "Na X jde jen zveřejněné video ze sady generátoru."))

    def _x_finish(self, reel_id: int, cb_id, msg_id, status: str) -> None:
        if not xpost.finish(self.conn, reel_id, status):
            self._answer(cb_id, "Položka už na X nečeká.")
            return
        self._answer(cb_id, "Hotovo" if status == "done" else "Zrušeno")
        self._drop_buttons(msg_id)

    def _x_done(self, reel_id: int, cb_id, msg_id) -> None:
        self._x_finish(reel_id, cb_id, msg_id, "done")

    def _x_skip(self, reel_id: int, cb_id, msg_id) -> None:
        self._x_finish(reel_id, cb_id, msg_id, "skipped")

    def _x_notify(self) -> None:
        """Zprávy o videích, která worker nahlásil (ready: text a odkaz na X, missing: video na PC není).
        Každá jednou (x_msg_id); když odeslání selže, zkusí se v dalším běhu."""
        for item in xpost.to_notify(self.conn):
            if item.x_status == "missing":
                msg_id = self.tg.send_text(self.chat_id, xpost.missing_text(item.id), xpost.missing_buttons(item.id))
            else:
                text, url = xpost.ready_message(item)
                try:
                    msg_id = self.tg.send_text(self.chat_id, text, xpost.ready_buttons(item.id, url),
                                               parse_mode="HTML")
                except TelegramError as e:
                    if not e.rejected:
                        raise
                    # Telegram odmítl tlačítko s odkazem (např. délka): odkaz do textu, tlačítka jen Hotovo / Zrušit
                    log.warning("Pro X #%s: tlačítko s odkazem odmítnuto: %s", item.id, self._safe(e))
                    msg_id = self.tg.send_text(self.chat_id, f"{text}\n\nOtevřít X: {html.escape(url)}",
                                               xpost.ready_buttons(item.id, None), parse_mode="HTML")
            xpost.mark_notified(self.conn, item.id, item.x_status, msg_id)

    def _update_status_messages(self) -> None:
        """Průběh vyžádaných zadání bez autopilota jen přepisem jejich stavové zprávy (žádné sendMessage)."""
        for job in jobs.with_status_msg(self.conn):
            self._render_status(job)

    def _render_status(self, job: jobs.Job) -> None:
        """Přepíše stavovou zprávu úlohy, když má událost novější než job_status:<id>.

        Aktivní úloha: celá historie, stav PC a Zrušit; konečný stav bez tlačítek a bez stavu PC. Úspěch i odmítnutí
        Telegramem (zpráva smazaná, „message is not modified“) = vyřízené, přechodná chyba = znovu v dalším cyklu.
        Po vykreslení konečné události se klíč smaže a zpráva se dál nepřepisuje. Konečný stav, ke kterému konečná
        událost nepřišla (server zapisuje stav a událost dvěma příkazy), se sleduje nejvýš STATUS_FINAL_WAIT.
        """
        key = f"{JOB_STATUS_KEY}{job.id}"
        raw = queue.get_state(self.conn, key)
        if raw is None or not job.status_msg_id:
            return
        shown = int(raw) if raw.isdigit() else 0
        history = events.for_job(self.conn, job.id)
        final = job.status not in jobs.ACTIVE
        if not history or history[-1].id <= shown:
            if final and (job.finished_at is None or job.finished_at < self.now() - STATUS_FINAL_WAIT):
                queue.delete_state(self.conn, key)
            return
        last = history[-1]
        what = _job_what(job.ct, job.focus_pn)
        text = messages.job_status_text(job.id, what, history, None if final else self._worker_line())
        buttons = None if final else messages.job_cancel_buttons(job.id)
        try:
            self.tg.edit_text(self.chat_id, job.status_msg_id, self._clip(self._scrub(text)), buttons)
        except TelegramError as e:
            if not e.rejected:
                log.warning("stavová zpráva zadání #%s se nepřepsala: %s", job.id, self._safe(e))
                return
            log.info("stavovou zprávu zadání #%s Telegram nepřepsal (%s), beru jako vyřízené", job.id, self._safe(e))
        if final and last.kind in FINAL_EVENTS:
            queue.delete_state(self.conn, key)
        else:
            queue.set_state(self.conn, key, str(last.id))

    def _job_label(self, job_id: int | None) -> tuple[str, jobs.Job | None]:
        """Popis zadání do zprávy („tisk 221“) a úloha; úloha, která v reel_jobs není, = „?“."""
        job = jobs.get(self.conn, job_id) if job_id is not None else None
        return (_job_what(job.ct, job.focus_pn) if job else "?"), job

    def _event_error(self, e: events.Event, job: jobs.Job | None) -> str:
        # chyba z události (stav v okamžiku selhání), u převzatých bez textu z úlohy; vždy bez tokenů
        return self._scrub(str(e.detail.get("error") or (job.error if job else "") or ""))

    def _check_silent(self) -> None:
        """Vydaná úloha bez známky života (propadlé vydání, PC je asi vypnuté): událost silent jednou na vydání.

        Vydání pozná klíč silent:<id>:<čas vydání claimed_at v s> (bez claimed_at čas založení), ne číslo pokusu:
        návrat env/infra pokus vrací, takže další vydání má stejné attempts. Zprávu dostane jen vyžádaná úloha
        (manual, auto_pick), automatická jde jen do historie. „Od“ = poslední známka života v pražském čase:
        nejpozdější z fáze (stage_at), vydání (claimed_at) a heartbeat workeru.
        """
        seen = _parse_time(queue.get_state(self.conn, WORKER_SEEN_KEY))
        for job in jobs.silent(self.conn, self.now()):
            since = max((t for t in (job.stage_at, job.claimed_at, seen) if t is not None), default=job.created_at)
            urgency = events.URGENT if job.source in ("manual", "auto_pick") else events.HISTORY
            issued = int((job.claimed_at or job.created_at).timestamp())
            events.add(self.conn, "silent", job_id=job.id, detail={"since_hm": jobs.hm(self.conn, since)},
                       urgency=urgency, dedupe=f"silent:{job.id}:{issued}")

    def _announced(self, e: events.Event, job: jobs.Job | None) -> bool:
        """Selhání, které už ohlásil starší bot (_notify_failed během výměny kontejnerů): reel_jobs.notified_at
        je vyplněné, událost se jen označí bez zprávy (urgentní, po výpadku i v denním souhrnu)."""
        return e.kind == "failed" and job is not None and job.notified_at is not None

    def _event_message(self, e: events.Event) -> tuple[str, list[tuple[str, str]] | None] | None:
        """Text a tlačítka naléhavé události; None = druh bez zprávy nebo už ohlášené selhání (jen se označí)."""
        if e.kind == "failed" and e.job_id is not None:
            what, job = self._job_label(e.job_id)
            if self._announced(e, job):
                return None
            return messages.failed_text(e.job_id, what, self._event_error(e, job)), messages.failed_buttons(e.job_id)
        if e.kind == "silent" and e.job_id is not None:
            what, _ = self._job_label(e.job_id)
            return messages.silent_text(e.job_id, what, str(e.detail.get("since_hm") or "?")), None
        if e.kind == "worker_paused":
            d = e.detail
            text = messages.worker_paused_text(d.get("reason"), d.get("detail"), d.get("until_hm"))
            return self._scrub(text), None
        if e.kind == "worker_resumed":
            return messages.WORKER_RESUMED, None
        return None

    def _mark_events(self, sent: list[events.Event]) -> None:
        """Události jako odeslané; u failed i reel_jobs.notified_at (starší bot by je po návratu neohlásil znovu)."""
        events.mark_sent(self.conn, [e.id for e in sent])
        for e in sent:
            if e.kind == "failed" and e.job_id is not None:
                jobs.mark_notified(self.conn, e.job_id)

    def _send_urgent(self) -> None:
        """Naléhavé události (urgency 1) po jedné v pořadí id; označí se až po vyřízeném sendMessage.

        Přechodná chyba (síť, 5xx, 429) ukončí průchod a další cyklus začne od téže události (pořadí zůstane).
        Zprávu, kterou Telegram trvale odmítl (TelegramError.rejected), bot zaloguje a označí jako vyřízenou, jinak
        by zastavila všechny další naléhavé zprávy. Když nejstarší neodeslaná událost je starší než events.OUTAGE,
        odejde místo všech jedna souhrnná zpráva (_send_outage).
        """
        pending = events.unsent(self.conn, events.URGENT)
        if not pending:
            return
        oldest = min(e.at for e in pending)
        if oldest < self.now() - events.OUTAGE:
            self._send_outage(pending, oldest)
            return
        for e in pending:
            msg = self._event_message(e)
            if msg is not None and not self._send_event(f"událost #{e.id} ({e.kind})", msg[0], msg[1]):
                return
            self._mark_events([e])

    def _send_event(self, what: str, text: str, buttons) -> bool:
        """sendMessage zprávy z událostí; True = vyřízeno (odesláno, nebo Telegram zprávu trvale odmítl),
        False = přechodná chyba (zkusí se v dalším cyklu). Chyba v logu bez tokenů."""
        try:
            self.tg.send_text(self.chat_id, self._clip(text), buttons)
        except TelegramError as e:
            if e.rejected:
                log.error("%s: Telegram odmítl zprávu, beru jako vyřízenou: %s", what, self._safe(e))
                return True
            log.warning("%s: sendMessage selhalo: %s", what, self._safe(e))
            return False
        except Exception as e:  # noqa: BLE001 — výpadek spojení i nečekaná odpověď = zkusit znovu
            log.warning("%s: sendMessage selhalo: %s", what, self._safe(e))
            return False
        return True

    def _send_outage(self, pending: list[events.Event], oldest: datetime) -> None:
        """Po dlouhém výpadku jedna zpráva: hotové sady od nejstarší neodeslané události, nepovedená zadání
        (Zkusit znovu u nejvýš messages.OUTAGE_BUTTONS; bez selhání, která ohlásil starší bot) a pozastavení PC.
        Bez obsahu, nebo když ji Telegram trvale odmítl, se události jen označí."""
        done = events.count_since(self.conn, "done", oldest)
        failed = list(dict.fromkeys(e.job_id for e in pending if e.kind == "failed" and e.job_id is not None
                                    and not self._announced(e, jobs.get(self.conn, e.job_id))))
        paused = any(e.kind == "worker_paused" for e in pending)
        text = messages.outage_text(done, failed, paused)
        if text and not self._send_event("zpráva po výpadku", text, messages.outage_buttons(failed)):
            return
        self._mark_events(pending)

    def _summary_event_lines(self, pending: list[events.Event]) -> list[str]:
        """Řádky denního souhrnu z událostí urgency 2 (selhání a vypršení automatických úloh); selhání, které
        ohlásil starší bot, řádek nemá (událost se se souhrnem jen označí)."""
        lines = []
        for e in pending:
            if e.job_id is None:
                continue
            what, job = self._job_label(e.job_id)
            if self._announced(e, job):
                continue
            line = messages.summary_event_line(e.kind, e.job_id, what, self._event_error(e, job))
            if line:
                lines.append(line)
        return lines

    def _show_queue(self) -> None:
        """/fronta: jedna zpráva s přehledem (na co která položka čeká), nic dalšího se samo neposílá.

        Ztracená tlačítka obnoví až tlačítko Poslat tlačítka znovu pod přehledem (resend:0).
        """
        now = self.now()
        units = queue.open_units(self.conn, now, per_day=SETS_PER_DAY, stale_publishing=STALE_PUBLISHING)
        text = messages.queue_overview(units, jobs.pending(self.conn), self._worker_line(), now,
                                       lambda t: jobs.hm(self.conn, t))
        self._say(self._scrub(text), messages.RESEND_BUTTONS)

    def _resend_buttons(self, _id: int, cb_id, msg_id) -> None:
        """Poslat tlačítka znovu (resend:0): tlačítka, která se mohla ztratit (výpadek Telegramu, smazané
        zprávy), přijdou znovu.

        pending → nový náhled s tlačítky schválení (sada bez výběru novou zprávou sady); held → Zkusit znovu /
        Zahodit; failed/unknown → Zkusit znovu; approved a dlouho běžící publishing jen textem (vyřeší je
        smyčka, _recover_stuck).
        """
        self._answer(cb_id)
        stale_before = _now() - STALE_PUBLISHING
        offered: set[int] = set()
        held_shown: set[int] = set()
        for reel in queue.by_status(self.conn, ("pending", "failed", "unknown", "approved", "publishing", "held")):
            head = f"{video_caption(reel)} · {reel.status}"
            if reel.status == "held":
                # jedna zpráva za jednotku (sada má held všechny queued varianty)
                unit = reel.set_id if reel.set_id is not None else -reel.id
                if unit not in held_shown:
                    held_shown.add(unit)
                    self._say(self._scrub(messages.held_text(reel)), messages.held_buttons(reel.id))
            elif self._unpicked(reel):
                # sada bez výběru: znovu jedna zpráva s tlačítky Vybrat (i když se původní neposlala)
                if reel.set_id not in offered:
                    offered.add(reel.set_id)
                    self._offer_set(reel.set_id)
            elif reel.status == "pending":
                self._resend_preview(reel)
            elif reel.status == "approved" and reel.autopilot:
                self._say(f"{head} · čeká na automatické zveřejnění")
            elif reel.status == "approved":
                self._say(f"{head} · čeká na zveřejnění (po 15 min bez publikace dostane Zkusit znovu)")
            elif reel.status == "publishing":
                started = reel.decided_at or reel.sent_at or reel.created_at
                if started < stale_before:
                    self._say(f"{head} · zveřejňování možná přerušeno — bot ho brzy převede na nejisté")
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
        """Pošle náhled popisku, tlačítka u poslední zprávy; vrací id zprávy s tlačítky.

        Vícedílný náhled si odeslané nekoncové části pamatuje (reel_state preview_sent:<id>): když další část
        neodejde, nový pokus se stejným textem pošle jen chybějící části (jiný text = celý náhled od první
        části). Po odeslání poslední části se záznam smaže.
        """
        parts = preview_parts(reel, threads=self.threads is not None)
        key = f"{queue.PREVIEW_SENT_KEY}{reel.id}"
        sha = hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()
        sent = self._preview_sent(key, sha) if len(parts) > 1 else []
        msg_id = 0
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            if not last and i < len(sent):
                continue  # tahle část náhledu už v chatu je
            msg_id = self.tg.send_text(self.chat_id, part, approval_buttons(reel.id) if last else None)
            if not last:
                # i na první část náhledu jde odpovědět novým popiskem
                queue.set_state(self.conn, f"edit_msg:{msg_id}", str(reel.id))
                sent.append(msg_id)
                queue.set_state(self.conn, key, json.dumps({"sha": sha, "sent": sent}))
        queue.delete_state(self.conn, key)
        return msg_id

    def _preview_sent(self, key: str, sha: str) -> list[int]:
        """Id už odeslaných nekoncových částí náhledu se stejným textem; jiný text nebo vadný záznam = []."""
        try:
            data = json.loads(queue.get_state(self.conn, key) or "null")
        except ValueError:
            return []
        if not isinstance(data, dict) or data.get("sha") != sha:
            return []
        sent = data.get("sent")
        if not isinstance(sent, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in sent):
            return []
        return sent

    def send_next(self, force: bool = False) -> None:
        """Pošle ke schválení nejvýš jednu jednotku (sadu nebo samostatné video) podle pravidel R1–R4.

        R1: vyžádaná jednotka (queue.REQUESTED_SOURCES) jde hned, na nerozhodnutých položkách jiných
        jednotek nezáleží. R2: automatická jen do denního limitu SETS_PER_DAY a když žádná jiná automatická
        sada nečeká nedotčená (queue.auto_blocked); force (/dalsi) R2 obchází. R3: jedno volání = nejvýš
        jedna jednotka. R4: přechodná chyba Telegramu (síť, 5xx, 429) jednotku odloží (queue.defer_send),
        výjimka nejde ven; po SEND_MAX_ATTEMPTS pokusech přejde jednotka do held a přijde jedna zpráva
        s tlačítky Zkusit znovu / Zahodit. Video, které Telegram odmítne (4xx) nebo jehož soubor nejde
        přečíst, se vyřadí (rejected, nikdy se z toho stavu nezveřejní).

        Pořadí zpráv u samostatného videa: video 9:16, video na šířku (když ho položka má), náhled s tlačítky.
        """
        now = self.now()
        limit_ok = queue.sets_sent_on(self.conn, queue.local_date(self.conn, now)) < SETS_PER_DAY
        auto_ok = force or (limit_ok and not queue.auto_blocked(self.conn, now))
        reel = queue.next_sendable(self.conn, now, auto_ok)
        if reel is None:
            if force:
                waiting = queue.by_status(self.conn, ("queued",))
                self._say(messages.NOTHING_READY if waiting else "Fronta je prázdná.")
            elif not auto_ok:
                self._announce_limit("limit" if not limit_ok else "blocked")
            return
        try:
            if reel.set_id is not None:
                self._send_set(reel.set_id)
            else:
                self._send_solo(reel)
        except TelegramError as e:
            # odmítnutí (4xx) vyřeší _send_set / _send_solo samy; sem dojde jen přechodná chyba
            if e.rejected:
                raise
            self._defer(reel, e)
        except Exception as e:  # noqa: BLE001 – vadná data nebo divná odpověď API nesmí ucpat frontu
            if self._db_lost(e):
                raise
            # jinak by jednotka zůstala nejstarší a blokovala frontu každý cyklus: odložit jako R4
            log.error("odeslání #%s spadlo: %s", reel.id, self._safe(e))
            self._rollback()
            self._defer(reel, e)

    def _send_solo(self, reel: Reel) -> None:
        """Samostatné video: videa (už odeslaná se přeskočí), pending, náhled s tlačítky.

        Přechodná chyba Telegramu jde dál jako výjimka (send_next položku odloží). Když náhled neodejde,
        doplní ho opravná smyčka (_repair_messages).
        """
        shape = "na výšku"
        try:
            if not reel.telegram_video_msg_id:
                video_id = self.tg.send_video(self.chat_id, reel.video_path, video_caption(reel))
                queue.mark_video_sent(self.conn, reel.id, video_id)
            if reel.video_landscape_path and reel.telegram_landscape_msg_id is None:
                shape = "na šířku"
                landscape_id = self.tg.send_video(self.chat_id, reel.video_landscape_path,
                                                  video_caption(reel, landscape=True))
                # otisk odeslané šířky: když ji reels-add-landscape mezitím vyměnil, zápis neproběhne
                # a novou pošle _send_landscape_updates
                queue.set_landscape_sent(self.conn, reel.id, landscape_id, reel.video_landscape_sha256)
        except TelegramError as e:
            if not e.rejected:
                raise
            self._give_up(reel, e, shape)
            return
        except OSError as e:
            self._give_up(reel, e, shape)
            return
        if queue.transition(self.conn, reel.id, "queued", "pending", sent_at=self.now()):
            self._resend_preview(queue.get(self.conn, reel.id))

    def _defer(self, reel: Reel, exc: Exception) -> None:
        """Chyba odeslání (přechodná nebo nečekaná): jednotka se odloží, po SEND_MAX_ATTEMPTS pokusech held."""
        # odkládá se queued položka jednotky (varianta sady mohla být mezitím vyřazena odmítnutím)
        unit = queue.set_items(self.conn, reel.set_id) if reel.set_id is not None else [queue.get(self.conn, reel.id)]
        target = next((r for r in unit if r is not None and r.status == "queued"), None)
        if target is None:
            return
        error = self._safe(exc)[:500]
        attempts = queue.defer_send(self.conn, target.id, error, self.now())
        log.warning("odeslání #%s odloženo (pokus %s): %s", target.id, attempts, error)
        if attempts >= queue.SEND_MAX_ATTEMPTS:
            held = queue.get(self.conn, target.id)
            if held is not None and held.status == "held":
                msg_id = self._say(self._scrub(messages.held_text(held)), messages.held_buttons(held.id))
                if msg_id:
                    # vypršení held (queue.expire) zprávě odebere tlačítka
                    queue.set_state(self.conn, f"{queue.HELD_MSG_KEY}{held.id}", str(msg_id))

    def _announce_limit(self, reason: str) -> None:
        """Automatická sada čeká (reason 'limit' = denní limit, 'blocked' = jiná nedotčená automatická sada).

        Ohlásí se jednou za sadu (reel_state sets_limit_notified).
        """
        waiting = next((r for r in queue.by_status(self.conn, ("queued",))
                        if r.source == queue.AUTO_SOURCE and r.set_id is not None), None)
        if waiting is None:
            return
        if queue.get_state(self.conn, LIMIT_NOTIFIED_KEY) == str(waiting.set_id):
            return
        if self._say(messages.auto_waiting_text(waiting.set_id, SETS_PER_DAY, reason)):
            queue.set_state(self.conn, LIMIT_NOTIFIED_KEY, str(waiting.set_id))

    def _repair_messages(self) -> None:
        """Doplní ztracené zprávy čekajících položek (bez telegram_msg_id), aniž by na to čekal /fronta.

        Nevybrané varianty sady dostanou zprávu sady s tlačítky Vybrat, samostatné nebo vybrané položky
        náhled se Schválit. Když zpráva zase neodejde, zkusí se to v dalším cyklu.
        """
        offered: set[int] = set()
        for reel in queue.by_status(self.conn, ("pending",)):
            if reel.telegram_msg_id:
                continue
            if self._unpicked(reel):
                if reel.set_id not in offered:
                    offered.add(reel.set_id)
                    self._offer_set(reel.set_id)
            else:
                self._resend_preview(reel)

    def _send_landscape_updates(self) -> None:
        """Video na šířku doplněné k čekající položce: pošle ho do chatu jen pro informaci.

        Schválit z původního náhledu platí i pro šířku (queue.approve), nový náhled s tlačítky se neposílá.
        Id videa se uloží hned po odeslání, aby se video neposílalo v každé obrátce znovu. Výpadek Telegramu
        (síť, 5xx, 429) se jen zaloguje a zkusí se znovu. Trvalou chybu (Telegram video odmítl, soubor nejde
        přečíst) bot ohlásí jednou a tutéž šířku (podle sha256) už nezkouší; nový soubor přes
        reels-add-landscape se zkusí znovu.
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
            # šířka jde do chatu jen pro informaci: Schválit z původního náhledu platí i pro ni,
            # nový náhled s tlačítky se neposílá (rozhodnutí 2026-11-24)

    def _give_up(self, reel: Reel, exc: Exception, shape: str = "na výšku", announce: bool = True) -> str:
        """Vyřadí položku, jejíž video nejde poslat; shape říká, které (na výšku / na šířku).

        Vrací text hlášení (prázdný, když se stav nezměnil); announce=False ho nepošle (pošle ho volající).
        """
        error = f"nelze poslat do Telegramu: {self._safe(exc)}"[:500]
        log.warning("#%s vyřazeno (video %s): %s", reel.id, shape, error)
        if not queue.transition(self.conn, reel.id, "queued", "rejected", error=error):
            return ""
        text = f"#{reel.id} vyřazeno, video {shape} {error}. Oprav video a zařaď ho znovu."
        if announce:
            self._say(text)
        return text

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

    def maybe_alive(self) -> None:
        """Známka života pro synchronizaci webu (bot svůj pád sám nenahlásí), nejvýš jednou za ALIVE_INTERVAL s.

        Smyčka ji volá na konci průchodu, jen když průchod (i poll_once) proběhl bez výjimky."""
        now = self.clock()
        if now < self._next_alive:
            return
        self._next_alive = now + ALIVE_INTERVAL
        self._guard("zápis známky života", lambda: queue.set_state(self.conn, BOT_SEEN_KEY, self.now().isoformat()))

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
        # autopilot jen pro úlohy s příznakem a jen dokud je přepínač zapnutý (jinak běžné schvalování)
        results = inbox.ingest_all(self.conn, Path(self.inbox_dir), Path(self.media_dir),
                                   autopilot_on=self._autopilot_on())
        for r in results:
            if not r.ok:
                # úloha je už failed; zprávu o ní pošle maybe_events (events.adopt_failed)
                log.warning("sada úlohy %s nepřevzata", r.job_id)
            elif r.autopilot and r.skipped:
                # nevzniklé varianty se ohlásí až po zveřejnění poslední položky sady (_announce_skipped)
                queue.set_state(self.conn, f"{AUTOPILOT_SKIPPED_KEY}{r.job_id}",
                                json.dumps(r.skipped, ensure_ascii=False))
            if r.ok and not r.autopilot and r.warnings:
                # vadná šířka: řádek v první zprávě sady (_offer_set); sada autopilota zprávu sady nemá,
                # zůstane jen log z inbox
                queue.set_state(self.conn, f"{queue.SET_WARNINGS_KEY}{r.job_id}",
                                json.dumps(r.warnings, ensure_ascii=False))
        # převzaté sady odešle smyčka (maybe_send_next, nejvýš jedna jednotka za SEND_INTERVAL, R3)

    def maybe_autopublish(self) -> None:
        """Zveřejní jednu položku autopilota, nejvýš jednou za SEND_INTERVAL s (sada tak jde po jedné za cyklus)."""
        now = self.clock()
        if now < self._next_autopublish:
            return
        self._next_autopublish = now + SEND_INTERVAL
        self._guard("zveřejnění autopilota", self._autopublish)

    def _autopublish(self) -> None:
        # vypnutý přepínač: nezveřejní se nic (převzaté položky už /autopilot off vrátil ke schválení)
        if not self._autopilot_on():
            return
        if self._fb_slot_free():
            # nejdřív položka, které FB odmítl limit (IG a Threads už vyšly): retry dohledá a pošle jen FB
            deferred = queue.next_fb_deferred(self.conn, publish.FB_LIMIT_PREFIX)
            if deferred is not None:
                reel = self._publish(deferred.id, publish.retry, auto=True)
            else:
                reel = queue.next_autopilot(self.conn)
                if reel is not None:
                    # publish.run sám přejde approved → publishing; chyba skončí failed/unknown se Zkusit znovu
                    # a další položka sady přijde na řadu v dalším cyklu
                    reel = self._publish(reel.id, publish.run, auto=True)
            if publish.fb_deferred(reel):
                queue.set_state(self.conn, FB_LIMIT_KEY, (_now() + FB_LIMIT_PAUSE).isoformat())
        # nevzniklé varianty sad, které už nemají co zveřejnit (i hlášení, které minule neodešlo)
        for key in queue.state_keys(self.conn, AUTOPILOT_SKIPPED_KEY):
            suffix = key[len(AUTOPILOT_SKIPPED_KEY):]
            if not suffix.isdigit():
                # vadný klíč by chybu hlásil každý cyklus: smazat
                log.warning("vadný klíč %s smazán", key)
                queue.delete_state(self.conn, key)
                continue
            self._announce_skipped(int(suffix))

    def _fb_slot_free(self) -> bool:
        """Autopilot smí zveřejnit: neběží pauza po limitu Meta, za 24 h je na FB méně než FB_DAILY_CAP
        a od posledního FB uplynul rozestup."""
        now = _now()
        until = _parse_time(queue.get_state(self.conn, FB_LIMIT_KEY))
        if until is not None and now < until:
            return False
        if queue.fb_published_since(self.conn, now - timedelta(hours=24)) >= FB_DAILY_CAP:
            return False
        last = queue.last_fb_published(self.conn)
        return last is None or now - last >= self.fb_min_gap

    def _announce_skipped(self, set_id: int) -> None:
        """Po poslední položce sady (žádná už nečeká ani se nezveřejňuje) ohlásí nevzniklé varianty jednou."""
        key = f"{AUTOPILOT_SKIPPED_KEY}{set_id}"
        raw = queue.get_state(self.conn, key)
        if raw is None:
            return
        if any(r.status in ("approved", "publishing") for r in queue.set_items(self.conn, set_id)):
            return
        try:
            text = autopilot_skipped(json.loads(raw))
        except (ValueError, TypeError, AttributeError):
            text = ""
        # když zpráva neodejde, klíč zůstane a další cyklus to zkusí znovu
        if not text or self._say(self._scrub(text)):
            queue.delete_state(self.conn, key)

    def maybe_fresh(self) -> None:
        """Čerstvé nálezy, stažení sad, lhůty čekání, připomínky a úklid stavu, nejvýš jednou za FRESH_INTERVAL s.

        Každý krok běží zvlášť: chyba jednoho nezastaví ostatní.
        """
        now = self.clock()
        if now < self._next_fresh:
            return
        self._next_fresh = now + FRESH_INTERVAL
        self._guard("hledání čerstvých nálezů", lambda: fresh.detect(self.conn, self.now()))
        self._guard("stažení sad mimo nálezy", self._retire)
        self._guard("vypršení lhůt", self._expire)
        self._guard("připomínky automatických sad", self._remind)
        self._guard("úklid stavu", self._cleanup)

    def _cleanup(self) -> None:
        """Úklid reel_state, starých událostí a složek zrušených úloh (uklid.run); nenulové počty do logu."""
        inbox_dir = Path(self.inbox_dir) if self.inbox_dir is not None else None
        counts = uklid.run(self.conn, self.now(), inbox_dir)
        if any(counts.values()):
            log.info("úklid: klíče stavu %s, události %s, složky zrušených úloh %s",
                     counts.get("state", 0), counts.get("events", 0), counts.get("inbox", 0))

    def _retire(self) -> None:
        for set_id, status in fresh.retire(self.conn, self.now()):
            items = [r for r in queue.set_items(self.conn, set_id) if r.status == status]
            if not any(r.sent_at for r in items):
                # sada ještě nebyla v Telegramu: změna stavu bez zprávy
                log.info("sada %s ještě neodeslaná, nyní %s", set_id, status)
                continue
            for msg_id in {r.telegram_msg_id for r in items if r.telegram_msg_id}:
                self._drop_buttons(msg_id)
            self._say(messages.withdrawn_text(set_id))

    def _expire(self) -> None:
        """Vypršelé jednotky (queue.expire): tlačítka pryč a jedna zpráva za jednotku."""
        for e in queue.expire(self.conn, self.now()):
            for msg_id in e.msg_ids:
                self._drop_buttons(msg_id)
            if not self._say(messages.expired_text(e)):
                # stav se už změnil; ztracená zpráva aspoň v logu (a nevybrané varianty v denním souhrnu)
                log.warning("zpráva o vypršení (%s, sada %s, položky %s) neodešla", e.kind, e.set_id, e.reel_ids)

    def _remind(self) -> None:
        """Jedna připomínka nedotčené automatické sady po AUTO_HOLD h; neodeslaná se zkusí příště.

        Připomínka je nová zpráva sady s tlačítky Vybrat a Hotovo (stará zpráva o tlačítka přijde),
        takže se dá vybrat rovnou z ní a nemusí se hledat původní zpráva nad videi.
        """
        for set_id in queue.auto_reminders(self.conn, self.now()):
            if self._offer_set(set_id, reminder=True):
                queue.mark_reminded(self.conn, set_id)

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
        text = hlidani.summary_text(self.conn, self.now())
        # řádky fronty Reels (vypršelé a stažené sady za 24 h, selhání automatických úloh) jen v denním souhrnu;
        # /stav ukazuje stejný přehled a problémy (data i provoz Reels) bez nich
        lines = queue.summary_lines(self.conn, self.now())
        # selhání a vypršení automatických úloh (události urgency 2) jdou jen sem
        pending = events.unsent(self.conn, events.SUMMARY)
        lines += self._summary_event_lines(pending)
        if lines:
            text += "\n\nFronta Reels:\n" + "\n".join(f"- {line}" for line in lines)
        # souhrn ani události se nezapíšou jako odeslané, když zpráva neodejde (další pokus za SUMMARY_CHECK_INTERVAL);
        # celý text bez tokenů (pauza PC nese text chyby prostředí z workeru)
        if self._say(self._clip(self._scrub(text))):
            hlidani.mark_summary(self.conn, day)
            self._mark_events(pending)

    def maybe_send_next(self) -> None:
        """Nově zařazené video (i doplněné video na šířku) dorazí samo, nejvýš jednou za SEND_INTERVAL s.

        Před odesláním doplní ztracené zprávy sad a náhledy (_repair_messages).
        """
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
        self._guard("doplnění ztracených zpráv", self._repair_messages)
        self._guard("obnova uvízlých položek", self._recover_stuck)
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

    def _recover_stuck(self, restart: bool = False) -> None:
        """Uvízlé položky dostanou tlačítko Zkusit znovu (retry nejdřív dohledá, co už na sítích je).

        restart=True (start bota): všechny bez ohledu na stáří, předchozí proces skončil. Za běhu jen starší
        než STALE_PUBLISHING: approved bez autopilota → failed (publikace nezačala), publishing → unknown.
        Schválené položky autopilota zveřejní maybe_autopublish (publikace u nich ještě nezačala).
        """
        stale_before = None if restart else _now() - STALE_PUBLISHING
        for reel in queue.by_status(self.conn, ("approved", "publishing")):
            started = reel.decided_at or reel.sent_at or reel.created_at
            if stale_before is not None and started >= stale_before:
                continue
            if reel.status == "publishing":
                if queue.transition(self.conn, reel.id, "publishing", "unknown"):
                    self._say(stuck_publishing_text(reel.id, restart), retry_buttons(reel.id))
            elif not reel.autopilot:
                # pád mezi schválením a publish.run: bez tlačítka by položka uvízla
                if queue.transition(self.conn, reel.id, "approved", "failed", error=INTERRUPTED):
                    self._say(interrupted_text(reel.id), retry_buttons(reel.id))

    def start(self) -> None:
        # jediná instance: zámek drží spojení bota po celý běh (uvolní ho až ukončení procesu)
        with self.conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (REELS_BOT_LOCK,))
            locked = cur.fetchone()[0]
        self.conn.commit()
        if not locked:
            log.error("jiná instance bota už běží (advisory lock %s), končím", REELS_BOT_LOCK)
            raise SystemExit(0)
        db.migrate(self.conn)
        if self.threads is not None and seed_threads_token(self.conn, self.threads_env_token, self.now()):
            log.info("token Threads převzat z THREADS_TOKEN")
        self._recover_stuck(restart=True)
        self._refresh_previews_for_threads()
        self._clean_public()
        # odeslání ke schválení až ve smyčce (maybe_send_next): start by jinak přidal jednotku navíc (R3)

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
                self.maybe_autopublish()
                self.maybe_events()
                self.maybe_fresh()
                self.maybe_refresh_threads_token()
                self.maybe_watch()
                self.poll_once()
                # známka života jen po průchodu bez výjimky: bot, kterému průchod padá pořád, „nežije“
                self.maybe_alive()
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
