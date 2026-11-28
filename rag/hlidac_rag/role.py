"""Role PostgreSQL pro služby, které jen čtou (spec chatbota 21.1). Zatím mcp_ro pro MCP server.

Spuštění (kontejner rag-ingest, jako vlastník tabulek hlidac, po migraci):
    docker compose exec -e MCP_DB_PASSWORD rag-ingest python -m hlidac_rag.role

Idempotentní: založí nebo upraví jen role, jejichž heslo dostane (z prostředí), a jejich oprávnění nastaví přesně
podle výčtů níže (co z výčtu zmizí, roli se odebere). Heslo se do DB posílá jen jako SCRAM otisk, nikdy v čitelné
podobě, a nikam se nevypisuje. Pro schéma public se nenastavují výchozí oprávnění: nová tabulka webu není pro roli
čitelná, dokud ji někdo nepřidá do TABULKY_CTENI a příkaz nespustí znovu.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sys
from dataclasses import dataclass, field

import psycopg
from psycopg import sql

# tabulky webu (schéma public), které čtou čtecí funkce v hlidac_rag.tools a nastroje/ (MCP i chat)
TABULKY_CTENI: tuple[str, ...] = (
    "tisky",               # get_tisk, seznam_tisku, tisky_menici_zakon, tisk_podle_sbirky, rozprava, hlasování, stav_dat
    "kroky",               # get_tisk (kroky), rozprava (čtení podle historie), hlasovani_k_tisku (krok)
    "pn",                  # pn_k_tisku, get_pn, najdi_osobu, stav_dat
    "pn_text",             # pn_k_tisku, get_pn, get_tisk (zakony z PN), seznam_tisku, stav_dat (fronta AI)
    "pn_vysledek",         # pn_k_tisku, get_pn (výsledek PN, úloha 3)
    "tisk_text",           # get_tisk (rozbor), stav_dat (fronta AI)
    "alerts",              # get_tisk (upozorneni), tools.upozorneni (chat, úloha 3)
    "pripravovane",        # tools.pripravovane (chat, úloha 3)
    "hlasovani",           # hlasovani_k_tisku, get_hlasovani
    "hlasovani_kluby",     # hlasovani_k_tisku, get_hlasovani
    "hlasovani_poslanci",  # get_hlasovani (hlasy poslanců)
)
# schémata jen s veřejnými daty, čitelná celá (i tabulky přidané později: ALTER DEFAULT PRIVILEGES)
SCHEMATA_CTENI: tuple[str, ...] = ("steno", "texty", "rag")
# co čte jen MCP (nástroj stav_dat); meta a reel_state obsahují tajemství, proto jen pohledy z schema.sql
MCP_NAVIC: tuple[str, ...] = (
    "sync_beh",                # stav_dat (historie běhů synchronizace); jen sloupce z MCP_SLOUPCE
    "mcp.meta_verejne",        # stav_dat: klíče steno_last_result, last_sync, ai_queue_seen_at
    "mcp.reel_state_verejne",  # stav_dat: klíč hlidani_vystrahy
)
# tabulky, ze kterých role smí číst jen některé sloupce (sync_beh.vysledek a .chyba mají texty chyb s adresami)
MCP_SLOUPCE: dict[str, tuple[str, ...]] = {"sync_beh": ("id", "zacatek", "konec", "ok")}
# funkce SECURITY DEFINER (schema.sql, schema_embeddingy.sql), které MCP volá
MCP_FUNKCE: tuple[str, ...] = (
    "mcp.over_token(text)",
    "mcp.zapis_volani(integer, text, integer, boolean)",
    "mcp.volani_od(integer, timestamp with time zone)",
    "rag.pricti_dotaz()",
)
# pool MCP (MCP_POOL, výchozí 4) + stejně spojení čítače embeddingů (hledani.nastav_citac) + jedno při startu
MCP_SPOJENI = 2 * 4 + 1

SCRAM_ITERACI = 4096   # výchozí scram_iterations PostgreSQL


@dataclass(frozen=True)
class Role:
    jmeno: str
    promenna_hesla: str
    spojeni: int
    tabulky: tuple[str, ...]
    sloupce: dict[str, tuple[str, ...]]
    schemata: tuple[str, ...]
    funkce: tuple[str, ...]
    nastaveni: dict[str, str] = field(default_factory=dict)


ROLE: dict[str, Role] = {
    "mcp_ro": Role(
        jmeno="mcp_ro", promenna_hesla="MCP_DB_PASSWORD", spojeni=MCP_SPOJENI,
        tabulky=TABULKY_CTENI + MCP_NAVIC, sloupce=MCP_SLOUPCE, schemata=SCHEMATA_CTENI, funkce=MCP_FUNKCE,
        # na úrovni role, ne SET LOCAL: rollback po chybě by nastavení transakce zrušil
        nastaveni={"default_transaction_read_only": "on", "statement_timeout": "15s",
                   "idle_in_transaction_session_timeout": "30s"},
    ),
}


def scram_otisk(heslo: str, sul: bytes | None = None, iteraci: int = SCRAM_ITERACI) -> str:
    """Otisk hesla ve formátu PostgreSQL (SCRAM-SHA-256$iter:sůl$StoredKey:ServerKey), RFC 5802 / 7677.

    Jen tisknutelné ASCII: PostgreSQL by jiné znaky upravil SASLprepem a přihlášení by nesedělo."""
    if not heslo or not all(33 <= ord(z) < 127 for z in heslo):
        raise ValueError("heslo musí být neprázdné a jen z tisknutelných znaků ASCII bez mezer")
    sul = sul or secrets.token_bytes(16)
    solene = hashlib.pbkdf2_hmac("sha256", heslo.encode("ascii"), sul, iteraci)
    klient = hmac.new(solene, b"Client Key", hashlib.sha256).digest()
    server = hmac.new(solene, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"SCRAM-SHA-256${iteraci}:{b64(sul)}${b64(hashlib.sha256(klient).digest())}:{b64(server)}"


def _tabulka(jmeno: str) -> sql.Identifier:
    schema, _, tab = jmeno.rpartition(".")
    return sql.Identifier(schema or "public", tab)


def _nastav_roli(conn: psycopg.Connection, r: Role, heslo: str) -> list[str]:
    prikazy: list[str] = []

    def ex(q: sql.Composable, zaznam: str | None = None) -> None:
        conn.execute(q)
        prikazy.append(zaznam or q.as_string(conn))

    ident = sql.Identifier(r.jmeno)
    db = sql.Identifier(conn.execute("SELECT current_database()").fetchone()[0])
    chybi = [f for f in r.funkce if conn.execute("SELECT to_regprocedure(%s)", (f,)).fetchone()[0] is None]
    chybi += [s for s in (*r.schemata, "mcp") if conn.execute("SELECT to_regnamespace(%s)", (s,)).fetchone()[0] is None]
    if chybi:
        raise RuntimeError(f"chybí {', '.join(chybi)}: spusťte nejdřív migraci (rag-ingest)")

    existuje = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (r.jmeno,)).fetchone()
    atributy = sql.SQL("LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS "
                       "CONNECTION LIMIT {}").format(sql.Literal(r.spojeni))
    prikaz = sql.SQL("ALTER ROLE {} WITH {}" if existuje else "CREATE ROLE {} WITH {}").format(ident, atributy)
    ex(prikaz + sql.SQL(" PASSWORD {}").format(sql.Literal(scram_otisk(heslo))),
       prikaz.as_string(conn) + " PASSWORD '***'")
    # role nesmí být členem jiné role (NOINHERIT nebrání SET ROLE)
    for (nad,) in conn.execute("SELECT roleid::regrole::text FROM pg_auth_members WHERE member = %s::regrole",
                               (r.jmeno,)).fetchall():
        ex(sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(nad), ident))
    for klic, hodnota in r.nastaveni.items():
        ex(sql.SQL("ALTER ROLE {} SET {} = {}").format(ident, sql.Identifier(klic), sql.Literal(hodnota)))

    # dočasné tabulky a nová schémata smí dnes každý (PUBLIC); superuser hlidac je nepotřebuje
    ex(sql.SQL("REVOKE TEMPORARY, CREATE ON DATABASE {} FROM PUBLIC").format(db))
    ex(sql.SQL("REVOKE TEMPORARY, CREATE ON DATABASE {} FROM {}").format(db, ident))
    ex(sql.SQL("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))

    # nejdřív vše odebrat, pak přidat podle výčtu: co z výčtu zmizí, role ztratí (vše v jedné transakci)
    schemata = sql.SQL(", ").join(sql.Identifier(s) for s in ("public", "mcp", *r.schemata))
    ex(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}").format(schemata, ident))
    ex(sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA {} FROM {}").format(schemata, ident))
    ex(sql.SQL("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {} FROM {}").format(schemata, ident))
    ex(sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(schemata, ident))
    ex(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schemata, ident))

    for t in r.tabulky:
        if conn.execute("SELECT to_regclass(%s)", (t if "." in t else f"public.{t}",)).fetchone()[0] is None:
            prikazy.append(f"-- přeskočeno: {t} neexistuje (po jejím založení spusťte znovu)")
            continue
        if t in r.sloupce:
            sl = sql.SQL(", ").join(sql.Identifier(c) for c in r.sloupce[t])
            ex(sql.SQL("GRANT SELECT ({}) ON {} TO {}").format(sl, _tabulka(t), ident))
        else:
            ex(sql.SQL("GRANT SELECT ON {} TO {}").format(_tabulka(t), ident))
    jen_data = sql.SQL(", ").join(sql.Identifier(s) for s in r.schemata)
    ex(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(jen_data, ident))
    ex(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT SELECT ON TABLES TO {}").format(jen_data, ident))
    for f in r.funkce:
        oid = conn.execute("SELECT to_regprocedure(%s)::oid", (f,)).fetchone()[0]
        nazev = conn.execute("SELECT %s::regprocedure::text", (oid,)).fetchone()[0]
        ex(sql.SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(sql.SQL(nazev), ident))
    return prikazy


def nastav(conn: psycopg.Connection, hesla: dict[str, str]) -> list[str]:
    """Založí nebo upraví role, jejichž heslo je v `hesla` (jméno role → heslo), a nastaví jejich oprávnění.

    Vše v jedné transakci (při chybě rollback). Vrací provedené příkazy, místo hesla '***'."""
    nezname = sorted(set(hesla) - set(ROLE))
    if nezname:
        raise ValueError(f"neznámé role: {', '.join(nezname)}")
    prikazy: list[str] = []
    try:
        for jmeno, r in ROLE.items():
            if jmeno in hesla:
                if not hesla[jmeno]:
                    raise ValueError(f"prázdné heslo role {jmeno}")
                prikazy += _nastav_roli(conn, r, hesla[jmeno])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return prikazy


def main() -> int:
    from . import db

    hesla = {j: os.environ[r.promenna_hesla] for j, r in ROLE.items() if os.environ.get(r.promenna_hesla)}
    if not hesla:
        promenne = ", ".join(r.promenna_hesla for r in ROLE.values())
        print(f"Žádné heslo v prostředí ({promenne}), nic se nemění.", file=sys.stderr)
        return 2
    conn = db.connect()
    try:
        for p in nastav(conn, hesla):
            print(p)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
