-- Rozprava ve stenoprotokolech: ke kterým tiskům projev patří, druh vystoupení, předchozí řečník,
-- odhad adresáta faktické poznámky a PN zmíněné v textu. Idempotentní, spouští se po schema.sql.
-- Plní hlidac_rag.psp.ingest.prepocitej_rozpravu při každém složení schůze (assemble).

ALTER TABLE steno.projev ADD COLUMN IF NOT EXISTS druh text;                 -- recnik, fakticka, zpravodaj, ministr, …
ALTER TABLE steno.projev ADD COLUMN IF NOT EXISTS predchozi_recnik_id bigint; -- poslední nepředsedající projev téhož bodu a dne
ALTER TABLE steno.projev ADD COLUMN IF NOT EXISTS reaguje_na_odhad text;     -- jen faktická: jméno osloveného řečníka

CREATE TABLE IF NOT EXISTS steno.projev_tisk (
  projev_id bigint NOT NULL REFERENCES steno.projev ON DELETE CASCADE,
  ct        int NOT NULL,
  zdroj     text NOT NULL CHECK (zdroj IN ('bod','rec','nadpis','text')),  -- 'text' zatím neplní nic
  PRIMARY KEY (projev_id, ct)
);
CREATE INDEX IF NOT EXISTS projev_tisk_ct ON steno.projev_tisk (ct);

CREATE TABLE IF NOT EXISTS steno.projev_pn (
  projev_id bigint NOT NULL REFERENCES steno.projev ON DELETE CASCADE,
  cislo_pn  int NOT NULL,
  PRIMARY KEY (projev_id, cislo_pn)
);
CREATE INDEX IF NOT EXISTS projev_pn_cislo ON steno.projev_pn (cislo_pn);
