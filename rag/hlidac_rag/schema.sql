-- Schéma služby hlidac_rag (stenoprotokoly + MCP). Idempotentní, spouští se při každém startu.
-- Stávající tabulky PHP webu (tisky, pn, kroky, ...) se nemění a nemají na sebe cizí klíče,
-- protože je Importer každou hodinu maže a plní znovu.

CREATE SCHEMA IF NOT EXISTS steno;
CREATE SCHEMA IF NOT EXISTS mcp;

-- odstranění diakritiky bez rozšíření unaccent (IMMUTABLE kvůli indexu)
CREATE OR REPLACE FUNCTION public.unaccent_simple(t text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT lower(translate(t, 'áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽäöüÄÖÜ', 'acdeeinorstuuyzACDEEINORSTUUYZaouAOU'))
$$;

CREATE TABLE IF NOT EXISTS steno.osoba (
  id_osoba   int PRIMARY KEY,
  titul_pred text,
  jmeno      text,
  prijmeni   text,
  titul_za   text
);

CREATE TABLE IF NOT EXISTS steno.klub_clenstvi (
  id_osoba int NOT NULL,
  klub     text NOT NULL,
  klub_nazev text,
  od       date,
  "do"     date
);
CREATE INDEX IF NOT EXISTS klub_clenstvi_osoba ON steno.klub_clenstvi (id_osoba);

CREATE TABLE IF NOT EXISTS steno.schuze (
  obdobi int NOT NULL,
  schuze int NOT NULL,
  id_schuze int,
  od timestamp,
  "do" timestamp,
  PRIMARY KEY (obdobi, schuze)
);

CREATE TABLE IF NOT EXISTS steno.bod (
  id_bod   int PRIMARY KEY,
  obdobi   int NOT NULL,
  schuze   int NOT NULL,
  cislo    int,
  nazev    text,
  konec    text,          -- uplny_kon, např. „/sněmovní tisk 285/ - prvé čtení“
  id_tisk  int,
  ct       int,
  ct_za    int
);
CREATE INDEX IF NOT EXISTS bod_ct ON steno.bod (ct);

-- jedna stránka sNNNTTT.htm (turn, ~10 minut jednání)
CREATE TABLE IF NOT EXISTS steno.cast (
  obdobi int NOT NULL,
  schuze int NOT NULL,
  turn   int NOT NULL,
  id_steno int,
  datum  date,
  jd     int,
  od_min int,
  do_min int,
  url    text NOT NULL,
  stav   text,            -- neautorizovano | konecna | chybi
  stav_text text,         -- doslovná hláška ze stránky
  hash   text,            -- sha256 vyříznutého obsahu
  html   text,            -- vyříznutý obsah (pro nové parsování bez stahování)
  stazeno timestamptz,
  zmeneno timestamptz,    -- kdy se naposledy změnil obsah
  zmen   int NOT NULL DEFAULT 0,
  dalsi_kontrola timestamptz,
  chyba  text,
  PRIMARY KEY (obdobi, schuze, turn)
);
CREATE INDEX IF NOT EXISTS cast_kontrola ON steno.cast (dalsi_kontrola);

-- vystoupení: začíná kotvou #r<aname> v části turn; aname 0 = text na začátku části bez řečníka
CREATE TABLE IF NOT EXISTS steno.projev (
  id      bigserial PRIMARY KEY,
  obdobi  int NOT NULL,
  schuze  int NOT NULL,
  turn    int NOT NULL,
  aname   int NOT NULL,
  datum   date,
  cas     text,           -- čas části, ve které projev začíná, např. „11.30“
  poradi  int,            -- pořadí ve schůzi
  id_osoba int,
  recnik  text,           -- text odkazu, např. „Poslanec Martin Kupka“
  role    text,
  klub    text,           -- klub k datu projevu
  predsedajici boolean NOT NULL DEFAULT false,
  id_bod  int,
  ct      int,
  ct_zdroj text,          -- rec | nadpis | bod
  text    text NOT NULL,
  znaku   int NOT NULL,
  hash    text NOT NULL,
  autorizovano boolean NOT NULL DEFAULT false,
  verze   int NOT NULL DEFAULT 1,
  turny   int[],          -- části, přes které projev vede
  hlasovani int[],
  url     text NOT NULL,
  zruseno timestamptz,    -- v poslední verzi stránky chybí (smaže se až po potvrzení)
  vytvoreno timestamptz NOT NULL DEFAULT now(),
  zmeneno timestamptz NOT NULL DEFAULT now(),
  UNIQUE (obdobi, schuze, turn, aname)
);
CREATE INDEX IF NOT EXISTS projev_ct ON steno.projev (ct);
CREATE INDEX IF NOT EXISTS projev_osoba ON steno.projev (id_osoba, datum);
CREATE INDEX IF NOT EXISTS projev_bod ON steno.projev (id_bod);
CREATE INDEX IF NOT EXISTS projev_fts ON steno.projev USING gin (to_tsvector('simple', unaccent_simple(text)));

-- předchozí znění projevu při každé změně textu (hlídání změn po autorizaci)
CREATE TABLE IF NOT EXISTS steno.projev_verze (
  projev_id bigint NOT NULL REFERENCES steno.projev ON DELETE CASCADE,
  verze   int NOT NULL,
  text    text NOT NULL,
  hash    text NOT NULL,
  autorizovano boolean,
  platne_do timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (projev_id, verze)
);

CREATE TABLE IF NOT EXISTS steno.beh (
  id bigserial PRIMARY KEY,
  zacatek timestamptz NOT NULL DEFAULT now(),
  konec timestamptz,
  vysledek jsonb
);

CREATE TABLE IF NOT EXISTS mcp.token (
  id serial PRIMARY KEY,
  jmeno text NOT NULL,
  hash text NOT NULL UNIQUE,    -- sha256 tokenu, token sám se neukládá
  vytvoren timestamptz NOT NULL DEFAULT now(),
  naposledy timestamptz,
  zrusen timestamptz
);

CREATE TABLE IF NOT EXISTS mcp.volani (
  id bigserial PRIMARY KEY,
  token_id int,
  nastroj text,
  ms int,
  chyba boolean NOT NULL DEFAULT false,
  cas timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS volani_token_cas ON mcp.volani (token_id, cas);

-- MCP server běží pod rolí mcp_ro (hlidac_rag.role), která mcp.token ani mcp.volani číst nesmí. Ověření tokenu, zápis
-- volání a počet volání tokenu proto dělají tyto funkce: SECURITY DEFINER (práva vlastníka, tj. role migrace),
-- pevný search_path, žádné dynamické SQL; EXECUTE má jen mcp_ro (GRANT v hlidac_rag.role), PUBLIC nic.
-- over_token vrátí jen id platného tokenu pro daný sha256, seznam hashů přes ni vyčíst nejde.
CREATE OR REPLACE FUNCTION mcp.over_token(p_hash text) RETURNS int
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, mcp AS $$
  UPDATE mcp.token SET naposledy = now() WHERE hash = p_hash AND zrusen IS NULL RETURNING id
$$;
REVOKE ALL ON FUNCTION mcp.over_token(text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION mcp.zapis_volani(p_token int, p_nastroj text, p_ms int, p_chyba boolean) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, mcp AS $$
  INSERT INTO mcp.volani (token_id, nastroj, ms, chyba) VALUES (p_token, left(p_nastroj, 100), p_ms, coalesce(p_chyba, true))
$$;
REVOKE ALL ON FUNCTION mcp.zapis_volani(int, text, int, boolean) FROM PUBLIC;

CREATE OR REPLACE FUNCTION mcp.volani_od(p_token int, p_od timestamptz) RETURNS int
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, mcp AS $$
  SELECT count(*)::int FROM mcp.volani WHERE token_id = p_token AND cas >= p_od
$$;
REVOKE ALL ON FUNCTION mcp.volani_od(int, timestamptz) FROM PUBLIC;

-- Tabulky meta (web) a reel_state (bot reels) drží i tajemství (meta.form_secret, reel_state.threads_token), proto je
-- mcp_ro čte jen přes pohledy s vyjmenovanými klíči, které potřebuje nástroj stav_dat (hlidac_rag.tools).
-- Obě tabulky zakládá jiná služba: pohled vznikne při první migraci, kdy tabulka už existuje (rag-ingest migruje
-- každou hodinu); po prvním vzniku pohledu je nutné znovu spustit python -m hlidac_rag.role (GRANT).
DO $$
BEGIN
  IF to_regclass('public.meta') IS NOT NULL THEN
    CREATE OR REPLACE VIEW mcp.meta_verejne AS
      SELECT k, v FROM public.meta WHERE k IN ('steno_last_result', 'last_sync', 'ai_queue_seen_at');
  END IF;
  IF to_regclass('public.reel_state') IS NOT NULL THEN
    CREATE OR REPLACE VIEW mcp.reel_state_verejne AS
      SELECT key, value FROM public.reel_state WHERE key = 'hlidani_vystrahy';   -- tools.HLIDANI_KLIC
  END IF;
END
$$;

-- Texty návrhů zákonů a PN rozdělené na celky (část, článek, §, bod novely) pro MCP.
-- Plní je hlidac_rag.texty.ingest.sync_texty; zdroj: tabulky PHP webu nebo vlastní stažení z psp.cz.
CREATE SCHEMA IF NOT EXISTS texty;
CREATE TABLE IF NOT EXISTS texty.dokument (
  id serial PRIMARY KEY, druh text NOT NULL CHECK (druh IN ('tisk','pn')),
  ct int NOT NULL, cislo_pn int, verze text NOT NULL DEFAULT 'puvodni' CHECK (verze IN ('puvodni','schvalene')),
  docx_url text, zdroj_textu text NOT NULL CHECK (zdroj_textu IN ('tisk_text','pn_text','vlastni_stazeni')),
  text text, hash text, znaku int NOT NULL DEFAULT 0, rozdeleno_at timestamptz, zkontrolovano_at timestamptz, chyba text,
  UNIQUE (druh, ct, cislo_pn, verze));   -- u tisku cislo_pn = 0 (ne NULL), aby UNIQUE platilo
CREATE TABLE IF NOT EXISTS texty.cel (
  id bigserial PRIMARY KEY, dokument_id int NOT NULL REFERENCES texty.dokument(id) ON DELETE CASCADE,
  poradi int NOT NULL, typ text NOT NULL, oznaceni text NOT NULL, cesta text NOT NULL, meni_zakon text,
  text text NOT NULL, znaku int NOT NULL, od_znaku int NOT NULL,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', unaccent_simple(text))) STORED,
  UNIQUE (dokument_id, poradi));
CREATE INDEX IF NOT EXISTS cel_tsv ON texty.cel USING gin (tsv);
CREATE INDEX IF NOT EXISTS cel_oznaceni ON texty.cel (dokument_id, oznaceni);
