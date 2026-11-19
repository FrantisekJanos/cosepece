# Nasazení webu Co se peče

Aplikace běží na Laravelu 13 a PHP 8.3+. Veřejným adresářem je výhradně `public/`.
Python ingest a MCP zůstávají samostatnými službami, viz [rag/README.md](rag/README.md).

## Místní spuštění

Potřebujete Composer 2, PHP 8.3+ a rozšíření `curl`, `iconv`, `mbstring`, `zip`, PDO a ovladač vybrané databáze.
Pro generování obrázků nainstalujte také GD s FreeType. Bez něj se používá záložní PNG.

```bash
composer install
cp .env.example .env
php artisan key:generate
mkdir -p data
touch data/hlidac.sqlite
php artisan migrate
php artisan serve
```

Do `.env` nastavte `APP_URL` na adresu webu. Výchozí databáze je SQLite `data/hlidac.sqlite`.
Pro celý systém včetně Python MCP použijte PostgreSQL. Připojení nastavují běžné proměnné
`DB_CONNECTION`, `DB_HOST`, `DB_PORT`, `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD`.
Nastavení sledovaných zákonů, vah a limitů je v `config/hlidac.php`; provozní hodnoty lze předat prostředím.
Podporované proměnné jsou v mapě `$overrides` tohoto souboru.

## Převod existující instalace

1. Zastavte původní cron, webcron a místní AI běhy. Zabraňte i přístupu k původnímu `cron.php`.
2. Zálohujte databázi, původní aplikaci, konfiguraci a adresář `data/`. Pro PostgreSQL můžete použít `docker/backup.sh`.
3. Připravte novou aplikaci v samostatném adresáři a nainstalujte závislosti pomocí `composer install --no-dev --prefer-dist --optimize-autoloader`.
4. Připojte původní databázi a provozní data. SQLite soubor kopírujte při zastavených zápisech nebo přes SQLite backup/VACUUM INTO, nikoli kopírováním živého souboru bez WAL.
5. Konfiguraci lze převzít jako `config.php` v kořeni nebo pomocí `HLIDAC_CONFIG=/cesta/config.php`. Když chcete převzít i její DSN, **nenastavujte `DB_CONNECTION`**. Explicitní Laravel `DB_CONNECTION` má přednost. `APP_URL`, AI a další hodnoty v prostředí mají přednost před hodnotami ze starého souboru.
6. Nastavte `APP_ENV=production`, `APP_DEBUG=false` a `APP_KEY`. Provozní data lze nasměrovat přes `HLIDAC_DATA_PATH`; výchozí je `data/` v projektu.
7. Spusťte `php artisan migrate --force`. Migrace převezme existující tabulky, doplní chybějící historické sloupce a indexy. Zachovává AI rozbory, upozornění, `pn_seen`, `post_log` a `meta`. Nevytváří nové klíče zdrojových záznamů.
8. Nastavte DocumentRoot na `public/`. Uživateli webu povolte zápis do `data/`, `storage/` a `bootstrap/cache/`.
9. Spusťte `php artisan config:cache`, `php artisan route:cache` a `php artisan view:cache`. Ověřte homepage, detail známého tisku, RSS, `/og?ct=221`, `/up` a Python MCP nad stejnou databází.
10. Obnovte právě jeden plánovač synchronizace a místní AI úlohu. Nejprve ponechte `notify.dry_run=true` a ověřte výsledky. Potom vraťte původní nastavení publikování.

Tabulky webu a jejich sloupce zůstávají kompatibilní s Pythonem, včetně JSON uloženého jako text.
Schémata `steno` a `texty` migrace Laravelu nemění. Před nasazením vyzkoušejte tento postup na kopii databáze.
Migrace schématu se neprovádí při veřejném HTTP požadavku.

Základní migrace je záměrně nevratná, protože může převzít tabulky vytvořené původní aplikací.
Pro návrat zastavte nové procesy, vraťte původní aplikaci, její konfiguraci a případně databázovou zálohu.
Nepoužívejte `migrate:fresh` ani `migrate:refresh` na provozní databázi, smazaly by i sdílené tabulky.

## Docker s PostgreSQL a MCP

Docker zachovává názvy služeb a volumes `appdata` a `pgdata`.
Při aktualizaci spusťte Compose ve stejném projektu se stejným názvem projektu jako dosud.
Nepoužívejte `down -v`, odstranilo by to data.

```bash
cp docker/.env.example docker/.env
cp config.example.php docker/config.php
```

Do `docker/.env` nastavte `DB_PASSWORD` a `APP_KEY`, který získáte pomocí `php artisan key:generate --show`.
Do `docker/config.php` nastavte veřejnou URL, tokeny, AI, sítě a připojení:

```php
'db_dsn' => 'pgsql:host=db;port=5432;dbname=hlidac',
'db_user' => 'hlidac',
'db_pass' => getenv('DB_PASSWORD'),
```

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

Web před spuštěním Apache provede migrace. Scheduler počká na zdravý web a spouští synchronizaci každou hodinu.
První synchronizaci lze provést ručně:

```bash
docker compose -f docker/docker-compose.yml exec -u www-data web php artisan hlidac:sync
```

Python služby `rag-ingest` a `mcp` dál používají stejnou PostgreSQL databázi.
MCP tokeny spravujte podle `rag/README.md`. Databáze nemá veřejný port.
Log aplikace jde v Dockeru na stderr, sledujte ho pomocí `docker compose logs web sync` v adresáři `docker/`.
OG obrázky a stažené zdroje zůstávají ve volume `appdata`.

## Cron a webcron

Na běžném serveru spouštějte Laravel scheduler každou minutu:

```cron
* * * * * cd /cesta/k/projektu && php artisan schedule:run >> /dev/null 2>&1
```

Uvnitř scheduleru je synchronizace naplánovaná jednou za hodinu. Docker používá `schedule:work`, další cron není potřeba.
Pro okamžitý běh slouží `php artisan hlidac:sync`. Původní `php bin/sync.php` funguje jako kompatibilní vstup.
Pořadí importu, upozornění, skórování, textů, AI a publikování zůstalo zachováno.
Sdílený souborový zámek v adresáři dat chrání i souběh webcronu a CLI.

Pro hosting bez cronu zůstává `GET /cron.php?token=...`. Podporuje i hlavičku `X-Hlidac-Token`,
která nevkládá token do přístupového logu. Nastavte dlouhý náhodný `HLIDAC_CRON_TOKEN`.

## Vzdálený AI rozbor

Počítač s přihlášeným Claude CLI potřebuje také PHP 8.3+ a `composer install`.
Místní databáze v režimu `--remote` nemusí obsahovat tabulky, ale aplikace potřebuje zapisovatelný adresář dat.

```bash
HLIDAC_TOKEN=... php artisan hlidac:ai --remote=https://web.cz --all
HLIDAC_TOKEN=... php artisan hlidac:ai --remote=https://web.cz --verify --all
php artisan hlidac:ai --resanitize
php artisan hlidac:texts --reanalyze
```

Původní `bin/ai.php` a `bin/texts.php` přijímají stejné argumenty a předávají je Artisanu.
`deploy/ai-remote.sh` a plánování přes launchd mohou zůstat. Na webu nastavte `ai.enabled=true`
a vlastní AI token. API zachovává `/ai_api.php?action=queue|result|done` a hlavičku `X-Hlidac-Token`.
Ověření citací, verze promptů a druhé kolo rozboru zůstávají stejné.

## Sdílený hosting a FTP

Hosting musí umět PHP 8.3 a DocumentRoot v `public/`, případně umožnit uložit zbytek aplikace mimo veřejný adresář.
Kořenové `.htaccess` aplikace přístup blokuje. Pouhé nahrání všech souborů do veřejného kořene původním postupem nestačí.
Composer lze spustit lokálně a nahrát také `vendor/`. Před uploadem nevytvářejte konfigurační cache s místními cestami.
Pro balík slouží `bin/package.sh`, který vynechává `.env`, provozní konfiguraci, cache a logy.
Po nahrání je nutné provést migrace přes SSH nebo nástroje hostingu. Veřejný endpoint pro migrace aplikace neposkytuje.

## Ověření

```bash
composer test
php artisan view:cache
php artisan route:cache
```

`tests/run.php` ověřuje import nad skutečnými řádky PSP, opakované synchronizace, skóre, AI pojistky a stránky.
PHPUnit ověřuje HTTP rozhraní, tokeny, RSS, OG obrázky, převzetí staré databáze a příkazy.
Pro kontrolu PostgreSQL použijte samostatnou prázdnou testovací databázi:

```bash
HLIDAC_TEST_DSN='pgsql:host=127.0.0.1;dbname=hlidac_test' HLIDAC_TEST_USER=postgres php tests/run.php
```

Testy nikdy nesměrujte na provozní databázi, import přepisuje tabulky zdrojových dat.

Po převzetí upstream oprav spusťte `php artisan migrate --force`. Nová migrace doplní lhůtu výborů,
volební období upozornění, datum kontroly textu a uložený výsledek prvního kola AI.
Na MySQL rozšíří text dokumentu a AI JSON na MEDIUMTEXT. Před migrací zálohujte databázi.

Neúspěšné stažení textů se opakuje podle `pn_text.retry_hours` a `tisk_text.retry_hours`.
`tisk_text.refresh_days` a `refresh_per_run` řídí kontrolu změněného dokumentu na psp.cz.
`notify.hold_hours` určuje, jak dlouho upozornění pod prahem čeká na rozbor.
Pravidlovou analýzu již uložených textů obnovíte příkazem `php artisan hlidac:texts --reanalyze`
nebo kompatibilním `php bin/reanalyze.php`.

Migrace `2026_11_19_120000_add_bill_votes` doplňuje tabulky `hlasovani` a `hlasovani_kluby`
a vazbu `kroky.id_hlasovani`. Před spuštěním nové synchronizace proveďte `php artisan migrate --force`.
Existující hlasování z původní aplikace zůstanou zachována.

Migrace `2026_11_19_140000_add_text_and_ai_revisions` zavádí historii textů a rozborů a archiv minulých
období. Spusťte ji před novou synchronizací nebo AI příkazy. První synchronizace doplní dosavadní
texty a rozbory do historie; pracovní data a fronta upozornění se tím nemění. Archiv a historie
zvyšují objem databáze a musí být zahrnuté do záloh.

## Hlášení chyb do Sentry

PHP používá `sentry.dsn` a `sentry.environment` v konfiguraci nebo `SENTRY_DSN` a `SENTRY_ENVIRONMENT` v prostředí. Prázdné DSN hlášení vypíná. Laravel výjimky hlásí přes svůj handler; chyby pomocných importů a publikování se hlásí i při pokračování synchronizace. Odesílač filtruje citlivá data, deduplikuje chyby a omezuje počet událostí. `docker/redeploy.sh` nastaví `SENTRY_RELEASE` podle nasazeného commitu. Python ingest a MCP používají samostatné `SENTRY_DSN_RAG`, viz `rag/README.md`.
