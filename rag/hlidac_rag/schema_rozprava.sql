-- Rozprava ve stenoprotokolech: ke kterým tiskům projev patří, druh vystoupení, předchozí řečník,
-- odhad adresáta faktické poznámky a PN zmíněné v textu. Idempotentní, spouští se po schema.sql.
-- Plní hlidac_rag.psp.rozprava_prepocet.prepocitej_rozpravu při každém složení schůze (ingest.assemble).

-- Sloupce steno.projev: druh (recnik, fakticka, zpravodaj, ministr, …), predchozi_recnik_id (poslední nepředsedající
-- projev téhož bodu a dne), reaguje_na_odhad (jen faktická: jméno osloveného řečníka).
-- ALTER TABLE bere exkluzivní zámek tabulky i s IF NOT EXISTS: jen když sloupec chybí, jinak by hodinová migrace
-- čekala za čtením MCP a blokovala další čtení.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_schema = 'steno' AND table_name = 'projev' AND column_name = 'druh') THEN
    ALTER TABLE steno.projev ADD COLUMN IF NOT EXISTS druh text;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_schema = 'steno' AND table_name = 'projev' AND column_name = 'predchozi_recnik_id') THEN
    ALTER TABLE steno.projev ADD COLUMN IF NOT EXISTS predchozi_recnik_id bigint;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_schema = 'steno' AND table_name = 'projev' AND column_name = 'reaguje_na_odhad') THEN
    ALTER TABLE steno.projev ADD COLUMN IF NOT EXISTS reaguje_na_odhad text;
  END IF;
END $$;

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
