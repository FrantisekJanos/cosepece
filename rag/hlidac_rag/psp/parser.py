"""Parser stránek stenoprotokolů psp.cz (část sNNNTTT.htm, index období).

Stránky jsou ve windows-1250. Obsah části leží mezi značkami <!-- ex --> a <!-- sy -->.
Řečník začíná odkazem <b><a href="..." id="r<N>">Role Jméno</a></b>: ...
Text před první kotvou patří řečníkovi z předchozí části (pokračování projevu).
"""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import date

MONTHS = {
    "ledna": 1, "února": 2, "března": 3, "dubna": 4, "května": 5, "června": 6,
    "července": 7, "srpna": 8, "září": 9, "října": 10, "listopadu": 11, "prosince": 12,
}

RE_CONTENT = re.compile(r"<!-- ex -->(.*?)<!-- sy -->", re.S)
RE_STATUS = re.compile(r'<p class="status[^"]*">(.*?)</p', re.S)
RE_TIME = re.compile(r"<!-- sttm -->\((\d{1,2}[.:]\d{2}) hodin\)<!-- ettm -->")
RE_DATE = re.compile(r'<p class="date"><a href="(\d+)-(\d+)\.html"[^>]*>(.*?)</a>', re.S)
RE_NEXT = re.compile(r'<a href="s(\d{3})(\d{3})\.htm" class="next">')
RE_SPEAKER = re.compile(
    r'<b>\s*<a href="([^"]*)" id="r(\d+)"[^>]*>(.*?)</a>\s*</b>\s*:?', re.S
)
RE_OSOBA = re.compile(r"detail\.sqw\?id=(\d+)")
RE_VOTE = re.compile(r"hlasy\.sqw\?G=(\d+)", re.I)
RE_HEADING = re.compile(r"<p align=\"?center\"?>\s*<b>(.*?)</b>\s*</p>", re.S | re.I)
RE_HEADING_CT = re.compile(r"historie\.sqw\?T=(\d+)&(?:amp;)?O=(\d+)", re.I)
RE_HEADING_BOD = re.compile(r"^\s*\d+\.\s")  # nadpis bodu pořadu začíná číslem bodu („203. Ústní interpelace“)
RE_CONTINUES = re.compile(r"^\s*\(pokračuje [^)]*\)\s*", re.S)
RE_TAG = re.compile(r"<[^>]+>")
RE_BLOCK_END = re.compile(r"</p\s*>|<br\s*/?>|</div\s*>", re.I)


def page_text(raw: bytes) -> str:
    """Dekóduje stránku psp.cz (windows-1250)."""
    return raw.decode("cp1250", errors="replace")


def content_hash(fragment: str) -> str:
    return hashlib.sha256(fragment.encode("utf-8")).hexdigest()


def to_plain(fragment: str) -> str:
    """HTML fragment na prostý text s odstavci oddělenými prázdným řádkem."""
    s = RE_BLOCK_END.sub("\n", fragment)
    s = RE_TAG.sub("", s)
    s = html.unescape(s).replace("\xa0", " ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in s.split("\n")]
    paras = [ln for ln in lines if ln]
    return "\n\n".join(paras)


@dataclass
class Segment:
    """Úsek textu jedné části: aname=None je pokračování projevu z předchozí části."""
    aname: int | None
    href: str = ""
    label: str = ""
    id_osoba: int | None = None
    text: str = ""
    votes: list[int] = field(default_factory=list)
    # čísla tisků z nadpisů bodů v tomto úseku (v pořadí výskytu); 0 = nadpis bodu bez tisku (interpelace, volba)
    headings: list[int] = field(default_factory=list)


@dataclass
class Part:
    status_text: str | None
    authorized: bool
    time: str | None
    datum: date | None
    schuze: int | None
    jd: int | None
    next_turn: int | None
    fragment: str
    hash: str
    segments: list[Segment]


def parse_cz_date(s: str) -> date | None:
    s = html.unescape(s).replace("\xa0", " ")
    m = re.search(r"(\d{1,2})\.\s*(\w+)\s+(\d{4})", s)
    if not m or m.group(2) not in MONTHS:
        return None
    return date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)))


def _segment_from(fragment: str, aname: int | None, href: str = "", label: str = "") -> Segment:
    headings: list[int] = []

    # nadpis bodu nahradit čitelným řádkem [194. Název /sněmovní tisk 280/] a zapamatovat si tisk;
    # číslovaný nadpis bez odkazu na tisk (bod bez tisku) se zapamatuje jako 0, nadpis bez čísla bodu vůbec
    def heading_repl(m: re.Match) -> str:
        inner = m.group(1)
        plain = to_plain(inner).replace("\n\n", " ")
        ct = RE_HEADING_CT.search(inner)
        if ct:
            headings.append(int(ct.group(1)))
        elif RE_HEADING_BOD.match(plain):
            headings.append(0)
        return "<p>[" + plain + "]</p>"

    text = to_plain(RE_HEADING.sub(heading_repl, fragment))
    if aname is None:
        text = RE_CONTINUES.sub("", text)
    osoba = RE_OSOBA.search(href)
    return Segment(
        aname=aname,
        href=href,
        label=to_plain(label).replace("\n\n", " "),
        id_osoba=int(osoba.group(1)) if osoba else None,
        text=text,
        votes=[int(v) for v in RE_VOTE.findall(fragment)],
        headings=headings,
    )


def parse_part(raw_html: str) -> Part:
    m = RE_CONTENT.search(raw_html)
    if not m:
        raise ValueError("stránka nemá značky <!-- ex --> / <!-- sy -->")
    fragment = m.group(1)
    st = RE_STATUS.search(raw_html)
    status_text = to_plain(st.group(1)) if st else None
    tm = RE_TIME.search(fragment)
    dm = RE_DATE.search(raw_html)
    nx = RE_NEXT.search(raw_html)
    return Part(
        status_text=status_text,
        authorized=status_text is None,
        time=tm.group(1).replace(":", ".") if tm else None,
        datum=parse_cz_date(dm.group(3)) if dm else None,
        schuze=int(dm.group(1)) if dm else None,
        jd=int(dm.group(2)) if dm else None,
        next_turn=int(nx.group(2)) if nx else None,
        fragment=fragment,
        # hash zahrnuje i hlášku o autorizaci: změní se i při autorizaci beze změny textu
        hash=content_hash((status_text or "") + "\n" + fragment),
        segments=segments_of(fragment),
    )


def part_time(fragment: str) -> str | None:
    tm = RE_TIME.search(fragment)
    return tm.group(1).replace(":", ".") if tm else None


def segments_of(fragment: str) -> list[Segment]:
    """Rozdělí obsah části na úseky podle kotev řečníků."""
    # obsah bez úvodní značky času (je v metadatech)
    body = RE_TIME.sub("", fragment)
    segments: list[Segment] = []
    matches = list(RE_SPEAKER.finditer(body))
    first_start = matches[0].start() if matches else len(body)
    lead = body[:first_start]
    # odříznout začátek odstavce, ve kterém kotva řečníka leží
    lead = re.sub(r"<p[^>]*>\s*$", "", lead)
    seg = _segment_from(lead, None)
    if seg.text or seg.headings or seg.votes:
        segments.append(seg)
    for i, sm in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        chunk = body[sm.end():end]
        chunk = re.sub(r"<p[^>]*>\s*$", "", chunk)
        segments.append(_segment_from(chunk, int(sm.group(2)), sm.group(1), sm.group(3)))
    return segments


RE_INDEX_DAY = re.compile(r'href="(\d{3})schuz/(\d+)-(\d+)\.html"')


def parse_index(raw_html: str) -> dict[int, list[int]]:
    """Index období: {schůze: [jednací dny]}."""
    out: dict[int, set[int]] = {}
    for m in RE_INDEX_DAY.finditer(raw_html):
        out.setdefault(int(m.group(2)), set()).add(int(m.group(3)))
    return {k: sorted(v) for k, v in sorted(out.items())}


RE_DAY_PART = re.compile(r'href="s(\d{3})(\d{3})\.htm')


def parse_day_turns(raw_html: str) -> list[int]:
    """Jednací den S-D.html: čísla částí (turnů), které den obsahuje."""
    return sorted({int(m.group(2)) for m in RE_DAY_PART.finditer(raw_html)})


ROLE_WORDS = ("Poslanec", "Poslankyně", "Předsedající", "Předseda", "Předsedkyně",
              "Místopředseda", "Místopředsedkyně", "Ministr", "Ministryně", "Senátor",
              "Senátorka", "Veřejný ochránce", "Veřejná ochránkyně", "Guvernér", "Prezident",
              "Prezidentka", "Člen", "Členka", "Náměstek", "Náměstkyně", "Zástupce", "Zástupkyně")


def split_role(label: str, name: str | None) -> str:
    """Role řečníka = text odkazu bez jména osoby."""
    if name and label.endswith(name):
        return label[: -len(name)].strip()
    words = label.split()
    return " ".join(words[:-2]) if len(words) > 2 else (words[0] if words else "")


def is_chair(label: str) -> bool:
    return label.startswith("Předsedající")
