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

Volitelné video na šířku (16:9, H.264 + AAC, 3–90 s, do 50 MB) pro Threads: `reels-enqueue ... --video-landscape /tmp/reel-sirka.mp4`.
Doplnit nebo vyměnit ho u položky, která je ještě `queued` nebo `pending`, jde příkazem
`reels-add-landscape --id N --video /tmp/reel-sirka.mp4` (video na šířku, které už ve frontě je, se odmítne).

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
Sada s 1–3 platnými variantami projde (i jediná platná se pošle), bez platné varianty worker nahlásí `fail`.
Nevzniklé varianty jdou v `result` jako `skipped: [{n, format, errors}]`: server je zapíše do manifestu a jako
poznámku do `reel_jobs.error` (úloha je přesto `done`). Úloha autopilota (`autopilot` v claimu, z `/pn`) má jen
2 varianty: v2 a v3 `pn`. Bez úlohy nebo po chybě sítě čeká
5 minut. Worker zpracuje vždy jen jednu úlohu a druhá instance (třeba naplánovaná úloha plus ruční spuštění) hned
skončí se zprávou (zámek `worker.lock` v pracovní složce).

Ke každé platné variantě vyrenderuje generátor i video na šířku 16:9 (šablony s `landscape.css`, soubor `…-sirka.mp4`,
kontrola `media.validate(..., "landscape")`), worker ho nahraje jako `PUT ?action=video&orientation=landscape`
(před videem na výšku, stejný limit 30 MB) a bot ho při převzetí sady uloží do `video_landscape_path`, takže Threads
dostane šířku. Vadná nebo chybějící šířka variantu nevyřadí, jen se zaloguje; `reels-generate --no-landscape` ji vypne.

**Kontrola a oprava variant (generátor):**

- Před kontrolou projde karta a popisek normalizací (`generator/normalize.py`): `hook.big` zapsané jako „číslo + jednotka“
  se rozdělí na číslo a jednotku, body formátu `zmena` bez citace se vynechají (jen když aspoň jeden bod citaci má),
  popisku bez závěrečné věty se věta doplní. Fakta se nemění. Každý zásah je věta v `variant-N/normalize.json`
  (prázdný seznam = bez zásahu) a upravená karta se zapíše zpět do `card.json`.
- Chyba rozvržení říká, o kolik pixelů a které pole přetéká (`přetéká o N px`, např. `title 120 znaků`). Šablona v3
  vměstná i dlouhou částku a jméno v háčku: jednotka se zalomí pod číslo a `fit()` zmenšuje podtitulek a meta
  (`hk-sub`, `hk-meta`), případně je zkrátí na celá slova, aniž by vznikla chyba rozvržení.
- `claude -p` vrací druh běhu (`ClaudeRun.kind`): `ok`, `env` (nenulový kód nebo `is_error`, třeba vyčerpaný limit
  předplatného) a `timeout`. Chyba prostředí bez jakéhokoli zápisu vyhodí `EnvError` a opravu nespustí. Běh, který
  vadnou variantu nezměnil (otisk `card.json` a `caption.txt`), dostane chybu „běh modelu nic nezapsal“.
- Stav úlohy je v `<out>/state.json` (stav variant `chybi|vadna|platna|vyrenderovana`, otisk vstupu a verzí promptů,
  počet běhů modelu). Opakované spuštění nad stejnou složkou pokračuje: hotová videa se nerenderují znovu, platné
  varianty se jen vyrenderují. Jiný vstup nebo prompt začne od nuly. „Stejný vstup“ je stejná karta z webu (bez `data_date`), takže hodinová synchronizace dat, která kartu změní, spustí úlohu znovu od nuly. Rozpočet je 4 běhy modelu na úlohu (generování
  plus nejvýš 3 cílené opravy jen vadných variant, s doslovnými chybami); běh přerušený chybou prostředí se nepočítá.
  Hotové platné varianty se renderují hned, takže je pozdější chyba prostředí nezahodí.
- Video k PN bez textu PN a úloha autopilota dostanou jen formát `pn`, sada má pak 2 varianty.

**Worker při chybě a výpadku:**

- Před každým claimem `check_tools` ověří `claude`, Chromium Playwrightu, `ffmpeg`, `ffprobe` a soubor `--mcp-config`;
  chybí-li něco, loguje chybu a úlohy nebere (čeká 5 minut).
- `EnvError` nahlásí úlohu jako `fail` („prostředí: …“) a worker se pozastaví, aby nespálil frontu (každý `fail`
  stojí úloze jeden z 3 pokusů). Odstup před sondou je 15 min, 1 h, pak 3 h. Sonda je krátké `claude -p` („odpověz OK“,
  bez nástrojů, timeout 120 s); po úspěchu worker pokračuje claimem, po neúspěchu čeká o stupeň déle. Heartbeat
  posílá i při pozastavení, na začátku každé smyčky (tedy jednou za krok pauzy).
- Nahrání videa (na výšku i na šířku) a `result` se při výpadku (chyba sítě, odpověď ≥ 500) opakují po 1, 5 a 15 minutách,
  4xx včetně 409 ne. Šířka po vyčerpání pokusů jen varuje, výška úlohu ukončí.

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
- `/pn`: video k vybraným pozměňovacím návrhům (ruční úloha, viz níže),
- `/autopilot on|off` (bez argumentu stav): zveřejňování videí z `/pn` bez schválení (viz Autopilot níže, výchozí vypnuto),
- `/fronta`: kromě položek ke schválení i zadání ve frontě (u úloh k PN i čísla PN) a kdy bylo PC naposledy vidět,
- `/stav`: stav dat (viz Hlídání dat níže).

**Video k PN (`/pn`):**

- `/pn` bez čísla: PN podané za posledních 48 h (podle `pn.cas`), nejvýš 10, nejnovější první, s autorem, časem
  a titulkem rozboru AI. U každého tlačítko „Video k PN N“, u tisku s víc PN v seznamu navíc „Jedno video ze všech“
  (z nejnovějších nejvýš 5 PN tisku),
- `/pn 2376`: úloha k jednomu PN; `/pn 2376 2377 2378`: tři samostatné úlohy (každá vlastní sada),
- `/pn 2376+2377`: jedno video o obou PN; všechny musí patřit k jednomu tisku, nejvýš 5,
- neznámý PN (ještě nedorazil synchronizací) i kombinaci PN z různých tisků bot odmítne s vysvětlením.

Úloha je ruční (`manual`, denní limit sad se jí netýká), tisk se vezme z tabulky `pn` a čísla PN se uloží seřazená
do `reel_jobs.focus_pn` (NULL = celý tisk). `claim` je vrátí jako `focus_pn` a k nim `focus_pn_info` (autor, čas,
titulek AI, odkaz na psp.cz). Generátor zapíše `podklady/zadani.md` a vezme pevně formáty v3 `pn` a `zmena` (formát
`cesta` ani `rozprava` o obsahu PN nevypráví a `/pn` formát nezadává). Návody staví hlavní fakt, titulek a háček
všech variant na zadaných PN, tisk je jen kontext; kontrola vyřadí variantu, jejíž karta nebo popisek číslo žádného
zadaného PN nezmíní. Ručně: `reels-generate … --pn 2376,2377`.

Ze sady pošle bot všechny varianty a pod ně jednu zprávu s tlačítky Vybrat N a Hotovo, zbytek zamítnout. Vybrat N
pošle variantě vlastní náhled se Schválit / Upravit popisek / Zamítnout (jako samostatné video) a zpráva sady přijde
jen o její tlačítko, takže jde vybrat víc variant. Hotovo zamítne zbylé nevybrané varianty (před výběrem celou sadu);
starší zprávy sad s tlačítkem Zamítnout vše fungují stejně. Schválit ani úprava popisku nejde u nevybrané varianty,
odpověď na zprávu sady nemění žádnou. `/fronta` pošle znovu zprávu sady se zbylými variantami a náhledy vybraných.
Sada se do limitu počítá jako jedna nevyřízená jednotka, dokud v ní něco čeká (výběr nebo schválení).

**Limity:**

- výdej úloh: nejdřív `manual`, pak `auto_pick`, pak `fresh` (čerstvé nálezy), uvnitř podle stáří; pro jeden tisk
  a jednu množinu PN nejvýš jedna aktivní úloha (index `reel_jobs_active_ct_pn`; úloha k celému tisku a úlohy
  k jeho PN běží vedle sebe),
- úloha `fresh` vzniká, když se mezi hlavními nálezy objeví nový tisk nebo nový důvod skóre; nejvýš 2 nové úlohy
  `fresh` za den (Europe/Prague), zbylé nálezy přijdou na řadu další dny; první běh detektoru jen uloží výchozí stav,
- vydaná úloha bez výsledku se po 60 minutách vrátí do fronty, po 3 pokusech skončí `failed`; `fresh` starší než
  7 dní se už nevydá (`expired`),
- do Telegramu jdou nejvýš 2 automatické sady (`fresh`, `auto_pick`) denně (Europe/Prague) a najednou nejvýš
  1 nevyřízená jednotka; sady ručních úloh (`/video`, `/pn`) denní limit neomezuje a nepočítají se do něj, `/dalsi` limit
  obchází; když automatická sada čeká kvůli limitu, bot to jednou ohlásí (s odkazem na `/dalsi`),
- každé čekání má lhůtu (`queue.expire`, jednou za hodinu) a vypršení bot ohlásí zprávou a odebere tlačítka; lhůty běží
  od odeslání do Telegramu (`sent_at`), neodeslaná sada nevyprší, autopilot se nepočítá:
  nedotčená automatická sada (`fresh`) 7 dní, nedotčená vyžádaná (`manual`, `auto_pick`, `ukazka`) 14 dní,
  vybraná nerozhodnutá varianta 14 dní od výběru (`picked_at`), nevybrané varianty dotčené sady 24 h od posledního
  rozhodnutí (dokud žádná vybraná nečeká na schválení) → `expired`; `held` 7 dní od posledního pokusu → `rejected`.
  Vypršení jen zamítá, nikdy neschvaluje. Nedotčená automatická sada po 12 h přestane brzdit další a přijde jedna
  připomínka (`reel_state` `reminded:<set_id>`),
- sada `fresh` nebo `auto_pick` bez výběru, jejíž tisk vypadl z hlavních nálezů, se stáhne (`withdrawn`, `fresh.retire`).
- denní souhrn (8:00) má oddíl „Fronta Reels“ (`queue.summary_lines`): vypršelé nevybrané varianty a stažené sady za
  24 h a vybrané varianty, kterým lhůta vyprší zítra.

## Autopilot (`/pn` bez schválení)

Přepínač `autopilot` (tabulka `reel_state`, výchozí vypnuto) mění jen chování `/pn`. Zapnutý autopilot nastaví nové
úloze z `/pn` příznak `reel_jobs.autopilot`; worker pro ni vygeneruje 2 varianty (v2 a v3 `pn`). Sada smí mít u všech
úloh 1 až 3 platné varianty (minimum 1): nevzniklé varianty jdou v manifestu jako `skipped` a jako poznámka do
`reel_jobs.error`, sada s jedinou platnou variantou se pošle.

- Při převzetí sady bot založí položky rovnou jako `approved` s `reel_queue.autopilot` jen tehdy, když má úloha příznak
  a přepínač je zapnutý. Jinak jde sada ke schválení jako obvykle.
- Bot zveřejní v jednom cyklu nejvýš jednu položku autopilota a pošle odkazy na FB, IG a Threads. Selhání dostane
  tlačítko Zkusit znovu.
- `/autopilot off` zastaví zveřejňování hned a ještě nezveřejněné položky autopilota přesune do běžného schvalování.
  Zároveň smaže příznak úlohám, které ještě čekají ve frontě nebo je má worker (`queued`, `claimed`): jejich sady
  půjdou ke schválení, i když se autopilot před převzetím sady znovu zapne. Odpověď uvede počty videí i úloh.
- `/video`, `/vyber` a `fresh` autopilot nikdy nepoužijí.
- Pořadí nasazení: nejdřív server (web a bot společně přes redeploy), potom worker na PC.
- **Autopilot nezapínat, dokud není aktualizovaný worker na PC.** Starý worker pole `autopilot` v claimu ignoruje
  a vygeneruje 3 varianty včetně formátu `zmena`, které by pak šly ven bez schválení.
- Hned po redeployi serveru může API workeru několik sekund vracet 500, dokud bot nevytvoří nové sloupce
  (`autopilot` v `reel_jobs` a `reel_queue`). Worker počká a zkusí to znovu, zásah není potřeba.

## Hlídání dat

Bot hlídá, že se data, ze kterých čerpá generátor a MCP, obnovují (`hlidac_reels/hlidani.py`, jen čtení z databáze
webu). Každou hodinu pošle jednou výstrahu při novém problému a jednou „Obnoveno“, když problém zmizí; stav drží
`reel_state` (`hlidani_aktivni`). V 8:00 Europe/Prague pošle denní souhrn (`hlidani_souhrn_den`), `/stav` ho pošle
na požádání. Poslední výsledek kontroly ukládá do `reel_state` `hlidani_vystrahy`, MCP `stav_dat` ho vrací jako `vystrahy`.

| Kontrola | Práh | Zdroj |
|---|---|---|
| synchronizace s psp.cz | poslední úspěšný běh > 2 h | `sync_beh` (dřív meta `last_sync`) |
| stenoprotokoly (`rag-ingest`) | poslední úspěšný běh > 3 h (chyba a nedokončený běh se nepočítají) | `steno.beh` |
| AI rozbor | nejstarší čekající nový text > 12 h, nebo poslední výsledek > 24 h a klient AI se na frontu 24 h neptal | `tisk_text`, `pn_text` (jako `ai_api.php?action=queue`), meta `ai_queue_seen_at` |
| schůze bez `rec` | > 7 dní po posledním dni jednání | `steno.schuze`, `steno.rec` |
| tisk Vyhlášeno bez čísla Sbírky | > 14 dní od kroku Sbírka | `tisky`, `kroky` |
| tisk mění jednací řád 90/1995 nebo Ústavu 1/1993 | fáze 3. čtení a dál; nová výstraha při každé změně fáze, bez „Obnoveno“ | jako MCP `tisky_menici_zakon`; pak zkontrolovat `generator/examples/lhuty.md` a `pravidla.md` |
| PC s generátorem | `worker_seen_at` > 24 h, jen v denním souhrnu | `reel_state` |

## Testy

```bash
pip install -e ".[dev]"
pytest -q -m "not render"                       # bez Chromia
DATABASE_URL=postgresql://… REELS_TEST_DB=1 pytest -q   # i testy nad PostgreSQL
```
