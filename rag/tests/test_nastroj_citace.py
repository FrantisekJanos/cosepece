"""Testy nástroje over_citaci: falešné spojení (řádky v pořadí dotazů) a pg test nad vloženým projevem a celky."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from hlidac_rag import mcp_server, tools
from hlidac_rag.citace_shoda import UVOZOVKY
from hlidac_rag.nastroje.citace import MAX_CITACE, _orizni, _over_citaci


class FakeConn:
    """Vrací připravené výsledky (seznamy řádků) v pořadí dotazů a pamatuje si SQL (kopie z test_tools)."""

    def __init__(self, *results):
        self.results = list(results)
        self.sql: list[str] = []
        self.params: list = []

    def cursor(self, row_factory=None):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.sql.append(sql)
        self.params.append(params)
        return self

    def fetchall(self):
        return self.results.pop(0)


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture
def fake_pool(monkeypatch):
    def make(conn):
        monkeypatch.setattr(mcp_server, "pool", FakePool(conn))
        return conn
    return make


# ---------------------------------------------------------------- projevy

PROJEV = ("Vážený pane předsedající, vážené dámy a pánové. " * 5
          + "Tento zákon nepodpoříme, protože zkracuje lhůtu pro vymáhání dotací. "
          + "Děkuji za pozornost. " * 10)
CITACE = "Tento zákon nepodpoříme, protože zkracuje lhůtu"


def _projev(**kw) -> dict:
    row = dict(recnik="Poslanec Jan Testovací", klub="TEST", datum=date(2026, 10, 14), autorizovano=True,
               url="https://www.psp.cz/x.htm#r6", zruseno=None, text=PROJEV)
    row.update(kw)
    return row


def test_projev_presne():
    conn = FakeConn([_projev()])
    r = _over_citaci(conn, CITACE, klic="35/10/6")
    assert "steno.projev" in conn.sql[0] and conn.params[0] == (10, 35, 10, 6)
    assert r["shoda"] == "presne" and r["skore"] == 1.0
    assert PROJEV[r["od_znaku"]:r["do_znaku"]] == r["nalezeny_text"] == CITACE
    assert r["klic"] == "10/35/10/6"           # klíč bez období doplněný na celý tvar
    assert r["recnik"] == "Poslanec Jan Testovací" and r["klub"] == "TEST"
    assert r["datum"] == "2026-10-14" and r["autorizovano"] is True and r["url"].endswith("#r6")
    assert "poznamka" not in r
    k = r["kontext"]
    assert k.startswith("… ") and k.endswith(" …") and CITACE in k
    assert k == "… " + PROJEV[r["od_znaku"] - 150:r["do_znaku"] + 150] + " …"


def test_projev_kontext_bez_orezu_na_okrajich():
    text = "Tento zákon nepodpoříme, protože zkracuje lhůtu."
    r = _over_citaci(FakeConn([_projev(text=text)]), CITACE, klic="10/35/10/6")
    assert r["kontext"] == text


def test_projev_jiny_recnik_nenalezeno():
    """Výrok přisouzený řečníkovi, jehož projev ho neobsahuje: nenalezeno bez kandidáta."""
    jiny = "Děkuji, pane předsedající. Navrhuji zamítnutí v prvním čtení."
    r = _over_citaci(FakeConn([_projev(text=jiny, recnik="Poslanec Jiný")]), CITACE, klic="10/35/10/7")
    assert r["shoda"] == "nenalezeno" and r["skore"] == 0.0
    assert r["od_znaku"] is None and r["do_znaku"] is None and r["nalezeny_text"] is None and r["kontext"] is None
    assert r["recnik"] == "Poslanec Jiný" and r["klic"] == "10/35/10/7"


def test_projev_obraceny_vyznam_je_nenalezeno_s_kandidatem():
    text = PROJEV.replace("nepodpoříme", "podpoříme")
    r = _over_citaci(FakeConn([_projev(text=text)]), CITACE, klic="10/35/10/6")
    assert r["shoda"] == "nenalezeno" and r["skore"] > 0
    assert "podpoříme" in r["nalezeny_text"] and "podpoříme" in r["kontext"]


def test_projev_zruseny_ma_poznamku_a_datum():
    zruseno = datetime(2026, 10, 20, 8, 30, tzinfo=timezone.utc)
    conn = FakeConn([_projev(zruseno=zruseno)])
    r = _over_citaci(conn, CITACE, klic="10/35/10/6")
    assert "zruseno" in conn.sql[0]
    assert r["shoda"] == "presne"                         # neodmítá se, jen varuje
    assert r["zruseno"] == "2026-10-20T08:30:00+00:00"
    assert "chybí" in r["poznamka"] and "url" in r["poznamka"]
    r = _over_citaci(FakeConn([_projev(zruseno=zruseno, autorizovano=False)]), CITACE, klic="10/35/10/6")
    assert tools.NOTE_UNAUTH in r["poznamka"] and "chybí" in r["poznamka"]
    r = _over_citaci(FakeConn([_projev()]), CITACE, klic="10/35/10/6")
    assert "zruseno" not in r and "poznamka" not in r


def test_projev_neautorizovany_ma_poznamku():
    r = _over_citaci(FakeConn([_projev(autorizovano=False)]), CITACE, klic="10/35/10/6")
    assert r["poznamka"] == tools.NOTE_UNAUTH and r["autorizovano"] is False


def test_projev_neexistuje():
    with pytest.raises(ValueError, match="Projev 10/1/2/3 neznám"):
        _over_citaci(FakeConn([]), CITACE, klic="10/1/2/3")
    with pytest.raises(ValueError, match="obdobi/schuze/turn/aname"):
        _over_citaci(FakeConn(), CITACE, klic="10/1")


# ---------------------------------------------------------------- texty návrhů a PN

_OK = [{"ok": True}]


def _dok(**kw) -> dict:
    row = dict(id=7, druh="tisk", ct=311, cislo_pn=0, verze="puvodni", docx_url="https://www.psp.cz/x.docx",
               znaku=50000, chyba=None, ma_text=True, dostupny=True)
    row.update(kw)
    return row


def _cel(poradi: int, oznaceni: str, text: str, **kw) -> dict:
    row = dict(poradi=poradi, typ="clanek", oznaceni=oznaceni, cesta=f"ČÁST PRVNÍ / {oznaceni}", text=text)
    row.update(kw)
    return row


def _meta(*radky: tuple) -> list[dict]:
    """Řádky (poradi, cesta[, typ, oznaceni]); označení se jinak odvodí z posledního dílu cesty bez „(k/n)“."""
    out = []
    for poradi, cesta, *zbytek in radky:
        typ, oznaceni = zbytek or ("clanek", cesta.split(" / ")[-1].split(" (")[0])
        out.append(dict(poradi=poradi, typ=typ, oznaceni=oznaceni, cesta=cesta))
    return out


C0 = "Zákon č. 256/2000 Sb., o Státním zemědělském intervenčním fondu, se mění takto:"
C1 = "1. V § 11a odst. 5 se slova „do 10 let“ nahrazují slovy „do 4 let“."
C2 = "2. Řízení o odvodu lze zahájit nejpozději do 4 let ode dne porušení rozpočtové kázně."
DZ = ("Lhůta se zkracuje. Řízení o odvodu lze zahájit nejpozději do 4 let ode dne porušení rozpočtové kázně, "
      "a to i zpětně.")
CELKY = [_cel(0, "Čl. I", C0), _cel(1, "bod 1", C1, typ="bod", cesta="ČÁST PRVNÍ / Čl. I / bod 1"),
         _cel(2, "bod 2", C2, typ="bod", cesta="ČÁST PRVNÍ / Čl. I / bod 2"),
         _cel(3, "Důvodová zpráva", DZ, typ="duvodova_zprava", cesta="Důvodová zpráva")]


def test_text_cely_dokument_shoda_v_dalsim_celku():
    conn = FakeConn(_OK, [_dok()], CELKY)
    cit = "lze zahájit nejpozději do 4 let ode dne porušení"
    r = _over_citaci(conn, cit, ct=311)
    assert "to_regclass" in conn.sql[0] and "texty.dokument" in conn.sql[1]
    assert conn.params[2] == (7,) and "duvodova" not in conn.sql[2]     # celý dokument včetně důvodové zprávy
    # stejná věta je i v důvodové zprávě, vyhrává první celek v pořadí (normativní část)
    assert r["shoda"] == "presne" and r["poradi"] == 2 and r["oznaceni"] == "bod 2" and r["typ"] == "bod"
    assert r["cesta"] == "ČÁST PRVNÍ / Čl. I / bod 2"
    assert r["od_znaku"] == C2.index("lze zahájit")                    # posun od začátku textu celku
    assert C2[r["od_znaku"]:r["do_znaku"]] == r["nalezeny_text"] == cit
    assert "do_poradi" not in r and "poznamka" not in r
    assert r["ct"] == 311 and r["pn"] is None and r["verze"] == "puvodni" and r["dostupne_verze"] == ["puvodni"]
    assert r["url_docx"] == "https://www.psp.cz/x.docx" and "t=311" in r["url_psp"]
    assert "id" not in r and "text" not in r
    assert C1 in r["kontext"]                     # kontext ze spojeného textu, i přes hranici celku


def test_text_shoda_v_duvodove_zprave():
    r = _over_citaci(FakeConn(_OK, [_dok()], CELKY), "kázně, a to i zpětně", ct=311)
    assert r["shoda"] == "presne" and r["poradi"] == 3 and r["typ"] == "duvodova_zprava"
    assert DZ[r["od_znaku"]:r["do_znaku"]] == "kázně, a to i zpětně"


def test_text_shoda_pres_hranici_celku():
    a, b = "Lhůta pro vymáhání odvodu činí", "čtyři roky ode dne poskytnutí dotace."
    conn = FakeConn(_OK, [_dok()], [_cel(5, "odst. 1", a), _cel(6, "odst. 2", b)])
    r = _over_citaci(conn, "Lhůta pro vymáhání odvodu činí čtyři roky", ct=311)
    assert r["shoda"] == "presne" and r["poradi"] == 5 and r["do_poradi"] == 6
    assert r["od_znaku"] == 0 and r["do_znaku"] == len(a) + 1 + len("čtyři roky")
    assert r["do_znaku_v_do_poradi"] == len("čtyři roky")
    assert r["nalezeny_text"] == a + "\nčtyři roky"


def test_text_priblizne_pres_hranici_celku():
    a, b = "Lhůta pro vymáhání odvodu činí", "čtyři roky ode dne poskytnutí dotace."
    conn = FakeConn(_OK, [_dok()], [_cel(5, "odst. 1", a), _cel(6, "odst. 2", b)])
    r = _over_citaci(conn, "Lhuta pro vymahani odvodu cini ctyri roky ode dne", ct=311)
    assert r["shoda"] == "priblizne" and r["skore"] == 1.0
    assert r["poradi"] == 5 and r["do_poradi"] == 6 and r["od_znaku"] == 0
    assert r["do_znaku_v_do_poradi"] == len("čtyři roky ode dne")
    assert b[:r["do_znaku_v_do_poradi"]] == "čtyři roky ode dne"
    assert r["nalezeny_text"] == a + "\nčtyři roky ode dne"
    assert "do_znaku_v_do_poradi" not in _over_citaci(FakeConn(_OK, [_dok()], CELKY), "kázně, a to i zpětně", ct=311)


def test_text_oznaceni_s_kusy_a_vnorenym_celkem():
    shody = _meta((1, "Čl. I (1/2)"), (2, "Čl. I (2/2)"))
    dalsi = _meta((1, "Čl. I (1/2)"), (2, "Čl. I (2/2)"), (3, "Čl. I / § 17c"), (4, "Čl. II"), (5, "Čl. II / § 3"))
    texty = [_cel(1, "Čl. I", "Zákon č. 586/1992 Sb. se mění takto:", cesta="Čl. I (1/2)"),
             _cel(2, "Čl. I", "V § 17c se doplňuje odstavec 3, který zní:", cesta="Čl. I (2/2)"),
             _cel(3, "§ 17c", "(3) Poplatník může uplatnit slevu na dani nejvýše jednou ročně.",
                  cesta="Čl. I / § 17c")]
    conn = FakeConn(_OK, [_dok()], shody, dalsi, texty)
    r = _over_citaci(conn, "může uplatnit slevu na dani", ct=311, oznaceni="čl.I")
    assert "regexp_replace" in conn.sql[2] and conn.params[2] == (7, "čl.I")
    assert conn.params[3] == (7, 1)                  # řádky od výchozího
    assert conn.params[4] == (7, 1, 3)               # kusy a vnořený § 17c, bez Čl. II
    assert len(conn.sql) == 5                        # kusy jdou za sebou, _jeden_celek nic nedotazuje
    assert r["shoda"] == "presne" and r["poradi"] == 3 and r["oznaceni"] == "§ 17c" and r["cesta"] == "Čl. I / § 17c"
    assert texty[2]["text"][r["od_znaku"]:r["do_znaku"]] == "může uplatnit slevu na dani"


def test_text_oznaceni_pokracuje_za_citovanym_znenim():
    shody = _meta((1, "Čl. I"), (4, "Čl. I"))
    mezi = [dict(cesta="Čl. I / § 17c"), dict(cesta="Čl. I / § 17d")]
    dalsi = _meta((1, "Čl. I"), (2, "Čl. I / § 17c"), (3, "Čl. I / § 17d"), (4, "Čl. I"), (5, "Čl. II"))
    texty = [_cel(p, "x", f"Text celku {p}.") for p in (1, 2, 3, 4)]
    conn = FakeConn(_OK, [_dok()], shody, mezi, dalsi, texty)
    r = _over_citaci(conn, "Text celku 4", ct=311, oznaceni="Čl. I")
    assert conn.params[-1] == (7, 1, 4) and r["poradi"] == 4 and r["od_znaku"] == 0


def test_text_poradi_cast_zahrne_sve_clanky():
    dalsi = _meta((0, "ČÁST PRVNÍ"), (1, "ČÁST PRVNÍ / Čl. I"), (2, "ČÁST PRVNÍ / Čl. II"), (3, "ČÁST DRUHÁ"),
                  (4, "ČÁST DRUHÁ / Čl. III"))
    texty = [_cel(0, "ČÁST PRVNÍ", "Změna zákona o daních z příjmů"), _cel(1, "Čl. I", "První článek části."),
             _cel(2, "Čl. II", "Druhý článek části první.")]
    conn = FakeConn(_OK, [_dok()], dalsi, texty)
    r = _over_citaci(conn, "Druhý článek části první", ct=311, poradi=0)
    assert conn.params[2] == (7, 0) and conn.params[3] == (7, 0, 2)
    assert r["poradi"] == 2 and r["shoda"] == "presne"


def test_text_poradi_bez_pokracovani_nebere_soused_se_stejnou_predponou():
    conn = FakeConn(_OK, [_dok()], _meta((1, "Čl. I"), (2, "Čl. II")), [_cel(1, "Čl. I", "Jen první článek zákona.")])
    _over_citaci(conn, "Jen první článek", ct=311, poradi=1)
    assert conn.params[3] == (7, 1, 1)               # „Čl. II“ nezačíná „Čl. I / “


def test_text_duvodova_zprava_jen_svuj_kus():
    """Kusy DZ mají stejnou cestu „důvodová zpráva“, ale jiné označení: DZ 3 nebere DZ 4 a dál."""
    dz = [(10, "důvodová zpráva", "duvodova_zprava", "důvodová zpráva 3"),
          (11, "důvodová zpráva", "duvodova_zprava", "důvodová zpráva 4"),
          (12, "důvodová zpráva", "duvodova_zprava", "důvodová zpráva 5")]
    texty = [_cel(10, "důvodová zpráva 3", "Obecná část odůvodnění.", typ="duvodova_zprava", cesta="důvodová zpráva")]
    conn = FakeConn(_OK, [_dok()], _meta((10, "důvodová zpráva")), _meta(*dz), texty)
    r = _over_citaci(conn, "Obecná část odůvodnění", ct=311, oznaceni="důvodová zpráva 3")
    assert "typ" in conn.sql[3] and "oznaceni" in conn.sql[3]
    assert conn.params[4] == (7, 10, 10) and r["shoda"] == "presne"
    conn = FakeConn(_OK, [_dok()], _meta(*dz), texty)
    _over_citaci(conn, "Obecná část odůvodnění", ct=311, poradi=10)
    assert conn.params[3] == (7, 10, 10)


def test_text_bod_pn_nebere_sve_oduvodneni():
    radky = _meta((0, "bod 1", "bod", "bod 1"), (1, "bod 1 / důvodová zpráva", "duvodova_zprava", "důvodová zpráva 1"),
                  (2, "bod 1 / § 5", "paragraf", "§ 5"), (3, "bod 2", "bod", "bod 2"))
    conn = FakeConn(_OK, [_dok(druh="pn", ct=76, cislo_pn=894)], radky, [_cel(0, "bod 1", "V § 5 se číslo 10 nahrazuje.")])
    _over_citaci(conn, "číslo 10 nahrazuje", pn=894, poradi=0)
    assert conn.params[3] == (7, 0, 0)              # odůvodnění bodu (DZ) rozsah ukončí


def test_text_nenalezeno_s_oznacenim_radi_hledat_v_celem_dokumentu():
    radky = _meta((1, "Čl. I"), (2, "Čl. II"))
    r = _over_citaci(FakeConn(_OK, [_dok()], radky, [_cel(1, "Čl. I", "Jen první článek zákona.")]),
                     "kočka mňouká večer potichu doma", ct=311, poradi=1)
    assert r["shoda"] == "nenalezeno"
    assert "bez oznaceni a poradi" in r["poznamka"] and "verze" not in r["poznamka"]
    verze = [_dok(), _dok(id=8, verze="schvalene")]
    shody = _meta((1, "Čl. I"))
    r = _over_citaci(FakeConn(_OK, verze, shody, radky, [_cel(1, "Čl. I", "Jen první článek zákona.")]),
                     "kočka mňouká večer potichu doma", ct=311, oznaceni="Čl. I")
    assert r["poznamka"].index("bez oznaceni a poradi") < r["poznamka"].index('verze="puvodni"')


def test_text_neexistujici_celek():
    with pytest.raises(ValueError, match=r"poradi 99 .*osnova_textu\(ct=311\)"):
        _over_citaci(FakeConn(_OK, [_dok()], []), CITACE, ct=311, poradi=99)
    with pytest.raises(ValueError, match="poradi 99"):
        _over_citaci(FakeConn(_OK, [_dok()], _meta((100, "Čl. V"))), CITACE, ct=311, poradi=99)
    with pytest.raises(ValueError, match=r"„§ 999“ v textu nenalezen.*osnova_textu\(ct=311\)"):
        _over_citaci(FakeConn(_OK, [_dok()], []), CITACE, ct=311, oznaceni="§ 999")


def test_text_opakovane_oznaceni_je_chyba_s_poradi():
    shody = _meta((5, "Čl. I / § 10a"), (9, "Čl. II / § 10a"))
    with pytest.raises(ValueError) as e:
        _over_citaci(FakeConn(_OK, [_dok()], shody), CITACE, ct=311, oznaceni="§ 10a")
    assert "poradi 5" in str(e.value) and "poradi 9 (Čl. II / § 10a)" in str(e.value)


def test_text_neexistujici_dokument():
    with pytest.raises(ValueError, match="zatím nemám"):
        _over_citaci(FakeConn(_OK, []), CITACE, ct=311)
    with pytest.raises(ValueError, match="Texty zatím nejsou"):
        _over_citaci(FakeConn([{"ok": False}]), CITACE, pn=894)


def test_text_pn():
    conn = FakeConn(_OK, [_dok(druh="pn", ct=76, cislo_pn=894)], [_cel(0, "bod 1", "V § 5 se číslo 10 nahrazuje číslem 4.")])
    r = _over_citaci(conn, "číslo 10 nahrazuje číslem 4", pn=894)
    assert r["shoda"] == "presne" and r["pn"] == 894 and r["ct"] == 76 and "cd=894" in r["url_psp"]


def test_text_nenalezeno_bez_kandidata_ma_udaje_dokumentu():
    r = _over_citaci(FakeConn(_OK, [_dok()], CELKY), "kočka mňouká večer potichu doma", ct=311)
    assert r["shoda"] == "nenalezeno" and r["kontext"] is None
    assert r["poradi"] is None and r["typ"] is None and r["oznaceni"] is None and r["cesta"] is None
    assert r["ct"] == 311 and r["verze"] == "puvodni" and r["url_psp"]
    assert "poznamka" not in r                        # jiná verze textu není


def test_text_nenalezeno_s_jinou_verzi_ma_poznamku():
    verze = [_dok(), _dok(id=8, verze="schvalene", docx_url="https://www.psp.cz/s.docx")]
    conn = FakeConn(_OK, verze, CELKY)
    r = _over_citaci(conn, "kočka mňouká večer potichu doma", ct=311)
    assert conn.params[2] == (8,)                     # celky výchozí verze = schválené znění
    assert r["verze"] == "schvalene" and r["dostupne_verze"] == ["puvodni", "schvalene"]
    assert r["url_docx"].endswith("s.docx")
    assert 'verze="puvodni"' in r["poznamka"]
    r = _over_citaci(FakeConn(_OK, verze, CELKY), "kočka mňouká večer potichu doma", ct=311, verze="puvodni")
    assert 'verze="schvalene"' in r["poznamka"]
    r = _over_citaci(FakeConn(_OK, verze, CELKY), "lze zahájit nejpozději do 4 let", ct=311)
    assert r["shoda"] == "presne" and "poznamka" not in r


# ---------------------------------------------------------------- parametry

@pytest.mark.parametrize("kw, chyba", [
    ({}, "právě jeden zdroj"),
    (dict(klic="10/35/10/6", ct=311), "právě jeden zdroj"),
    (dict(klic="10/35/10/6", pn=894), "právě jeden zdroj"),
    (dict(ct=311, pn=894), "právě jedno z ct"),
    (dict(klic="10/35/10/6", verze="puvodni"), "verze"),
    (dict(klic="10/35/10/6", oznaceni="Čl. I"), "ct"),
    (dict(oznaceni="Čl. I"), "zadejte k nim ct"),
    (dict(poradi=3), "zadejte k nim ct"),
    (dict(ct=311, oznaceni="Čl. I", poradi=3), "ne obojí"),
])
def test_parametry_spatny_zdroj(kw, chyba):
    conn = FakeConn()
    with pytest.raises(ValueError, match=chyba):
        _over_citaci(conn, CITACE, **kw)
    assert conn.sql == []                         # chyba dřív, než se sáhne do DB


def test_citace_delsi_nez_2000_znaku():
    conn = FakeConn()
    with pytest.raises(ValueError, match="2 000.*rozdělte"):
        _over_citaci(conn, "slovo " * 400, klic="10/35/10/6")
    assert conn.sql == []


def test_citace_kratsi_nez_3_slova():
    for citace, zdroj in (("Tento zákon", dict(klic="10/35/10/6")), ("„ “", dict(ct=311)), ("„… dvě slova …“", dict(pn=1))):
        conn = FakeConn()
        with pytest.raises(ValueError, match="aspoň 3 slova"):
            _over_citaci(conn, citace, **zdroj)
        assert conn.sql == []                     # odmítnuto před dotazem do DB


def test_dlouhy_beh_mezer_se_odmitne_rychle():
    """Ořez okrajů je lineární a délka se kontroluje dřív než cokoli jiného (hrubá pojistka času)."""
    for citace in (CITACE + " " * 20_000 + "konec", " " * 1_000_000 + CITACE, "„" * 50_000):
        conn = FakeConn()
        t0 = time.perf_counter()
        with pytest.raises(ValueError, match="nejvýš 2 000"):
            _over_citaci(conn, citace, klic="10/35/10/6")
        assert time.perf_counter() - t0 < 0.5 and conn.sql == []
    t0 = time.perf_counter()
    assert _orizni(" " * (2 * MAX_CITACE) + CITACE + "…" * MAX_CITACE) == CITACE
    assert time.perf_counter() - t0 < 0.5
    r = _over_citaci(FakeConn([_projev()]), " " * (3 * MAX_CITACE) + CITACE + " \n" * 400, klic="10/35/10/6")
    assert r["shoda"] == "presne"


@pytest.mark.parametrize("citace, oriznuta", [
    ("„… i tehdy, … tohoto zákona“", "i tehdy, … tohoto zákona"),
    ("… i tehdy, pokud byla …", "i tehdy, pokud byla"),
    ("[…] i tehdy [...]", "i tehdy"),
    ("(...) „i tehdy“ (…)", "i tehdy"),
    ("\"... i tehdy...\"", "i tehdy"),
    ("i tehdy, pokud byla.", "i tehdy, pokud byla."),
])
def test_orez_krajnich_vypustek(citace, oriznuta):
    assert _orizni(citace) == oriznuta


def test_citace_s_krajni_vypustkou_je_presne():
    text = "Ustanovení se použije i tehdy, pokud byla neoprávněná platba dotace poskytnuta přede dnem nabytí účinnosti."
    r = _over_citaci(FakeConn([_projev(text=text)]), "„… i tehdy, pokud byla …“", klic="10/35/10/6")
    assert r["shoda"] == "presne" and r["nalezeny_text"] == "i tehdy, pokud byla"


@pytest.mark.parametrize("obal", ["„{}“", '"{}"', "'{}'", "»{}«", "«{}»", "‚{}‘", "“{}”", "‘{}’",
                                  "  „{}“ \n", "\"„{}“\""] + [q + "{}" + q for q in UVOZOVKY])
def test_citace_bez_krajnich_uvozovek(obal):
    r = _over_citaci(FakeConn([_projev()]), obal.format(CITACE), klic="10/35/10/6")
    assert r["shoda"] == "presne" and r["nalezeny_text"] == CITACE


# ---------------------------------------------------------------- registrace v MCP serveru

def test_over_citaci_je_nastroj_serveru():
    assert "citace" in mcp_server.MODULY_NASTROJU
    t = mcp_server.server._tool_manager.get_tool("over_citaci")
    assert t is not None
    assert t.annotations.read_only_hint is True and t.annotations.open_world_hint is False
    assert set(t.parameters["properties"]) == {"citace", "klic", "ct", "pn", "oznaceni", "poradi", "verze"}
    assert t.parameters["required"] == ["citace"]
    for slovo in ("presne", "priblizne", "nenalezeno", "nalezeny_text", "skore", "2 000", "3 slova",
                  'klic="10/34/633/1"', 'oznaceni="Čl. VI"', "i u presne", "vnořen", "do_znaku_v_do_poradi",
                  "get_cel(offset_znaku)"):
        assert slovo in t.description, slovo


def test_over_citaci_pres_call(fake_pool):
    conn = fake_pool(FakeConn([_projev()]))
    t = mcp_server.server._tool_manager.get_tool("over_citaci")
    r = t.fn(citace=CITACE, klic="10/35/10/6")
    assert r["shoda"] == "presne" and r["klic"] == "10/35/10/6"
    assert conn.sql[0] == "SET TRANSACTION READ ONLY" and conn.sql[1].startswith("SET LOCAL statement_timeout")
    assert conn.sql[-1].startswith("INSERT INTO mcp.volani") and conn.params[-1][1] == "over_citaci"


def test_over_citaci_chyba_je_toolerror(fake_pool):
    fake_pool(FakeConn([]))
    t = mcp_server.server._tool_manager.get_tool("over_citaci")
    with pytest.raises(ToolError, match="neznám"):
        t.fn(citace=CITACE, klic="10/1/2/3")
    fake_pool(FakeConn())
    with pytest.raises(ToolError, match="právě jeden zdroj"):
        t.fn(citace=CITACE)


# ---------------------------------------------------------------- PostgreSQL

bez_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)")
SCHUZE, CT = 9999, 99999          # neobvyklá čísla, ať se test nesrazí s jinými


def _uklid(c) -> None:
    with c.cursor() as cur:
        cur.execute("DELETE FROM steno.projev WHERE obdobi = 10 AND schuze = %s", (SCHUZE,))
        cur.execute("DELETE FROM texty.dokument WHERE druh = 'tisk' AND ct = %s", (CT,))
    c.commit()


@pytest.fixture
def pg_conn():
    from hlidac_rag import db

    c = db.connect()
    # pojistka: test maže řádky, smí běžet jen nad testovací databází
    jmeno = c.execute("SELECT current_database()").fetchone()[0]
    if not jmeno.endswith("_test"):
        c.close()
        pytest.skip(f"databáze {jmeno} nekončí na _test")
    db.migrate(c)
    _uklid(c)
    with c.cursor() as cur:
        cur.execute("""INSERT INTO steno.projev (obdobi, schuze, turn, aname, datum, recnik, klub, text, znaku, hash,
                                                 autorizovano, url)
                       VALUES (10, %s, 1, 2, '2026-11-18', 'Poslanec Test', 'TEST', %s, %s, 'x', false,
                               'https://www.psp.cz/test.htm#r2')""", (SCHUZE, PROJEV, len(PROJEV)))
        cur.execute("""INSERT INTO texty.dokument (druh, ct, cislo_pn, verze, zdroj_textu, text, znaku, docx_url,
                                                   rozdeleno_at, chyba)
                       VALUES ('tisk', %s, 0, 'puvodni', 'tisk_text', %s, %s, 'https://www.psp.cz/t.docx', now(), NULL)
                       RETURNING id""", (CT, "\n".join(c_["text"] for c_ in CELKY), 1000))
        dok = cur.fetchone()[0]
        od = 0
        for cel in CELKY:
            cur.execute("""INSERT INTO texty.cel (dokument_id, poradi, typ, oznaceni, cesta, text, znaku, od_znaku)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (dok, cel["poradi"], cel["typ"], cel["oznaceni"], cel["cesta"], cel["text"], len(cel["text"]), od))
            od += len(cel["text"]) + 1
    c.commit()
    c.execute("SET TRANSACTION READ ONLY")        # nástroj jen čte, stejně jako v mcp_server._call
    yield c
    c.rollback()
    _uklid(c)
    c.close()


@pytest.mark.pg
@bez_db
def test_pg_projev(pg_conn):
    r = _over_citaci(pg_conn, CITACE, klic=f"10/{SCHUZE}/1/2")
    assert r["shoda"] == "presne" and PROJEV[r["od_znaku"]:r["do_znaku"]] == CITACE
    assert r["recnik"] == "Poslanec Test" and r["datum"] == "2026-11-18" and r["poznamka"] == tools.NOTE_UNAUTH
    r = _over_citaci(pg_conn, "Navrhuji zamítnutí v prvním čtení", klic=f"10/{SCHUZE}/1/2")
    assert r["shoda"] == "nenalezeno"
    with pytest.raises(ValueError, match="neznám"):
        _over_citaci(pg_conn, CITACE, klic=f"10/{SCHUZE}/1/3")


@pytest.mark.pg
@bez_db
def test_pg_celek(pg_conn):
    cit = "lze zahájit nejpozději do 4 let ode dne porušení"
    r = _over_citaci(pg_conn, cit, ct=CT)
    assert r["shoda"] == "presne" and r["poradi"] == 2 and C2[r["od_znaku"]:r["do_znaku"]] == cit
    assert r["url_docx"] == "https://www.psp.cz/t.docx" and r["verze"] == "puvodni"
    r = _over_citaci(pg_conn, cit, ct=CT, oznaceni="BOD 2")
    assert r["shoda"] == "presne" and r["oznaceni"] == "bod 2"
    r = _over_citaci(pg_conn, "nahrazují slovy „do 4 let“", ct=CT, oznaceni="čl.i")   # článek s vnořenými body
    assert r["shoda"] == "presne" and r["poradi"] == 1
    r = _over_citaci(pg_conn, "kázně, a to i zpětně", ct=CT, oznaceni="Čl. I")      # DZ mimo rozsah článku
    assert r["shoda"] == "nenalezeno"
    r = _over_citaci(pg_conn, "kočka mňouká večer potichu doma", ct=CT)
    assert r["shoda"] == "nenalezeno" and r["poradi"] is None and r["ct"] == CT
