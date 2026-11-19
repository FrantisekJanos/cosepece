# Hlídač zákonů

Web, který sleduje legislativní proces v Poslanecké sněmovně a upozorňuje na postupy obcházející běžné projednávání:

- **pozměňovací návrhy** – kdo je podal, ke kterému zákonu, kdy (včetně „návalů“ desítek návrhů najednou),
- **poslanecké a senátní návrhy zákonů** – neprocházejí meziresortním připomínkovým řízením,
- **schválení už v 1. čtení** podle § 90 odst. 2 jednacího řádu,
- **zkrácené lhůty**,
- schválení Sněmovnou a vyhlášení ve Sbírce zákonů.

Data se berou z [otevřených dat Poslanecké sněmovny](https://www.psp.cz/sqw/hp.sqw?k=1300). Upozornění jdou na web, do RSS a na sociální sítě (Bluesky, Mastodon, Telegram, X).

Podněty a chyby: <https://github.com/moodixmarket/hlidac-zakonu-podnety/issues>

## Požadavky a spuštění

Web běží na Laravelu 13, PHP 8.3+ a Composeru 2. Vyžaduje rozšíření `pdo_sqlite`, `pdo_pgsql` nebo `pdo_mysql`,
`zip`, `curl`, `iconv`, `mbstring`; pro vlastní OG obrázky také GD s FreeType.
Veřejný adresář webserveru musí směřovat do `public/`.

```bash
composer install
cp .env.example .env
php artisan key:generate
mkdir -p data
touch data/hlidac.sqlite
php artisan migrate
php artisan serve
```

Nastavte `APP_URL`, databázi, tokeny a AI v `.env`. Sledované zákony, váhy a další výchozí nastavení jsou
v `config/hlidac.php`. Původní `config.php` lze převzít pomocí `HLIDAC_CONFIG`; jeho DSN se použije,
pokud není explicitně nastavena `DB_CONNECTION`.

Synchronizaci spustíte pomocí `php artisan hlidac:sync`. Pro pravidelný provoz použijte Laravel scheduler.
Docker Compose obsahuje web, scheduler, PostgreSQL a samostatné Python služby pro ingest a MCP.
Podrobný postup, převod existujících dat, FTP a AI plánování jsou v [NASAZENI.md](NASAZENI.md).
Při přechodu se zachovávají adresy stránek, RSS, AI API i databázové tabulky čtené Pythonem.

## Sociální sítě

V `config/hlidac.php` je u každé sítě `enabled`, provozní hodnoty lze nastavit v `.env`. Dokud je `notify.dry_run = true`, nic se neposílá a texty příspěvků se zapisují do `data/posts.log` – vhodné pro vyzkoušení.

| Síť | Co potřebujete |
|---|---|
| Bluesky | handle účtu a **heslo aplikace** (Nastavení → Soukromí a zabezpečení → Hesla aplikací) |
| Mastodon | instance a přístupový token aplikace s oprávněním `write:statuses` (Předvolby → Vývoj → Nová aplikace) |
| Telegram | bot od @BotFather, přidaný do kanálu jako administrátor; `chat_id` = `@nazev_kanalu` |
| X | vývojářský účet, aplikace s oprávněním Read and write, 4 klíče OAuth 1.0a |

Co se posílá, určuje `notify.post`: `all` = vše, `watch` = jen sledované zákony, `off` = nic. Počet příspěvků za jeden běh omezuje `max_posts_per_run`; zbytek se pošle při dalším běhu. Neúspěšné odeslání se opakuje nejvýš třikrát.

## Skóre závažnosti – co vytáhnout a co potlačit

Každý tisk dostane skóre 0–100 složené z procedurálních signálů (poslanecký návrh bez připomínkového řízení,
schválení v 1. čtení, zkrácené lhůty, krátký čas mezi pozměňovacími návrhy a 3. čtením, návrhy podané na poslední
chvíli, přílepky a rozsáhlé změny v pozměňovacích návrzích, citlivé oblasti jako střet zájmů nebo dotace).
Každý bod má na webu vypsaný důvod. Skóre hodnotí **postup**, ne politický obsah.

- **50+ Závažné** – „Hlavní nálezy“ na úvodní stránce, příspěvky na sociální sítě,
- **25–49 Pozor** – v upozorněních a RSS,
- **pod 25 Běžné** – potlačeno, dostupné přes „Včetně běžné agendy“.

Texty pozměňovacích návrhů se stahují ve Wordu a hledají se v nich přílepky (návrh mění jiný zákon než projednávaný).
Volitelně je posoudí Claude (`ai` v konfiguraci): zpětná účinnost, zkrácení lhůt, prospěch úzké skupiny, zásadní nová
úprava. Váhy jdou přenastavit v `config/hlidac.php → scoring`.

## AI rozbor přes předplatné Claude

Texty návrhů zákonů a pozměňovacích návrhů může posoudit Claude (levný model Haiku) přes `claude` CLI
a vaše předplatné Claude Code – bez API klíče. Sdílený hosting CLI spustit neumí, proto běží rozbor na vašem počítači:

```bash
HLIDAC_TOKEN=VAS_TOKEN php bin/ai.php --remote=https://vas-web.cz --limit=20
```

Skript stáhne frontu textů z `ai_api.php`, každý posoudí přes `claude -p` a výsledky pošle zpět. Server ověří,
že citace v rozboru jsou doslova v textu návrhu, a přepočítá skóre. Na hostingu musí být `HLIDAC_AI_ENABLED=true`; token je `ai.remote_token`, nebo `cron_token`, pokud je prázdný. Místo předplatného lze
použít placené API (`ai.backend = 'api'` a `api_key`).

Levný model posoudí všechny texty, nejzávažnější nálezy (závažnost ≥ `ai.verify_min`, výchozí 6) pak znovu posoudí
silnější model `ai.verify_model` (Sonnet) – i ten přes předplatné:

```bash
php bin/ai.php --verify --all                                       # místní databáze
HLIDAC_TOKEN=VAS_TOKEN php bin/ai.php --remote=https://vas-web.cz --verify   # hosting
```

Výsledek ověření nahradí první rozbor; na homepage je u nálezu poznámka „ověřeno druhým modelem“. Automatické
korekce (např. snížení závažnosti u zpětnosti ve prospěch veřejnosti) jsou vypsané v detailu tisku.

## Jak se počítají příznaky

| Příznak | Zdroj |
|---|---|
| Poslanecký / senátní návrh | druh tisku 2 a začátek úplného názvu |
| Navrženo § 90/2 | v historii tisku je stav „1. čtení (§ 90, odst. 2)“ |
| Schváleno v 1. čtení | § 90/2 a tisk odešel ze Sněmovny bez 2. a 3. čtení |
| Zkrácená lhůta | akce „změna lhůty“, „změna lhůty pozm. návrhu“ nebo „vráceno výborům, změna lhůty“ |
| Nával PN | při jednom běhu přibylo k tisku aspoň `surge_threshold` pozměňovacích návrhů |

Omezení: otevřená data obsahují u pozměňovacího návrhu jen číslo, autora a čas. Obsah je v PDF na psp.cz a Sněmovna neeviduje, které návrhy byly platně podány ve 2. čtení.

## Struktura

```text
routes/web.php               veřejné stránky, RSS, OG, webcron a AI API
routes/console.php           hodinový plán synchronizace
app/Http/Controllers/        převod HTTP vstupů a odpovědi
app/Http/DTO/                neměnné typované RequestDTO a validace všech HTTP akcí
app/Services/               příprava dat stránek, AI operace, RSS a OG
app/Models/                 Eloquent modely všech devíti tabulek webu
app/View/Composers/          napojení společné hlavičky na službu
app/Console/Commands/        hlidac:sync, hlidac:ai, hlidac:texts
resources/views/             Blade šablony a společné části
public/assets/               CSS, písma a záložní OG obrázek
config/hlidac.php            doménová konfigurace a mapování proměnných prostředí
database/migrations/         schéma webu a převzetí původní databáze
app/Source.php               stažení a čtení ZIP/UNL z psp.cz
app/Importer.php             import tisků, historie, PN a poslanců
app/Analyzer.php             příznaky a fáze projednávání
app/AlertBuilder.php         tvorba upozornění bez duplicit
app/Publisher.php            rozeslání na sítě
app/Notifier/                Bluesky, Mastodon, Telegram, X
bin/                         kompatibilní CLI vstupy a převod SQLite → PostgreSQL/MySQL
rag/                         Python ingest a MCP nad společnou PostgreSQL databází
data/                        databáze, zdrojová cache a log příspěvků, mimo public/
```

## Testy

```
composer test
```

Test sestaví ZIPy z reálných řádků otevřených dat (tisky 90, 311, 221, 78), spustí tři synchronizace a vyrenderuje všechny stránky.

PHPUnit navíc ověřuje HTTP endpointy, převzetí původní databáze, AI API, RSS a obrázky pro sdílení.

Modely webu: `Bill` → `tisky`, `BillStep` → `kroky`, `Amendment` → `pn`,
`SeenAmendment` → `pn_seen`, `BillText` → `tisk_text`, `AmendmentText` → `pn_text`,
`Alert` → `alerts`, `PostLog` → `post_log`, `Meta` → `meta`.
Používají původní primární klíče, bez automatického číslování a timestampů. JSON a data mají Eloquent casty;
čísla zákonů oddělená čárkami zůstávají řetězci. `PostLog` nemá primární klíč a dovoluje přidávání a čtení,
nikoli ukládání či mazání jednotlivého existujícího záznamu. Dávkový import a skórování zatím zachovávají původní SQL.
Stránkové služby dotazují přímo Eloquent modely a jejich scopes. `BillPresentationService` připravuje příznaky a AI nálezy,
`ReviewStatusService` stav posouzení textů. Původní společný repozitář byl odstraněn.

Každá akce controlleru přijímá vlastní `RequestDTO`. Kontejner DTO vytvoří z požadavku a ověří ho před
spuštěním služby. Vstupní vlastnosti DTO jsou `readonly`; controllery je používají místo čtení query
parametrů. Neplatné vstupy stránek vracejí JSON s chybami a stavem 422. AI API zachovává stavy 400/413
pro chybný vstup a dosavadní 403/409 z middleware. Metodika bez parametrů má DTO bez vstupních polí.

Architekturu controllerů kontroluje vlastní PHPStan pravidlo `controller.requestDto` v
`tools/phpstan/Rules/ControllerRequestDTORule.php`. Spouští se přes `composer analyse` a v CI.
Každá veřejná metoda v `App\Http\Controllers` musí jako první povinný parametr deklarovat konkrétního
potomka `RequestDTO`. Výjimkou jsou konstruktor a destruktor. Privátní a chráněné helpery se nekontrolují.
Nullable, union, variadický či pouze PHPDoc typ podmínku nesplňuje. `phpstan.neon` záměrně zapíná pouze
tuto kontrolu architektury, nikoli kompletní typovou analýzu celé aplikace. Pravidlo testuje `RuleTestCase`;
běžné HTTP testy dál ověřují validaci DTO.

Validaci RequestDTO zajišťuje Spatie Laravel Data. Základ `RequestDTO` rozšiřuje `Spatie\LaravelData\Data`;
pravidla se deklarují výhradně PHP atributy, například `#[Required, IntegerType, Min(1)]`.
`MapInputName` zachovává původní query názvy `ct`, `q`, `vse` apod. V `config/data.php` je aktivní pouze
`AttributesRuleInferrer`, takže pravidla nejsou skrytě přidávána podle PHP typů.
`RequestDTOValidationAttributesRule` přes PHPStan vyžaduje Spatie validační atributy na veřejných vstupních
vlastnostech a zakazuje vlastní validační hooky, ruční validátory, `WithoutValidation`, obecný atribut `Rule`
a vlastní validační atributy mimo Spatie. Kontroluje i hooky přidané přes trait. Akce bez vstupních polí jsou povolené.
HTTP výběr AI akce a limit velikosti těla řeší `ValidateAiTransport`, schéma payloadu je v atributech DTO.

Controller má pojmenované akce a routy používají `[Controller::class, 'method']`. PHPStan pravidlo
`controller.invokable` zakazuje `__invoke()` v controllerech, včetně podadresářů.

Pravidlo PHPStan `code.comments` zakazuje běžné PHP komentáře `//`, `#` a `/* */`, PHPDoc `/** */` ponechává.
Kontroluje aplikační PHP v `app`, `routes`, `config`, vstupy `bootstrap` a `public`, `database`, `bin` a zdroj vlastních pravidel v `tools/phpstan`.
Používá PHP tokenizaci; URL, řetězce, heredoc, HTML a textové prompty nejsou komentáře.
