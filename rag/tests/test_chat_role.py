"""Role chat_ro a chat_rw: chat čte jen vyjmenované tabulky a zapisuje jen do schématu chat (spec chatbota 21.1).

Nad PostgreSQL (bez DATABASE_URL se přeskočí, jen databáze *_test). Tabulky a data webu jsou stejné jako v testu role
mcp_ro (test_role_mcp.py): GRANT jmenuje tabulky v public, v CI je jinak nikdo nevytvoří. Modul založí role chat_ro,
chat_rw a mcp_ro (kontrola, že MCP nečte chat.*) a na konci vše uklidí. Když tabulky webu nebo některá z rolí v DB už
jsou, modul se přeskočí (cizí data).

Nástroje, kontext, karta a náhled běží přes PgData nad poolem pod chat_ro (stejná cesta jako ve službě). Služba chyby
databáze sama zachytí a nahradí větou („data se aktualizují“), proto kurzor `_Kurzor` každou chybu databáze zapíše:
chybějící GRANT = červený test, i když ho služba schová.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool
from test_role_mcp import CT, DATA, HLAS, OSOBA, PN, WEB_TABULKY

pytestmark = [pytest.mark.pg,
              pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)")]

HESLO_RO = "Heslo21ro-" + secrets.token_hex(12)
HESLO_RW = "Heslo21rw-" + secrets.token_hex(12)
HESLO_MCP = "Heslo21mcp-" + secrets.token_hex(12)
ROLE = ("chat_ro", "chat_rw", "mcp_ro")
TED = datetime(2031, 7, 1, 12, 0, tzinfo=ZoneInfo("Europe/Prague"))   # útrata testu mimo skutečné dny

# minimální argumenty pro každý nástroj chatu (nový nástroj bez položky = červený test úplnosti)
MIN_ARGUMENTY = {
    "get_tisk": {"ct": CT, "vcetne": ["rozbor", "zakony", "kroky", "pn", "upozorneni", "body"]},
    "seznam_tisku": {"q": "daních"},
    "tisky_menici_zakon": {"sb": "586/1992"},
    "tisk_podle_sbirky": {"sb": "90/2026"},
    "pn_k_tisku": {"ct": CT},
    "get_pn": {"cislo": PN},
    "osnova_textu": {"ct": CT},
    "get_cel": {"ct": CT, "poradi": 0},
    "hledej_v_textech": {"dotaz": "daň"},
    "rozprava": {"ct": CT},
    "projevy_k_tisku": {"ct": CT},
    "projevy_recnika": {"osoba": "Novák"},
    "get_projev": {"klic": "10/1/1/1"},
    "hledej_v_projevech": {"dotaz": "daně z příjmů"},
    "najdi_osobu": {"jmeno": "Novák"},
    "hlasovani_k_tisku": {"ct": CT},
    "get_hlasovani": {"id_hlasovani": HLAS, "jmenovite": True},
    "upozorneni": {"ct": CT},
    "pripravovane": {"q": "daních"},
}
# klíče náhledů všech typů zdrojů (nahled._STAVBY)
KLICE_NAHLEDU = (f"tisk:{CT}", f"pn:{PN}", f"cel:tisk:{CT}:puvodni:Čl. I", f"cel:tisk:{CT}:schvalene:poradi 0",
                 f"cel:pn:{PN}:bod 1", "projev:10/1/1/1", f"hlas:{HLAS}", "zakon:586/1992", f"rozbor:tisk:{CT}",
                 f"rozbor:pn:{PN}")

CHYBY_DB: list[psycopg.Error] = []


class _Kurzor(psycopg.Cursor):
    """Kurzor, který každou chybu databáze zapíše do CHYBY_DB (služba ji jinak schová za obecnou větu)."""

    def execute(self, *a, **kw):
        try:
            return super().execute(*a, **kw)
        except psycopg.Error as e:
            CHYBY_DB.append(e)
            raise


def _dsn(role: str, heslo: str) -> str:
    return make_conninfo(os.environ["DATABASE_URL"], user=role, password=heslo)


def _jedna(conn, sql: str, *params):
    return conn.execute(sql, params).fetchone()[0]


@pytest.fixture(scope="module")
def prostredi():
    """Tabulky webu, migrace, data a role chat_ro, chat_rw, mcp_ro; po modulu úklid."""
    from hlidac_rag import db, role

    su = db.connect()
    if not _jedna(su, "SELECT current_database()").endswith("_test"):
        su.close()
        pytest.skip("databáze nekončí na _test")
    existuji = [t for t in WEB_TABULKY if _jedna(su, "SELECT to_regclass(%s) IS NOT NULL", f"public.{t}")]
    roli = _jedna(su, "SELECT count(*) FROM pg_roles WHERE rolname = ANY(%s)", list(ROLE))
    if existuji or roli:
        su.close()
        pytest.skip(f"v testovací DB už jsou tabulky webu nebo role {', '.join(ROLE)} ({', '.join(existuji)})")
    relace: list[str] = []
    citac = None
    try:   # úklid i po chybě při přípravě (jinak by tabulky webu zůstaly a další běhy se přeskočily)
        for ddl in WEB_TABULKY.values():
            su.execute(ddl)
        db.migrate(su)
        for sql, params in DATA:
            su.execute(sql, params)
        citac = su.execute(
            "SELECT den, pocet FROM rag.dotazy_den WHERE den = (now() AT TIME ZONE 'utc')::date").fetchone()
        su.commit()
        role.nastav(su, {"chat_ro": HESLO_RO, "chat_rw": HESLO_RW, "mcp_ro": HESLO_MCP})
        yield {"su": su, "relace": relace}
    finally:
        su.rollback()
        for rid in relace:
            kody = [r[0] for r in su.execute(
                """SELECT z.kod FROM chat.zprava z JOIN chat.konverzace k ON k.id = z.konverzace_id
                   WHERE k.relace_id = %s AND z.kod IS NOT NULL""", (rid,))]
            su.execute("DELETE FROM chat.volani_llm WHERE relace_id = %s", (rid,))
            su.execute("DELETE FROM chat.zprava_log WHERE relace_id = %s", (rid,))
            su.execute("DELETE FROM chat.relace WHERE id = %s", (rid,))
            su.execute("DELETE FROM chat.nahlaseni WHERE kod = ANY(%s)", (kody,))
            su.execute("DELETE FROM chat.otisk_odpovedi WHERE kod = ANY(%s)", (kody,))
        su.execute("DELETE FROM rag.dotazy_den WHERE den = (now() AT TIME ZONE 'utc')::date")
        if citac:
            su.execute("INSERT INTO rag.dotazy_den (den, pocet) VALUES (%s, %s)", citac)
        su.execute("DROP TABLE IF EXISTS public.tajne_test21, chat.nova_test21")
        for t in WEB_TABULKY:
            su.execute(f"DROP TABLE IF EXISTS public.{t} CASCADE")   # s nimi zmizí i pohledy v mcp
        for r in ROLE:
            if _jedna(su, "SELECT count(*) FROM pg_roles WHERE rolname = %s", r):
                su.execute(f"DROP OWNED BY {r}")
                su.execute(f"DROP ROLE {r}")
        su.execute(f"GRANT TEMPORARY ON DATABASE {_jedna(su, 'SELECT current_database()')} TO PUBLIC")
        su.commit()
        su.close()


@pytest.fixture
def superuser_conn(prostredi):
    su = prostredi["su"]
    yield su
    su.rollback()


def _spojeni(role: str, heslo: str, **kw):
    c = psycopg.connect(_dsn(role, heslo), **kw)
    yield c
    c.close()


@pytest.fixture
def chat_ro_conn(prostredi):
    yield from _spojeni("chat_ro", HESLO_RO)


@pytest.fixture
def chat_rw_conn(prostredi):
    yield from _spojeni("chat_rw", HESLO_RW, autocommit=True)


@pytest.fixture
def mcp_ro_conn(prostredi):
    yield from _spojeni("mcp_ro", HESLO_MCP)


@pytest.fixture
def data_pod_roli(prostredi):
    """PgData nad poolem pod chat_ro (jako ve službě), kurzor zapisuje chyby databáze."""
    from hlidac_rag.chat import karta
    from hlidac_rag.chat.pg import PgData

    karta.vycisti_cache()
    CHYBY_DB.clear()
    pool = ConnectionPool(_dsn("chat_ro", HESLO_RO), min_size=1, max_size=2, open=True, timeout=5,
                          kwargs={"cursor_factory": _Kurzor})
    try:
        yield PgData(pool)
    finally:
        pool.close()
        karta.vycisti_cache()


def _bez_chyb_db(co: str) -> None:
    chyby = [f"{type(e).__name__}: {e}" for e in CHYBY_DB]
    CHYBY_DB.clear()
    if chyby:
        pytest.fail(f"{co}: chyba databáze pod chat_ro ({'; '.join(chyby)})")


# ---------------------------------------------------------------- chat_ro: nástroje, kontext, karta, náhled

def test_kazdy_nastroj_chatu_ma_minimalni_argumenty():
    from hlidac_rag.chat import nastroje

    assert set(MIN_ARGUMENTY) == set(nastroje.NAZVY)


@pytest.mark.parametrize("nastroj", sorted(MIN_ARGUMENTY))
def test_kazdy_nastroj_chatu_funguje_pod_chat_ro(data_pod_roli, nastroj):
    from hlidac_rag.chat import nastroje

    v = data_pod_roli.nastroj(nastroj, MIN_ARGUMENTY[nastroj])
    _bez_chyb_db(nastroj)   # chybějící GRANT (služba by ho schovala za „data se aktualizují“)
    assert v.text not in (nastroje.DB_CHYBA, nastroje.DB_TIMEOUT), v.text
    assert "selhal na chybě serveru" not in v.text


@pytest.mark.parametrize("nastroj", ["get_tisk", "pn_k_tisku", "get_pn", "hlasovani_k_tisku", "get_hlasovani",
                                     "upozorneni", "pripravovane", "seznam_tisku"])   # osoby: steno.osoba je prázdná
def test_nastroj_pod_chat_ro_vraci_data(data_pod_roli, nastroj):
    v = data_pod_roli.nastroj(nastroj, MIN_ARGUMENTY[nastroj])
    assert not v.chyba and v.polozky, (nastroj, v.text)


def test_kontext_a_karta_pod_chat_ro(data_pod_roli):
    from hlidac_rag.chat.kontext import Kontext

    k = data_pod_roli.over_kontext(Kontext("tisk", ct=CT, pn=PN, hlasovani=HLAS))
    assert k == Kontext("tisk", ct=CT, pn=PN, hlasovani=HLAS)
    assert data_pod_roli.over_kontext(Kontext("pripravovane", pid="KORNTEST")).pid == "KORNTEST"
    for kontext in (k, Kontext("zakon", z="586/1992"), Kontext("pripravovane", pid="KORNTEST")):
        text, _ = data_pod_roli.karta(kontext, 1200)
        assert text, kontext
    _bez_chyb_db("kontext a karta")


@pytest.mark.parametrize("klic", KLICE_NAHLEDU)
def test_nahled_pod_chat_ro(data_pod_roli, klic):
    data_pod_roli.nahled(klic, None)   # None = zdroj nebo jeho text v datech není (texty test nezakládá)
    _bez_chyb_db(klic)


def test_jmena_osob_a_citac_embeddingu_pod_chat_ro(data_pod_roli, superuser_conn):
    from hlidac_rag.embed import hledani

    assert data_pod_roli.jmena_osob("Řekl to Novák.") == []   # steno.osoba je prázdná, jde jen o oprávnění
    _bez_chyb_db("steno.osoba")
    hledani.nastav_citac(lambda: psycopg.connect(_dsn("chat_ro", HESLO_RO), connect_timeout=2))
    try:
        assert hledani._zapocti_dotaz() is True
    finally:
        hledani.nastav_citac(None)
    assert _jedna(superuser_conn, "SELECT pocet FROM rag.dotazy_den WHERE den = (now() AT TIME ZONE 'utc')::date") >= 1


def test_chat_ro_pricte_dotaz_funkci(chat_ro_conn):
    chat_ro_conn.execute("SET TRANSACTION READ WRITE")
    assert _jedna(chat_ro_conn, "SELECT rag.pricti_dotaz()") >= 1
    chat_ro_conn.commit()


# ---------------------------------------------------------------- chat_ro: co nesmí

@pytest.mark.parametrize("sql", ["SELECT * FROM mcp.token", "SELECT * FROM mcp.volani", "SELECT * FROM chat.zprava",
                                 "SELECT * FROM chat.relace", "SELECT * FROM chat.volani_llm", "SELECT * FROM meta",
                                 "SELECT * FROM reel_state", "SELECT * FROM sync_beh", "SELECT * FROM mcp.meta_verejne",
                                 "SELECT * FROM priprava"])
def test_chat_ro_necte_mcp_token_ani_chat(chat_ro_conn, sql):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        chat_ro_conn.execute(sql)


@pytest.mark.parametrize("sql", [
    "INSERT INTO tisky DEFAULT VALUES", "UPDATE steno.projev SET text = ''", "DELETE FROM pn",
    "CREATE TABLE public.x (a int)", "CREATE TEMP TABLE x (a int)", "TRUNCATE texty.cel",
    "UPDATE mcp.token SET zrusen = NULL", "INSERT INTO rag.dotazy_den VALUES (current_date, 0)",
    "CREATE SCHEMA x", "CREATE TABLE steno.x (a int)", "CREATE VIEW mcp.x AS SELECT 1",
    "CREATE FUNCTION public.x() RETURNS int LANGUAGE sql AS 'SELECT 1'",
    "INSERT INTO chat.relace (id) VALUES ('x')", "DELETE FROM chat.volani_llm", "CREATE TABLE chat.x (a int)",
])
def test_chat_ro_nezapisuje_ani_po_read_write(chat_ro_conn, sql):
    chat_ro_conn.execute("SET TRANSACTION READ WRITE")   # i když si roli „odemkne“, GRANT chybí
    with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction)):
        chat_ro_conn.execute(sql)


@pytest.mark.parametrize("sql", ["SELECT mcp.over_token('x')", "SELECT mcp.volani_od(1, now())",
                                 "SELECT mcp.zapis_volani(NULL, 'x', 1, false)"])
def test_chat_ro_nevola_funkce_mcp(chat_ro_conn, sql):
    chat_ro_conn.execute("SET TRANSACTION READ WRITE")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        chat_ro_conn.execute(sql)


def test_chat_ro_vychozi_transakce_jen_pro_cteni(chat_ro_conn):
    assert _jedna(chat_ro_conn, "SHOW default_transaction_read_only") == "on"
    assert _jedna(chat_ro_conn, "SHOW statement_timeout") == "5s"
    assert _jedna(chat_ro_conn, "SHOW idle_in_transaction_session_timeout") == "30s"
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        chat_ro_conn.execute("SELECT rag.pricti_dotaz()")


def test_chat_rw_ma_casove_limity(chat_rw_conn):
    """Zaseknutý dotaz nebo opuštěná transakce nesmí držet spojení chat_rw (limit 5 spojení)."""
    assert _jedna(chat_rw_conn, "SHOW statement_timeout") == "10s"
    assert _jedna(chat_rw_conn, "SHOW idle_in_transaction_session_timeout") == "30s"


def test_chat_ro_cte_presne_vycet(superuser_conn):
    """V public, mcp a chat čte chat_ro přesně TABULKY_CTENI; ve schématech steno, texty a rag všechno."""
    from hlidac_rag import role

    citelne = {r[0] for r in superuser_conn.execute(
        """SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relkind IN ('r', 'v', 'm', 'p') AND n.nspname IN ('public', 'mcp', 'chat')
             AND has_table_privilege('chat_ro', c.oid, 'SELECT')""")}
    assert citelne == {f"public.{t}" for t in role.TABULKY_CTENI}
    necitelne = superuser_conn.execute(
        """SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relkind IN ('r', 'v', 'm', 'p') AND n.nspname = ANY(%s)
             AND NOT has_table_privilege('chat_ro', c.oid, 'SELECT')""", (list(role.SCHEMATA_CTENI),)).fetchall()
    assert necitelne == []
    assert _jedna(superuser_conn, "SELECT has_schema_privilege('chat_ro', 'chat', 'USAGE')") is False
    assert _jedna(superuser_conn, "SELECT has_schema_privilege('chat_ro', 'mcp', 'USAGE')") is False


def test_nova_tabulka_v_public_neni_pro_chat_citelna(superuser_conn, chat_ro_conn, chat_rw_conn):
    """Žádné ALTER DEFAULT PRIVILEGES v public: nová tabulka webu není čitelná, dokud ji někdo nepřidá do výčtu."""
    superuser_conn.execute("CREATE TABLE public.tajne_test21 (a int)")
    superuser_conn.commit()
    for c in (chat_ro_conn, chat_rw_conn):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM public.tajne_test21")
        if not c.autocommit:
            c.rollback()


# ---------------------------------------------------------------- chat_rw

def test_uloziste_chatu_funguje_pod_chat_rw(prostredi):
    """Celý tah přes PgUloziste nad poolem pod chat_rw (jako ve službě), včetně smazání a úklidu."""
    from hlidac_rag.chat import uloziste
    from hlidac_rag.chat.pg import PgUloziste
    from hlidac_rag.chat.registr import Zdroj
    from hlidac_rag.chat.relace import den_prahy

    pool = ConnectionPool(_dsn("chat_rw", HESLO_RW), min_size=1, max_size=2, open=True, timeout=5,
                          kwargs={"autocommit": True})
    try:
        u = PgUloziste(pool)
        rid = u.zaloz_relaci()
        prostredi["relace"].append(rid)
        otisk = u.otisk_ip("198.51.100.21", den_prahy(TED))
        u.zapis_overeni(rid, otisk, TED + timedelta(hours=6))
        assert u.pricti_zpravu_od_overeni(rid) == 1
        token, utrata = u.zahaj_tah(rid, otisk, TED)
        assert utrata.relace == 0
        kid = u.zaloz_konverzaci(rid)
        zid, kod = u.zaloz_odpoved(kid, "Prošel PN?", {"typ": "tisk", "ct": CT})
        u.prubezne(zid, "Ano")
        u.zapis_volani_llm(zprava_id=zid, relace_id=rid, otisk_ip=otisk, model="claude-sonnet-5-5", vstup=10,
                           vystup=5, cache_cteni=0, cache_zapis=0, cena_usd=0.01, ms=5, stop_reason="end_turn", cas=TED)
        u.zapis_volani_nastroje(zprava_id=zid, nastroj="get_pn", argumenty={"cislo": PN}, ms=3, znaku=10, chyba=None)
        u.uloz_zdroje(kid, [Zdroj(1, f"pn:{PN}", "pn", "PN")])
        assert u.dokonci(zid, "Ano, prošel [1].", "ok", "claude-sonnet-5-5", [1], {})
        assert u.skonci_tah(rid, token)
        assert [z.klic for z in u.zdroje(kid)] == [f"pn:{PN}"] and u.zpravy(kid) and u.tahy(kid)
        assert u.utrata(rid, otisk, TED).relace == pytest.approx(0.01)
        assert u.existuje_kod(kod) and u.hodnoceni(kod, 1) and u.nahlas(kod, "test", otisk)
        assert u.pocet_nahlaseni_dne(datetime.now(ZoneInfo("Europe/Prague"))) >= 1
        assert u.posledni_kontext(kid) == {"typ": "tisk", "ct": CT}
        assert u.souhrn_dne(TED)[0] >= 0
        assert u.smaz_konverzaci(kid, rid)
        with pool.connection() as conn:
            uloziste.uklid(conn, datetime.now(ZoneInfo("Europe/Prague")))   # úklid jde spustit i pod chat_rw
    finally:
        pool.close()


@pytest.mark.parametrize("sql", [
    # public jmenovitě: schéma bez USAGE by search_path přeskočil a chyba by byla „tabulka neexistuje“
    "SELECT * FROM public.tisky", "SELECT * FROM mcp.token", "SELECT * FROM mcp.volani", "SELECT * FROM steno.projev",
    "SELECT * FROM texty.cel", "SELECT * FROM public.meta", "INSERT INTO public.tisky DEFAULT VALUES",
    "UPDATE steno.projev SET text = ''", "UPDATE mcp.token SET zrusen = NULL", "DELETE FROM public.pn",
    "INSERT INTO rag.dotazy_den VALUES (current_date, 0)", "SELECT rag.pricti_dotaz()",
    "SELECT mcp.over_token('x')", "CREATE TABLE chat.x (a int)", "CREATE TABLE public.x (a int)",
    "CREATE TEMP TABLE x (a int)", "TRUNCATE chat.zprava", "CREATE SCHEMA x",
])
def test_chat_rw_nezapisuje_do_dat_a_nevidi_mimo_chat(chat_rw_conn, sql):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        chat_rw_conn.execute(sql)


def test_chat_rw_ma_pravo_jen_ve_schematu_chat(superuser_conn):
    s_pravem = {r[0] for r in superuser_conn.execute(
        """SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relkind IN ('r', 'v', 'm', 'p', 'S') AND n.nspname NOT IN ('pg_catalog', 'information_schema')
             AND (CASE WHEN c.relkind = 'S' THEN has_sequence_privilege('chat_rw', c.oid, 'USAGE')
                       ELSE has_table_privilege('chat_rw', c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER') END)""")}
    assert s_pravem and all(t.startswith("chat.") for t in s_pravem), s_pravem
    bez_prava = superuser_conn.execute(
        """SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'chat' AND c.relkind = 'r'
             AND NOT (has_table_privilege('chat_rw', c.oid, 'SELECT') AND has_table_privilege('chat_rw', c.oid, 'INSERT')
                      AND has_table_privilege('chat_rw', c.oid, 'UPDATE')
                      AND has_table_privilege('chat_rw', c.oid, 'DELETE'))""").fetchall()
    assert bez_prava == []
    for pravo in ("TRUNCATE", "REFERENCES", "TRIGGER"):
        assert _jedna(superuser_conn, "SELECT has_table_privilege('chat_rw', 'chat.zprava', %s)", pravo) is False
    for schema in ("mcp", "steno", "texty", "rag"):   # USAGE na public má PUBLIC, tabulky v něm ale ne
        assert _jedna(superuser_conn, "SELECT has_schema_privilege('chat_rw', %s, 'USAGE')", schema) is False, schema
    assert _jedna(superuser_conn, "SELECT has_schema_privilege('chat_rw', 'chat', 'CREATE')") is False


def test_nova_tabulka_ve_schematu_chat_je_jen_pro_chat_rw(superuser_conn, chat_rw_conn, chat_ro_conn):
    """Migrace (jako hlidac) přidá tabulku do chat: chat_rw ji smí rovnou používat, chat_ro ani mcp_ro ne."""
    superuser_conn.execute("CREATE TABLE chat.nova_test21 (id bigserial PRIMARY KEY, a int)")
    superuser_conn.commit()
    chat_rw_conn.execute("INSERT INTO chat.nova_test21 (a) VALUES (1)")
    assert _jedna(chat_rw_conn, "SELECT count(*) FROM chat.nova_test21") == 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        chat_ro_conn.execute("SELECT * FROM chat.nova_test21")


# ---------------------------------------------------------------- mcp_ro proti chat.* (F30)

@pytest.mark.parametrize("sql", ["SELECT * FROM chat.zprava", "SELECT * FROM chat.volani_llm",
                                 "SELECT * FROM chat.relace", "SELECT * FROM chat.sul_dne"])
def test_mcp_ro_necte_chat(mcp_ro_conn, sql):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        mcp_ro_conn.execute(sql)


# ---------------------------------------------------------------- role

def _opravneni(conn, role: str) -> set:
    return {tuple(r) for r in conn.execute(
        """SELECT n.nspname, c.relname, p.pravo FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace,
                  unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']) AS p(pravo)
           WHERE c.relkind IN ('r', 'v') AND n.nspname NOT IN ('pg_catalog', 'information_schema')
             AND has_table_privilege(%s, c.oid, p.pravo)""", (role,))}


def test_chat_role_nejsou_superuser_a_maji_limit_spojeni(superuser_conn):
    from hlidac_rag import role

    ocekavany_limit = {"chat_ro": role.CHAT_RO_SPOJENI, "chat_rw": role.CHAT_RW_SPOJENI}
    db = _jedna(superuser_conn, "SELECT current_database()")
    for jmeno, limit in ocekavany_limit.items():
        r = superuser_conn.execute("""SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolreplication,
                                             rolbypassrls, rolconnlimit, rolcanlogin
                                      FROM pg_roles WHERE rolname = %s""", (jmeno,)).fetchone()
        assert r == (False, False, False, False, False, False, limit, True), jmeno
        assert _jedna(superuser_conn, "SELECT count(*) FROM pg_auth_members WHERE member = %s::regrole", jmeno) == 0
        assert _jedna(superuser_conn, "SELECT has_database_privilege(%s, %s, 'TEMPORARY')", jmeno, db) is False
        assert _jedna(superuser_conn, "SELECT has_database_privilege(%s, %s, 'CREATE')", jmeno, db) is False
        assert _jedna(superuser_conn, "SELECT rolpassword FROM pg_authid WHERE rolname = %s", jmeno).startswith(
            "SCRAM-SHA-256$")
    # pool čtení (chat/__main__.py POOL_RO) + čítač embeddingů na vlastním spojení ke každému + rezerva
    from hlidac_rag.chat import __main__ as sluzba

    assert role.CHAT_RO_SPOJENI >= 2 * sluzba.POOL_RO + 1
    assert role.CHAT_RW_SPOJENI >= sluzba.POOL_RW + 1   # pool úložiště + spojení kontroly schématu při startu


def test_role_je_idempotentni(superuser_conn):
    from hlidac_rag import role

    pred = {r: _opravneni(superuser_conn, r) for r in ROLE}
    hesla = {"chat_ro": HESLO_RO, "chat_rw": HESLO_RW, "mcp_ro": HESLO_MCP}
    prikazy = role.nastav(superuser_conn, hesla)
    prikazy += role.nastav(superuser_conn, hesla)
    assert {r: _opravneni(superuser_conn, r) for r in ROLE} == pred
    assert prikazy and not [p for p in prikazy if any(h in p for h in hesla.values())]
    assert not [p for p in prikazy if "přeskočeno" in p]


def test_role_jen_chatu_nemeni_mcp_ro(superuser_conn):
    from hlidac_rag import role

    pred = _opravneni(superuser_conn, "mcp_ro")
    heslo = _jedna(superuser_conn, "SELECT rolpassword FROM pg_authid WHERE rolname = 'mcp_ro'")
    prikazy = role.nastav(superuser_conn, {"chat_ro": HESLO_RO})
    assert not [p for p in prikazy if "mcp_ro" in p or "chat_rw" in p]
    assert _opravneni(superuser_conn, "mcp_ro") == pred
    assert _jedna(superuser_conn, "SELECT rolpassword FROM pg_authid WHERE rolname = 'mcp_ro'") == heslo


def test_cli_bez_hesla_vypise_vsechny_promenne(monkeypatch, capsys):
    from hlidac_rag import role

    for p in ("MCP_DB_PASSWORD", "CHAT_DB_RO_PASSWORD", "CHAT_DB_RW_PASSWORD"):
        monkeypatch.delenv(p, raising=False)
    assert role.main() == 2
    err = capsys.readouterr().err
    assert "CHAT_DB_RO_PASSWORD" in err and "CHAT_DB_RW_PASSWORD" in err and "MCP_DB_PASSWORD" in err


def test_zdravi_projde_pod_chat_ro_i_chat_rw(data_pod_roli, prostredi):
    """/chat-api/zdravi (final review Minor 6): SELECT 1 pod oběma rolemi jako ve službě."""
    from hlidac_rag.chat.pg import PgUloziste

    data_pod_roli.zdravi()
    _bez_chyb_db("zdravi chat_ro")
    pool = ConnectionPool(_dsn("chat_rw", HESLO_RW), min_size=1, max_size=1, open=True, timeout=5,
                          kwargs={"autocommit": True})
    try:
        PgUloziste(pool).zdravi()
    finally:
        pool.close()
