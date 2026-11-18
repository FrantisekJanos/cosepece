# hlidac_rag: stenoprotokoly a MCP server

Python služba vedle PHP webu. Stahuje stenoprotokoly Poslanecké sněmovny (10. volební období) do PostgreSQL,
propojuje projevy s tisky, body pořadu a poslanci a zpřístupňuje data přes MCP server (jen čtení).

## Data

- Metadata z otevřených dat psp.cz (`poslanci.zip`, `schuze.zip`, `tisky.zip`, `steno.zip`) jednou denně.
- Text projevů z HTML částí `…/stenprot/NNNschuz/sNNNTTT.htm` (windows-1250), jeden požadavek za sekundu.
- Tabulky ve schématu `steno`: `osoba`, `klub_clenstvi`, `schuze`, `bod`, `cast`, `projev`, `projev_verze`, `rec`, `beh`.
  Stávající tabulky webu (`tisky`, `pn`, …) se nemění, vazba je hodnotou `ct`.

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
`zmeny_projevu`, `stav_dat`. Sémantické hledání (embeddingy) přijde v další fázi.
