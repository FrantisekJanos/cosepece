# hlidac_rag: stenoprotokoly a MCP server

Python služba vedle PHP webu. Stahuje stenoprotokoly Poslanecké sněmovny (10. volební období) do PostgreSQL,
propojuje projevy s tisky, body pořadu a poslanci a zpřístupňuje data přes MCP server (jen čtení).

## Data

- Metadata z otevřených dat psp.cz (`poslanci.zip`, `schuze.zip`, `tisky.zip`, `steno.zip`) jednou denně.
- Text projevů z HTML částí `…/stenprot/NNNschuz/sNNNTTT.htm` (windows-1250), jeden požadavek za sekundu.
- Tabulky ve schématu `steno`: `osoba`, `klub_clenstvi`, `schuze`, `bod`, `cast`, `projev`, `projev_verze`, `rec`, `beh`.
  Stávající tabulky webu (`tisky`, `pn`, …) se nemění, vazba je hodnotou `ct`.
- Rozprava (`schema_rozprava.sql`): `projev_tisk` (ke kterým tiskům projev patří: bod z `rec`, konec nebo název bodu,
  nadpis), `projev_pn` (čísla PN zmíněná v textu) a sloupce `projev.druh`, `predchozi_recnik_id`, `reaguje_na_odhad`.
  Plní je přepočet při každém složení schůze; hodnoty jsou odvozené z textu, ne z otevřených dat.

## Texty návrhů zákonů a PN (schéma `texty`)

Krok ingestu po stenoprotokolech (`hlidac_rag.texty.ingest.sync_texty`, chyba neshodí běh stenoprotokolů):

1. převzetí textů z tabulek webu (`tisk_text`, `pn_text`); změna textu (hash) = nové rozdělení,
2. stažení chybějících textů tisků a PN z psp.cz (DOCX), nejvýš `--texty-limit` dokumentů za běh (výchozí 40),
3. schválené znění tisků po 3. čtení (historie → stránka schváleného znění → „Originál dokumentu“),
4. rozdělení dokumentů na celky (části, hlavy, články, paragrafy, body PN; `texty.cel`).

Chyba jednoho dokumentu (nečitelný DOCX, HTTP 403/500/503, timeout) se zapíše do `texty.dokument.chyba` a běh
pokračuje dalším. Opakování: chybějící původní text po 7 dnech, po přechodné síťové chybě (timeout, spojení,
HTTP 5xx) po 24 h („jen PDF“ se neopakuje), schválené znění po 24 h
(nečitelný DOCX po 7 dnech). Výpadek spojení s psp.cz nebo 3 síťové chyby za sebou stahování v běhu ukončí.
Když dělení selže, celky dokumentu se smažou a nástroje vrací chybu, dokud se text nezmění.
Výsledek běhu (ok a počty) je v `stav_dat` jako `steno_last_result.texty_ok` a `steno_last_result.texty`.

## Hlídání změn

psp.cz zveřejní část neautorizovanou a za 2–3 týdny ji nahradí autorizovanou verzí. `Last-Modified` nepomůže,
proto se u každé části ukládá hash obsahu včetně hlášky o autorizaci a termín další kontroly:

| Stav části | Další kontrola |
|---|---|
| den jednání před ≤ 2 dny | za 2 hodiny |
| neautorizováno | denně |
| konečná, změněná v posledních 60 dnech | týdně |
| konečná, starší | za 90 dní |

Při změně se projevy schůze složí znovu. Změněný projev dostane `verze + 1`, předchozí znění zůstane
v `steno.projev_verze`. Projev, který z nové verze zmizí, se nejdřív označí `zruseno` a smaže až při dalším složení.

## Příkazy

```bash
pip install -e ".[dev]"
python -m pytest tests                       # parser a skládání projevů nad uloženými stránkami
python -m hlidac_rag.run                     # jeden běh ingestu (DATABASE_URL nebo PGHOST/PGUSER/PGPASSWORD)
python -m hlidac_rag.run --reassemble        # znovu složit projevy všech schůzí
python -m hlidac_rag.run --prepocitej-rozpravu   # znovu složit projevy všech schůzí z uložených částí a přepočítat rozpravu, bez sítě
python -m hlidac_rag.tokens add <jméno>      # nový token pro MCP (vypíše se jen jednou)
python -m hlidac_rag.mcp_server              # MCP server na :8000, endpoint /mcp
```

V Dockeru běží jako služby `rag-ingest` (smyčka po hodině) a `mcp` (viz `docker/docker-compose.yml`).

## MCP

Streamable HTTP na `/mcp`, hlavička `Authorization: Bearer <token>`, limit 60 volání za minutu a 2 000 denně.
Připojení v Claude Code:

```bash
claude mcp add --transport http hlidac https://<doména>/mcp --header "Authorization: Bearer <token>"
```

Nástroje: `get_tisk`, `seznam_tisku`, `tisky_menici_zakon`, `tisk_podle_sbirky`, `pn_k_tisku`, `get_pn`,
`rozprava`, `projevy_k_tisku`, `projevy_recnika`, `get_projev`, `hledej_v_projevech`, `najdi_osobu`, `program_schuze`,
`zmeny_projevu`, `stav_dat`, `over_citaci`, `hlasovani_k_tisku`, `get_hlasovani` a nad texty návrhů a PN:

- `osnova_textu(ct | pn, verze, vcetne_duvodove, limit, offset)`: seznam celků textu (označení, cesta, začátek),
  stránkovaně. U tisku se bez `verze` vrací schválené znění, je-li, jinak původní (`verze="puvodni"|"schvalene"`).
- `get_cel(ct | pn, oznaceni | poradi, verze, max_znaku, offset_znaku)`: jeden celek (např. `oznaceni="§ 17c"`),
  nejvýš 8 000 znaků na volání, se sousedními celky.
- `hledej_v_textech(dotaz, ct, pn, druh, meni_zakon, verze, vcetne_duvodove, limit, offset)`: fulltext nad celky
  s úryvkem kolem shody; prázdný dotaz s filtrem (`ct`, `pn` nebo `meni_zakon`) vrátí všechny celky filtru.
- `over_citaci(citace, klic | ct | pn, oznaceni | poradi, verze)`: ověří, že citace (3 slova až 2 000 znaků) zazněla
  v projevu, nebo stojí v celku textu; `shoda` presne | priblizne | nenalezeno, `nalezeny_text` k citování, `kontext`
  a celek (`poradi`, `oznaceni`), kde shoda začíná. Bez `oznaceni` hledá v celém dokumentu (u největšího PN do ~1 s).

Nad rozpravou:

- `rozprava(ct, format, osoby, cteni, druh, od_poradi, max_znaku)`: celá rozprava k tisku v pořadí, jak zazněla
  (bez předsedajících), s druhem vystoupení, čtením, zmíněnými PN a odhadem adresáta faktické poznámky. `format="prehled"`
  (prvních 150 znaků) nebo `"plny"` (celé texty, u faktické i ohlášení předsedajícího). Souhrn řečníků a klubů platí pro
  celou filtrovanou rozpravu. Stránkování `od_poradi` = `dalsi_od_poradi`, rozpočet stránky `max_znaku` 1 000–40 000.

Nad hlasováním Sněmovny (tabulky webu `hlasovani`, `hlasovani_kluby`; data, párování a známé vady
v [`docs/hlasovani-data.md`](docs/hlasovani-data.md)):

- `hlasovani_k_tisku(ct, limit, offset)`: hlasování k tisku chronologicky (schůze, číslo, datum, výsledek, počty,
  kvórum, krok historie, kluby `{klub: [pro, proti, zdrzel, nepritomen]}`), nejvýš 70 na stránku.
- `get_hlasovani(id_hlasovani)`: detail jednoho hlasování s tiskem, všemi kluby a projevy, které ho zmiňují.

Sémantické hledání (embeddingy) přijde v další fázi.

### Změny rozhraní (větev mcp-kvalita)

- Nenalezený tisk, PN, projev, osoba, schůze nebo celek vrací chybu nástroje (`isError`), ne text v odpovědi.
- `get_pn` už nemá parametr `s_textem`; text PN je přes `osnova_textu(pn=…)` a `get_cel` (`osnova_dostupna`).
- Hledání odmítne příliš krátký dotaz (žádné slovo o 3 a více znacích) jako chybu nástroje.
- Seznamy mají `celkem`, `offset` a `dalsi_offset`; `klub` se hledá podle začátku zkratky nebo názvu.

### Změny rozhraní (větev mcp-rozprava)

- `projevy_k_tisku` hledá přes `projev_tisk`: projevy sloučené rozpravy (tisky 76 a 77) vrátí u obou tisků; nový filtr
  `druh` a pole `druh` v řádcích.
- `hledej_v_projevech`, `projevy_recnika` a `zmeny_projevu` filtrují `ct` přes `projev_tisk` (projevy sloučené rozpravy
  u obou tisků).
- `get_tisk`: `projevy` a `body_poradu` počítá z `projev_tisk`; `body_poradu` má jednu položku na schůzi (`body` = čísla
  bodů, `projevu`, `projevu_vse`), i u schůze bez projevů a u sloučené rozpravy.
- `get_pn`: `projevy` (nejvýš 20 projevů, které PN zmiňují) a `projevy_celkem`.
- Projev v bodu bez tisku (interpelace, volby) už nedědí tisk z nadpisu předchozího bodu.

## Eval

Sada otázek `eval/otazky.json` se pouští proti běžícímu MCP serveru (z adresáře `rag/`):

```bash
MCP_TOKEN=… python -m eval.run --url https://<doména>/mcp                 # celá sada
MCP_TOKEN=… python -m eval.run --url https://<doména>/mcp --jen-faze dnes,pr2
```

Token se čte jen z proměnné prostředí `MCP_TOKEN` (ne z argumentu ani souboru). Výsledek se zapíše
do `eval/vysledky/RRRR-MM-DD.md` (výběr fází do `RRRR-MM-DD-<fáze>.md`) a porovná s posledním během stejného druhu.
`--jen-faze` bez odpovídající otázky skončí kódem 2.

## Sentry

Ingest i MCP server hlásí neošetřené chyby do Sentry (`sentry-sdk`, modul `hlidac_rag/sentry.py`).
Zapíná se proměnnou `SENTRY_DSN_RAG` v `docker/.env` (do kontejnerů jde jako `SENTRY_DSN`); prázdná = vypnuto.
Události nesou tag `component` (`ingest` / `mcp`) a release z `SENTRY_RELEASE`. Před odesláním se z nich odstraní
citlivé klíče, e-maily, IP adresy, tokeny, hesla z řetězců připojení (`password=…`) a cesty serveru (stejná pravidla
jako PHP `app/Sentry.php`); lokální proměnné framů se neposílají. `ToolError` (očekávané chyby nástrojů) se neodesílá.
Limit: každý proces pošle nejvýš 5 událostí za hodinu a stejný otisk jen jednou za hodinu.

## Embeddingy a hledání podle významu (schéma `rag`, fáze 4)

`hledej_v_projevech` a `hledej_v_textech` mají parametr `rezim`: `auto` (výchozí) sloučí výsledky podle slov
(fulltext, top 50) a podle významu (vektory, top 50) metodou RRF (k = 60), `fulltext` hledá jen slova, `vyznam`
jen vektory. Odpověď nese `rezim_pouzity` (`hybrid` | `vyznam` | `fulltext`) a u každé položky `shoda`
(`slova` | `vyznam` | `obe`). Když není klíč, index je prázdný, API neodpoví do 3 s nebo je vyčerpaný denní strop,
hledá se jen podle slov a `poznamka` řekne proč. Prázdný dotaz s filtrem v `hledej_v_textech` je vždy lexikální.

- Data: `rag.usek` (`schema_embeddingy.sql`, pgvector `halfvec(1024)`, HNSW). Úsek = kus projevu (od 200 znaků)
  nebo celku textu do 2 400 znaků s překryvem ~300, embeduje se s hlavičkou (řečník, klub, datum, schůze, bod,
  tisky; u celku tisk/PN, název, cesta, měněný zákon). Z jednoho projevu nebo celku jdou do výsledků nejvýš 2 úseky.
- Indexování: krok 6 v `python -m hlidac_rag.run` po textech, jen nové a změněné zdroje (otisk textu a údajů
  hlavičky v SQL; změna modelu = přeindexování), zrušené projevy a nedostupné dokumenty úseky ztratí. Zdroj se
  zapisuje celý v jedné transakci. Úvodní indexace ručně s vyšším stropem:
  `python -m hlidac_rag.run --jen-embeddingy --embed-max-tokenu 40000000`.
- Proměnné: `OPENAI_API_KEY` (bez něj se indexování přeskočí a hledá se jen podle slov; klíč nikdy do repa ani
  logu), `EMBED_MODEL` (výchozí `text-embedding-3-large`), `EMBED_DIM` (1024, musí sedět se sloupcem),
  `EMBED_MAX_TOKENS_PER_RUN` (strop tokenů za hodinový běh, výchozí 3 000 000; odhad 2 znaky na token),
  `EMBED_DOTAZY_DEN` (denní strop embeddingů dotazů v procesu MCP napříč tokeny, výchozí 20 000, den podle UTC), `EMBED_MAX_VZDALENOST` (práh kosinové vzdálenosti hustých výsledků, výchozí 0,60: věcné
  dotazy mají nejbližší úseky 0,43–0,55, nesouvisející 0,61+, takže dotaz mimo témata Sněmovny podle významu nic nevrátí).
- Cena: `-large` 0,13 USD, `-small` 0,02 USD za milion tokenů. Čeština s hlavičkami vychází na 2,2 znaku na
  token (změřeno na vzorku), celý korpus (~65 mil. znaků textu) tedy odhadem 35–40 mil. tokenů, asi 5 USD s `-large`. Výsledek běhu (úseky, tokeny, odhad USD) je v `steno.beh` a
  v `stav_dat` (`embeddingy`, `steno_last_result.embeddingy`). Během indexace se součty zapisují do
  `steno.beh.vysledek.embeddingy` po každé dávce (`bezi: true`); běh ukončený zvenku (restart kontejneru při
  redeployi, ukončené `docker compose exec`) označí další běh `nedokonceno: true` a zaplacené tokeny v záznamu
  zůstanou. Úvodní indexaci proto nepouštět těsně před redeployem. Oba stropy nastavuje `docker/docker-compose.yml`.
- Porovnání modelů na vzorku (jen proti lokální kopii dat):
  `DATABASE_URL=… python -m eval.porovnani_modelu --vzorek 1500` (odhad ceny předem, nad 0,50 USD jen s `--ano`;
  recall@10/@20 na otázkách fáze 4 s polem `cile` v `eval/otazky.json`).
