# hlidac_rag: stenoprotokoly a MCP server

Python služba vedle PHP webu. Stahuje stenoprotokoly Poslanecké sněmovny (10. volební období) do PostgreSQL,
propojuje projevy s tisky, body pořadu a poslanci a zpřístupňuje data přes MCP server (jen čtení).

## Data

- Metadata z otevřených dat psp.cz (`poslanci.zip`, `schuze.zip`, `tisky.zip`, `steno.zip`) jednou denně.
- Text projevů z HTML částí `…/stenprot/NNNschuz/sNNNTTT.htm` (windows-1250), jeden požadavek za sekundu.
- Tabulky ve schématu `steno`: `osoba`, `klub_clenstvi`, `schuze`, `bod`, `cast`, `projev`, `projev_verze`, `rec`, `beh`.
  Stávající tabulky webu (`tisky`, `pn`, …) se nemění, vazba je hodnotou `ct`.

## Texty návrhů zákonů a PN (schéma `texty`)

Krok ingestu po stenoprotokolech (`hlidac_rag.texty.ingest.sync_texty`, chyba neshodí běh stenoprotokolů):

1. převzetí textů z tabulek webu (`tisk_text`, `pn_text`); změna textu (hash) = nové rozdělení,
2. stažení chybějících textů tisků a PN z psp.cz (DOCX), nejvýš `--texty-limit` dokumentů za běh (výchozí 40),
3. schválené znění tisků po 3. čtení (historie → stránka schváleného znění → „Originál dokumentu“),
4. rozdělení dokumentů na celky (části, hlavy, články, paragrafy, body PN; `texty.cel`).

Chyba jednoho dokumentu (nečitelný DOCX, HTTP 403/500/503, timeout) se zapíše do `texty.dokument.chyba` a běh
pokračuje dalším. Opakování: chybějící původní text po 7 dnech („jen PDF“ se neopakuje), schválené znění po 24 h
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
`projevy_k_tisku`, `projevy_recnika`, `get_projev`, `hledej_v_projevech`, `najdi_osobu`, `program_schuze`,
`zmeny_projevu`, `stav_dat` a nad texty návrhů a PN:

- `osnova_textu(ct | pn, verze, vcetne_duvodove, limit, offset)`: seznam celků textu (označení, cesta, začátek),
  stránkovaně. U tisku se bez `verze` vrací schválené znění, je-li, jinak původní (`verze="puvodni"|"schvalene"`).
- `get_cel(ct | pn, oznaceni | poradi, verze, max_znaku, offset_znaku)`: jeden celek (např. `oznaceni="§ 17c"`),
  nejvýš 8 000 znaků na volání, se sousedními celky.
- `hledej_v_textech(dotaz, ct, pn, druh, meni_zakon, verze, vcetne_duvodove, limit, offset)`: fulltext nad celky
  s úryvkem kolem shody; prázdný dotaz s filtrem (`ct`, `pn` nebo `meni_zakon`) vrátí všechny celky filtru.

Sémantické hledání (embeddingy) přijde v další fázi.

### Změny rozhraní (větev mcp-kvalita)

- Nenalezený tisk, PN, projev, osoba, schůze nebo celek vrací chybu nástroje (`isError`), ne text v odpovědi.
- `get_pn` už nemá parametr `s_textem`; text PN je přes `osnova_textu(pn=…)` a `get_cel` (`osnova_dostupna`).
- Hledání odmítne příliš krátký dotaz (žádné slovo o 3 a více znacích) jako chybu nástroje.
- Seznamy mají `celkem`, `offset` a `dalsi_offset`; `klub` se hledá podle začátku zkratky nebo názvu.

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
