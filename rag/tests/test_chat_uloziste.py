"""Schéma chat a úložiště konverzací a útraty (spec 14.1, 11.1, 15) nad PostgreSQL. Bez DATABASE_URL se přeskočí.

Součty útraty jsou globální (strop dne a měsíce), proto testy počítají v roce 2031, kde nic jiného není, a po sobě
uklízejí. Spojení jsou v autocommit režimu (jako ve službě): každá operace úložiště je atomická sama o sobě."""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)"),
]

PRAHA = ZoneInfo("Europe/Prague")
TED = datetime(2031, 6, 15, 12, 0, tzinfo=PRAHA)


def _uklid_testu(conn):
    conn.execute("DELETE FROM chat.volani_llm WHERE cas >= '2031-01-01'")
    conn.execute("DELETE FROM chat.zprava_log WHERE cas >= '2031-01-01'")


@pytest.fixture
def conn():
    from hlidac_rag import db

    c = db.connect()
    db.migrate(c)
    c.autocommit = True
    _uklid_testu(c)
    c._relace = []
    yield c
    _uklid_testu(c)
    for rid in c._relace:
        kody = [r[0] for r in c.execute("""SELECT z.kod FROM chat.zprava z JOIN chat.konverzace k ON k.id = z.konverzace_id
                                           WHERE k.relace_id = %s AND z.kod IS NOT NULL""", (rid,)).fetchall()]
        c.execute("DELETE FROM chat.volani_llm WHERE relace_id = %s", (rid,))
        c.execute("DELETE FROM chat.zprava_log WHERE relace_id = %s", (rid,))
        c.execute("DELETE FROM chat.relace WHERE id = %s", (rid,))
        c.execute("DELETE FROM chat.otisk_odpovedi WHERE kod = ANY(%s)", (kody,))
    c.close()


@pytest.fixture
def relace(conn):
    from hlidac_rag.chat import uloziste

    def nova() -> str:
        rid = uloziste.zaloz_relaci(conn)
        conn._relace.append(rid)
        return rid
    return nova


def _volani(conn, relace_id, ip, cena, cas, zprava_id=None):
    from hlidac_rag.chat import uloziste

    uloziste.zapis_volani_llm(conn, zprava_id=zprava_id, relace_id=relace_id, otisk_ip=ip, model="m", vstup=1, vystup=1,
                              cache_cteni=0, cache_zapis=0, cena_usd=cena, ms=1, stop_reason="end_turn", cas=cas)


def test_migrace_je_idempotentni():
    from hlidac_rag import db

    c = db.connect()
    try:
        db.migrate(c)
        db.migrate(c)
        assert c.execute("SELECT to_regclass('chat.volani_llm') IS NOT NULL").fetchone()[0]
        idx = [r[0] for r in c.execute("SELECT indexdef FROM pg_indexes WHERE schemaname='chat' AND tablename='volani_llm'")]
        for sloupce in ("(cas)", "(relace_id, cas)", "(otisk_ip, cas)"):
            assert any(sloupce in d for d in idx), sloupce
        sloupce = [r[0] for r in c.execute("""SELECT column_name FROM information_schema.columns
                                              WHERE table_schema='chat' AND table_name='volani_llm'""")]
        for s in ("zprava_id relace_id otisk_ip model vstup vystup cache_cteni cache_zapis cena_usd ms stop_reason cas").split():
            assert s in sloupce, s
        # surová IP se nikde neukládá
        assert not c.execute("""SELECT column_name FROM information_schema.columns WHERE table_schema='chat'
                                AND column_name IN ('ip', 'ip_adresa', 'adresa')""").fetchall()
    finally:
        c.rollback()
        c.close()


def test_utrata_scita_den_hodinu_relaci_ip(conn, relace):
    from hlidac_rag.chat import uloziste

    r1, r2 = relace(), relace()
    _volani(conn, r1, "ipA", 0.10, TED - timedelta(minutes=90))
    _volani(conn, r1, "ipA", 0.05, TED - timedelta(minutes=30))
    _volani(conn, r2, "ipA", 0.02, TED - timedelta(minutes=10))
    _volani(conn, r1, "ipA", 0.50, TED - timedelta(days=1))
    u = uloziste.utrata(conn, r1, "ipA", TED)
    assert u.den == pytest.approx(0.17)
    assert u.hodina == pytest.approx(0.07)
    assert u.relace == pytest.approx(0.15)
    assert u.ip == pytest.approx(0.17)
    assert u.mesic == pytest.approx(0.67)
    cizi = uloziste.utrata(conn, relace(), "ipB", TED)
    assert (cizi.relace, cizi.ip) == (0, 0) and cizi.den == pytest.approx(0.17)


def test_den_je_prazsky(conn, relace):
    from hlidac_rag.chat import uloziste

    r = relace()
    _volani(conn, r, "ip", 0.30, datetime(2031, 6, 14, 23, 30, tzinfo=timezone.utc))   # 15. 6. 01:30 praha
    _volani(conn, r, "ip", 0.20, datetime(2031, 6, 14, 21, 30, tzinfo=timezone.utc))   # 14. 6. 23:30 praha
    u = uloziste.utrata(conn, r, "ip", TED)
    assert u.den == pytest.approx(0.30)
    assert u.mesic == pytest.approx(0.50)
    # měsíc je také pražský: 31. 5. 23:30 praha je ještě květen
    _volani(conn, r, "ip", 0.07, datetime(2031, 5, 31, 21, 30, tzinfo=timezone.utc))
    assert uloziste.utrata(conn, r, "ip", TED).mesic == pytest.approx(0.50)
    with pytest.raises(ValueError):
        uloziste.utrata(conn, r, "ip", datetime(2031, 6, 15, 12, 0))


def test_pocitadlo_zprav(conn, relace):
    from hlidac_rag.chat import uloziste

    r = relace()
    k = uloziste.zaloz_konverzaci(conn, r)
    now = datetime.now(PRAHA)
    uloziste.zaloz_odpoved(conn, k, "a", {})
    uloziste.zaloz_odpoved(conn, k, "b", {})
    u = uloziste.utrata(conn, r, "ip", now)
    assert u.zprav_min == 2
    assert u.zprav_den in (0, 2)   # 0 jen při přechodu přes pražskou půlnoc mezi dvěma řádky testu
    assert uloziste.utrata(conn, r, "ip", now + timedelta(minutes=5)).zprav_min == 0
    # smazání konverzace počítadlo nevynuluje
    assert uloziste.smaz_konverzaci(conn, k, r)
    assert uloziste.utrata(conn, r, "ip", now).zprav_min == 2


def test_relace_a_overeni(conn, relace):
    from hlidac_rag.chat import uloziste

    r = relace()
    zaklad = uloziste.relace(conn, r)
    assert zaklad["id"] == r and zaklad["overeno_do"] is None and zaklad["zprav_od_overeni"] == 0 and zaklad["otisk"] is None
    assert uloziste.relace(conn, "neexistuje") is None
    do = datetime(2031, 6, 15, 14, 0, tzinfo=PRAHA)
    uloziste.pricti_zpravu_od_overeni(conn, r)
    uloziste.zapis_overeni(conn, r, "otisk1", do)
    v = uloziste.relace(conn, r)
    assert v["overeno_do"] == do and v["otisk"] == "otisk1" and v["zprav_od_overeni"] == 0
    assert uloziste.pricti_zpravu_od_overeni(conn, r) == 1
    uloziste.pricti_zpravu_od_overeni(conn, r)
    assert uloziste.relace(conn, r)["zprav_od_overeni"] == 2


def test_konverzace_ciziho_je_none(conn, relace):
    from hlidac_rag.chat import uloziste

    r1, r2 = relace(), relace()
    k = uloziste.zaloz_konverzaci(conn, r1)
    assert len(k) == 32 and int(k, 16) >= 0
    assert uloziste.konverzace(conn, k, r1)["id"] == k
    assert uloziste.konverzace(conn, k, r2) is None
    assert uloziste.konverzace(conn, "x" * 32, r1) is None
    assert uloziste.smaz_konverzaci(conn, k, r2) is False
    assert uloziste.konverzace(conn, k, r1) is not None
    assert uloziste.konverzace(conn, uloziste.zaloz_konverzaci(conn, r1, eval_=True), r1)["eval"] is True


def test_kod_odpovedi_ma_8_znaku_a_je_unikatni(conn, relace, monkeypatch):
    from hlidac_rag.chat import uloziste

    r = relace()
    k = uloziste.zaloz_konverzaci(conn, r)
    kody = {uloziste.zaloz_odpoved(conn, k, f"q{i}", {})[1] for i in range(40)}
    assert len(kody) == 40 and all(len(x) == 8 and x.isalnum() for x in kody)
    # kolize s existujícím kódem (i s kódem smazané konverzace) se přeskočí
    zname = next(iter(kody))
    uloziste.smaz_konverzaci(conn, k, r)
    k2 = uloziste.zaloz_konverzaci(conn, r)
    nabidky = iter([zname, zname, "abcdefg2"])
    monkeypatch.setattr(uloziste, "_nahodny_kod", lambda: next(nabidky))
    _, kod = uloziste.zaloz_odpoved(conn, k2, "q", {})
    assert kod == "abcdefg2"
    conn.execute("DELETE FROM chat.otisk_odpovedi WHERE kod = ANY(%s)", (list(kody | {kod}),))


def test_probiha_se_prubezne_uklada(conn, relace):
    from hlidac_rag.chat import uloziste

    r = relace()
    k = uloziste.zaloz_konverzaci(conn, r)
    zid, kod = uloziste.zaloz_odpoved(conn, k, "Co je tisk 221?", {"znacka": "[tisk 221]", "karta": "x"})
    assert uloziste.tahy(conn, k) == []   # rozpracovaný tah se do historie nedostane
    uloziste.prubezne(conn, zid, "Tisk 221 je ")
    uloziste.prubezne(conn, zid, "Tisk 221 je novela.")
    stav, text = conn.execute("SELECT stav, text FROM chat.zprava WHERE id = %s", (zid,)).fetchone()
    assert (stav, text) == ("probiha", "Tisk 221 je novela.")
    uloziste.dokonci(conn, zid, "Tisk 221 je novela [1].", "ok", "claude-x", [1], {"presne": 0})
    stav, text, cit, ov, model = conn.execute(
        "SELECT stav, text, citovane, overeni, model FROM chat.zprava WHERE id = %s", (zid,)).fetchone()
    assert (stav, text, cit, ov, model) == ("ok", "Tisk 221 je novela [1].", [1], {"presne": 0}, "claude-x")
    # po dokončení průběžný zápis nic nepřepíše
    uloziste.prubezne(conn, zid, "jiný text")
    assert conn.execute("SELECT text FROM chat.zprava WHERE id = %s", (zid,)).fetchone()[0].endswith("[1].")
    tahy = uloziste.tahy(conn, k)
    assert len(tahy) == 1
    assert (tahy[0].text_uzivatele, tahy[0].znacka, tahy[0].text_bota, tahy[0].zdroje) == (
        "Co je tisk 221?", "[tisk 221]", "Tisk 221 je novela [1].", [1])
    assert uloziste.existuje_kod(conn, kod) is not None
    with pytest.raises(ValueError):
        uloziste.dokonci(conn, zid, "x", "probiha", "m", [], {})


def test_zdroje_se_ukladaji_a_citovan_se_nevraci(conn, relace):
    from hlidac_rag.chat import uloziste
    from hlidac_rag.chat.registr import Zdroj

    r = relace()
    k = uloziste.zaloz_konverzaci(conn, r)
    uloziste.uloz_zdroje(conn, k, [Zdroj(1, "tisk:221", "tisk", "Tisk 221", {"ct": 221}, False),
                                   Zdroj(2, "pn:5", "pn", "PN 5", {}, True)])
    uloziste.uloz_zdroje(conn, k, [Zdroj(1, "tisk:221", "tisk", "Tisk 221", {"ct": 221}, True)])
    z = uloziste.zdroje(conn, k)
    assert [(x.n, x.klic, x.citovan) for x in z] == [(1, "tisk:221", True), (2, "pn:5", True)]
    assert z[0].meta == {"ct": 221}


def test_smazani_maze_zpravy_zdroje_a_nechava_otisk(conn, relace):
    from hlidac_rag.chat import uloziste
    from hlidac_rag.chat.registr import Zdroj

    r = relace()
    k = uloziste.zaloz_konverzaci(conn, r)
    zid, kod = uloziste.zaloz_odpoved(conn, k, "otázka s osobním údajem", {})
    uloziste.uloz_zdroje(conn, k, [Zdroj(1, "tisk:1", "tisk", "T", {}, False)])
    uloziste.zapis_volani_nastroje(conn, zprava_id=zid, nastroj="get_tisk", argumenty={"ct": 1}, ms=3, znaku=10, chyba=None)
    _volani(conn, r, "ip", 0.04, TED, zprava_id=zid)
    uloziste.dokonci(conn, zid, "odpověď", "ok", "m", [], {})
    assert uloziste.smaz_konverzaci(conn, k, r) is True
    assert uloziste.smaz_konverzaci(conn, k, r) is False
    for tab in ("zprava", "zdroj"):
        assert conn.execute(f"SELECT count(*) FROM chat.{tab} WHERE konverzace_id = %s", (k,)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM chat.volani_nastroje WHERE zprava_id = %s", (zid,)).fetchone()[0] == 0
    assert uloziste.existuje_kod(conn, kod) is not None          # otisk zůstal
    assert uloziste.utrata(conn, r, "ip", TED).relace == pytest.approx(0.04)   # cena také


def test_hodnoceni_a_nahlaseni(conn, relace):
    from hlidac_rag.chat import uloziste

    r = relace()
    k = uloziste.zaloz_konverzaci(conn, r)
    zid, kod = uloziste.zaloz_odpoved(conn, k, "otázka", {})
    uloziste.dokonci(conn, zid, "odpověď", "ok", "m", [], {})
    assert uloziste.existuje_kod(conn, "nenexist") is None
    assert uloziste.hodnoceni(conn, kod, 1) is True
    assert uloziste.hodnoceni(conn, kod, -1) is True
    assert conn.execute("SELECT palec FROM chat.hodnoceni WHERE kod = %s", (kod,)).fetchone()[0] == -1
    assert uloziste.hodnoceni(conn, "nenexist", 1) is False
    with pytest.raises(ValueError):
        uloziste.hodnoceni(conn, kod, 5)
    assert uloziste.nahlas(conn, kod, "špatná citace") is True
    assert uloziste.nahlas(conn, "nenexist", "x") is False
    # nahlášená odpověď přežije smazání konverzace (kopie textu)
    uloziste.smaz_konverzaci(conn, k, r)
    t = conn.execute("SELECT text_otazky, text_odpovedi, duvod FROM chat.nahlaseni WHERE kod = %s", (kod,)).fetchone()
    assert t == ("otázka", "odpověď", "špatná citace")
    conn.execute("DELETE FROM chat.nahlaseni WHERE kod = %s", (kod,))


def test_uklid_po_30_dnech(conn, relace):
    from hlidac_rag.chat import uloziste
    from hlidac_rag.chat.registr import Zdroj

    ted = datetime.now(PRAHA)
    r = relace()
    stara = uloziste.zaloz_konverzaci(conn, r)
    nova = uloziste.zaloz_konverzaci(conn, r)
    zid, kod = uloziste.zaloz_odpoved(conn, stara, "stará", {})
    uloziste.dokonci(conn, zid, "odpověď", "ok", "m", [], {})
    uloziste.uloz_zdroje(conn, stara, [Zdroj(1, "tisk:1", "tisk", "T", {}, False)])
    conn.execute("UPDATE chat.konverzace SET posledni_aktivita = %s WHERE id = %s", (ted - timedelta(days=31), stara))
    conn.execute("UPDATE chat.konverzace SET posledni_aktivita = %s WHERE id = %s", (ted - timedelta(days=29), nova))
    # otisky IP: 49 h se maže, 47 h zůstane; relace s otiskem z ověření
    _volani(conn, r, "stary", 0.01, ted - timedelta(hours=49))
    _volani(conn, r, "cerstvy", 0.01, ted - timedelta(hours=47))
    uloziste.zapis_overeni(conn, r, "otisk", ted + timedelta(hours=1))
    conn.execute("UPDATE chat.relace SET overeni_cas = %s WHERE id = %s", (ted - timedelta(hours=49), r))
    conn.execute("DELETE FROM chat.otisk_odpovedi WHERE kod IN ('uklid-st', 'uklid-no')")
    # otisk odpovědi: 13 měsíců se maže, 11 měsíců zůstane
    conn.execute("INSERT INTO chat.otisk_odpovedi (kod, hash, datum) VALUES ('uklid-st', 'h', %s), ('uklid-no', 'h', %s)",
                 ((ted - timedelta(days=13 * 31)).date(), (ted - timedelta(days=11 * 30)).date()))
    stara_relace = uloziste.zaloz_relaci(conn)
    conn.execute("UPDATE chat.relace SET posledni_aktivita = %s WHERE id = %s", (ted - timedelta(days=31), stara_relace))

    vysledek = uloziste.uklid(conn, ted)

    assert uloziste.konverzace(conn, stara, r) is None
    assert uloziste.konverzace(conn, nova, r) is not None
    assert conn.execute("SELECT count(*) FROM chat.zdroj WHERE konverzace_id = %s", (stara,)).fetchone()[0] == 0
    assert uloziste.existuje_kod(conn, kod) is not None
    assert uloziste.relace(conn, stara_relace) is None
    assert uloziste.relace(conn, r)["otisk"] is None
    ips = {x[0] for x in conn.execute("SELECT otisk_ip FROM chat.volani_llm WHERE relace_id = %s", (r,))}
    assert ips == {None, "cerstvy"}
    assert uloziste.existuje_kod(conn, "uklid-st") is None and uloziste.existuje_kod(conn, "uklid-no") is not None
    assert vysledek["konverzace"] >= 1 and vysledek["relace"] >= 1 and vysledek["otisky_ip"] >= 1
    assert vysledek["otisky_odpovedi"] >= 1
    conn.execute("DELETE FROM chat.otisk_odpovedi WHERE kod IN ('uklid-st', 'uklid-no')")


# --- Review Focus 2: dvě souběžné zprávy jedné relace ---

def test_dva_tahy_jedne_relace_nejedou_zaroven(conn, relace):
    from hlidac_rag import db
    from hlidac_rag.chat import uloziste

    r = relace()
    druhe = db.connect(autocommit=True)
    try:
        token1, u1 = uloziste.zahaj_tah(conn, r, "ip", TED)
        assert u1.relace == 0
        # druhý tah téže relace je odmítnut, dokud první neskončí, a to i z jiného spojení
        assert uloziste.zahaj_tah(druhe, r, "ip", TED) is None
        assert uloziste.zahaj_tah(conn, r, "ip", TED) is None
        # první zapíše cenu a skončí: druhý už cenu vidí
        _volani(conn, r, "ip", 0.30, TED)
        assert uloziste.skonci_tah(conn, r, token1) is True
        token2, u2 = uloziste.zahaj_tah(druhe, r, "ip", TED)
        assert u2.relace == pytest.approx(0.30) and token2 != token1
        # jiná relace tím blokována není
        assert uloziste.zahaj_tah(druhe, relace(), "ip", TED) is not None
    finally:
        druhe.close()


def test_tah_po_padu_se_uvolni_az_po_vyprseni(conn, relace):
    from hlidac_rag.chat import uloziste

    r = relace()
    assert uloziste.zahaj_tah(conn, r, "ip", TED, drz_s=120) is not None
    assert uloziste.zahaj_tah(conn, r, "ip", TED + timedelta(seconds=119), drz_s=120) is None
    assert uloziste.zahaj_tah(conn, r, "ip", TED + timedelta(seconds=121), drz_s=120) is not None


def test_stary_tah_neuvolni_prevzaty(conn, relace):
    from hlidac_rag.chat import uloziste

    r = relace()
    token_a, _ = uloziste.zahaj_tah(conn, r, "ip", TED, drz_s=120)
    token_b, _ = uloziste.zahaj_tah(conn, r, "ip", TED + timedelta(seconds=121), drz_s=120)   # B převzal po vypršení A
    assert uloziste.skonci_tah(conn, r, token_a) is False                  # A skončil pozdě: B zůstává vlastníkem
    assert uloziste.zahaj_tah(conn, r, "ip", TED + timedelta(seconds=122), drz_s=120) is None   # C neprojde
    assert uloziste.skonci_tah(conn, r, token_b) is True
    assert uloziste.zahaj_tah(conn, r, "ip", TED + timedelta(seconds=123), drz_s=120) is not None


def test_neexistujici_relace_neni_obsazena_relace(conn):
    from hlidac_rag.chat import uloziste

    with pytest.raises(uloziste.RelaceNeexistuje):
        uloziste.zahaj_tah(conn, "neexistuje", "ip", TED)


def test_zahaj_tah_vyzaduje_autocommit(conn, relace):
    from hlidac_rag import db
    from hlidac_rag.chat import uloziste

    r = relace()
    c = db.connect()   # bez autocommit
    try:
        with pytest.raises(RuntimeError, match="autocommit"):
            uloziste.zahaj_tah(c, r, "ip", TED)
    finally:
        c.close()


def test_dokonci_nepřepíše_hotovou_odpoved(conn, relace):
    from hlidac_rag.chat import uloziste

    r = relace()
    k = uloziste.zaloz_konverzaci(conn, r)
    zid, kod = uloziste.zaloz_odpoved(conn, k, "q", {})
    assert uloziste.dokonci(conn, zid, "první", "ok", "m", [1], {}) is True
    assert uloziste.dokonci(conn, zid, "druhá", "chyba", "m2", [], {}) is False
    stav, text, model = conn.execute("SELECT stav, text, model FROM chat.zprava WHERE id = %s", (zid,)).fetchone()
    assert (stav, text, model) == ("ok", "první", "m")
    h = conn.execute("SELECT hash FROM chat.otisk_odpovedi WHERE kod = %s", (kod,)).fetchone()[0]
    assert h == __import__("hashlib").sha256("první".encode()).hexdigest()


def test_soubeh_vlaken_pusti_prave_jeden_tah(conn, relace):
    from hlidac_rag import db
    from hlidac_rag.chat import uloziste

    r = relace()
    n = 8
    spojeni = [db.connect(autocommit=True) for _ in range(n)]
    start = threading.Barrier(n)
    vysledky: list = [None] * n

    def beh(i):
        start.wait()
        vysledky[i] = uloziste.zahaj_tah(spojeni[i], r, "ip", TED)

    try:
        vlakna = [threading.Thread(target=beh, args=(i,)) for i in range(n)]
        [v.start() for v in vlakna]
        [v.join(30) for v in vlakna]
    finally:
        [c.close() for c in spojeni]
    assert sum(v is not None for v in vysledky) == 1
