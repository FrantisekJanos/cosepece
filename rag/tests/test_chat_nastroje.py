"""Testy adaptéru nástrojů chatbota (chat/nastroje.py) bez databáze.

conn_fake podstrčí místo čtecích funkcí z tools.py odpovědi ve tvaru, jaký vracejí skutečné funkce;
conn_padajici je spojení, na kterém každý dotaz skončí chybou databáze (výpadek při hodinovém přepisu tabulek).
"""
from __future__ import annotations

import json

import psycopg
import pytest

from hlidac_rag import tools
from hlidac_rag.chat import nastroje
from hlidac_rag.chat.historie import odhad_tokenu
from hlidac_rag.chat.nastroje import NAZVY, SCHEMATA, Vysledek, vykonej
from hlidac_rag.chat.prompt import SYSTEM

CEL_221 = {
    "dokument": {"druh": "tisk", "ct": 221, "pn": None, "verze": "schvalene", "url_docx": "https://www.psp.cz/x.docx",
                 "url_psp": "https://www.psp.cz/sqw/historie.sqw?o=10&t=221", "znaku": 50000,
                 "dostupne_verze": ["puvodni", "schvalene"]},
    "cel": {"poradi": 12, "typ": "paragraf", "oznaceni": "§ 17c", "cesta": "Čl. I / § 17c", "meni_zakon": "159/2006",
            "od_znaku": 4000, "text": "(1) Lhůta pro zahájení řízení činí 4 roky.", "znaku_celkem": 42,
            "offset_znaku": 0, "pokracuje": False, "dalsi_offset_znaku": None},
    "predchozi": "§ 17b", "predchozi_poradi": 11, "dalsi": "§ 18", "dalsi_poradi": 13,
}


def _rozprava(conn, ct, format="prehled", osoby=None, cteni=None, druh=None, od_poradi=0, max_znaku=30000):
    vystoupeni = [{"poradi": i, "klic": f"10/34/{600 + i}/1", "datum": "2026-11-12", "recnik": f"Řečník {i}",
                   "klub": "ANO", "druh": "recnik", "znaku": 3000, "url": "https://www.psp.cz/x.htm",
                   "text": "Vážený pane předsedající, " + "slovo " * 500} for i in range(1, 6)]
    return {"ct": ct, "nazev": "Novela", "format": format, "celkem": 5, "od_poradi": 1, "dalsi_od_poradi": None,
            "souhrn": {"recnici": [], "kluby": []}, "vystoupeni": vystoupeni}


def _get_hlasovani(conn, id_hlasovani):
    return {"id_hlasovani": id_hlasovani, "ct": 221, "nazev": "Novela", "url_web": "https://cosepece.cz/tisk/221",
            "schuze": 34, "cislo": 45, "datum": "2026-11-12", "vysledek": "přijato", "pro": 101, "proti": 80,
            "zdrzel": 3, "nehlasoval": 0, "prihlaseno": 184, "kvorum": 93, "krok": None,
            "url_psp": f"https://www.psp.cz/sqw/hlasy.sqw?g={id_hlasovani}",
            "kluby": [{"klub": "ANO", "pro": 70, "proti": 0, "zdrzel": 0, "nepritomen": 2}],
            "projevy": [], "projevu_celkem": 0,
            "poslanci": [{"id_osoba": 1, "jmeno": "Jan Novák", "klub": "ANO", "hlas": "pro"}]}


@pytest.fixture
def conn_fake(monkeypatch):
    monkeypatch.setitem(nastroje._FUNKCE, "get_cel", lambda conn, **kw: CEL_221)
    monkeypatch.setitem(nastroje._FUNKCE, "rozprava", _rozprava)
    monkeypatch.setitem(nastroje._FUNKCE, "get_hlasovani", _get_hlasovani)
    return object()


class PadajiciConn:
    """Každý dotaz skončí chybou databáze; volani počítá pokusy, rollbacky se jen zapíšou."""

    def __init__(self):
        self.volani = 0
        self.rollbacky = 0

    def cursor(self, row_factory=None):
        self.volani += 1
        raise psycopg.OperationalError("server closed the connection unexpectedly")

    def execute(self, *a, **kw):
        return self.cursor()

    def rollback(self):
        self.rollbacky += 1


@pytest.fixture
def conn_padajici():
    return PadajiciConn()


def test_schemata_jsou_uplna_a_prisna():
    assert {s["name"] for s in SCHEMATA} == NAZVY | {"nacti_zdroj"}
    assert all(s["strict"] is True and s["input_schema"]["additionalProperties"] is False for s in SCHEMATA)
    assert "over_citaci" not in NAZVY and "stav_dat" not in NAZVY
    assert len(NAZVY) == 19


def test_schemata_v_limitech_strict_rezimu():
    """Limity API pro strict: nejvýš 20 nástrojů, 24 nepovinných parametrů a 16 parametrů s union typem celkem.
    Nepovinné parametry proto nejsou: všechny jsou v required a „nezadáno“ je prázdná hodnota (0, "", [])."""
    assert len(SCHEMATA) <= 20
    nepovinnych = unionu = 0
    for s in SCHEMATA:
        sch = s["input_schema"]
        assert sch["type"] == "object" and set(sch["required"]) == set(sch["properties"])
        nepovinnych += len(set(sch["properties"]) - set(sch["required"]))
        unionu += sum(1 for p in sch["properties"].values() if isinstance(p.get("type"), list) or "anyOf" in p)
        assert set(s) == {"name", "description", "input_schema", "strict"}
    assert nepovinnych <= 24 and unionu <= 16


def test_prefix_nastroju_se_vejde():   # prefix se platí v každém tahu
    assert len(json.dumps(SCHEMATA, ensure_ascii=False)) < 16000
    assert odhad_tokenu(json.dumps(SCHEMATA, ensure_ascii=False) + SYSTEM) <= 6000


def test_get_cel_vraci_polozku_s_klicem(conn_fake):
    v = vykonej(conn_fake, "get_cel", {"ct": 221, "oznaceni": "§ 17c"})
    assert v.polozky[0].klic == "cel:tisk:221:schvalene:§ 17c" and v.polozky[0].text
    assert not v.chyba and "cel:tisk:221:schvalene:§ 17c" in v.text   # hlavička s identitou
    assert v.polozky[0].titulek == "Tisk 221, § 17c" and v.polozky[0].meta["poradi"] == 12


def test_get_cel_nad_skutecnou_funkci_tools():
    """Klíč se skládá z výstupu skutečné tools.get_cel (falešné spojení vrací řádky v pořadí dotazů)."""

    class FakeConn:
        def __init__(self, *results):
            self.results = list(results)

        def cursor(self, row_factory=None):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=()):
            return self

        def fetchall(self):
            return self.results.pop(0)

    dok = dict(id=7, druh="pn", ct=221, cislo_pn=2329, verze="puvodni", docx_url=None, znaku=900, chyba=None,
               ma_text=True, dostupny=True)
    cel = dict(poradi=3, typ="bod", oznaceni="bod 3", cesta="bod 3", meni_zakon=None, od_znaku=10, text="3. V § 5 …")
    conn = FakeConn([{"ok": True}], [dok], [dict(poradi=3, cesta="bod 3")], [cel], [])
    v = vykonej(conn, "get_cel", {"ct": 0, "pn": 2329, "oznaceni": "bod 3", "poradi": -1, "verze": "",
                                  "offset_znaku": 0})
    assert not v.chyba, v.text
    assert v.polozky[0].klic == "cel:pn:2329:bod 3" and v.polozky[0].text == "3. V § 5 …"


def test_orez_a_hlavicka(conn_fake):
    v = vykonej(conn_fake, "rozprava", {"ct": 221, "format": "plny"}, max_znaku=2000)
    assert len(v.text) <= 2000 and "zkráceno" in v.text
    assert v.text.startswith("Výsledek nástroje rozprava")
    assert v.polozky[0].klic == "projev:10/34/601/1"


def test_rozprava_dostane_rozpocet_podle_max_znaku(monkeypatch):
    dostal = {}

    def fake(conn, ct, **kw):
        dostal.update(kw)
        return {"ct": ct, "vystoupeni": []}

    monkeypatch.setitem(nastroje._FUNKCE, "rozprava", fake)
    vykonej(object(), "rozprava", {"ct": 221, "format": "prehled", "osoby": [], "cteni": "", "druh": [],
                                   "od_poradi": 0}, max_znaku=18000)
    assert dostal["max_znaku"] < 18000 and "osoby" not in dostal and "cteni" not in dostal


def test_neznamy_nastroj_a_spatny_argument_nevyhodi(conn_fake):
    assert vykonej(conn_fake, "smaz_vse", {}).chyba
    assert vykonej(conn_fake, "get_tisk", {"ct": "abc"}).chyba
    assert vykonej(conn_fake, "get_tisk", {"ct": True}).chyba          # bool není číslo
    assert vykonej(conn_fake, "get_tisk", {"ct": 221, "smaz": 1}).chyba  # neznámý parametr
    assert vykonej(conn_fake, "rozprava", {"ct": 221, "format": "vse"}).chyba
    assert vykonej(conn_fake, "nacti_zdroj", {"n": 1}).chyba            # řeší smyčka, ne adaptér


def test_prazdne_hodnoty_se_nepredaji_a_enum_bez_ohledu_na_velikost(monkeypatch):
    dostal = {}

    def fake(conn, ct=None, pn=None, oznaceni=None, poradi=None, verze=None, max_znaku=8000, offset_znaku=0):
        dostal.update(ct=ct, pn=pn, oznaceni=oznaceni, poradi=poradi, verze=verze)
        return CEL_221

    monkeypatch.setitem(nastroje._FUNKCE, "get_cel", fake)
    v = vykonej(object(), "get_cel", {"ct": 221, "pn": 0, "oznaceni": "", "poradi": 0, "verze": "Schvalene",
                                      "offset_znaku": 0})
    assert not v.chyba
    assert dostal == {"ct": 221, "pn": None, "oznaceni": None, "poradi": 0, "verze": "schvalene"}


def _telo(text: str):
    """JSON za hlavičkou textu pro model."""
    return json.loads(text[text.index("\n{") + 1:])


@pytest.mark.parametrize("nazev, argumenty, povinne", [
    ("get_tisk", {"ct": 0, "vcetne": []}, "ct"),
    ("get_pn", {"cislo": 0}, "cislo"),
    ("rozprava", {"ct": 0, "format": "prehled"}, "ct"),
    ("pn_k_tisku", {"ct": 0, "id_osoba": 0, "offset": 0}, "ct"),
    ("hlasovani_k_tisku", {"ct": 0, "offset": 0}, "ct"),
    ("projevy_k_tisku", {"ct": 0}, "ct"),
    ("get_hlasovani", {"id_hlasovani": 0, "jmenovite": False}, "id_hlasovani"),
    ("tisky_menici_zakon", {"sb": "", "offset": 0}, "sb"),
    ("tisk_podle_sbirky", {"sb": ""}, "sb"),
    ("get_projev", {"klic": "", "okoli": 0, "offset_znaku": 0}, "klic"),
    ("najdi_osobu", {"jmeno": ""}, "jmeno"),
    ("projevy_recnika", {"osoba": ""}, "osoba"),
])
def test_prazdny_povinny_identifikator_je_chyba_argumentu(monkeypatch, caplog, nazev, argumenty, povinne):
    """Prázdná hodnota u parametru bez výchozí hodnoty ve funkci = čitelná chyba argumentu, ne TypeError
    ve funkci („selhal na chybě serveru“ a log.exception do Sentry)."""
    volano = []
    monkeypatch.setitem(nastroje._FUNKCE, nazev, lambda conn, *a, **kw: volano.append(1))
    v = vykonej(object(), nazev, argumenty)
    assert v.chyba and f"{povinne} je povinné" in v.text and not volano
    assert "chybě serveru" not in v.text and not [r for r in caplog.records if r.levelname == "ERROR"]


def test_prazdny_dotaz_se_preda(monkeypatch):
    """hledej_v_textech s prázdným dotazem a filtrem vypíše všechny celky (tools.py), dotaz se proto předá."""
    dostal = {}

    def fake(conn, dotaz, **kw):
        dostal.update(dotaz=dotaz, **kw)
        return {"celkem": 0, "vysledky": []}

    monkeypatch.setitem(nastroje._FUNKCE, "hledej_v_textech", fake)
    v = vykonej(object(), "hledej_v_textech", {"dotaz": "", "ct": 221, "pn": 0, "meni_zakon": "", "rezim": "auto",
                                               "offset": 0})
    assert not v.chyba and dostal["dotaz"] == "" and dostal["ct"] == 221
    # hledej_v_projevech: prázdný dotaz jde do funkce, ta vrátí čitelnou ValueError z tools._tsquery
    v = vykonej(object(), "hledej_v_projevech", {"dotaz": "", "ct": 221})
    assert v.chyba and "Dotaz je příliš krátký" in v.text


def test_dlouha_rozprava_se_zkrati_po_vystoupenich(monkeypatch):
    """Přetečení stránky rozpravy: celá vystoupení, platný JSON a dalsi_od_poradi = první nevrácené."""
    def fake(conn, ct, od_poradi=0, **kw):
        start = max(1, od_poradi)
        vystoupeni = [{"poradi": i, "klic": f"10/34/{600 + i}/1", "datum": "2026-11-12", "recnik": f"Řečník {i}",
                       "druh": "recnik", "znaku": 2000, "text": f"Projev {i}. " + "slovo " * 330}
                      for i in range(start, start + 8)]
        return {"ct": ct, "format": "plny", "celkem": 20, "od_poradi": start, "dalsi_od_poradi": start + 8,
                "souhrn": {"recnici": [{"recnik": f"Řečník {i}", "vystoupeni": 1} for i in range(1, 21)]},
                "vystoupeni": vystoupeni}

    monkeypatch.setitem(nastroje._FUNKCE, "rozprava", fake)
    v = vykonej(object(), "rozprava", {"ct": 221, "format": "plny", "od_poradi": 5}, max_znaku=7000)
    assert not v.chyba and len(v.text) <= 7000
    d = _telo(v.text)
    vracena = [r["poradi"] for r in d["vystoupeni"]]
    assert vracena == list(range(5, 5 + len(vracena))) and 1 <= len(vracena) < 8
    assert d["dalsi_od_poradi"] == vracena[-1] + 1
    assert all(r["text"].endswith("slovo ") for r in d["vystoupeni"])          # texty celé
    assert [p.klic for p in v.polozky] == [f"projev:10/34/{600 + i}/1" for i in vracena]
    assert f"od_poradi = {vracena[-1] + 1}" in v.text and "zkráceno" in v.text


def test_seznam_s_offsetem_se_zkrati_a_posune_dalsi_offset(monkeypatch):
    monkeypatch.setitem(nastroje._FUNKCE, "projevy_k_tisku", lambda conn, ct, **kw: {
        "ct": ct, "celkem": 100, "offset": 40, "dalsi_offset": 60,
        "projevy": [{"klic": f"10/34/{i}/1", "recnik": "R", "datum": "2026-11-12", "autorizovano": True,
                     "uryvek": "x" * 400} for i in range(20)]})
    v = vykonej(object(), "projevy_k_tisku", {"ct": 221, "offset": 40}, max_znaku=3000)
    d = _telo(v.text)
    assert len(v.text) <= 3000 and d["dalsi_offset"] == 40 + len(d["projevy"]) and len(d["projevy"]) < 20
    assert len(v.polozky) == len(d["projevy"])


def test_jedine_dlouhe_vystoupeni_dostane_pokracovani(conn_fake):
    v = vykonej(conn_fake, "rozprava", {"ct": 221, "format": "plny"}, max_znaku=2000)
    d = _telo(v.text)
    r = d["vystoupeni"][0]
    assert len(v.text) <= 2000 and len(d["vystoupeni"]) == 1 and d["dalsi_od_poradi"] == 2
    assert r["zkraceno"] is True and r["pokracovani"] == f'get_projev("{r["klic"]}", offset_znaku={len(r["text"])})'


def test_valueerror_jde_do_textu_beze_zmeny(monkeypatch):
    def fake(conn, ct, vcetne=None):
        raise ValueError("Tisk 9999 v 10. období nesleduji. Zkuste seznam_tisku(q=…).")

    monkeypatch.setitem(nastroje._FUNKCE, "get_tisk", fake)
    v = vykonej(object(), "get_tisk", {"ct": 9999, "vcetne": []})
    assert v.chyba and v.text == "Tisk 9999 v 10. období nesleduji. Zkuste seznam_tisku(q=…)." and not v.polozky


def test_chyba_db_se_jednou_opakuje_a_nerika_neexistuje(conn_padajici):
    v = vykonej(conn_padajici, "get_tisk", {"ct": 221})
    assert v.chyba and conn_padajici.volani == 2
    assert "neexistuje" not in v.text and "zkuste" in v.text   # Review Focus 1
    assert conn_padajici.rollbacky == 2                       # spojení se po chybě vrátí do použitelného stavu


def test_druhy_pokus_po_chybe_db_uspeje(monkeypatch):
    pokusy = []

    def fake(conn, ct, vcetne=None):
        pokusy.append(1)
        if len(pokusy) == 1:
            raise psycopg.errors.UndefinedTable("relation \"tisky\" does not exist")
        return {"ct": ct, "nazev": "Novela", "texty": {"puvodni": False, "schvalene": False}}

    monkeypatch.setitem(nastroje._FUNKCE, "get_tisk", fake)

    class Conn:
        def rollback(self):
            pass

    v = vykonej(Conn(), "get_tisk", {"ct": 221, "vcetne": []})
    assert not v.chyba and len(pokusy) == 2 and v.polozky[0].klic == "tisk:221"
    assert "dokument existuje, text zatím nemáme" in v.text and v.polozky[0].meta["text_dostupny"] is False


def test_prazdny_vysledek_nerika_neexistuje(monkeypatch):
    monkeypatch.setitem(nastroje._FUNKCE, "seznam_tisku", lambda conn, **kw: {"pocet": 0, "tisky": []})
    v = vykonej(object(), "seznam_tisku", {"q": "zákon o ničem", "faze": "", "typ_navrhu": "", "razeni": "skore"})
    assert not v.chyba and "neexistuje" not in v.text and "nic není" in v.text


def test_hlasovani_bez_jmenovitych_hlasu(conn_fake):
    v = vykonej(conn_fake, "get_hlasovani", {"id_hlasovani": 90001})
    assert "poslanci" not in v.text and v.polozky[0].klic == "hlas:90001"
    assert v.polozky[0].titulek == "Hlasování č. 45, 12. 11. 2026"


def test_hlasovani_jmenovite_na_vyzadani(conn_fake):
    v = vykonej(conn_fake, "get_hlasovani", {"id_hlasovani": 90001, "jmenovite": True})
    assert '"poslanci"' in v.text and "Jan Novák" in v.text


def test_polozky_seznamu_a_detailu(monkeypatch):
    monkeypatch.setitem(nastroje._FUNKCE, "pn_k_tisku", lambda conn, ct, **kw: {
        "ct": ct, "celkem": 1, "pn": [{"cislo": 2329, "osoba": "Jan Novák", "klub": "ANO",
                                       "vysledek": {"stav": "prijat"}, "url_psp": "u"}]})
    v = vykonej(object(), "pn_k_tisku", {"ct": 221, "id_osoba": 0, "offset": 0})
    assert [(p.klic, p.typ, p.titulek) for p in v.polozky] == [("pn:2329", "pn", "PN 2329 (tisk 221)")]

    monkeypatch.setitem(nastroje._FUNKCE, "get_projev", lambda conn, klic, **kw: {
        "klic": klic, "recnik": "Jan Novák", "klub": "ANO", "datum": "2026-11-12", "autorizovano": False,
        "url": "u", "text": "Celý text projevu."})
    v = vykonej(object(), "get_projev", {"klic": "10/34/633/1", "okoli": 0, "offset_znaku": 0})
    p = v.polozky[0]
    assert p.klic == "projev:10/34/633/1" and p.titulek == "Jan Novák (ANO), 12. 11. 2026"
    assert p.text == "Celý text projevu." and p.meta["autorizovano"] is False

    monkeypatch.setitem(nastroje._FUNKCE, "tisky_menici_zakon", lambda conn, sb, **kw: {
        "zakon": "159/2006", "celkem": 1, "tisky": [{"ct": 221, "nazev": "Novela"}]})
    v = vykonej(object(), "tisky_menici_zakon", {"sb": "159/2006", "offset": 0})
    assert [p.klic for p in v.polozky] == ["zakon:159/2006", "tisk:221"]

    monkeypatch.setitem(nastroje._FUNKCE, "upozorneni", lambda conn, **kw: {"pocet": 1, "upozorneni": [
        {"typ": "prep_review", "ct": 0, "cas": "2026-11-20 10:00:00", "text": "Připomínky končí",
         "url": "https://cosepece.cz/pripravovane#prep-ABC123"}]})
    v = vykonej(object(), "upozorneni", {"ct": 0})
    assert v.polozky[0].klic == "upoz:prep_review:ABC123:2026-11-20 10:00:00" and v.polozky[0].typ == "upozorneni"


def test_autorizace_projevu_v_metadatech(conn_fake, monkeypatch):
    v = vykonej(conn_fake, "rozprava", {"ct": 221, "format": "prehled"})
    assert v.polozky[0].meta["autorizovano"] is True          # řádek rozpravy bez neautorizovano
    monkeypatch.setitem(nastroje._FUNKCE, "get_pn", lambda conn, cislo: {
        "cislo": cislo, "ct": 221, "osnova_dostupna": False, "ai": None,
        "projevy": [{"klic": "10/34/633/1", "datum": "2026-11-12", "recnik": "Jan Novák", "klub": "ANO"}]})
    v = vykonej(object(), "get_pn", {"cislo": 2329})
    assert v.polozky[1].meta["autorizovano"] is None           # get_pn autorizaci neuvádí = neznámo
    assert f"pn:2329 | PN 2329 (tisk 221) | {nastroje.TEXT_CHYBI}" in v.text


def test_nastroje_nic_nezapisuji_ani_nestahuji():
    """Adaptér volá jen čtecí funkce z tools.py a nástrojových modulů MCP (žádná síť, žádný zápis)."""
    from hlidac_rag.nastroje import hlasovani, rozprava

    povolene = [getattr(tools, n) for n in dir(tools) if callable(getattr(tools, n))]
    povolene += [rozprava.rozprava, hlasovani._hlasovani_k_tisku, hlasovani._get_hlasovani]
    assert set(nastroje._FUNKCE) == NAZVY
    assert all(f in povolene for f in nastroje._FUNKCE.values())
    assert "stav_dat" not in {f.__name__ for f in nastroje._FUNKCE.values()}


def test_vysledek_je_dataclass_uloh_6():
    assert Vysledek("x").polozky == [] and Vysledek("x").chyba is False
