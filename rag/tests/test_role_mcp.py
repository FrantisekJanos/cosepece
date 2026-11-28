"""Role mcp_ro: MCP server čte jen vyjmenované tabulky a tokeny ověřuje funkcemi SECURITY DEFINER (spec chatbota 21.1).

Nad PostgreSQL (bez DATABASE_URL se přeskočí, jen databáze *_test). Testy zakládají v public minimální tabulky webu
podle app/Db.php (GRANT jmenuje tabulky v public, vlastní schéma s search_path tu nejde), roli mcp_ro a dva tokeny;
na konci modulu vše uklidí. Když některá z tabulek webu v public už existuje, modul se přeskočí (cizí data).
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import secrets

import anyio
import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool

pytestmark = [pytest.mark.pg,
              pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)")]

HESLO = "Heslo8b-" + secrets.token_hex(12)   # ne „test“: to je i v názvu DB hlidac_test
CT, PN, HLAS, OSOBA = 99901, 99902, 99903, 99904
TOKEN = "test-8b-" + secrets.token_hex(12)
TOKEN_ZRUSENY = "test-8b-zruseny-" + secrets.token_hex(12)

# minimální tabulky webu (sloupce podle app/Db.php včetně addColumn), jen ty, které čtou nástroje, a meta/reel_state
WEB_TABULKY = {
    "tisky": """CREATE TABLE public.tisky (id_tisk int PRIMARY KEY, ct int NOT NULL, druh int NOT NULL,
        typ_navrhu varchar(20) NOT NULL, navrhovatel varchar(255), nazev varchar(255), uplny_nazev text,
        predlozeno varchar(19), faze varchar(60), posledni varchar(19), p90 int NOT NULL DEFAULT 0,
        p90ok int NOT NULL DEFAULT 0, zl int NOT NULL DEFAULT 0, pn_count int NOT NULL DEFAULT 0, pn_last varchar(19),
        zakony text, sbirka varchar(20), score int NOT NULL DEFAULT 0, reasons text, vyhlaseno varchar(10), lhuta int,
        souhrn_pn int, souhrn_pn_at varchar(16), navrhovatel_klub varchar(120), navrhovatele text)""",
    "kroky": """CREATE TABLE public.kroky (id_hist int PRIMARY KEY, id_tisk int NOT NULL, ct int NOT NULL,
        datum varchar(19), typ_stavu int, stav varchar(120), akce_id int, akce varchar(120), id_hlasovani int)""",
    "hlasovani": """CREATE TABLE public.hlasovani (id_hlasovani int PRIMARY KEY, ct int NOT NULL, schuze int, cislo int,
        datum varchar(16), pro int, proti int, zdrzel int, nehlasoval int, prihlaseno int, kvorum int,
        vysledek varchar(1), nazev varchar(255), krok int NOT NULL DEFAULT 0)""",
    "hlasovani_kluby": """CREATE TABLE public.hlasovani_kluby (id_hlasovani int NOT NULL, klub varchar(40) NOT NULL,
        pro int, proti int, zdrzel int, nepritomen int)""",
    "hlasovani_poslanci": """CREATE TABLE public.hlasovani_poslanci (id_hlasovani int NOT NULL, id_osoba int NOT NULL,
        klub varchar(40), hlas varchar(1) NOT NULL, PRIMARY KEY (id_hlasovani, id_osoba))""",
    "priprava": """CREATE TABLE public.priprava (ct int PRIMARY KEY, druh varchar(20) NOT NULL, pid varchar(20) NOT NULL,
        pripominky_od varchar(10), pripominky_do varchar(10), dni int, mist int NOT NULL DEFAULT 0, vlada varchar(10),
        duvod text, poznamka text, mimo_plan int NOT NULL DEFAULT 0, vyjimka int NOT NULL DEFAULT 0,
        znovu int NOT NULL DEFAULT 0)""",
    "pripravovane": """CREATE TABLE public.pripravovane (pid varchar(20) PRIMARY KEY, nazev text, resort varchar(255),
        faze varchar(20) NOT NULL, stav varchar(80), zalozeno varchar(10), zmeneno varchar(10),
        pripominky_od varchar(10), pripominky_do varchar(10), dni int, mist int NOT NULL DEFAULT 0, vlada varchar(10),
        duvod text, popis text, mimo_plan int NOT NULL DEFAULT 0, vyjimka int NOT NULL DEFAULT 0,
        verzi int NOT NULL DEFAULT 1, pidy text)""",
    "pn": """CREATE TABLE public.pn (id_dokument int PRIMARY KEY, cislo int NOT NULL, ct int NOT NULL, id_osoba int,
        osoba varchar(120), cas varchar(19), klub varchar(40))""",
    "pn_vysledek": """CREATE TABLE public.pn_vysledek (ct int NOT NULL, cislo int NOT NULL, pismena varchar(120),
        stav varchar(20) NOT NULL, hlasovani text, stanovisko varchar(40), updated_at varchar(19),
        PRIMARY KEY (ct, cislo))""",
    "alerts": """CREATE TABLE public.alerts (alert_key varchar(190) PRIMARY KEY, kind varchar(20) NOT NULL,
        ct int NOT NULL, event_at varchar(19) NOT NULL, created_at varchar(19) NOT NULL, title varchar(255) NOT NULL,
        body text NOT NULL, watched int NOT NULL DEFAULT 0, posted int NOT NULL DEFAULT 0,
        score int NOT NULL DEFAULT 0, obdobi int)""",
    "pn_text": """CREATE TABLE public.pn_text (id_dokument int PRIMARY KEY, ct int NOT NULL, cislo int NOT NULL,
        docx_url varchar(255), text text, chars int NOT NULL DEFAULT 0, laws text, signals text,
        rule_score int NOT NULL DEFAULT 0, ai_json text, ai_score int, fetched_at varchar(19), ai_at varchar(19),
        error text, ai_model varchar(60), ai_prompt int, praxe_json text, praxe_model varchar(60), praxe_at varchar(19),
        praxe_v int, praxe_error text)""",
    "tisk_text": """CREATE TABLE public.tisk_text (ct int PRIMARY KEY, docx_url varchar(255), text text,
        chars int NOT NULL DEFAULT 0, signals text, ai_json text, ai_score int, fetched_at varchar(19),
        ai_at varchar(19), error text, checked_at varchar(19), ai_model varchar(60), ai_prompt int)""",
    "sync_beh": """CREATE TABLE public.sync_beh (id int NOT NULL PRIMARY KEY, zacatek varchar(19) NOT NULL,
        konec varchar(19), ok int, vysledek text, chyba text)""",
    "meta": "CREATE TABLE public.meta (k varchar(190) PRIMARY KEY, v text)",
    "reel_state": "CREATE TABLE public.reel_state (key text PRIMARY KEY, value text)",
}

DATA = [
    ("INSERT INTO tisky (id_tisk, ct, druh, typ_navrhu, navrhovatel, nazev, faze, zakony, sbirka, score, reasons) "
     "VALUES (%s, %s, 1, 'vládní', 'Vláda', 'Novela zákona o daních z příjmů', 'Vyhlášeno', '586/1992', '90/2026', 3, "
     "'[]')", (CT, CT)),
    ("INSERT INTO kroky (id_hist, id_tisk, ct, datum, stav, akce, id_hlasovani) "
     "VALUES (%s, %s, %s, '2026-10-24', 'Projednáno', 'Schváleno', %s)", (CT, CT, CT, HLAS)),
    ("INSERT INTO hlasovani (id_hlasovani, ct, schuze, cislo, datum, pro, proti, zdrzel, nehlasoval, prihlaseno, "
     "kvorum, vysledek, nazev) VALUES (%s, %s, 34, 1, '2026-10-24 10:00', 100, 50, 5, 2, 157, 79, 'A', 'Novela')",
     (HLAS, CT)),
    ("INSERT INTO hlasovani_kluby VALUES (%s, 'ODS', 20, 0, 0, 1)", (HLAS,)),
    ("INSERT INTO hlasovani_poslanci VALUES (%s, %s, 'ODS', 'A')", (HLAS, OSOBA)),
    ("INSERT INTO pripravovane (pid, nazev, resort, faze, zmeneno) VALUES ('KORNTEST', 'Zákon o daních', "
     "'Ministerstvo financí', 'pripominky', '2026-10-01')", ()),
    ("INSERT INTO pn (id_dokument, cislo, ct, id_osoba, osoba, cas, klub) "
     "VALUES (%s, %s, %s, %s, 'Jan Novák', '2026-10-01 10:00', 'ODS')", (PN, PN, CT, OSOBA)),
    ("INSERT INTO pn_vysledek (ct, cislo, stav) VALUES (%s, %s, 'prijat')", (CT, PN)),
    ("INSERT INTO alerts (alert_key, kind, ct, event_at, created_at, title, body) "
     "VALUES ('test8b:1', 'stav', %s, '2026-10-24', '2026-10-24', 'Upozornění', 'Text')", (CT,)),
    ("INSERT INTO pn_text (id_dokument, ct, cislo, chars, laws, ai_json, ai_score) "
     "VALUES (%s, %s, %s, 10, '586/1992', '{}', 2)", (PN, CT, PN)),
    ("INSERT INTO tisk_text (ct, chars, ai_json, ai_score, fetched_at) VALUES (%s, 10, '{}', 3, '2026-10-01 10:00:00')",
     (CT,)),
    ("INSERT INTO sync_beh (id, zacatek, konec, ok, chyba) "
     "VALUES (1, '2026-10-24 10:00:00', '2026-10-24 10:05:00', 1, 'tajná cesta')", ()),
    ("INSERT INTO meta (k, v) VALUES ('last_sync', '2026-10-24 10:05:00'), ('ai_queue_seen_at', '2026-10-24 10:00:00'), "
     "('steno_last_result', '{\"seconds\": 1}'), ('form_secret', 'TAJNE')", ()),
    ("INSERT INTO reel_state (key, value) VALUES ('hlidani_vystrahy', '{\"at\": \"2026-10-24\", \"vystrahy\": []}'), "
     "('threads_token', 'TAJNE')", ()),
]

# minimální argumenty pro každý nástroj MCP (nový nástroj bez položky = červený test úplnosti)
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
    "projevy_k_tisku": {"ct": CT},
    "projevy_recnika": {"osoba": "Novák"},
    "get_projev": {"klic": "10/1/1/1"},
    "hledej_v_projevech": {"dotaz": "daně z příjmů"},
    "najdi_osobu": {"jmeno": "Novák"},
    "program_schuze": {"schuze": 34},
    "zmeny_projevu": {},
    "stav_dat": {},
    "over_citaci": {"citace": "zákon o daních z příjmů", "ct": CT},
    "hlasovani_k_tisku": {"ct": CT},
    "get_hlasovani": {"id_hlasovani": HLAS},
    "rozprava": {"ct": CT},
}


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _dsn_ro() -> str:
    return make_conninfo(os.environ["DATABASE_URL"], user="mcp_ro", password=HESLO)


def _jedna(conn, sql: str, *params):
    return conn.execute(sql, params).fetchone()[0]


@pytest.fixture(scope="module")
def prostredi():
    """Tabulky webu, migrace, data, tokeny a role mcp_ro; po modulu úklid. Vrací slovník s id tokenů."""
    from hlidac_rag import db, role

    su = db.connect()
    if not _jedna(su, "SELECT current_database()").endswith("_test"):
        su.close()
        pytest.skip("databáze nekončí na _test")
    existuji = [t for t in WEB_TABULKY if _jedna(su, "SELECT to_regclass(%s) IS NOT NULL", f"public.{t}")]
    if existuji or _jedna(su, "SELECT count(*) FROM pg_roles WHERE rolname = 'mcp_ro'"):
        su.close()
        pytest.skip(f"v testovací DB už jsou tabulky webu nebo role mcp_ro ({', '.join(existuji)})")
    for ddl in WEB_TABULKY.values():
        su.execute(ddl)
    db.migrate(su)   # pohledy mcp.meta_verejne a mcp.reel_state_verejne vzniknou, až jsou meta a reel_state
    for sql, params in DATA:
        su.execute(sql, params)
    platny = _jedna(su, "INSERT INTO mcp.token (jmeno, hash) VALUES ('test-8b', %s) RETURNING id", _sha(TOKEN))
    zruseny = _jedna(su, "INSERT INTO mcp.token (jmeno, hash, zrusen) VALUES ('test-8b-z', %s, now()) RETURNING id",
                     _sha(TOKEN_ZRUSENY))
    citac = su.execute("SELECT den, pocet FROM rag.dotazy_den WHERE den = (now() AT TIME ZONE 'utc')::date").fetchone()
    zacatek = _jedna(su, "SELECT now()")
    su.commit()
    role.nastav(su, {"mcp_ro": HESLO})
    try:
        yield {"su": su, "platny": platny, "zruseny": zruseny}
    finally:
        su.rollback()
        su.execute("DELETE FROM mcp.volani WHERE token_id IN (%s, %s) OR (token_id IS NULL AND cas >= %s)",
                   (platny, zruseny, zacatek))
        su.execute("DELETE FROM mcp.token WHERE id IN (%s, %s)", (platny, zruseny))
        su.execute("DELETE FROM rag.dotazy_den WHERE den = (now() AT TIME ZONE 'utc')::date")
        if citac:
            su.execute("INSERT INTO rag.dotazy_den (den, pocet) VALUES (%s, %s)", citac)
        su.execute("DROP TABLE IF EXISTS public.tajne_test")
        for t in WEB_TABULKY:
            su.execute(f"DROP TABLE IF EXISTS public.{t} CASCADE")   # s nimi zmizí i pohledy v mcp
        su.execute("DROP OWNED BY mcp_ro")
        su.execute("DROP ROLE mcp_ro")
        su.execute(f"GRANT TEMPORARY ON DATABASE {_jedna(su, 'SELECT current_database()')} TO PUBLIC")
        su.commit()
        su.close()


@pytest.fixture
def superuser_conn(prostredi):
    su = prostredi["su"]
    yield su
    su.rollback()


@pytest.fixture
def mcp_ro_conn(prostredi):
    c = psycopg.connect(_dsn_ro())
    yield c
    c.close()


def rw(conn, sql: str, *params):
    """Jeden dotaz v transakci READ WRITE (jako ověření tokenu v MCP), výsledek prvního sloupce."""
    conn.execute("SET TRANSACTION READ WRITE")
    v = _jedna(conn, sql, *params)
    conn.commit()
    return v


class _PodRoli:
    """MCP server s poolem pod mcp_ro: nástroje přes server.call_tool (stejná cesta jako klient, tedy _call)."""

    def __init__(self, server_mod):
        self.s = server_mod

    def zavolej(self, nastroj: str, argumenty: dict):
        from mcp.server.mcpserver.exceptions import ToolError

        try:
            return asyncio.run(self.s.server.call_tool(nastroj, argumenty))
        except ToolError as e:
            chyba = _psycopg_v_retezci(e)
            if chyba is not None:
                raise chyba from None   # _call ji schová za „Dočasná chyba databáze“, test chce původní
            raise ValueError(str(e)) from None

    def http(self, token: str, middleware=None) -> int:
        """POST /mcp přes AuthMiddleware (aplikace za ní jen odpoví 200); vrací stavový kód."""
        mw = middleware or self.s.AuthMiddleware(_ok_app)
        poslano = []

        async def send(msg):
            poslano.append(msg)

        async def receive():
            return {"type": "http.request", "body": b""}

        scope = {"type": "http", "path": "/mcp", "method": "POST",
                 "headers": [(b"authorization", f"Bearer {token}".encode())]}
        anyio.run(mw, scope, receive, send)
        return poslano[0]["status"]


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"{}"})


def _psycopg_v_retezci(e: BaseException | None) -> psycopg.Error | None:
    videno = set()
    while e is not None and id(e) not in videno:
        videno.add(id(e))
        if isinstance(e, psycopg.Error):
            return e
        e = e.__cause__ or e.__context__
    return None


@pytest.fixture
def mcp_pod_roli(prostredi, monkeypatch):
    from hlidac_rag import mcp_server
    from hlidac_rag.embed import hledani

    pool = ConnectionPool(_dsn_ro(), min_size=1, max_size=2, open=True, timeout=5)
    monkeypatch.setattr(mcp_server, "pool", pool)
    hledani.nastav_citac(lambda: psycopg.connect(_dsn_ro(), connect_timeout=2))
    try:
        yield _PodRoli(mcp_server)
    finally:
        hledani.nastav_citac(None)
        pool.close()


# ---------------------------------------------------------------- nástroje pod rolí

def test_kazdy_nastroj_mcp_ma_minimalni_argumenty():
    from hlidac_rag.mcp_server import server

    assert set(MIN_ARGUMENTY) == {t.name for t in asyncio.run(server.list_tools())}


@pytest.mark.parametrize("nastroj", sorted(MIN_ARGUMENTY))
def test_nastroj_funguje_pod_mcp_ro(mcp_pod_roli, nastroj):
    try:
        mcp_pod_roli.zavolej(nastroj, MIN_ARGUMENTY[nastroj])
    except psycopg.Error as e:   # chybějící GRANT, ale i tabulka nebo sloupec, který test nezná
        pytest.fail(f"{nastroj}: chyba databáze pod mcp_ro ({type(e).__name__}: {e})")
    except ValueError:
        pass   # „text zatím nemám“, „projev neznám“ apod. je v pořádku


def test_volani_nastroje_se_zapise_pod_roli(mcp_pod_roli, superuser_conn):
    pred = _jedna(superuser_conn, "SELECT count(*) FROM mcp.volani WHERE nastroj = 'najdi_osobu'")
    superuser_conn.commit()
    mcp_pod_roli.zavolej("najdi_osobu", {"jmeno": "Novák"})
    assert _jedna(superuser_conn, "SELECT count(*) FROM mcp.volani WHERE nastroj = 'najdi_osobu'") == pred + 1


def test_cteci_funkce_chatu_nad_vyctem_tabulek(mcp_ro_conn):
    """TABULKY_CTENI sdílí i chat: upozornění a připravované (úloha 3) musí jít číst."""
    from hlidac_rag import tools

    mcp_ro_conn.row_factory = psycopg.rows.dict_row
    assert tools.upozorneni(mcp_ro_conn, CT)["pocet"] == 1
    assert tools.pripravovane(mcp_ro_conn, q="daních")


def test_citac_embeddingu_pod_roli(mcp_pod_roli, superuser_conn):
    from hlidac_rag.embed import hledani

    assert hledani._zapocti_dotaz() is True
    assert _jedna(superuser_conn, "SELECT pocet FROM rag.dotazy_den WHERE den = (now() AT TIME ZONE 'utc')::date") >= 1


# ---------------------------------------------------------------- co role nesmí

def test_mcp_ro_necte_tokeny_ani_volani(mcp_ro_conn):
    for sql in ("SELECT * FROM mcp.token", "SELECT * FROM mcp.volani"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            mcp_ro_conn.execute(sql)
        mcp_ro_conn.rollback()


def test_mcp_ro_necte_tajemstvi_z_meta_ani_reel_state(mcp_ro_conn):
    for sql in ("SELECT * FROM public.meta", "SELECT * FROM public.reel_state"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            mcp_ro_conn.execute(sql)
        mcp_ro_conn.rollback()
    klice = {r[0] for r in mcp_ro_conn.execute("SELECT k FROM mcp.meta_verejne")}
    assert klice == {"last_sync", "ai_queue_seen_at", "steno_last_result"}
    assert [r[0] for r in mcp_ro_conn.execute("SELECT key FROM mcp.reel_state_verejne")] == ["hlidani_vystrahy"]


@pytest.mark.parametrize("sql", [
    "INSERT INTO tisky DEFAULT VALUES", "UPDATE steno.projev SET text = ''", "DELETE FROM pn",
    "CREATE TABLE public.x (a int)", "CREATE TEMP TABLE x (a int)", "TRUNCATE texty.cel",
    "UPDATE mcp.token SET zrusen = NULL", "INSERT INTO rag.dotazy_den VALUES (current_date, 0)",
    "CREATE SCHEMA x", "CREATE TABLE steno.x (a int)", "CREATE VIEW mcp.x AS SELECT 1",
    "CREATE FUNCTION public.x() RETURNS int LANGUAGE sql AS 'SELECT 1'",
])
def test_mcp_ro_nezapisuje(mcp_ro_conn, sql):
    mcp_ro_conn.execute("SET TRANSACTION READ WRITE")   # i když si roli „odemkne“, GRANT chybí
    with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction)):
        mcp_ro_conn.execute(sql)


def test_mcp_ro_vychozi_transakce_jen_pro_cteni(mcp_ro_conn):
    assert _jedna(mcp_ro_conn, "SHOW default_transaction_read_only") == "on"
    assert _jedna(mcp_ro_conn, "SHOW statement_timeout") == "15s"
    assert _jedna(mcp_ro_conn, "SHOW idle_in_transaction_session_timeout") == "30s"
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        mcp_ro_conn.execute("SELECT mcp.zapis_volani(NULL, 'x', 1, false)")


def test_nova_tabulka_v_public_neni_citelna(superuser_conn, mcp_ro_conn):   # žádné ALTER DEFAULT PRIVILEGES v public
    superuser_conn.execute("CREATE TABLE public.tajne_test (a int)")
    superuser_conn.commit()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        mcp_ro_conn.execute("SELECT * FROM public.tajne_test")


def test_opravneni_na_tabulky_odpovidaji_vyctu(superuser_conn):
    """V public a mcp čte mcp_ro přesně výčet z role.py; ve schématech steno, texty a rag všechno."""
    from hlidac_rag import role

    citelne = {r[0] for r in superuser_conn.execute(
        """SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relkind IN ('r', 'v', 'm', 'p') AND n.nspname IN ('public', 'mcp')
             AND has_table_privilege('mcp_ro', c.oid, 'SELECT')""")}
    ocekavane = {t if "." in t else f"public.{t}" for t in role.TABULKY_CTENI + role.MCP_NAVIC
                 if t not in role.MCP_SLOUPCE}   # tabulky jen s vybranými sloupci nemají SELECT na celou tabulku
    assert citelne == ocekavane
    necitelne = superuser_conn.execute(
        """SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relkind IN ('r', 'v', 'm', 'p') AND n.nspname = ANY(%s)
             AND NOT has_table_privilege('mcp_ro', c.oid, 'SELECT')""", (list(role.SCHEMATA_CTENI),)).fetchall()
    assert necitelne == []


def test_sync_beh_jen_vybrane_sloupce(superuser_conn, mcp_ro_conn):
    sloupce = {r[0]: r[1] for r in superuser_conn.execute(
        """SELECT column_name, has_column_privilege('mcp_ro', 'public.sync_beh', column_name, 'SELECT')
           FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'sync_beh'""")}
    assert {c for c, smi in sloupce.items() if smi} == {"id", "zacatek", "konec", "ok"}
    assert sloupce["chyba"] is False and sloupce["vysledek"] is False
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        mcp_ro_conn.execute("SELECT chyba FROM sync_beh")


def test_pohledy_jsou_security_barrier(superuser_conn):
    for v in ("mcp.meta_verejne", "mcp.reel_state_verejne"):
        moznosti = _jedna(superuser_conn, "SELECT reloptions FROM pg_class WHERE oid = %s::regclass", v)
        assert "security_barrier=true" in (moznosti or []), v


# ---------------------------------------------------------------- funkce SECURITY DEFINER

def test_over_token(mcp_ro_conn, superuser_conn, prostredi):
    assert rw(mcp_ro_conn, "SELECT mcp.over_token(%s)", _sha(TOKEN)) == prostredi["platny"]
    assert rw(mcp_ro_conn, "SELECT mcp.over_token(%s)", _sha(TOKEN_ZRUSENY)) is None
    assert rw(mcp_ro_conn, "SELECT mcp.over_token(%s)", "neexistuje") is None
    assert _jedna(superuser_conn, "SELECT naposledy FROM mcp.token WHERE id = %s", prostredi["platny"]) is not None
    assert _jedna(superuser_conn, "SELECT naposledy FROM mcp.token WHERE id = %s", prostredi["zruseny"]) is None


def test_zapis_a_pocet_volani(mcp_ro_conn, prostredi):
    pred = rw(mcp_ro_conn, "SELECT mcp.volani_od(%s, now() - interval '1 minute')", prostredi["platny"])
    rw(mcp_ro_conn, "SELECT mcp.zapis_volani(%s, 'get_tisk', 12, false)", prostredi["platny"])
    assert rw(mcp_ro_conn, "SELECT mcp.volani_od(%s, now() - interval '1 minute')", prostredi["platny"]) == pred + 1
    assert rw(mcp_ro_conn, "SELECT mcp.volani_od(%s, now() + interval '1 minute')", prostredi["platny"]) == 0


def test_zapis_volani_jen_pro_platny_token_nebo_null(mcp_ro_conn, superuser_conn, prostredi):
    """Neexistující ani zrušený token se nezapíše a volani_od pro něj vrací 0; NULL (bez tokenu) se zapíše."""
    neexistuje = _jedna(superuser_conn, "SELECT coalesce(max(id), 0) + 1000 FROM mcp.token")
    superuser_conn.commit()
    pocet = "SELECT count(*) FROM mcp.volani WHERE token_id = %s"
    pred_zruseny = _jedna(superuser_conn, pocet, prostredi["zruseny"])
    pred_null = _jedna(superuser_conn, "SELECT count(*) FROM mcp.volani WHERE token_id IS NULL AND nastroj = 'test8b'")
    superuser_conn.commit()
    for tid in (neexistuje, prostredi["zruseny"], None):
        rw(mcp_ro_conn, "SELECT mcp.zapis_volani(%s, 'test8b', 1, false)", tid)
    assert _jedna(superuser_conn, pocet, neexistuje) == 0
    assert _jedna(superuser_conn, pocet, prostredi["zruseny"]) == pred_zruseny
    assert _jedna(superuser_conn, "SELECT count(*) FROM mcp.volani WHERE token_id IS NULL AND nastroj = 'test8b'")         == pred_null + 1
    superuser_conn.execute("INSERT INTO mcp.volani (token_id, nastroj, ms) VALUES (%s, 'test8b', 1)",
                           (prostredi["zruseny"],))
    superuser_conn.commit()
    assert rw(mcp_ro_conn, "SELECT mcp.volani_od(%s, now() - interval '1 minute')", prostredi["zruseny"]) == 0


def test_funkce_jsou_zamcene(superuser_conn):
    for f in ("mcp.over_token(text)", "mcp.zapis_volani(int,text,int,boolean)", "mcp.volani_od(int,timestamptz)",
              "rag.pricti_dotaz()"):
        r = superuser_conn.execute("SELECT prosecdef, proconfig FROM pg_proc WHERE oid = %s::regprocedure",
                                   (f,)).fetchone()
        assert r[0] is True and r[1][0].startswith("search_path=pg_catalog, ") and r[1][0].endswith(", pg_temp"), f
        assert _jedna(superuser_conn, "SELECT has_function_privilege('public', %s, 'EXECUTE')", f) is False, f
        assert _jedna(superuser_conn, "SELECT has_function_privilege('mcp_ro', %s, 'EXECUTE')", f) is True, f
    for f in ("mcp.over_token(text)", "mcp.zapis_volani(int,text,int,boolean)", "mcp.volani_od(int,timestamptz)"):
        r = superuser_conn.execute("SELECT proconfig FROM pg_proc WHERE oid = %s::regprocedure", (f,)).fetchone()
        assert "search_path=pg_catalog, mcp, pg_temp" in r[0], f


# ---------------------------------------------------------------- role

def test_role_je_omezena_a_idempotentni(superuser_conn):
    from hlidac_rag import role

    role.nastav(superuser_conn, {"mcp_ro": HESLO})
    prikazy = role.nastav(superuser_conn, {"mcp_ro": HESLO})
    r = superuser_conn.execute("""SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolreplication,
                                         rolbypassrls, rolconnlimit, rolcanlogin
                                  FROM pg_roles WHERE rolname = 'mcp_ro'""").fetchone()
    assert r == (False, False, False, False, False, False, role.MCP_SPOJENI, True)
    assert role.MCP_SPOJENI >= 2 * 4 + 1   # pool MCP (MCP_POOL 4) + čítač embeddingů na vlastním spojení + rezerva
    assert prikazy and all(HESLO not in p for p in prikazy)
    assert _jedna(superuser_conn, "SELECT count(*) FROM pg_auth_members WHERE member = 'mcp_ro'::regrole") == 0
    db = _jedna(superuser_conn, "SELECT current_database()")
    assert _jedna(superuser_conn, "SELECT has_database_privilege('mcp_ro', %s, 'TEMPORARY')", db) is False
    assert _jedna(superuser_conn, "SELECT has_database_privilege('mcp_ro', %s, 'CREATE')", db) is False


def test_heslo_se_uklada_jako_scram(superuser_conn):
    assert _jedna(superuser_conn, "SELECT rolpassword FROM pg_authid WHERE rolname = 'mcp_ro'").startswith(
        "SCRAM-SHA-256$")


def test_role_bez_hesla_se_nemeni(superuser_conn):
    from hlidac_rag import role

    assert role.nastav(superuser_conn, {}) == []
    with pytest.raises(ValueError):
        role.nastav(superuser_conn, {"neznama_role": "x"})
    with pytest.raises(ValueError):
        role.nastav(superuser_conn, {"mcp_ro": ""})


def test_cli_bez_hesla_konci_chybou(monkeypatch, capsys):
    from hlidac_rag import role

    monkeypatch.delenv("MCP_DB_PASSWORD", raising=False)
    assert role.main() == 2
    assert "MCP_DB_PASSWORD" in capsys.readouterr().err


# ---------------------------------------------------------------- ověření tokenu přes HTTP

def test_overeni_tokenu_pres_http_pod_roli(mcp_pod_roli, monkeypatch):
    from hlidac_rag import mcp_server

    assert mcp_pod_roli.http(TOKEN) == 200
    assert mcp_pod_roli.http("x") == 401
    assert mcp_pod_roli.http(TOKEN_ZRUSENY) == 401
    monkeypatch.setattr(mcp_server, "PER_MINUTE", 2)
    mw = mcp_server.AuthMiddleware(_ok_app)
    assert [mcp_pod_roli.http(TOKEN, mw) for _ in range(3)] == [200, 200, 429]


def test_denni_limit_prezije_restart(mcp_pod_roli, mcp_ro_conn, prostredi, monkeypatch):
    """Nový proces (prázdná paměť) načte počet volání tokenu za posledních 24 h z mcp.volani přes mcp.volani_od."""
    from hlidac_rag import mcp_server

    vcera = rw(mcp_ro_conn, "SELECT mcp.volani_od(%s, now() - interval '1 day')", prostredi["platny"])
    monkeypatch.setattr(mcp_server, "PER_DAY", vcera + 1)
    mw = mcp_server.AuthMiddleware(_ok_app)
    assert [mcp_pod_roli.http(TOKEN, mw) for _ in range(2)] == [200, 429]
