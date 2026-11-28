-- Schéma chat: relace, konverzace, zprávy, volání LLM a útrata chatbota (spec 14.1, 11.1, 15). Idempotentní, spouští ji
-- db.migrate po schema.sql. Role ani hesla tu nejsou (role.py). Schéma se z denního dumpu vyřazuje (--exclude-schema=chat).
-- Mazání: konverzace po 30 dnech nebo na požádání (kaskádou zprávy, zdroje, volání nástrojů). Cena volání (volani_llm) a
-- počítadlo zpráv (zprava_log) po smazání konverzace zůstávají, jinak by smazání vynulovalo limity útraty a zpráv.
-- Surová IP se neukládá nikdy, jen otisk (HMAC s náhodnou solí dne z chat.sul_dne); otisky i soli se po 48 h mažou.

CREATE SCHEMA IF NOT EXISTS chat;

CREATE TABLE IF NOT EXISTS chat.relace (
  id                text PRIMARY KEY,
  vytvoreno         timestamptz NOT NULL DEFAULT now(),
  posledni_aktivita timestamptz NOT NULL DEFAULT now(),
  overeno_do        timestamptz,                 -- Turnstile: ověřeno do
  zprav_od_overeni  int NOT NULL DEFAULT 0,
  otisk_ip          text,                        -- otisk IP při ověření (maže se po 48 h)
  overeni_cas       timestamptz,                 -- kdy se ověřilo (stáří otisku)
  tah_od            timestamptz,                 -- příznak „běží tah“: nejvýš jeden tah relace (spec 11.1)
  tah_token         text                         -- vlastník příznaku: tah smí příznak uvolnit jen svým tokenem
);
ALTER TABLE chat.relace ADD COLUMN IF NOT EXISTS tah_token text;

CREATE TABLE IF NOT EXISTS chat.konverzace (
  id                text PRIMARY KEY,            -- 128 bitů náhodně
  relace_id         text NOT NULL REFERENCES chat.relace ON DELETE CASCADE,
  vytvoreno         timestamptz NOT NULL DEFAULT now(),
  posledni_aktivita timestamptz NOT NULL DEFAULT now(),
  eval              boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS konverzace_relace_idx ON chat.konverzace (relace_id);
CREATE INDEX IF NOT EXISTS konverzace_aktivita_idx ON chat.konverzace (posledni_aktivita);

-- Otisk odpovědi (kód, hash, datum) žije 12 měsíců i po smazání konverzace; kód se tu rezervuje při založení odpovědi.
CREATE TABLE IF NOT EXISTS chat.otisk_odpovedi (
  kod   text PRIMARY KEY,
  hash  text,                                    -- sha256 textu odpovědi (po dokončení)
  datum date NOT NULL DEFAULT ((now() AT TIME ZONE 'Europe/Prague')::date)
);

CREATE TABLE IF NOT EXISTS chat.zprava (
  id             bigserial PRIMARY KEY,
  konverzace_id  text NOT NULL REFERENCES chat.konverzace ON DELETE CASCADE,
  role           text NOT NULL CHECK (role IN ('uzivatel', 'bot')),
  otazka_id      bigint REFERENCES chat.zprava ON DELETE CASCADE,   -- bot: zpráva uživatele, na kterou odpovídá
  text           text NOT NULL DEFAULT '',
  kod            text UNIQUE,                    -- kód odpovědi (8 znaků), jen bot
  kontext        jsonb NOT NULL DEFAULT '{}',    -- kontext obrazovky (karta, značka stránky)
  citovane       int[] NOT NULL DEFAULT '{}',    -- čísla citovaných zdrojů [N]
  overeni        jsonb,                          -- výsledek ověření citací
  model          text,
  stav           text,                           -- jen bot
  vytvoreno      timestamptz NOT NULL DEFAULT now(),
  CHECK ((role = 'uzivatel' AND stav IS NULL AND kod IS NULL)
      OR (role = 'bot' AND stav IN ('probiha', 'ok', 'nemam', 'odmitnuto', 'chyba', 'strop', 'preruseno')))
);
CREATE INDEX IF NOT EXISTS zprava_konverzace_idx ON chat.zprava (konverzace_id, id);

CREATE TABLE IF NOT EXISTS chat.zdroj (
  konverzace_id text NOT NULL REFERENCES chat.konverzace ON DELETE CASCADE,
  n             int NOT NULL,
  klic          text NOT NULL,
  typ           text NOT NULL,
  titulek       text NOT NULL,
  meta          jsonb NOT NULL DEFAULT '{}',
  citovan       boolean NOT NULL DEFAULT false,
  PRIMARY KEY (konverzace_id, n)
);

-- Cena a tokeny volání modelu. Bez cizího klíče na relaci a se zprava_id SET NULL: přežije smazání konverzace.
CREATE TABLE IF NOT EXISTS chat.volani_llm (
  id          bigserial PRIMARY KEY,
  zprava_id   bigint REFERENCES chat.zprava ON DELETE SET NULL,
  relace_id   text NOT NULL,
  otisk_ip    text,                              -- maže se po 48 h (uklid)
  model       text NOT NULL,
  vstup       int NOT NULL DEFAULT 0,
  vystup      int NOT NULL DEFAULT 0,
  cache_cteni int NOT NULL DEFAULT 0,
  cache_zapis int NOT NULL DEFAULT 0,
  cena_usd    numeric(10,6) NOT NULL DEFAULT 0,
  ms          int NOT NULL DEFAULT 0,
  stop_reason text,
  cas         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS volani_llm_cas_idx ON chat.volani_llm (cas);
CREATE INDEX IF NOT EXISTS volani_llm_relace_idx ON chat.volani_llm (relace_id, cas);
CREATE INDEX IF NOT EXISTS volani_llm_ip_idx ON chat.volani_llm (otisk_ip, cas);

-- Texty argumentů mohou obsahovat dotaz uživatele, proto mizí s konverzací.
CREATE TABLE IF NOT EXISTS chat.volani_nastroje (
  id        bigserial PRIMARY KEY,
  zprava_id bigint NOT NULL REFERENCES chat.zprava ON DELETE CASCADE,
  nastroj   text NOT NULL,
  argumenty jsonb NOT NULL DEFAULT '{}',
  ms        int NOT NULL DEFAULT 0,
  znaku     int NOT NULL DEFAULT 0,
  chyba     text,
  cas       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS volani_nastroje_zprava_idx ON chat.volani_nastroje (zprava_id);

-- Počítadlo přijatých zpráv relace pro limity „za minutu / za den“; nesmí zmizet se smazáním konverzace (uklid po 2 dnech).
CREATE TABLE IF NOT EXISTS chat.zprava_log (
  relace_id text NOT NULL,
  cas       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS zprava_log_idx ON chat.zprava_log (relace_id, cas);

CREATE TABLE IF NOT EXISTS chat.hodnoceni (
  kod   text PRIMARY KEY REFERENCES chat.otisk_odpovedi ON DELETE CASCADE,
  palec int NOT NULL CHECK (palec IN (-1, 1)),
  cas   timestamptz NOT NULL DEFAULT now()
);

-- Nahlášená odpověď se drží do vyřízení; kopie textu, protože konverzace se po 30 dnech smaže.
CREATE TABLE IF NOT EXISTS chat.nahlaseni (
  id            bigserial PRIMARY KEY,
  kod           text NOT NULL,
  duvod         text NOT NULL,
  text_otazky   text,
  text_odpovedi text,
  cas           timestamptz NOT NULL DEFAULT now(),
  vyrizeno      timestamptz
);
CREATE INDEX IF NOT EXISTS nahlaseni_kod_idx ON chat.nahlaseni (kod);
-- jedno nahlášení kódu od jednoho otisku IP (opakované jen přepíše důvod); otisk se po 48 h maže jako jinde
ALTER TABLE chat.nahlaseni ADD COLUMN IF NOT EXISTS otisk_ip text;
CREATE UNIQUE INDEX IF NOT EXISTS nahlaseni_kod_ip_uniq ON chat.nahlaseni (kod, otisk_ip);

-- Náhodná sůl pro otisk IP, jedna na pražský den (spec 11.1). Vzniká při prvním použití dne, úklid ji maže po 48 h:
-- bez ní nejde otisk ani výčtem adres zpětně přepočítat.
CREATE TABLE IF NOT EXISTS chat.sul_dne (
  den        date PRIMARY KEY,
  sul        bytea NOT NULL CHECK (octet_length(sul) = 32),
  vytvoreno  timestamptz NOT NULL DEFAULT now()
);
