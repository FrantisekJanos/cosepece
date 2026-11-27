"""Vlákno x_sync workeru na PC: videa, která vlastník v Telegramu označil Na X, zkopíruje do složky Pro X.

Každých INTERVAL_S s se zeptá serveru (x_pending): položky wanted zkopíruje ze složky sady
(<root>/<set_id>/variant-<n>/, přednost má video na šířku *-sirka.mp4) jako NNNN-tiskCT.mp4 a nahlásí ready, bez
videa nahlásí missing; soubory vyřízených položek (remove) ze složky smaže. Kopie jde přes .part a přejmenování,
v okně výběru souboru se tak nikdy neobjeví půlka videa.

Běží vedle generování ve vlastním vlákně (daemon) a nic z něj generování nezdrží ani neshodí: každá výjimka
se jen zaloguje (stejná chyba jednou), chyba jedné položky ostatní nezastaví a položka zůstane wanted na další běh.
Starší server akci nezná (400 Neznámá akce, 404): jedno upozornění v logu, další pokus za UNSUPPORTED_RETRY_S.
Složka: REELS_X_DIR v --env (nebo v prostředí), jinak %USERPROFILE%\\Videos\\Pro X; vlákno ji založí.
Po zkopírování otevře na Windows Průzkumníka s označeným videem (stačí ho přetáhnout do X) a serveru pošle celou
cestu, kterou bot vypíše ve zprávě; vypnout jde přes REELS_X_OPEN=0.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from typing import Callable
from pathlib import Path

from ..xpost import file_name
from .api import ApiError

log = logging.getLogger("hlidac_reels.worker.xsync")

INTERVAL_S = 60
UNSUPPORTED_RETRY_S = 3600
LANDSCAPE_SUFFIX = "-sirka.mp4"


def open_enabled(env: dict[str, str]) -> bool:
    """Otevírat Průzkumníka po zkopírování? Jen na Windows a když REELS_X_OPEN (--env, prostředí) není 0/false/ne."""
    value = (env.get("REELS_X_OPEN") or os.environ.get("REELS_X_OPEN") or "1").strip().lower()
    return os.name == "nt" and value not in ("0", "false", "ne", "no", "off")


def reveal(path: Path) -> None:
    """Otevře Průzkumníka se složkou a označeným souborem. explorer.exe vrací nenulový kód i při úspěchu,
    proto se na něj nečeká; chyba spuštění jde volajícímu (jen do logu)."""
    subprocess.Popen(["explorer.exe", f"/select,{path}"])


def default_dir(env: dict[str, str]) -> Path:
    """Složka Pro X: REELS_X_DIR z --env, pak z prostředí, jinak Videos\\Pro X v profilu uživatele."""
    value = env.get("REELS_X_DIR") or os.environ.get("REELS_X_DIR")
    if value:
        return Path(value)
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "Videos" / "Pro X"


def find_video(root: Path, set_id: int | None, variant: int | None) -> Path | None:
    """Video varianty sady na PC: na šířku, jinak na výšku (při více souborech nejnovější); None = není."""
    if set_id is None or variant is None:
        return None
    d = Path(root) / str(int(set_id)) / f"variant-{int(variant)}"
    if not d.is_dir():
        return None
    videos = [p for p in d.glob("*.mp4") if p.is_file()]
    landscape = [p for p in videos if p.name.endswith(LANDSCAPE_SUFFIX)]
    pick = landscape or [p for p in videos if not p.name.endswith(LANDSCAPE_SUFFIX)]
    return max(pick, key=lambda p: p.stat().st_mtime) if pick else None


def _copy(src: Path, dst: Path) -> None:
    part = dst.with_name(dst.stem + ".part")
    try:
        shutil.copyfile(src, part)
        os.replace(part, dst)
    finally:
        part.unlink(missing_ok=True)


def sync_once(api, root: Path, x_dir: Path, opener: Callable[[Path], None] | None = None) -> None:
    """Jeden běh: kopie čekajících položek a úklid vyřízených. Chyby jednotlivých položek jen do logu;
    chyba x_pending (síť, server) jde volajícímu. opener(cesta) = ukázat zkopírované video (Průzkumník);
    jeho chyba položku neshodí, video už je ve složce a server o něm ví."""
    data = api.x_pending()
    x_dir = Path(x_dir)
    x_dir.mkdir(parents=True, exist_ok=True)
    for item in data["wanted"]:
        try:
            reel_id = int(item["id"])
            ct = int(item["ct"]) if item.get("ct") is not None else None
            video = find_video(root, item.get("set_id"), item.get("variant"))
            if video is None:
                log.info("Pro X #%s: video na PC není", reel_id)
                api.x_ready(reel_id, "missing")
                continue
            target = x_dir / file_name(reel_id, ct)
            _copy(video, target)
            api.x_ready(reel_id, "ready", str(target))
            log.info("Pro X #%s: %s připraveno", reel_id, target.name)
            if opener is not None:
                try:
                    opener(target)
                except Exception as e:  # noqa: BLE001 — Průzkumník je jen pohodlí navíc
                    log.warning("Pro X #%s: Průzkumník se neotevřel: %s: %s", reel_id, type(e).__name__, e)
        except Exception as e:  # noqa: BLE001 — jedna položka nesmí zastavit ostatní ani vlákno
            log.warning("Pro X %s: %s: %s", item.get("id"), type(e).__name__, e)
    for reel_id in data["remove"]:
        for p in [*x_dir.glob(f"{reel_id:04d}-*.mp4"), x_dir / f"{reel_id:04d}.mp4"]:
            try:
                p.unlink(missing_ok=True)
            except OSError as e:  # soubor třeba drží otevřený prohlížeč: příště znovu
                log.warning("Pro X #%s: smazání %s selhalo: %s", reel_id, p.name, e)


class XSync:
    """Vlákno, které každých `interval` s pustí sync_once; start() a stop() volá main() workeru."""

    def __init__(self, api, root: Path, x_dir: Path, interval: float = INTERVAL_S,
                 opener: Callable[[Path], None] | None = None):
        self.api = api
        self.root = Path(root)
        self.x_dir = Path(x_dir)
        self.interval = interval
        self.opener = opener
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="reels-xsync", daemon=True)
        self._last_error: str | None = None
        self._unsupported = False

    def start(self) -> None:
        log.info("Pro X: složka %s%s", self.x_dir, ", po kopii otevřu Průzkumníka" if self.opener else "")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=10)

    def _loop(self) -> None:
        delay: float = 0
        while not self._stop.wait(delay):
            delay = self.tick()

    def tick(self) -> float:
        """Jeden běh; vrací, za kolik sekund další. Nikdy nevyhodí výjimku."""
        try:
            sync_once(self.api, self.root, self.x_dir, self.opener)
        except ApiError as e:
            if e.status in (400, 404):
                if not self._unsupported:
                    log.warning("Pro X: server akci x_pending neumí (starší verze, HTTP %s), zkusím za hodinu",
                                e.status)
                self._unsupported = True
                return UNSUPPORTED_RETRY_S
            self._log_error(f"HTTP {e.status}: {e.snippet}")
            return self.interval
        except Exception as e:  # noqa: BLE001 — síť, disk, cokoli: jen do logu, vlákno běží dál
            self._log_error(f"{type(e).__name__}: {e}")
            return self.interval
        if self._unsupported or self._last_error:
            log.info("Pro X: spojení se serverem zase funguje")
        self._unsupported = False
        self._last_error = None
        return self.interval

    def _log_error(self, text: str) -> None:
        # stejná chyba každou minutu by zahltila worker.log
        if text != self._last_error:
            log.warning("Pro X: %s", text)
        self._last_error = text
