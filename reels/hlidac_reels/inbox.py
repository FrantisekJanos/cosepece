"""Převzetí sady variant z mezisložky (zapisuje PHP API) do fronty reel_queue.

Varianta může mít i video na šířku 16:9 (variant-<n>-sirka.mp4, v manifestu "landscape": true), které dostane
Threads. Vadná šířka variantu nevyřadí: položka vznikne jen s videem na výšku a důvod je v IngestResult.warnings.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import media, queue
from .enqueue import CAPTION_LIMIT
from .generator.formats import FORMATS

log = logging.getLogger(__name__)

MAX_VIDEO_BYTES = 30 * 1024 * 1024
MIN_VALID = 2
_SOURCES = ("manual", "auto_pick", "fresh")
_TEMPLATES = ("template_v2", "template_v3")
_ERROR_LIMIT = 500


@dataclass
class IngestResult:
    job_id: int
    reel_ids: list[int] = field(default_factory=list)
    rejected: dict[int, list[str]] = field(default_factory=dict)  # varianta -> chyby
    ok: bool = False
    warnings: dict[int, list[str]] = field(default_factory=dict)  # varianta -> proč nemá video na šířku


class _BadManifest(Exception):
    pass


def _parse_manifest(path: Path) -> dict:
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
        job_id, ct, source = m["job_id"], m.get("ct"), m["source"]
        if isinstance(job_id, bool) or not isinstance(job_id, int):
            raise ValueError("job_id")
        if source not in _SOURCES:
            raise ValueError(f"source {source!r}")
        if ct is not None and (isinstance(ct, bool) or not isinstance(ct, int)):
            raise ValueError("ct")
        variants = m["variants"]
        if not isinstance(variants, list) or not variants:
            raise ValueError("variants")
        seen = set()
        for v in variants:
            n = v["n"]
            if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 3 or n in seen:
                raise ValueError(f"n {n!r}")
            seen.add(n)
            if v["template"] not in _TEMPLATES or not isinstance(v["caption"], str):
                raise ValueError("template/caption")
            if v.get("format") is not None and not isinstance(v["format"], str):
                raise ValueError("format")
            if not isinstance(v.get("landscape", False), bool):
                raise ValueError("landscape")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise _BadManifest(f"neplatný manifest: {type(e).__name__}: {e}") from e
    return m


def _job_status(conn, job_id: int) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM reel_jobs WHERE id=%s", (job_id,))
        r = cur.fetchone()
    conn.commit()
    return r[0] if r else None


def _fail_job(conn, job_id: int, error: str) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE reel_jobs SET status='failed', error=%s, finished_at=now() "
                    "WHERE id=%s AND status IN ('queued','claimed','done')", (error[:_ERROR_LIMIT], job_id))
    conn.commit()


def _copy_video(src: Path, digest: str, media_dir: Path) -> tuple[Path, bool]:
    """Zkopíruje do <media>/videos/<sha>.mp4 (tmp + os.replace); vrací cestu a zda soubor vznikl nově."""
    target_dir = media_dir / "videos"
    target = target_dir / f"{digest}.mp4"
    target_dir.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return target, False
    tmp = target.with_suffix(".tmp")
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return target, True


def _sha_taken(conn, digest: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM reel_queue WHERE video_sha256=%s", (digest,))
        r = cur.fetchone()
    conn.commit()
    return r is not None


def _check_variant(conn, folder: Path, v: dict, seen_sha: set[str]) -> tuple[list[str], str | None]:
    errs: list[str] = []
    src = folder / f"variant-{v['n']}.mp4"
    caption = v["caption"].strip()
    fmt = v.get("format")
    if v["template"] == "template_v3":
        if fmt not in FORMATS:
            errs.append(f"formát {fmt!r} není z {', '.join(FORMATS)}")
    elif fmt is not None:
        errs.append("template_v2 nesmí mít formát")
    if not caption:
        errs.append("popisek je prázdný")
    elif len(caption) > CAPTION_LIMIT:
        errs.append(f"popisek má {len(caption)} znaků, limit je {CAPTION_LIMIT}")
    if not src.is_file():
        errs.append("soubor videa chybí")
    else:
        size = src.stat().st_size
        if size > MAX_VIDEO_BYTES:
            errs.append(f"video má {size / 1024 / 1024:.1f} MB, limit je 30 MB")
        try:
            errs += media.validate(media.probe(src))
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            errs.append(f"kontrola videa selhala: {e}")
    if errs:
        return errs, None
    digest = media.sha256(src)
    if digest in seen_sha or _sha_taken(conn, digest):
        return ["toto video už ve frontě je"], None
    return [], digest


def _check_landscape(conn, folder: Path, v: dict, seen_sha: set[str]) -> tuple[list[str], str | None]:
    """Video na šířku varianty: (varování, otisk). Bez šířky v manifestu ([], None)."""
    if not v.get("landscape"):
        return [], None
    src = folder / f"variant-{v['n']}-sirka.mp4"
    if not src.is_file():
        return ["soubor videa na šířku chybí"], None
    errs: list[str] = []
    size = src.stat().st_size
    if size > MAX_VIDEO_BYTES:
        errs.append(f"video na šířku má {size / 1024 / 1024:.1f} MB, limit je 30 MB")
    try:
        errs += media.validate(media.probe(src), orientation="landscape")
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        errs.append(f"kontrola videa na šířku selhala: {e}")
    if errs:
        return errs, None
    digest = media.sha256(src)
    if digest in seen_sha or queue.sha_in_use(conn, digest):
        return ["toto video na šířku už ve frontě je"], None
    return [], digest


def _ingest_one(conn, folder: Path, media_dir: Path, m: dict) -> IngestResult:
    job_id = m["job_id"]
    res = IngestResult(job_id)
    good: list[tuple[dict, str, str | None]] = []
    seen_sha: set[str] = set()
    for v in sorted(m["variants"], key=lambda x: x["n"]):
        errs, digest = _check_variant(conn, folder, v, seen_sha)
        if errs:
            res.rejected[v["n"]] = errs
            continue
        seen_sha.add(digest)
        warns, land = _check_landscape(conn, folder, v, seen_sha)
        if warns:
            res.warnings[v["n"]] = warns
            log.warning("sada %s, varianta %s: video na šířku vynecháno: %s", job_id, v["n"], "; ".join(warns))
        if land:
            seen_sha.add(land)
        good.append((v, digest, land))

    created: list[Path] = []
    if len(good) >= MIN_VALID:
        try:
            rows = []
            for v, digest, land in good:
                target, new = _copy_video(folder / f"variant-{v['n']}.mp4", digest, media_dir)
                if new:
                    created.append(target)
                land_target = None
                if land:
                    land_target, new = _copy_video(folder / f"variant-{v['n']}-sirka.mp4", land, media_dir)
                    if new:
                        created.append(land_target)
                rows.append((v, target, digest, land_target, land))
            try:
                for v, target, digest, land_target, land in rows:
                    fmt = v["format"] if v["template"] == "template_v3" else "v2"
                    res.reel_ids.append(queue.add(
                        conn, video_path=str(target), video_sha256=digest, caption=v["caption"].strip(),
                        ct=m.get("ct"), format=fmt, source=m["source"], set_id=job_id,
                        variant=v["n"], template=v["template"],
                        video_landscape_path=str(land_target) if land_target else None,
                        video_landscape_sha256=land, commit=False))
                conn.commit()  # celá sada v jedné transakci
            except BaseException:
                conn.rollback()
                raise
            res.ok = True
        except Exception as e:  # noqa: BLE001 - sada se nesmí uložit napůl
            log.exception("převzetí sady %s selhalo", job_id)
            res.reel_ids.clear()
            res.rejected.setdefault(0, []).append(f"zápis sady selhal: {e}")
    if not res.ok:
        for t in created:  # po rollbacku nezůstanou osiřelé soubory
            t.unlink(missing_ok=True)
        reasons = "; ".join(f"varianta {n}: {', '.join(e)}" for n, e in sorted(res.rejected.items()))
        _fail_job(conn, job_id, f"méně než {MIN_VALID} platné varianty. {reasons}")
    return res


def ingest_all(conn, inbox_dir: Path, media_dir: Path) -> list[IngestResult]:
    """Převezme hotové sady (složky s manifest.json a úlohou ve stavu done)."""
    inbox_dir, media_dir = Path(inbox_dir), Path(media_dir)
    results: list[IngestResult] = []
    if not inbox_dir.is_dir():
        return results
    for folder in sorted(p for p in inbox_dir.iterdir() if p.is_dir()):
        manifest = folder / "manifest.json"
        if not manifest.is_file():
            continue  # napůl zapsaná sada: čeká se na manifest
        try:
            m = _parse_manifest(manifest)
        except _BadManifest as e:
            log.warning("%s: %s", folder.name, e)
            if folder.name.isdigit():
                _fail_job(conn, int(folder.name), str(e))
                results.append(IngestResult(int(folder.name)))
            shutil.rmtree(folder, ignore_errors=True)
            continue
        job_id = m["job_id"]
        if str(job_id) != folder.name:
            log.warning("%s: job_id %s neodpovídá složce", folder.name, job_id)
            if folder.name.isdigit():
                _fail_job(conn, int(folder.name), f"neplatný manifest: job_id {job_id} neodpovídá složce")
                results.append(IngestResult(int(folder.name)))
            shutil.rmtree(folder, ignore_errors=True)
            continue
        status = _job_status(conn, job_id)
        if status in ("queued", "claimed"):
            continue  # PHP možná ztratil nárok; další claim složku přepíše
        if status != "done":
            log.info("%s: úloha %s je %s, složka se maže", folder.name, job_id, status)
            shutil.rmtree(folder, ignore_errors=True)
            continue
        if queue.set_items(conn, job_id):
            # sada už je ve frontě (pád mezi commitem a smazáním složky): jen úklid, jinak by duplicity
            # shodily hotovou úlohu na failed a bot by hlásil falešnou chybu
            log.info("%s: sada %s už je převzatá, složka se maže", folder.name, job_id)
            shutil.rmtree(folder, ignore_errors=True)
            continue
        results.append(_ingest_one(conn, folder, media_dir, m))
        shutil.rmtree(folder, ignore_errors=True)
    return results
