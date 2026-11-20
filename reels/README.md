# Reels (hlidac_reels)

Krátká videa z hlavních nálezů Hlídače zákonů na Facebook, Instagram a Threads. Video se vyrenderuje lokálně,
na serveru se zařadí do fronty a člověk ho v Telegramu schválí. Teprve potom ho bot zveřejní.

- `reels-enqueue`: zkontroluje video (ffprobe), zkopíruje ho do `/media/videos/` a zařadí do fronty (tabulka v PostgreSQL webu).
- `reels-bot`: pošle čekající video do Telegramu ke schválení, po schválení ho zveřejní na FB, IG a Threads (když je nastavené) a výsledek ohlásí.
- `reels-threads-backfill --id N`: doplní na Threads položku, která už vyšla jen na FB a IG (viz Threads níže).
- Render (`render.py`, Playwright a numpy) do kontejneru nepatří, běží lokálně: `pip install -e ".[render,dev]"`.

## Kontejner

Služba `reels` v `docker/docker-compose.yml` (obraz z `reels/Dockerfile`, výchozí příkaz `reels-bot`).
Data jsou ve volume `reelsmedia`, v kontejneru `/media`:

- `videos/`: videa ve frontě (název = sha256 obsahu),
- `public/`: dočasná veřejná kopie pod náhodným tokenem, jen po dobu publikace na Instagram a Threads. Obě sítě si video
  stahuje sám z veřejné adresy, proto web připojuje stejný volume jen pro čtení a vydává
  `https://cosepece.cz/reels-media/<token>.mp4` (jen soubory `*.mp4`, bez výpisu složky). Po publikaci se kopie smaže;
  `Cache-Control: no-store` brání tomu, aby ji CDN vydávala i potom.

Volume `reelsmedia` musí vytvořit compose prázdný: Docker do něj při prvním připojení zkopíruje `/media` z obrazu
i s vlastníkem (uid 10001). Předem vytvořený nebo rootem naplněný volume zápis bota rozbije.
Před prvním `docker/redeploy.sh` s touto službou musí být proměnné `META_*` a `REELS_*` v `docker/.env`,
jinak kontejner `reels` skončí chybou a stále se restartuje.

## Proměnné prostředí

Hodnoty patří jen do `docker/.env` na serveru (vzor `docker/.env.example`), nikdy do gitu.

| Proměnná | Význam |
|---|---|
| `META_SYSTEM_TOKEN` | systémový token Meta (Business portfolio) s oprávněními stránky a Instagramu |
| `META_PAGE_ID` | id stránky na Facebooku |
| `META_IG_USER_ID` | id účtu Instagram |
| `META_GRAPH_VERSION` | verze Graph API, výchozí `v23.0` |
| `REELS_TELEGRAM_TOKEN` | token Telegram bota |
| `REELS_TELEGRAM_CHAT_ID` | id soukromého chatu vlastníka s botem (číslo); skupina ne, tam by Schválit mohl zmáčknout kdokoli z členů |
| `REELS_PUBLIC_BASE` | veřejná adresa složky s kopiemi, výchozí `https://cosepece.cz/reels-media` |
| `REELS_MEDIA_DIR` | složka s daty, v kontejneru `/media` |
| `THREADS_USER_ID` | id účtu Threads; prázdné = Threads vypnuté, bot publikuje jen na FB a IG |
| `THREADS_TOKEN` | počáteční dlouhodobý token Threads, bot si ho při prvním startu uloží do databáze |
| `THREADS_GRAPH_VERSION` | verze Threads API, výchozí `v1.0` |
| `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` | databáze webu (nastaví compose), lokálně stačí `DATABASE_URL` |

Bez povinných proměnných bot skončí s chybou a vypíše, které chybí (hodnoty nevypisuje).

## Zařazení videa do fronty

Video a text popisku musí být uvnitř kontejneru, například přes `docker compose cp`:

```bash
cd ~/hlidac-zakonu
docker compose -f docker/docker-compose.yml cp reel.mp4 reels:/tmp/reel.mp4
docker compose -f docker/docker-compose.yml cp popisek.txt reels:/tmp/popisek.txt
docker compose -f docker/docker-compose.yml exec reels \
  reels-enqueue --video /tmp/reel.mp4 --caption-file /tmp/popisek.txt --ct 221 --format v3
```

`--ct` (číslo sněmovního tisku) a `--format` (označení formátu videa) jsou nepovinné údaje pro přehled ve frontě. Stejné video (stejný sha256) se do fronty
nezařadí dvakrát. `docker compose cp` ukládá soubory jako root, uživatel `app` je musí umět číst (práva aspoň 0644).
Po zařazení je smaž: `docker compose -f docker/docker-compose.yml exec -u root reels rm /tmp/reel.mp4 /tmp/popisek.txt`.

## Telegram

Bot posílá do nastaveného chatu video ke schválení s popiskem a tlačítky:

- **Schválit**: zveřejní video na FB, IG a Threads a pošle odkazy (řádek za každou síť),
- **Upravit popisek**: nový text se pošle jako odpověď na zprávu (funguje i odpověď přímo na video nebo náhled),
- **Zamítnout**: video se nezveřejní,
- **Zkusit znovu**: u položky, jejíž publikace selhala nebo skončila nejistě (nejdřív dohledá, co už na sítích je).
  Dohledání porovná prvních 80 znaků popisku a čas od odeslání ke schválení.
  Když selže jen Threads, FB a IG zůstanou zveřejněné a Zkusit znovu zkusí jen Threads.

Příkazy:

- `/fronta`: počty položek podle stavů, k čekajícím znovu náhled s tlačítky, k neúspěšným tlačítko Zkusit znovu,
  schválené čekající na restart a zveřejňování starší 15 minut jen vypíše,
- `/dalsi`: pošle další video ze fronty ke schválení.

Nově zařazené video bot pošle sám do minuty. Video, které Telegram odmítne (např. přes 50 MB) nebo jehož soubor chybí,
vyřadí (stav rejected) a ohlásí. Zprávy z jiných chatů bot ignoruje.

## Threads

Threads je volitelné: bez `THREADS_USER_ID` bot publikuje jen na FB a IG a o Threads nepíše. S ním ukáže náhled
i blok „Threads:“ s přesným textem a po zveřejnění řádek Threads. Když Threads selže (třeba chybí nebo neplatí token),
položka skončí `failed` s FB a IG venku, výsledek napíše „Threads: nepublikováno – důvod“ a nabídne Zkusit znovu.
Po prvním startu se zapnutým Threads pošle bot čekajícím položkám nový náhled (i s textem pro Threads) a starému
vezme tlačítka, aby Schválit nezveřejnil text, který vlastník neviděl (`reel_state.preview_has_threads`). Totéž
(nový náhled, starý bez tlačítek) dělá `/fronta`.

**Text pro Threads** (limit 500 znaků): první odstavec popisku (do prázdného řádku), zkrácený na celou větu do 440 znaků
(když věta nestačí, na slovo a „…“), za ním `Celý rozbor: https://cosepece.cz/tisk.php?ct=N` (bez čísla tisku jen
`https://cosepece.cz`). FB a IG dostávají celý popisek beze změny. Úprava popisku změní i text pro Threads.

**Token:** dlouhodobý uživatelský token účtu Threads (60 dní) z jednorázového přihlášení OAuth. Do `docker/.env` patří
`THREADS_USER_ID` a `THREADS_TOKEN`; bot si token při prvním startu uloží do tabulky `reel_state` (`threads_token`)
a dál používá jen ten. Jednou denně zkontroluje stáří a token starší než 7 dní prodlouží (`th_refresh_token`).
Když prodloužení selže, platí dosavadní token, bot pošle do Telegramu jednu zprávu a zkusí to další den.
Token se nevypisuje do logu ani do zpráv. Nový token (třeba po vypršení) se nasadí smazáním uloženého
a restartem bota s novým `THREADS_TOKEN`:

```bash
docker compose -f docker/docker-compose.yml exec db \
  psql -U hlidac -d hlidac -c "DELETE FROM reel_state WHERE key LIKE 'threads_token%'"
docker compose -f docker/docker-compose.yml up -d reels
```

**Doplnění starších videí:** položku, která vyšla jen na FB a IG (stav `published` bez Threads), doplní

```bash
docker compose -f docker/docker-compose.yml exec reels reels-threads-backfill --id N
```

Příkaz nejdřív dohledá, zda příspěvek na Threads už není (prvních 80 znaků textu, od času zveřejnění), a když je,
jen uloží jeho id. Jinak video zveřejní jen na Threads přes dočasnou veřejnou kopii a vypíše odkaz. FB a IG se nedotkne
a stav položky nemění. Jiný stav, položka už na Threads nebo chybějící `THREADS_USER_ID` či token = chyba s kódem 1. Dvě souběžná spuštění pro stejnou položku
nehrozí: příkaz drží zámek v PostgreSQL (`pg_try_advisory_lock`), druhé skončí „backfill už běží“.

## Generátor Reels (worker na PC)

Sady variant k tiskům generuje `reels-worker` na počítači s přihlášeným Claude Code (předplatné, žádný API klíč).
Server mu jen vydává úlohy a přebírá výsledky, sám nic negeneruje.

Postup jedné úlohy: heartbeat → `claim` (úloha s kartou tisku) → `generate()` (`claude -p` napíše 3 varianty, render
v2/v3, kontrola videa) → nahrání platných videí (`PUT`, každé nejvýš 30 MB; větší varianta vypadne) → `result`.
Se 2 nebo 3 platnými variantami server sadu přijme, s méně worker nahlásí `fail`. Bez úlohy nebo po chybě sítě čeká
5 minut. Worker zpracuje vždy jen jednu úlohu a druhá instance (třeba naplánovaná úloha plus ruční spuštění) hned
skončí se zprávou (zámek `worker.lock` v pracovní složce).

```bash
pip install -e ".[render]"
reels-worker --env <soubor .env> --root <pracovní složka> --mcp-config <mcp.json> [--once]
```

| Volba | Význam |
|---|---|
| `--env` | soubor `KEY=VALUE` mimo repo: `REELS_API_URL` (adresa `reels_api.php`, jen https), `REELS_WORKER_TOKEN`, volitelně `REELS_MODEL` (jinak výchozí model generátoru) |
| `--root` | pracovní složka: podsložka na úlohu, `worker.lock`, `worker.log` |
| `--mcp-config` | JSON s MCP serverem cosepece pro `claude -p` (mimo repo) |
| `--once` | jeden průchod bez čekání, na zkoušku |

**Web (`reels_api.php`, `app/ReelsApi.php`):** v `config.php` klíč `reels`:

- `worker_token`: stejný token jako `REELS_WORKER_TOKEN` workeru (hlavička `X-Hlidac-Token`). Prázdný nebo `ZMENTE…`
  = rozhraní vypnuté (403). Token umí jen `claim`, `video`, `result`, `fail` a `heartbeat`.
- `inbox_dir`: mezisložka, kam web ukládá videa a `manifest.json` (v kontejneru `/srv/reels-inbox`). Když chybí nebo
  do ní nejde zapisovat, `claim` úlohu nevydá a vrátí 500.

Mezisložka je volume `reelsinbox`: web do něj zapisuje, služba `reels` ho čte (`REELS_INBOX_DIR`, v kontejneru
`/inbox`) a po převzetí sady složku úlohy maže. Bot bere jen složky s `manifest.json`, který web zapíše jako poslední.

**Příkazy bota:**

- `/video <číslo tisku> [cesta|pn|zmena|rozprava]`: ruční zadání (formát je jen nápověda pro generátor),
- `/vyber`: tisk vybere automatika z hlavních nálezů (nikdy formát `rozprava`),
- `/fronta`: kromě položek ke schválení i zadání ve frontě a kdy bylo PC naposledy vidět.

Ze sady pošle bot všechny varianty a vlastník jednu vybere tlačítkem Vybrat; teprve vybraná jde ke schválení.

**Limity:**

- výdej úloh: nejdřív `manual`, pak `auto_pick`, pak `fresh` (čerstvé nálezy), uvnitř podle stáří; pro jeden tisk
  nejvýš jedna aktivní úloha,
- úloha `fresh` vzniká, když se mezi hlavními nálezy objeví nový tisk nebo nový důvod skóre; nejvýš 2 nové úlohy
  `fresh` za den (Europe/Prague), zbylé nálezy přijdou na řadu další dny; první běh detektoru jen uloží výchozí stav,
- vydaná úloha bez výsledku se po 60 minutách vrátí do fronty, po 3 pokusech skončí `failed`; `fresh` starší než
  7 dní se už nevydá (`expired`),
- do Telegramu jdou nejvýš 2 sady denně (Europe/Prague) a najednou nejvýš 1 nevyřízená; `/dalsi` limit obchází,
- sada `fresh` nebo `auto_pick` bez výběru vyprší po 7 dnech (`fresh` se počítá od založení úlohy), sada, jejíž tisk
  vypadl z hlavních nálezů, se stáhne; ruční sady nevyprší.

## Testy

```bash
pip install -e ".[dev]"
pytest -q -m "not render"                       # bez Chromia
DATABASE_URL=postgresql://… REELS_TEST_DB=1 pytest -q   # i testy nad PostgreSQL
```
