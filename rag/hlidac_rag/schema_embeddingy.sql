-- Embeddingy projevů a celků textů pro hledání podle významu (MCP fáze 4). Idempotentní, spouští se po schema.sql.
-- Plní hlidac_rag.embed.indexer.indexuj v hodinovém běhu rag-ingest; čte hlidac_rag.embed.hledani.
-- Úsek = souvislý kus textu zdroje (od_znaku/do_znaku), hlavička se embeduje spolu s ním. Filtry (ct, osoba, klub,
-- datum…) jsou zkopírované ze zdroje, ať hustý dotaz nemusí joinovat; při změně textu nebo hlavičky se úseky
-- zdroje nahradí (zdroj_hash), zrušený projev úseky ztratí při dalším běhu indexeru, smazaný přes ON DELETE CASCADE.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS rag;

CREATE TABLE IF NOT EXISTS rag.usek (
  id          bigserial PRIMARY KEY,
  zdroj       text NOT NULL CHECK (zdroj IN ('projev', 'cel')),
  projev_id   bigint REFERENCES steno.projev ON DELETE CASCADE,
  cel_id      bigint REFERENCES texty.cel ON DELETE CASCADE,
  poradi      int NOT NULL,
  od_znaku    int NOT NULL,
  do_znaku    int NOT NULL,
  hlavicka    text NOT NULL,
  text_hash   text NOT NULL,
  zdroj_hash  text NOT NULL,     -- otisk zdroje (text + údaje hlavičky a filtrů) spočítaný v SQL: změna = nové úseky
  model       text NOT NULL,
  embedding   halfvec(1024),
  ct          int,
  id_osoba    int,
  klub        text,
  datum       date,
  predsedajici boolean,
  druh        text,             -- projev: druh vystoupení (recnik, fakticka…); celek: druh dokumentu (tisk, pn)
  verze       text,             -- celek: puvodni | schvalene
  vcetne_duvodove boolean,      -- celek: true = normativní část (hledá se i bez vcetne_duvodove), false = důvodová zpráva
  indexovano  timestamptz NOT NULL DEFAULT now(),
  CHECK ((zdroj = 'projev' AND projev_id IS NOT NULL AND cel_id IS NULL)
      OR (zdroj = 'cel' AND cel_id IS NOT NULL AND projev_id IS NULL))
);

CREATE INDEX IF NOT EXISTS usek_embedding ON rag.usek USING hnsw (embedding halfvec_cosine_ops)
  WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS usek_projev ON rag.usek (projev_id);
CREATE INDEX IF NOT EXISTS usek_cel ON rag.usek (cel_id);
CREATE INDEX IF NOT EXISTS usek_ct ON rag.usek (ct);
CREATE INDEX IF NOT EXISTS usek_osoba ON rag.usek (id_osoba, datum);

-- Denní čítač embeddingů dotazů (strop EMBED_DOTAZY_DEN), sdílený MCP a chatem. Role pro čtení do tabulky nepíšou,
-- čítač zvyšuje jen funkce níže (SECURITY DEFINER, pevný search_path); EXECUTE dostanou role mcp_ro a chat_ro.
CREATE TABLE IF NOT EXISTS rag.dotazy_den (
  den    date PRIMARY KEY,
  pocet  int NOT NULL
);

CREATE OR REPLACE FUNCTION rag.pricti_dotaz() RETURNS int
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, rag AS $$
  INSERT INTO rag.dotazy_den (den, pocet) VALUES ((now() AT TIME ZONE 'utc')::date, 1)
  ON CONFLICT (den) DO UPDATE SET pocet = dotazy_den.pocet + 1
  RETURNING pocet
$$;
REVOKE ALL ON FUNCTION rag.pricti_dotaz() FROM PUBLIC;
