"""Příkaz reels-generate: sada 3 variant k tisku (1× v2 z karty webu, 2× v3 z MCP) přes claude -p.

Úloha s jedním PN (/pn 2376, „Všechny zvlášť“, /pnnew) od serveru, který posílá výsledek PN: jediná varianta
`pribeh` (šablona template_pn_v1, příběh jednoho PN), formats.wants_pribeh.

Každá platná varianta dostane i video na šířku 16:9 (pro Threads), když ho šablona umí (landscape.css).
Selhání šířky variantu nevyřadí: zůstane jen na výšku a důvod je ve warnings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..card import Card
from ..media import probe
from ..media import validate as media_validate
from ..render import has_landscape, render_reel
from .claude import (DEFAULT_MODEL, EnvError, build_prompt, prepare_workdir, rotate_log, run_claude, write_focus,
                     write_published)
from .formats import PRIBEH, PRIBEH_TEMPLATE, pick_formats, wants_pribeh
from .normalize import normalize
from .validate import check_layout, validate_variant

MIN_OK = 1  # sada s jedinou platnou variantou se posílá (rozhodnutí vlastníka; nevzniklé jdou jako skipped)
NOTHING_WRITTEN = "běh modelu nic nezapsal"
BUDGET_RUNS = 4  # běhy modelu na úlohu přes všechna vydání: 1 generování + 3 cílené běhy
STATE_FILE = "state.json"
PROMPTS = ("v2.md", "v3.md", "orchestrator.md")  # návody, jejichž verze patří do otisku vstupu
# stavy variant ve state.json
MISSING, BROKEN, VALID, RENDERED = "chybi", "vadna", "platna", "vyrenderovana"
# platná karta, jejíž render RENDER_TRIES× selhal (obvykle prostředí: Chromium, ffmpeg): konečný stav varianty,
# model ji neopravuje a na render se dál nečeká; počet pokusů je v záznamu varianty (render_fails) ke kartě
RENDER_FAILED = "render_selhal"
RENDER_TRIES = 2


class Cancelled(Exception):
    """Úloha už workeru nepatří (zrušená nebo propadlé vydání, 409 na progress): generování skončí na kontrole.

    Kontrola je před každým během modelu a renderem; běžící claude -p se nepřerušuje. Stav v state.json zůstane,
    další vydání téže úlohy pokračuje.
    """


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
    runs: list[dict] = field(default_factory=list)  # běhy claude -p tohoto vydání (timeout, návratový kód)
    model_runs: int = 0  # běhy modelu úlohy přes všechna vydání (rozpočet BUDGET_RUNS)
    # platné varianty bez videa (render selhal): další vydání je jen vyrenderuje, bez běhu modelu;
    # varianta po RENDER_TRIES neúspěšných renderech (render_selhal) sem nepatří, další pokus nemá smysl
    unrendered: list[int] = field(default_factory=list)


def _append_notes(path: Path, notes: list[str]) -> None:
    """Připojí zásahy normalizace k těm z dřívějších kontrol varianty (oprava jede nad už upravenou kartou)."""
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = []
    if not isinstance(old, list):
        old = []
    path.write_text(json.dumps(old + notes, ensure_ascii=False, indent=1), encoding="utf-8")


def _fingerprints(workdir: Path, numbers: list[int]) -> dict[int, tuple[str | None, ...]]:
    """sha256 souborů card.json a caption.txt zadaných variant (None = soubor chybí)."""
    def digest(path: Path) -> str | None:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None
    return {n: tuple(digest(workdir / f"variant-{n}" / name) for name in ("card.json", "caption.txt"))
            for n in numbers}


def _check(v: Variant, workdir: Path, ct: int, reason_keys: list[str], layout: bool,
           focus_pn: list[int] | None = None, focus_info: list[dict] | None = None,
           server_card: dict | None = None, autopilot: bool = False) -> dict | None:
    """Načte, znormalizuje a ověří variantu; vrací kartu, nebo None (chyby v v.errors).

    Normalizovaná karta a popisek se zapíšou zpět do složky varianty, zásahy do `normalize.json`.
    server_card = karta tisku ze serveru (`card_v2`): ve variantě v2 z ní platí `predkladatel` a autor PN
    z titulku (`pn_autor`), ve v3 a příběhu PN pole `kdo`, viz normalize(). autopilot = úloha autopilota
    (přísnější kontrola: jen autorizované projevy, bez neověřených klubů).
    """
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
        card, caption, notes = normalize(card, v.caption, fmt=v.format, focus_info=focus_info or [],
                                         server_card=server_card, autopilot=autopilot)
        if notes:
            (d / "card.json").write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
            if caption != v.caption:
                (d / "caption.txt").write_text(caption + "\n", encoding="utf-8")
        v.caption = caption
        _append_notes(d / "normalize.json", notes)
        v.errors = validate_variant(card, v.caption, template=v.template, ct=ct, reason_keys=reason_keys,
                                    fmt=v.format, focus_pn=focus_pn, focus_info=focus_info,
                                    autopilot=autopilot)
        if not v.errors and layout:
            v.errors = check_layout(card, v.template)
    except Exception as e:  # noqa: BLE001
        v.errors = [f"kontrola selhala: {type(e).__name__}: {e}"]
    return None if v.errors else card


def _missing_authors(focus_pn: list[int] | None, focus_info: list[dict] | None) -> list[int]:
    """Zadané PN, u kterých server autora neuvádí (`osoba` chybí nebo je prázdná, nebo PN v údajích chybí)."""
    if not focus_pn:
        return []
    by_num = {str(i.get("cislo")): i for i in focus_info or [] if isinstance(i, dict)}
    return [n for n in focus_pn
            if not (isinstance(by_num.get(str(n), {}).get("osoba"), str) and by_num[str(n)]["osoba"].strip())]


def _render_landscape(v: Variant, card: dict, portrait: Path) -> None:
    """Video na šířku k platné variantě; chyba jde do v.warnings, varianta zůstane platná na výšku.

    Před renderem se zkontroluje rozvržení na šířku (bezpečná zóna, přetečení); chyba vyřadí jen šířku.
    """
    if not has_landscape(v.template):
        return
    video = portrait.with_name(portrait.stem + "-sirka.mp4")
    try:
        layout_errs = check_layout(card, v.template, orientation="landscape")
    except Exception as e:  # noqa: BLE001 — pád kontroly šířky variantu nevyřadí
        layout_errs = [f"kontrola rozvržení selhala: {type(e).__name__}: {e}"]
    if layout_errs:
        v.warnings.append("video na šířku vynecháno: rozvržení: " + "; ".join(layout_errs))
        video.unlink(missing_ok=True)  # šířka z dřívějšího renderu by ke kartě už nepatřila
        return
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


def _prompt_versions() -> dict[str, str]:
    """sha256 návodů, ze kterých model karty píše; jiná verze návodu = jiný vstup úlohy."""
    d = Path(__file__).parent / "prompts"
    return {name: hashlib.sha256((d / name).read_bytes()).hexdigest() for name in PROMPTS}


def _story_prompt_version() -> str:
    """sha256 návodu pribeh.md (jen v otisku úloh příběhu PN, ostatní úlohy jeho změna nerestartuje)."""
    return hashlib.sha256((Path(__file__).parent / "prompts" / f"{PRIBEH}.md").read_bytes()).hexdigest()


def _input_hash(card_v2: dict, focus_pn: list[int] | None, formats: list[str],
                outcome: list | None = None) -> str:
    """Otisk vstupu úlohy: karta webu, zadané PN, formáty a verze návodů (kanonický JSON).

    Příběh PN (formats == ["pribeh"]): v otisku je i návod pribeh.md a výsledek PN ze serveru (`outcome`),
    po hlasování tak úloha nepoužije kartu se starým výsledkem. Ostatní úlohy mají otisk jako dřív.
    """
    # data_date server přepočítá při každém claimu (dnešní datum): do otisku nepatří, jinak by půlnoc smazala rozdělanou práci
    card = {k: v for k, v in card_v2.items() if k != "data_date"}
    story = formats == [PRIBEH]
    data = {"card_v2": card, "focus_pn": focus_pn, "formats": formats, "prompts": _prompt_versions()}
    if story:
        data["prompts"] = {**data["prompts"], f"{PRIBEH}.md": _story_prompt_version()}
        data["outcome"] = outcome
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_state(out: Path, input_hash: str) -> dict | None:
    """Stav minulého vydání téže úlohy; None = žádný, nečitelný, nebo k jinému vstupu."""
    try:
        st = json.loads((out / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(st, dict) or st.get("input") != input_hash or not isinstance(st.get("variants"), dict)
            or not isinstance(st.get("model_runs"), int)):
        return None
    return st


def _save_state(out: Path, st: dict) -> None:
    """Zapíše state.json přes dočasný soubor (pád uprostřed zápisu nesmí stav rozbít)."""
    tmp = out / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out / STATE_FILE)


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _rel(path: Path | None, out: Path) -> str | None:
    return path.relative_to(out).as_posix() if path else None


def _resume(v: Variant, entry: dict, out: Path, layout: bool) -> dict | bool | None:
    """Obnoví variantu z minulého vydání bez modelu.

    Vrací True = hotové video (nic dalšího), kartu = platná, None = vadná, chybí nebo render selhal
    (chyby v v.errors), False = stav nesedí se soubory a variantu je nutné znovu zkontrolovat.
    Hotové video platí jen ke kartě, ze které vzniklo: změněná card.json (jiný otisk) = znovu kontrola a render.
    """
    d = out / f"variant-{v.n}"
    state = entry.get("state")
    if state in (MISSING, BROKEN, RENDER_FAILED):
        v.errors = [str(e) for e in entry.get("errors") or []] or ["varianta chybí"]
        return None
    try:
        caption = (d / "caption.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return False
    same_card = _sha(d / "card.json") == entry.get("card_sha")
    if state == RENDERED and same_card and entry.get("video") and (out / entry["video"]).is_file():
        v.caption, v.errors = caption, []
        v.video = out / entry["video"]
        land = entry.get("video_landscape")
        v.video_landscape = out / land if land and (out / land).is_file() else None
        v.warnings = [str(w) for w in entry.get("warnings") or []]
        return True
    # platná (nebo vyrenderovaná bez videa): kartu stačí načíst, pokud se od kontroly nezměnila
    if state not in (VALID, RENDERED) or not same_card:
        return False
    if layout and not entry.get("layout"):  # platná bez kontroly rozvržení (render=False) se před renderem zkontroluje
        return False
    try:
        card = json.loads((d / "card.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    v.caption, v.errors = caption, []
    return card if isinstance(card, dict) else False


def _variants_note(numbers: list[int]) -> str:
    """Poznámka k fázi: „varianta 3“, „varianty 2, 3“."""
    nums = ", ".join(str(n) for n in numbers)
    return f"varianta {nums}" if len(numbers) == 1 else f"varianty {nums}"


def _render_variant(v: Variant, card: dict, out: Path, ct: int, landscape: bool) -> None:
    """Vyrenderuje platnou variantu na výšku (a na šířku); chyba na výšku jde do v.errors."""
    name = "v2" if v.format is None else v.format
    video = out / f"variant-{v.n}" / f"reel-{ct}-{name}.mp4"
    v.warnings = []
    try:
        render_reel(Card.from_json(card), video, template=v.template)
        v.errors = media_validate(probe(video))
    except Exception as e:  # noqa: BLE001 — chyba renderu vyřadí jen tuto variantu
        v.errors = [f"render selhal: {e}"]
    if v.errors:
        return
    v.video = video
    if landscape:
        _render_landscape(v, card, video)


def generate(*, ct: int, card_v2: dict, reason_keys: list[str], has_amendments: bool, out: Path, mcp_config: Path,
             model: str = DEFAULT_MODEL, hint: str | None = None, render: bool = True,
             timeout_s: int = 1200, landscape: bool = True,
             published: list[dict] | None = None, focus_pn: list[int] | None = None,
             focus_info: list[dict] | None = None, autopilot: bool = False,
             on_stage: Callable[[str, str | None], None] | None = None,
             should_stop: Callable[[], bool] | None = None) -> GenResult:
    """Sada variant k tisku: 1 = v2, další = v3 podle `formats` (obvykle 3, u /pn bez textu PN a u autopilota 2).

    focus_pn = video jen o těchto PN (/pn), focus_info = jejich údaje z claimu.
    Stav úlohy je v `out/state.json`. Další vydání se stejným otiskem vstupu pokračuje: hotová videa
    se nerenderují znovu (jen ke stejné kartě), platné varianty se jen vyrenderují (nejvýš RENDER_TRIES pokusů
    na kartu, pak render_selhal), vadné jdou do cíleného běhu se svými chybami.
    Rozpočet BUDGET_RUNS běhů modelu platí přes všechna vydání. Jiný vstup = složka se vyčistí.
    Chyba prostředí claude -p (limit, přihlášení) bez zapsaných souborů vyhodí EnvError; rozpočet nespálí
    a hotová práce zůstane ve stavu pro další vydání.
    on_stage(fáze, poznámka) hlásí fázi práce: ("model", None) před prvním během modelu, ("kontrola", None)
    po každém běhu, ("oprava", "varianty 2, 3") před cíleným během, ("render", "varianta N") před renderem.
    should_stop() se ptá před každým během modelu a renderem; True = výjimka Cancelled, stav zůstane.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rotate_log(out)  # každé vydání píše do nového claude.log, log minulého vydání zůstane jako claude-<n>.log
    focus_pn = sorted({int(n) for n in focus_pn}) if focus_pn else None
    # server pole has_text posílá u každého zadaného PN (F4); chybějící pole (starší server) = text je k dispozici
    has_text = all(i.get("has_text", True) for i in focus_info or [] if isinstance(i, dict))
    story = wants_pribeh(focus_pn, focus_info)
    # formáty jsou v otisku vstupu: přepnutí autopilota úlohu správně začne znovu
    if story:  # příběh jednoho PN: jediná varianta, ručně i s autopilotem
        formats = [PRIBEH]
    else:
        formats = pick_formats(reason_keys, has_amendments, hint, focus=focus_pn is not None, has_text=has_text,
                               autopilot=autopilot)
    no_author = _missing_authors(focus_pn, focus_info) if autopilot else []
    if no_author:
        # autopilot (bez lidské kontroly): PN bez autora v evidenci psp.cz nejde podepsat ani „podle evidence“;
        # video se nevyrobí a model se vůbec nespouští (rozhodnutí majitele 2026-11-28)
        why = (f"autopilot: autor PN {', '.join(map(str, no_author))} v evidenci psp.cz chybí, "
               "video se nevyrobí")
        planned = ([Variant(1, PRIBEH_TEMPLATE, PRIBEH)] if story else
                   [Variant(1, "template_v2", None)] + [Variant(n, "template_v3", f) for n, f in enumerate(formats, 2)])
        for v in planned:
            v.errors = [why]
        return GenResult(ct=ct, formats=formats, variants=planned, ok=False)
    outcome = [i.get("vysledek") for i in focus_info or [] if isinstance(i, dict)] if story else None
    input_hash = _input_hash(card_v2, focus_pn, formats, outcome)
    st = _load_state(out, input_hash)
    if st is None:  # nový vstup: výstup minulého běhu se nesmí vydávat za nový (smaže i normalize.json)
        for old in out.glob("variant-*"):
            shutil.rmtree(old, ignore_errors=True)
        st = {"input": input_hash, "model_runs": 0, "variants": {}}
    (out / "vysledek.json").unlink(missing_ok=True)
    (out / "tisk.json").write_text(json.dumps({"ct": ct, "reason_keys": reason_keys, "formats": formats,
                                               "focus_pn": focus_pn}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "card_v2.json").write_text(json.dumps(card_v2, ensure_ascii=False, indent=1), encoding="utf-8")
    prepare_workdir(out, ct)
    write_published(out, published or [])
    write_focus(out, focus_pn, focus_info or [], outcome=story)
    if story:
        variants = [Variant(1, PRIBEH_TEMPLATE, PRIBEH)]
    else:
        variants = ([Variant(1, "template_v2", None)]
                    + [Variant(n, "template_v3", f) for n, f in enumerate(formats, 2)])
    entries: dict[str, dict] = st["variants"]
    for v in variants:
        entries.setdefault(str(v.n), {"state": MISSING, "errors": []})
    _save_state(out, st)

    runs: list[dict] = []
    cards: dict[int, dict | None] = {}  # platné karty, které čekají na render
    tried: set[int] = set()  # varianty, jejichž render v tomto vydání selhal (nezkoušet znovu)

    def render_fails(n: int) -> int:
        fails = entries.get(str(n), {}).get("render_fails")
        return fails if isinstance(fails, int) and fails >= 0 else 0

    def save(v: Variant, state: str, fails: int | None = None) -> None:
        """Zapíše záznam varianty; fails = nový počet neúspěšných renderů.

        Bez fails se počet přenese ze starého záznamu, jen když je karta stejná (nová karta začíná od nuly).
        """
        d = out / f"variant-{v.n}"
        sha = _sha(d / "card.json")
        if fails is None:
            fails = render_fails(v.n) if entries.get(str(v.n), {}).get("card_sha") == sha else 0
        entries[str(v.n)] = {"state": state, "errors": list(v.errors), "card_sha": sha,
                             "layout": render, "video": _rel(v.video, out),
                             "video_landscape": _rel(v.video_landscape, out), "warnings": list(v.warnings),
                             "render_fails": fails}
        _save_state(out, st)

    def stage(name: str, note: str | None = None) -> None:
        if on_stage is not None:
            on_stage(name, note)

    def checkpoint() -> None:
        """Před během modelu a renderem: úloha zrušená (nebo propadlé vydání) skončí, stav zůstane uložený."""
        if should_stop is not None and should_stop():
            raise Cancelled()

    def model_run(prompt: str, numbers: list[int], name: str, note: str | None = None) -> set[int]:
        """Jeden běh claude -p (fáze name); vrací čísla variant, jejichž card.json ani caption.txt se nezměnily.

        Chyba prostředí (limit, přihlášení) bez jediného zapsaného souboru ukončí generování výjimkou
        EnvError: oprava by skončila stejně a spálila by pokus. Takový běh se do rozpočtu nepočítá.
        Po běhu ohlásí fázi „kontrola“ (kontrola variant následuje vždy).
        """
        checkpoint()
        stage(name, note)
        before = _fingerprints(out, numbers)
        r = run_claude(prompt, workdir=out, mcp_config=mcp_config, model=model, timeout_s=timeout_s)
        runs.append({"ok": r.ok, "returncode": r.returncode, "timed_out": r.timed_out,
                     "kind": r.kind, "result": r.result})
        after = _fingerprints(out, numbers)
        unchanged = {n for n in numbers if after[n] == before[n]}
        if r.kind == "env" and len(unchanged) == len(numbers):
            raise EnvError(r.result or f"claude -p skončil s kódem {r.returncode}")
        st["model_runs"] += 1
        _save_state(out, st)
        stage("kontrola")
        return unchanged

    def check(v: Variant, unchanged: set[int] | None = None) -> None:
        """Kontrola varianty (po běhu modelu i bez něj) a zápis stavu."""
        cards[v.n] = card = _check(v, out, ct, reason_keys, render, focus_pn, focus_info, card_v2, autopilot)
        if card is not None:
            save(v, VALID)
            return
        if unchanged is not None:
            if v.n in unchanged:  # rc 0 bez zápisu (např. nepovolený nástroj) nesmí vypadat jako chyba obsahu
                v.errors.insert(0, NOTHING_WRITTEN)
            if runs[-1]["timed_out"]:
                v.errors.insert(0, "běh claude -p vypršel (timeout)")
        d = out / f"variant-{v.n}"
        exists = (d / "card.json").is_file() and (d / "caption.txt").is_file()
        save(v, BROKEN if exists else MISSING)

    def render_pending() -> None:
        """Vyrenderuje platné karty, které ještě video nemají; vždy jen jednu najednou.

        Render jedné karty se zkouší nejvýš jednou za vydání a RENDER_TRIES× celkem (render_fails přes vydání);
        pak varianta skončí jako render_selhal s poslední chybou.
        """
        for v in variants:
            card = cards.get(v.n)
            if card is None or not render or v.n in tried:
                continue
            checkpoint()
            stage("render", f"varianta {v.n}")
            tried.add(v.n)
            _render_variant(v, card, out, ct, landscape)
            if not v.errors:
                cards[v.n] = None
                save(v, RENDERED)
                continue
            fails = render_fails(v.n) + 1
            if fails < RENDER_TRIES:  # stav zůstane platná: další vydání zkusí render znovu, bez běhu modelu
                save(v, VALID, fails)
                continue
            # opakovaný pád renderu (prostředí) nesmí pálit běhy modelu ani nechat úlohu čekat na další pokus
            last = "; ".join(e.removeprefix("render selhal: ") for e in v.errors)
            v.errors = [f"render selhal {RENDER_TRIES}×: {last}"]
            cards[v.n] = None
            save(v, RENDER_FAILED, fails)

    def recheck_changed(skip: set[int]) -> None:
        """Platné a vyrenderované varianty (mimo skip), jejichž card.json se od kontroly změnil: opravný běh nad
        jinými variantami ho může přepsat. Znovu kontrola; platnou pak vyrenderuje render_pending (nová karta =
        render_fails od nuly, pokus i v tomto vydání). Staré video ke kartě nepatří, worker by jinak nahrál novou
        kartu se starým videem."""
        for v in variants:
            entry = entries[str(v.n)]
            if v.n in skip or entry["state"] not in (VALID, RENDERED):
                continue
            if _sha(out / f"variant-{v.n}" / "card.json") == entry.get("card_sha"):
                continue
            v.video = v.video_landscape = None
            v.warnings = []
            tried.discard(v.n)
            check(v)

    def broken() -> dict[int, list[str]]:
        """Varianty pro opravný běh modelu: chybí nebo vadná karta (render_selhal se modelem neopravuje)."""
        return {v.n: v.errors or ["varianta chybí"] for v in variants
                if entries[str(v.n)]["state"] in (MISSING, BROKEN)}

    if st["model_runs"] == 0:  # první generování celé sady
        unchanged = model_run(build_prompt(ct=ct, formats=formats, workdir=out), [v.n for v in variants], "model")
        for v in variants:
            check(v, unchanged)
    else:  # pokračování: obnovit, co minulá vydání udělala
        for v in variants:
            got = _resume(v, entries[str(v.n)], out, render)
            if got is False:
                check(v)
            elif isinstance(got, dict):
                cards[v.n] = got
            elif got is None:
                cards[v.n] = None
    render_pending()

    while (bad := broken()) and st["model_runs"] < BUDGET_RUNS:  # cílené běhy jen nad stále vadnými
        unchanged = model_run(build_prompt(ct=ct, formats=formats, workdir=out, repair=bad), sorted(bad),
                              "oprava", _variants_note(sorted(bad)))
        for v in variants:
            if v.n in bad:
                check(v, unchanged)
        recheck_changed(set(bad))
        render_pending()

    for v in variants:
        state = entries[str(v.n)]["state"]
        v.ok = state == RENDERED or (state == VALID and not render)
    unrendered = [v.n for v in variants if render and entries[str(v.n)]["state"] == VALID]
    result = GenResult(ct=ct, formats=formats, variants=variants,
                       ok=sum(v.ok for v in variants) >= MIN_OK, runs=runs, model_runs=st["model_runs"],
                       unrendered=unrendered)
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
