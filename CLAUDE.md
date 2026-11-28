# CLAUDE.md – Hlídač zákonů

## Účel

Web, který z otevřených dat Poslanecké sněmovny (psp.cz) vytahuje **procedurální „prasárny“** v legislativním procesu
a běžnou agendu potlačuje. Inspirace: kritika Bohumila Pečinky (zrychlené projednávání, obcházení připomínkového
řízení, zásadní změny vkládané pozměňovacími návrhy, např. zkrácení lhůty pro vymáhání dotací SZIF z 10 na 4 roky
i zpětně v novele zákona o střetu zájmů).

Výstupy: web (přehled, tisky, detail tisku, upozornění), RSS a příspěvky na sociální sítě (Bluesky, Mastodon,
Telegram, X). E-mail se záměrně nepoužívá.

**Zásada neutrality:** skóre hodnotí *postup a formu* (jak se zákon prosazuje), nikdy politický obsah nebo stranu.
Žádné váhy podle strany či jména poslance. Každý bod skóre musí mít čitelný důvod zobrazený na webu.

## Nasazení (čti jako první, pokud jde o instalaci)

Postup pro člověka je v **`NASAZENI.md`** – drž se ho a nic nevynechávej. Shrnutí:

- **Hosting (PHP, FTP):** obsah ZIPu do kořene webu, `config.php` z `config.example.php` (`site_url`, `cron_token`,
  `ai.enabled = true`, `ai.remote_token`, `notify.dry_run = true`), `data/` zapisovatelné, ověřit 403 na
  `config.php` a `data/`, první běh `cron.php?token=…`, pak cron/webcron každou hodinu.
- **AI rozbor:** neběží na hostingu, ale na počítači s přihlášeným Claude Code (předplatné, ne API klíč):
  `deploy/ai-remote.sh` (nastavení `deploy/ai-remote.env` podle `.example`) naplánovaný přes launchd
  (`deploy/cz.hlidac.ai.plist`, nahradit `CESTA_K_PROJEKTU`) nebo cron na Linuxu. Skript volá
  `bin/ai.php --remote … --all` (Haiku) a `--verify` (Sonnet); log `data/ai-remote.log`.
- **Balík:** `bin/package.sh` vytvoří ZIP s hotovou databází `data/hlidac.sqlite` (texty + AI rozbor, čistá kopie
  přes VACUUM INTO), ale bez `config.php`, logů, záloh, `deploy/ai-remote.env` a `.git` (`--bez-dat` = bez DB).
  Přibalená DB funguje jen se SQLite; při aktualizaci webu se `data/` na hostingu nepřepisuje.
- Tokeny a klíče sítí patří jen do `config.php` na hostingu a do `deploy/ai-remote.env`. Nikdy je nevypisuj
  do chatu, nenahrávej do gitu ani nevkládej do URL mimo vlastní web.
- Při nasazování neměň logiku skóre ani prompty AI; při chybě nejdřív `php tests/run.php` (musí vypsat `VŠE OK`).

Docker (cosepece.cz): `docker/redeploy.sh [commit]` – před výměnou kontejnerů pustí `tests/run.php` v novém obrazu webu
(`docker run --network none`), po výměně ověří web a MCP a při neúspěchu vrátí předchozí verzi; služba `chat` jde až
po nich (`/chat-api/zdravi`, kouřový eval) a její selhání nasazení nevrací, chat jen vypne; `docker/rollback.sh`
= ruční návrat (commit v `docker/.previous-commit`, obrazy `:previous`). CI: `.github/workflows/php-tests.yml` (PHP 8.3,
SQLite i PostgreSQL; testy musí projít na obou), `deploy.yml` (po sloučení do `main`, vypnuto do `DEPLOY_ENABLED`).
`Http::post(..., $retryDelays)` opakuje jen při výpadku (bez odpovědi, 502–504); `bin/ai.php` tak přečká výměnu kontejneru.

Struktura databáze: `db()` volá `Db::migrateIfNeeded()` – `migrate()` běží jen na nové databázi nebo když se změní otisk
souboru `app/Db.php` (meta `schema_stamp`). Všechny změny struktury (tabulky, sloupce, indexy) proto patří jen do
`Db::migrate()`; DDL jinde by se po nasazení nespustilo.

## Konvence

- Komentáře v kódu **česky**, názvy funkcí, tříd a proměnných **anglicky**.
- Texty na webu a v příspěvcích **česky** (cílovka je česká veřejnost).
- Při úpravách vždy dodávej **celé funkce**, ne fragmenty.
- PHP 8.1+, bez frameworku a bez Composeru (cíl je sdílený PHP hosting přes FTP). `declare(strict_types=1)`.
- Databáze přes `Hlidac\Db` (PDO); SQL musí fungovat v **SQLite i MySQL/MariaDB** (žádné `||`, `INSERT OR REPLACE`,
  `ON CONFLICT`; nové sloupce přidávej přes `Db::addColumn()` v `migrate()`).

Změny, které čtenář uvidí (oprava dat, jiné skóre, nová funkce), zapiš nahoru do `zmeny.php` (datum, nadpis,
popis srozumitelně pro veřejnost, čísla PR, příznak „mění skóre“).

## Příkazy

```bash
php tests/run.php                 # end-to-end test nad reálnými řádky z psp.cz (bez sítě)
php bin/sync.php                  # synchronizace (import → upozornění → texty PN a tisků → AI → skóre → sítě)
php bin/ai.php --ct=221           # AI rozbor přes claude CLI (předplatné) nad místní DB
HLIDAC_TOKEN=… php bin/ai.php --remote=https://web.cz   # fronta z hostingu (ai_api.php, token v hlavičce X-Hlidac-Token)
php bin/ai.php --verify --all     # 2. kolo: nálezy se závažností ≥ ai.verify_min znovu posoudí Sonnet
php bin/ai.php --resanitize       # pojistky (AiReviewer::sanitize) znovu na uložené rozbory, bez modelu
php bin/ai.php --praxe [--verify] # vysvětlení PN „v praxi“ (PnPractice); s --remote fronta z hostingu
php bin/ai.php --praxe --resanitize   # pojistky znovu na uložená vysvětlení (bez modelu); nevyhovující zpět do fronty
php bin/ai.php --praxe --retry    # PN s vyčerpanými pokusy (odmítnuté pojistkami) zpět do fronty, bez modelu
php bin/ai.php --hlaseni          # roztřídí hlášení čtenářů k vysvětlením (Reports); s --remote fronta z hostingu
php bin/ai.php --hlaseni --verify # silnější model zkontroluje vysvětlení s platným hlášením (oprava jen přes pojistky)
php bin/hlaseni.php [--vse]       # přehled hlášení čtenářů; --rozhodnout=ID --jako=platne|spam|… ruční rozhodnutí
php bin/reanalyze.php             # pravidlové signály PnText/TiskText znovu na stažené texty, bez sítě a AI
HLIDAC_CONFIG=/cesta/config.php php -S 127.0.0.1:8099 bin/router.php   # lokální náhled (router kvůli adresám bez .php)
```

Test vypisuje `VŠE OK`; po každé změně logiky ho spusť a případně rozšiř.

## Architektura

```
cron.php / bin/sync.php → Hlidac\Sync::run()   (každý běh zapíše řádek do sync_beh, app/SyncRuns.php)
  0. HistoryHtml   stránky historie tisků (historie.sqw) pro tisky se změnou za 14 dní a na pořadu schůze, nejvýš
                   history_html.per_run za běh: kroky z <!-- LLLP id_hist id_prechod -->, PN z tabulky; do html_kroky /
                   html_pn jen to, co otevřená data nemají. Importer je přimíchá, po příchodu v nočních datech smaže
  1. Importer      stáhne tisky.zip, sd.zip, poslanci.zip, přepíše tabulky tisky / kroky / pn (aktuální období)
  2. AlertBuilder  vytvoří upozornění s jednoznačným klíčem (bez duplicit); 1. běh = bootstrap (posted=2, nic se nepošle)
  3. Scorer        předběžné skóre (určuje pořadí front textů a AI)
  4. PnText        stáhne Word verze PN (sd.sqw → odkaz class="file docx"), pravidla: přílepek, rozsah,
                   zpětné použití (retro) a změny lhůt vymáhání (deadline) s doslovnými citacemi
  5. TiskText      stáhne Word samotného návrhu (tiskt.sqw?o=&ct=&ct1=0 → title="Dokument DOCX"), stejná pravidla
                   na normativní části (bez důvodové zprávy); přednost poslanecké/senátní, § 90/2, zkrácená lhůta
  6. AiReviewer    volitelně: Claude (claude-haiku-5-5) posoudí text tisku i PN (JSON, závažnost 0–10, citace
                   ověřené proti textu – vymyšlené se zahodí). Backend 'cli' = `claude -p` přes předplatné (bez
                   nástrojů, bez ANTHROPIC_API_KEY v prostředí), 'api' = placené API. V Sync běží, jen když je CLI
                   dostupné; na FTP hostingu CLI není → bin/ai.php --remote stáhne frontu z ai_api.php (token
                   ai.remote_token nebo cron_token), posoudí ji na Macu a pošle výsledky zpět.
                   Pojistky (sanitize): zpětnost ve prospěch veřejnosti max. 3/10 a bez „zpětně“ v titulku,
                   zásahy v ai_json.upravy. 2. kolo --verify: ai.verify_model (claude-sonnet-5-5) přepíše
                   rozbor nálezů ≥ ai.verify_min (6); ai_model říká, kdo rozbor udělal (homepage: „ověřeno“).
                   Spolehlivost (#7 bod 7): hraniční hodnocení repeat_min–repeat_max (3–7) posoudí rychlý model
                   repeat_runs× (ai_runs, ai_run_count), platí medián; --verify bere i namátkový vzorek nízkých
                   hodnocení (< sample_below, číslo textu % sample_every = AiReviewer::SAMPLE_REMAINDER).
                   Repo::aiReliability() počítá shodu modelů pro metodika.php#spolehlivost-ai. Po změně promptu zvyš
                   AiReviewer::PROMPT_VERSION – fronta pak postupně znovu posoudí starší rozbory (ai_prompt)
  6b. PnPractice   vysvětlení PN „v praxi“ (co by se změnilo, kdyby prošel): samostatný průchod s vlastním promptem,
                   sloupce pn_text.praxe_*, do skóre nevstupuje. Model dostane zvlášť text změn, odůvodnění
                   předkladatele a dotčenou část návrhu zákona (billContext). Fronta ai_api.php?praxe=1 (kind praxe),
                   klient bin/ai.php --praxe [--verify]: Haiku všechny PN, Sonnet znovu PN s nálezem a všechny věcné
                   změny (ai.praxe_verify_types; smí přidat modelový příklad). Pojistky PnPractice::sanitize: citace doslova z textu změn, čísla jen z podkladů
                   (nebo součet/rozdíl dvou z nich, počet dnů i jako rozdíl dvou dat téhož měsíce), bez hodnoticích slov a jména předkladatele, pole „nejiste“ bez
                   poznámek o podkladech a první osoby (vypustí se); odmítnutý výsledek
                   se neuloží (praxe_error, nejvýš praxe_tries pokusů; výpadek modelu nebo spojení se počítá zvlášť
                   v praxe_fails se stropem ai.praxe_fails a takové PN jdou ve frontě až za ostatní; počitadla patří
                   k verzi praxe_try_v, po zvýšení VERSION se nepoužijí). Čísla paragrafů a zákonů nejsou hodnoty
                   ke sčítání. `bin/ai.php --praxe --resanitize` pustí pojistky znovu na uložená vysvětlení. Znění s vyznačením změn přiložené
                   za odůvodněním jde modelu jako samostatný blok (PnPractice::attachment), necituje se z něj. Opakovaný pokus
                   dostane v zadání důvod minulého odmítnutí; `--praxe --retry` vrátí do fronty PN s vyčerpanými pokusy.
                   Po změně promptu zvyš PnPractice::VERSION (přepíše všechna vysvětlení; u drobného zpřesnění stačí --retry)
  6c. Reports     hlášení chyb ve vysvětlení „v praxi“ od čtenářů (app/Reports.php, tabulka hlaseni): praporek
                   v rohu bloku vysvětlení (View::reportLink) otevře vrstvu s formulářem (View::reportDialog,
                   assets/report.js; bez JavaScriptu stránka hlaseni.php). submit(): stejný původ, past na roboty,
                   limit z adresy a denní strop, levné filtry (bez poznámky = platné, víc odkazů = spam).
                   Ostatní třídí Haiku (fronta ai_api.php?hlaseni=1, kind hlaseni, klient bin/ai.php --hlaseni):
                   poznámka čtenáře je cizí text mezi náhodnými značkami, model běží bez nástrojů a smí vrátit
                   jen verdikt v_poradku / spam / nesmysl / manipulace / mimo_tema; cokoli jiného = stav rucne
                   (počká na člověka). Odkazy na ustanovení se uloží, jen když doslova stojí v poznámce i v textu PN
                   (verifyRefs). Vyřazená hlášení se po hlaseni.keep_days dnech mažou (Sync → purge). Poznámka
                   se nesmí předávat dalšímu modelu jako pokyn. Skóre se nemění.
                   Zveřejnění na GitHubu (hlaseni.github, výchozí vypnuto; Sync → publish, ručně bin/hlaseni.php
                   --odeslat): platná hlášení s poznámkou a se souhlasem (sloupec souhlas = formulář upozornil
                   na zveřejnění) jdou jako podnět, další k témuž vysvětlení jako komentář (gh_issue), nejvýš
                   github.per_day za 24 h. Poznámka je v bloku kódu s ohraničením delším než nejdelší řada
                   zpětných apostrofů v ní (Reports::fence). dry_run jen zapisuje do data/hlaseni-github.log
                   (gh_zkouska) a po vypnutí se hlášení odešlou. 401/403 běh ukončí bez počítání pokusu.
                   Kontrola nahlášeného vysvětlení (hlaseni.check; fronta ai_api.php?hlaseni=1&verify=1, kind kontrola,
                   id = id dokumentu PN; klient bin/ai.php --hlaseni --verify): Sonnet dostane běžné zadání
                   vysvětlení (PnPractice::prompt) a dodatek Reports::checkPrompt s dosavadním vysvětlením, označenými
                   částmi a ověřenými ustanoveními; text čtenáře nikdy. Výsledek "kontrola": beze_zmeny = vysvětlení
                   se nepřepisuje; jinak oprava musí projít PnPractice::clean (stejné pojistky), pak ji uloží
                   PnPractice::store a původní znění zůstane v hlaseni.kontrola_puvodni. Neprojde-li check_tries×,
                   výsledek je neproslo a vysvětlení zůstává. Opakované hlášení téže části bez nových ustanovení
                   převezme výsledek bez volání modelu (inheritChecks), strop check_per_day. Web u vysvětlení
                   ukáže větu o kontrole (View::reportCheckNote), na GitHub jde výsledek jako komentář k podnětu
  7. Scorer        skóre tisků 0–100 + důvody (tisky.reasons JSON), skóre upozornění = skóre tisku + bonus typu
  1b. Votes      hlasování k tiskům (hl-{rok}ps.zip, schuze.zip): z kroků historie (hist.id_hlasovani) a podle bodu
                schůze + stejného krátkého názvu; souhrn a rozdělení podle klubů (tabulky hlasovani, hlasovani_kluby),
                nespárovaná s ct = 0, hlasy poslanců (hlasovani_poslanci, jen nová hlasování, po dávkách).
                Do skóre nevstupuje; chyba nezastaví synchronizaci
  1c. GovPrep    příprava návrhu u vlády z otevřených dat Úřadu vlády (odok.gov.cz/opendata, JSON po letech, cache 20 h;
                robots.txt portálu zakazuje procházení webu, proto jen tyto soubory): vládní návrhy přes tisk_eklep.unl
                + stejný název verze v připomínkách, nevládní podle „(sněmovní tisk č. N)“; tabulka priprava.
                Do skóre nevstupuje, upřesňuje jen popis bodů za poslanecký návrh. Z týchž dat tabulka pripravovane
                (pripravovane.php, api.php?pripravovane=1): typ NZ z tohoto období, verze seskupené podle normTitle,
                fáze z nejnovější verze (GovPrep::phaseOf), vyřazené už ve Sněmovně (pole psp, tisk_eklep, název tisku)
  8. Publisher     pošle na sítě jen upozornění se skóre ≥ notify.min_score (výchozí 50); post_log, max 3 pokusy
```

Náhledy pro sítě: každá stránka má `og:`/`twitter:` značky (`View::header`, 4. parametr), obrázek 1200 × 630
kreslí `og.php` → `app/OgCard.php` (GD + FreeType, písmo `assets/fonts`, cache `data/cache/og`, bez GD
`assets/og-default.png`). Při změně vzhledu zvyš `OgCard::VERSION`.

Hlavní nálezy (index.php, `Repo::findings`): řazení `?razeni=` (skore, ai = ověřená závažnost AI s citací, nove,
hlasovani = podle fáze) a druh problému `?druh=` (`Repo::PROBLEM_GROUPS` z klíčů důvodů skóre + ověřené AI příznaky
přílepku/zpětnosti + krátké připomínky u vlády). Skupiny drž úzké, jinak mezi nálezy nic neodfiltrují.

Vyhledávání: `app/SearchIndex.php` staví při synchronizaci index (tisky + titulky AI, PN a autoři, zákony z názvů
tisků, připravované, druhy problémů, stránky) do `data/cache/search-index-<db>.json`; `hledat.php?index=<otisk>`
ho posílá prohlížeči (gzip, cache), `assets/search.js` našeptává stejným algoritmem jako `SearchIndex::search()`
(PHP stránka výsledků) – při změně jednoho uprav i druhý. Dotazy se neukládají.

Tokeny MCP z webu: `app/McpTokens.php` + formulář v `mcp.php`; zapisuje do `mcp.token` služby rag/ (jen sha256,
jméno „web: datum · účel“), jen při PostgreSQL. Ochrana: podepsaný údaj formuláře vázaný na IP (3 s – 1 h,
klíč meta `form_secret`, ne cron_token), kontrola Origin/Sec-Fetch-Site, skryté pole, 3 tokeny/IP/den
(RateLimit 'mcp_token', započítá se před vložením), 200 tokenů/den celkem; synchronizace ruší tokeny z webu
nepoužité 7 dní nebo nepoužívané 90 dní (`config.php → mcp`).

Adresy stránek jsou bez `.php` (`app/Url.php`): `/tisk/221`, `/zakon/159/2006`, `/zakony`, `/tisky`, `/data-a-api`
(ne `/data`, to je adresář), `/hledat?q=…`. Odkazy v HTML vždy přes `url('tisk', ['ct' => 221], 'pn-5')` (adresa
od kořene webu, nikdy relativní ke stránce a bez `<base>`), celé adresy přes `absUrl()`, soubory přes `asset()`.
Strojové adresy (`api.php`, `rss.php`, `og.php`, `ai_api.php`, `reels_api.php`, `cron.php`) se nemění. Přepis
hezkých adres na skripty dělá `.htaccess` (stejná pravidla má `Url::route()` pro `bin/router.php`, shodu hlídá test),
staré adresy s `.php` přesměruje `bootstrap.php` (301, jen GET/HEAD). Nová stránka = řádek v `Url::PAGES`
a pravidlo v `.htaccess`. Bez mod_rewrite: `config.php → pretty_urls = false`.

Reels: `reels_api.php` (generátor Reels na PC): akce `claim` (odpověď navíc `attempt`, `last_error`, `lease_s`, `progress_s`; `focus_pn_info` s klubem ke dni podání, `has_text` a `vysledek` z `pn_vysledek` s hlasováními; úloha s jedním PN = jedna varianta `template_pn_v1` formátu `pribeh`; `card_v2` nese `predkladatel` z dat, `ReelJobs::proposerCard` + `app/Proposers.php` + `tisky.navrhovatele` z Importeru, a u titulku z PN `pn_autor` s klubem ke dni podání), `progress`, `result`, `fail` (`kind`, `retry_after_s`, `reason`), `heartbeat` (stav workeru); 409 u zrušené úlohy. `ReelJobs` zapisuje přechody do `reel_events` (časy `G:i`, `dedupe` nejdřív ověří). `Sync` hned po `Revisions::backfill` hlídá živost bota Reels (`reel_state.bot_seen_at` > 15 min → Sentry, výsledek `reels_bot`). Klub poslance ke dni: `app/Clubs.php`, v den přestupu platí novější klub (uložené hlasy z toho dne `Votes` přepočítá, `Clubs::ambiguous`).

Vstupy a limity: `bootstrap.php` odmítá jiné metody než GET/HEAD (POST jen mcp.php a ai_api.php), parametry-pole
zahazuje a čísla o 10+ číslicích ořízne (jinak chyba INTEGER v PostgreSQL). `RateLimit` počítá IPv4 adresu
a u IPv6 síť /56; za CDN je nutné doplnit důvěryhodné proxy do `RemoteIPInternalProxy` (docker/Dockerfile),
jinak by všichni návštěvníci sdíleli jeden limit.

Upozornění na připravované: `AlertBuilder::fromUpcoming` (ct = 0, klíč `prep_review|prep_plan|prep_vlada:<pid>`,
skóre = `AlertBuilder::UPCOMING_KINDS`: < 10 dní 50, < 21 dní 30, mimo plán 30, schváleno vládou 0); první běh
bootstrap (meta `prep_alerts_bootstrapped`; zapíše se až s daty v tabulce `pripravovane`). Verze téhož materiálu mají
jiný PID: `pripravovane.pidy` drží PID všech verzí, `addUpcoming()` nevydá upozornění, které už existuje pod kteroukoli
z nich. `View::alertUrl` vede na `pripravovane.php#prep-<pid>`, jinak do VeKLEP (tabulka PID `View::upcomingIndex()`,
po změně tabulky `View::forgetUpcoming()`). Filtr `zakon=` v API a RSS páruje tato upozornění podle čísla zákona v názvu.

Které PN prošly (#5, bez skóre): `app/PnOutcome.php`. Souhrn PN po 2. čtení (tisk X/n, `tisky.souhrn_pn`) → písmena a čísla
SD (`parseSummary`, tabulka `pn_souhrn`; písmena předkladatelů čísluje často Word, pak se odvodí z pořadí = „odhad“ a platí
jen se jménem předkladatele v ohlášení). Stenozáznam 3. čtení ze schématu `steno` služby rag/ (`steno.projev.hlasovani`):
zpravodaj těsně před hlasováním řekne písmeno nebo rovnou číslo dokumentu (`mapVotes`; číslo má přednost, popis hlasovací
procedury a dlouhé projevy se přeskakují, „návrh B“ bez čísla se k B1, B2 nepřiřazuje), výsledek z tabulky `hlasovani`. Stavy v `pn_vysledek`: prijat,
castecne, zamitnut, nehlasovatelny (zpravodaj řekl, že se o něm nehlasuje: `notVoted`), nezaznel (není v souhrnu), ceka (3. čtení
ještě neproběhlo), neurceno. Ohlášení se čte po větách (`announced`): věty o nehlasovatelných návrzích se vynechají, společné
hlasování („jedním hlasováním“, „E1 až E6“, „návrhy pod písmeny E1, E2 a E4“) platí pro všechny jmenované návrhy. Bez schématu
`steno` (SQLite, hosting) jen zazněl / nezazněl. Zobrazení `tisk.php` (`View::pnOutcome`), API `tisk.vysledky_pn`.

Senát a kroky po něm (#6, bez skóre): `Analyzer::senate()` z historie tisku – výsledek je akce kroku, který následuje
po stavu 12 „Senát“ (21/33 schválen, 24 vrácen, 27 zamítnut, 11/37/53 nezabýval se, 6 nečinný, 36 bez usnesení); po stavu
14/63 akce 20 = Sněmovna setrvala (přehlasovala Senát), 15 = přijala znění Senátu; po stavu 8 akce 7 = veto, po stavu 13
akce 20 = veto přehlasováno. `Analyzer::senateLines()` dává věty pro detail tisku (`tisk.php#senat`) a API (`tisk.senat`).
Upozornění `senate:<ct>`, `override:<ct>`, `vetoover:<ct>` (druhy `senate` a `override`, bonus 0, meta
`senate_alerts_bootstrapped`). Data kroků jsou den, kdy výsledek dorazil do Sněmovny; senat.cz se nenačítá (robots.txt).

Připravovaná nařízení vlády (#59): typ NNV z týchž dat ODok, vlastní tabulka `pripravovana_narizeni` se stejnými sloupci
(`GovPrep::upcoming(..., 'narizeni')`). Verze se spojují jen v okně `REGULATION_VERSION_DAYS` (každoroční nařízení mají
stejný název), schválené nařízení zmizí po `REGULATION_KEEP_DAYS` (vyhlášení ve Sbírce data neuvádějí), název resortu se
doplní podle zkratky z jiných záznamů. Stránka `pripravovane.php?druh=narizeni`, API `api.php?pripravovane=1&druh=narizeni`,
upozornění jen na připomínky < 10 dní (`prep_narizeni`, klíč `prep_nv:<pid>`, 30 bodů, meta `prep_nv_alerts_bootstrapped`).
Nařízení se nepárují se zákony (čísla nařízení a zákonů se překrývají). Vyhlášky (NV) a nelegislativní materiály se neberou.

Stránka zákona: `zakon.php?z=159/2006` (bez parametru přehled), `app/Laws.php` – slovník názvů z úplných názvů tisků
a připravovaných (nejčastější varianta), tisky podle `tisky.zakony`, PN k jiným tiskům podle `pn_text.laws`,
připravované podle čísla v názvu. Hledání (`SearchIndex`) bere názvy zákonů ze stejného slovníku.

Soubory: `app/` (třídy, namespace `Hlidac\`), stránky v kořeni (`index.php`, `tisky.php`, `tisk.php`,
`upozorneni.php`, `rss.php`, `revize.php`), `assets/style.css`, `data/` (SQLite, cache, logy – neveřejné, .htaccess).

Nové volební období (`Importer::switchPeriod`): stará upozornění dostanou klíč `9/…` a odkaz na psp.cz (`alerts.obdobi`),
texty a rozbory se nemažou: celé řádky se zkopírují do `tisk_text_archiv` / `pn_text_archiv` (sloupec `obdobi`) a z pracovních
tabulek zmizí, takže detail tisku, skóre a fronty pracují jen s aktuálním obdobím. Nové období začne bootstrapem (nic se neposílá).

Historie verzí (`app/Revisions.php`, #2 bod 2): `text_revize` (každé nové znění textu tisku nebo PN: stažení, obnova při
změně idd) a `ai_revize` (každý uložený rozbor AI: 1. i 2. kolo, `--resanitize`; model, verze promptu, závažnost, ai_json,
otisk textu). Záznam přibude jen při skutečné změně (sha1), klíč `(druh, obdobi, ref_id, verze)`, nic se nemaže. Stav, který
v historii ještě není, se zapíše před přepsáním; jednorázové doplnění dosavadních dat `Revisions::backfill` na začátku
`Sync::run` (meta `revize_backfill`, opakování nic nepřidá). Web: odkaz „Historie (N verzí)“ v detailu tisku (u textu návrhu
i u PN, když je verzí textu nebo rozboru víc než jedna) → `revize.php?druh=tisk|pn&id=…` (seznam verzí, rozdíl dvou verzí
textu po odstavcích a slovech přes `app/Diff.php`, změny závažnosti a titulku rozboru). Skóre historie neovlivňuje. Nával PN = ≥ `surge_threshold` PN k tisku
za kalendářní den podle času podání, ne za běh synchronizace.

Tabulky: `tisky`, `kroky` (historie), `pn`, `pn_seen`, `pn_text`, `tisk_text`, `alerts`, `post_log`, `meta`, `pripravovane`,
`text_revize`, `ai_revize` (historie verzí), `tisk_text_archiv`, `pn_text_archiv` (texty minulých období), `sync_beh`
(historie běhů synchronizace, 90 dní), `html_kroky`, `html_pn` (záznamy ze stránek historie tisků), `hlasovani_poslanci`, `hlaseni` (hlášení čtenářů k vysvětlením).

## Zdroj dat (psp.cz open data)

- `https://www.psp.cz/eknih/cdrom/opendata/{tisky,sd,poslanci}.zip`, soubory `.unl`, oddělovač `|`, **windows-1250**.
- Popis: https://www.psp.cz/sqw/hp.sqw?k=1300 (sněmovní dokumenty: k=1309).
- Aktuální období: `organy.unl`, id_typ_organu = 11, zkratka `PSPn`, prázdné datum konce (10. období = id 174).
- `tisky.unl`: 0 id_tisk, 1 id_druh, 3 ct, 4 cislo_za (0 = hlavní tisk), 7 id_org_obd, 9 navrhovatel, 10 nazev, 11 předloženo, 15 úplný název.
- `hist.unl`: 0 id_hist, 1 id_tisk, 2 datum, 4 id_prechod, 11 datum vyhlášení, 13 číslo ve Sbírce.
  Stav kroku = `prechody.unl` (id → kam, id_akce) → `stavy.unl` (id → id_typ_stavu) → `typ_stavu.unl`.
- Typy stavů: 17/18/67 = 1. čtení podle § 90/2, 2–5 = 2. a 3. čtení, 21 = Pozměňovací návrhy, 64 = Odesíláno do Senátu,
  11 = Sbírka. Akce: 47/59/72 = změna (zkrácení) lhůty, 238 = zahájeno, 23 = ukončena.
- `sd_dokument.unl`: 0 id, 1 id_obdobi, 2 číslo, 3 typ (13 = písemný PN), 6 ct, 7 id_osoba autora, 8 čas.
  Stav platnosti PN Sněmovna neeviduje.
- Stránka PN: `https://www.psp.cz/sqw/sd.sqw?cd={cislo}&o={obdobi}` → odkazy `/sqw/text/orig2.sqw?idd=…` (PDF a DOCX).
- Soubory tisku: `https://www.psp.cz/sqw/text/tiskt.sqw?o={obdobi}&ct={ct}&ct1=0` → `<a href="/sqw/text/orig2.sqw?idd=…" title="Dokument DOCX">`.
  Text tisku 221 obsahuje „… lze zahájit nejpozději do 4 let …“ (§ 44a rozpočtových pravidel) a přechodné ustanovení
  „… i tehdy, pokud byla neoprávněná platba dotace poskytnuta přede dnem nabytí účinnosti …“ – vzorový případ.
- WebFetch na psp.cz blokuje robots.txt; z cloudového kontejneru není psp.cz dostupné. Ověřování proběhlo v prohlížeči.

## Skóre závažnosti (app/Scorer.php)

Výchozí váhy v `Scorer::defaults()`, přepis přes `config.php → scoring`. Úrovně: ≥ 50 Závažné, ≥ 25 Pozor, jinak Běžné
(potlačeno: neposílá se, na webu skryto za odkazem „běžná agenda“).

| Signál | Body |
|---|---|
| Poslanecký návrh: postupuje (2. čtení a dál) / zatím v 1. čtení; senátní / krajský (podle `tisky.id_navrh`) | 15 / 5; 5 / 5 |
| § 90/2 navrženo (krok v historii nebo `tisky.rj = 1`, jen rozpracovaný tisk) / schváleno v 1. čtení | 10 / 25 |
| Zkrácená lhůta (akce změny lhůty nebo lhůta pro výbory `tisky.lhuta` < 60 dní) | 15 |
| Mezi PN a zahájením 3. čtení < 7 dní | 15 |
| ≥ 3 PN a ≥ 50 % všech PN tisku v den konce podrobné rozpravy | 10 |
| Nával PN (≥ surge_threshold PN k tisku za kalendářní den) | 5 |
| PN mění jiný zákon než návrh – podle názvu i textu návrhu (přílepek; nepočítá se, když AI potvrdí souvislost) / rozsáhlá nová úprava v PN | 20 / 10 |
| ≥ `pn_many_min` (3) problematické PN (pravidlo, nebo AI ≥ `pn_flag_ai_min` 6) a zároveň ≥ `pn_many_share` (20 %) všech PN tisku; od `pn_many_abs` (10) stačí počet | 5 |
| Text návrhu nebo PN mění lhůtu vymáhání a použije ji i zpětně (retro + deadline); nepočítá se, když nález potvrdil rozbor AI téhož textu (≥ 4 se zpětnou účinností nebo zkrácením lhůt) | 15 |
| AI závažnost textu návrhu (0–10) × 3 | 0–30 |
| AI závažnost nejhoršího PN (0–10) × 3; nález ≥ `ai.verify_min` neověřený Sonnetem nejvýš 5/10; plné body jen když ≥ `ai_pn_full_count` (2) PN nebo ≥ `ai_pn_full_share` (25 %) PN má závažnost ≥ min(`ai_pn_full_min` 6, nejhorší), jinak × `ai_pn_single_factor` (0,5) | 0–30 |
| Citlivá oblast (redakční výběr: střet zájmů, dotace/SZIF, veřejné zakázky, kontrolní instituce, policie a služby, volby, média…; samotný trestní zákoník ne) | 5 |
| Souhrnná novela / sledovaný zákon | 3 / 10 |

## Stav a další kroky

Hotovo a otestováno na reálných řádcích (tisky 90, 311, 221, 78): import, příznaky, fáze, Sbírka, upozornění,
bootstrap, dry-run rozesílání, skóre, pravidlová analýza textu PN, všechny stránky.

Neověřeno / TODO:
1. **První běh na produkčním hostingu** (hosting se zatím vybírá) – výkon a paměť, kontrola počtů
   (testovací běh v prohlížeči 17. 11. 2026: 176 návrhů zákonů, 887 PN v 10. období).
2. **Kalibrace vah** na reálných datech: projít top 20 podle skóre a ověřit, že nahoře jsou opravdu sporné věci
   (tisk 221 novela střetu zájmů, tisk 90 veřejné rozpočty, tisk 311 daně – PN o zemědělských zvýhodněních).
3. Regexy přílepků v `PnText::analyze()` – ověřit na reálných PN (formulace „V zákoně č. …“, „ČÁST … Změna zákona …“);
   u tisků bez čísla zákona v názvu se přílepek zatím nepozná.
4. AI rozbor celého období proběhl lokálně 17.–18. 11. 2026 (519 textů Haiku, 124 nálezů ověřeno Sonnetem).
   Plánování na Macu je připravené (`deploy/`), na produkčním hostingu ještě neběželo.
5. GitHub: soukromé repo `moodixmarket/hlidac-zakonu` (kód) + veřejné `moodixmarket/hlidac-zakonu-podnety`
   (jen README a šablony issues – připravené v adresáři `podnety/` v tomto repu, k přesunu do vlastního repa).
6. Účty na sociálních sítích a klíče (jen do `config.php` na hostingu, nikdy do gitu).
