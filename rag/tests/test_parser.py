"""Testy parseru a skládání projevů nad uloženými stránkami psp.cz (bez sítě a bez DB)."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from hlidac_rag.psp import ingest, parser

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> parser.Part:
    return parser.parse_part(parser.page_text((FIX / name).read_bytes()))


def test_final_version_has_no_status():
    p = load("001_s001001.htm")
    assert p.authorized and p.status_text is None
    assert (p.schuze, p.jd, p.datum) == (1, 1, date(2025, 11, 3))
    assert p.next_turn == 2
    # úvodní poznámka bez řečníka + 7 vystoupení
    assert [s.aname for s in p.segments] == [None, 1, 2, 3, 4, 5, 6, 7]
    assert p.segments[1].id_osoba == 6214 and p.segments[1].label == "Předsedající Věra Kovářová"


def test_unauthorized_part_continuation_heading_and_minister():
    p = load("034_s034633.htm")
    assert not p.authorized and "neautorizováno" in p.status_text
    assert p.time == "11.00"
    lead, minister = p.segments
    assert lead.aname is None and not lead.text.startswith("(pokračuje")
    assert lead.headings == [280] and 89012 in lead.votes
    assert "[194. Zdravotně pojistné plány" in lead.text
    # člen vlády odkazuje na vlada.gov.cz, id_osoba v HTML chybí
    assert minister.aname == 1 and minister.id_osoba is None
    assert minister.label == "Ministr zdravotnictví ČR Adam Vojtěch"


def test_hash_includes_status():
    p = load("034_s034633.htm")
    assert p.hash != parser.content_hash(p.fragment)


def test_index_and_day():
    idx = parser.parse_index(parser.page_text((FIX / "index.htm").read_bytes()))
    assert idx[34] == list(range(1, 13)) and 36 in idx
    turns = parser.parse_day_turns(parser.page_text((FIX / "034_34-12.html").read_bytes()))
    assert 633 in turns and turns == sorted(turns)


def test_build_speeches_joins_parts_and_resets_on_gap():
    a, b = load("034_s034633.htm"), load("034_s034636.htm")
    casts = [(633, a.datum, 12, "neautorizovano", a.fragment), (636, b.datum, 12, "neautorizovano", b.fragment)]
    sp = ingest.build_speeches(casts)
    # 633: text bez řečníka (předchozí část chybí) + ministr; 636 navazuje s mezerou → nové projevy
    assert [(s.turn, s.aname) for s in sp] == [(633, 0), (633, 1), (636, 1), (636, 2), (636, 3)]
    assert sp[0].heading_ct == 280 and sp[1].heading_ct == 280
    # nadpis bodu 285 v projevu předsedajícího mění tisk pro další projevy
    assert sp[3].heading_ct == 280 and sp[4].heading_ct == 285
    assert not sp[1].authorized


def test_build_speeches_continuation():
    a = load("034_s034633.htm")
    # stejná část dvakrát za sebou jako turn 1 a 2: text bez řečníka se připojí k poslednímu projevu
    casts = [(1, a.datum, 1, "konecna", a.fragment), (2, a.datum, 1, "konecna", a.fragment)]
    sp = ingest.build_speeches(casts)
    assert [(s.turn, s.aname) for s in sp] == [(1, 0), (1, 1), (2, 1)]
    assert sp[1].turns == [1, 2] and len(sp[1].texts) == 2


def test_next_check_schedule():
    now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
    assert ingest.next_check("neautorizovano", date(2026, 11, 17), None, now) - now == timedelta(hours=2)
    assert ingest.next_check("neautorizovano", date(2026, 11, 1), None, now) - now == timedelta(days=1)
    assert ingest.next_check("konecna", date(2026, 10, 1), now - timedelta(days=5), now) - now == timedelta(days=7)
    assert ingest.next_check("konecna", date(2025, 11, 3), None, now) - now == timedelta(days=90)


def test_split_role():
    assert parser.split_role("Ministr zdravotnictví ČR Adam Vojtěch", "Adam Vojtěch") == "Ministr zdravotnictví ČR"
    assert parser.split_role("Poslanec Martin Kupka", None) == "Poslanec"
