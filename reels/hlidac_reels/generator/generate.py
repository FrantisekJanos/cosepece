"""Příkaz reels-generate: sada 3 variant k tisku (1× v2 z karty webu, 2× v3 z MCP) přes claude -p.

Každá platná varianta dostane i video na šířku 16:9 (pro Threads), když ho šablona umí (landscape.css).
Selhání šířky variantu nevyřadí: zůstane jen na výšku a důvod je ve warnings.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..card import Card
from ..media import probe
from ..media import validate as media_validate
from ..render import has_landscape, render_reel
from .claude import DEFAULT_MODEL, build_prompt, prepare_workdir, run_claude, write_focus, write_published
from .formats import pick_formats
from .validate import check_layout, validate_variant

MIN_OK = 2


@dataclass
class Variant:
    n: int
    template: str
    format: str | None
    ok: bool = False
    errors: list[str] = field(default_factory=list)
    video: Path | None = None
    caption: str = ""
    video_landscape: Path | None = None  # 16:9; None = šablona šířku neumí nebo render či kontrola selhaly
    warnings: list[str] = field(default_factory=list)  # chyby, které variantu nevyřadí (šířka)


@dataclass
class GenResult:
    ct: int
    formats: list[str]
    variants: list[Variant]
    ok: bool
    runs: list[dict] = field(default_factory=list)  # stav běhů claude -p (timeout, návratový kód)


def _check(v: Variant, workdir: Path, ct: int, reason_keys: list[str], layout: bool,
           focus_pn: list[int] | None = None) -> dict | None:
    """Načte a ověří variantu; vrací kartu, nebo None (chyby v v.errors)."""
    d = workdir / f"variant-{v.n}"
    v.errors = []
    try:
        card = json.loads((d / "card.json").read_text(encoding="utf-8"))
        v.caption = (d / "caption.txt").read_text(encoding="utf-8").strip()
    except FileNotFoundError as e:
        v.errors = [f"chybí soubor {Path(e.filename).name}"]
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        v.errors = [f"card.json není platný JSON: {e}"]
        return None
    if not isinstance(card, dict):
        v.errors = ["card.json není objekt"]
        return None
    try:  # pád kontroly (nečekaný tvar karty, chybějící Chromium) vyřadí jen tuto variantu
        v.errors = validate_variant(card, v.caption, template=v.template, ct=ct, reason_keys=reason_keys,
                                    fmt=v.format, focus_pn=focus_pn)
        if not v.errors and layout:
            v.errors = check_layout(card, v.template)
    except Exception as e:  # noqa: BLE001
        v.errors = [f"kontrola selhala: {type(e).__name__}: {e}"]
    return None if v.errors else card


def _render_landscape(v: Variant, card: dict, portrait: Path) -> None:
    """Video na šířku k platné variantě; chyba jde do v.warnings, varianta zůstane platná na výšku."""
    if not has_landscape(v.template):
        return
    video = portrait.with_name(portrait.stem + "-sirka.mp4")
    try:
        render_reel(Card.from_json(card), video, template=v.template, orientation="landscape")
        errs = media_validate(probe(video), orientation="landscape")
    except Exception as e:  # noqa: BLE001 — šířka je navíc, její chyba variantu nevyřadí
        errs = [f"render selhal: {e}"]
    if errs:
        v.warnings.append(f"video na šířku vynecháno: {'; '.join(errs)}")
        video.unlink(missing_ok=True)
        return
    v.video_landscape = video


def generate(*, ct: int, card_v2: dict, reason_keys: list[str], has_amendments: bool, out: Path, mcp_config: Path,
             model: str = DEFAULT_MODEL, hint: str | None = None, render: bool = True,
             timeout_s: int = 1200, landscape: bool = True,
             published: list[dict] | None = None, focus_pn: list[int] | None = None,
             focus_info: list[dict] | None = None) -> GenResult:
    """Sada 3 variant k tisku; focus_pn = video jen o těchto PN (/pn), focus_info = jejich údaje z claimu."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("variant-*"):  # výstup minulého běhu se nesmí vydávat za nový
        shutil.rmtree(old, ignore_errors=True)
    (out / "vysledek.json").unlink(missing_ok=True)
    focus_pn = sorted({int(n) for n in focus_pn}) if focus_pn else None
    formats = pick_formats(reason_keys, has_amendments, hint, focus=focus_pn is not None)
    (out / "tisk.json").write_text(json.dumps({"ct": ct, "reason_keys": reason_keys, "formats": formats,
                                               "focus_pn": focus_pn}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "card_v2.json").write_text(json.dumps(card_v2, ensure_ascii=False, indent=1), encoding="utf-8")
    prepare_workdir(out, ct)
    write_published(out, published or [])
    write_focus(out, focus_pn, focus_info or [])
    variants = [Variant(1, "template_v2", None), Variant(2, "template_v3", formats[0]),
                Variant(3, "template_v3", formats[1])]

    runs: list[dict] = []

    def note_run(r) -> None:
        runs.append({"ok": r.ok, "returncode": r.returncode, "timed_out": r.timed_out})

    def with_timeout(v: Variant) -> None:
        if runs[-1]["timed_out"] and v.errors:
            v.errors.insert(0, "běh claude -p vypršel (timeout)")

    note_run(run_claude(build_prompt(ct=ct, formats=formats, workdir=out), workdir=out, mcp_config=mcp_config,
                        model=model, timeout_s=timeout_s))
    cards = {v.n: _check(v, out, ct, reason_keys, render, focus_pn) for v in variants}
    for v in variants:
        with_timeout(v)
    broken = {v.n: v.errors for v in variants if cards[v.n] is None}
    if broken:  # jedna oprava jen vadných variant, pak vypadnou
        note_run(run_claude(build_prompt(ct=ct, formats=formats, workdir=out, repair=broken), workdir=out,
                            mcp_config=mcp_config, model=model, timeout_s=timeout_s))
        for v in variants:
            if v.n in broken:
                cards[v.n] = _check(v, out, ct, reason_keys, render, focus_pn)
                with_timeout(v)

    for v in variants:  # render vždy jen jeden najednou
        card = cards[v.n]
        if card is None:
            continue
        if render:
            name = "v2" if v.format is None else v.format
            video = out / f"variant-{v.n}" / f"reel-{ct}-{name}.mp4"
            try:
                render_reel(Card.from_json(card), video, template=v.template)
                v.errors = media_validate(probe(video))
            except Exception as e:  # noqa: BLE001 — chyba renderu vyřadí jen tuto variantu
                v.errors = [f"render selhal: {e}"]
            if v.errors:
                continue
            v.video = video
            if landscape:
                _render_landscape(v, card, video)
        v.ok = True

    result = GenResult(ct=ct, formats=formats, variants=variants,
                       ok=sum(v.ok for v in variants) >= MIN_OK, runs=runs)
    data = asdict(result)
    for item in data["variants"]:
        item["video"] = str(item["video"]) if item["video"] else None
        item["video_landscape"] = str(item["video_landscape"]) if item["video_landscape"] else None
    (out / "vysledek.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sada 3 variant Reels k tisku přes claude -p (předplatné).")
    ap.add_argument("--ct", type=int, required=True)
    ap.add_argument("--card-v2", type=Path, required=True, help="karta tisku z webu (cards-export.php)")
    ap.add_argument("--reasons", default="", help="klíče důvodů skóre seřazené podle váhy, čárkou")
    ap.add_argument("--has-amendments", action="store_true", help="návrh má novelizační body „nahrazuje se“")
    ap.add_argument("--format", dest="hint", choices=["cesta", "pn", "zmena", "rozprava"])
    ap.add_argument("--pn", default="", help="video jen o těchto PN tisku, čísla čárkou (jako /pn)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--mcp-config", type=Path, required=True, help="JSON s MCP serverem cosepece (mimo repo)")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--no-landscape", action="store_true", help="bez videa na šířku 16:9")
    ap.add_argument("--timeout", type=int, default=1200, help="s na jeden běh claude -p")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        card_v2 = json.loads(args.card_v2.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"chybná karta v2: {e}", file=sys.stderr)
        return 1
    if not args.mcp_config.is_file():
        print(f"chybí konfigurace MCP: {args.mcp_config}", file=sys.stderr)
        return 1
    reasons = [k for k in args.reasons.split(",") if k]
    try:
        focus = [int(n) for n in args.pn.split(",") if n.strip()] or None
    except ValueError:
        print(f"chybné --pn: {args.pn}", file=sys.stderr)
        return 1
    r = generate(ct=args.ct, card_v2=card_v2, reason_keys=reasons, has_amendments=args.has_amendments,
                 out=args.out, mcp_config=args.mcp_config, model=args.model, hint=args.hint,
                 render=not args.no_render, timeout_s=args.timeout, landscape=not args.no_landscape,
                 focus_pn=focus)
    for v in r.variants:
        print(f"varianta {v.n} ({v.template} {v.format or ''}): {'OK' if v.ok else 'CHYBA'} "
              f"{'; '.join(v.errors + v.warnings)}")
    return 0 if r.ok else 2


if __name__ == "__main__":
    sys.exit(main())
