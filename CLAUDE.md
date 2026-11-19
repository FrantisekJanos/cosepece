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

## Nasazení

Při instalaci, aktualizaci, konfiguraci hostingu nebo převodu databáze čti **NASAZENI.md**.
Web běží na Laravelu 13 a PHP 8.3+, DocumentRoot je `public/`. Docker zachovává původní datové volumes.
`config/hlidac.php` obsahuje výchozí nastavení; `.env` předává provozní hodnoty.
Původní konfiguraci lze načíst přes `HLIDAC_CONFIG`, její DSN platí pouze bez explicitního `DB_CONNECTION`.
Změny konfigurace se po `config:cache` projeví až po opětovném vytvoření cache.

## Konvence

- Běžné PHP komentáře `//`, `#` a `/* */` jsou v aplikačním kódu zakázané pravidlem PHPStan `code.comments`. PHPDoc `/** */` pro typové informace je povolený. Názvy funkcí, tříd a proměnných jsou anglicky.
- Texty na webu a v příspěvcích **česky** (cílovka je česká veřejnost).
- Při úpravách vždy dodávej **celé funkce**, ne fragmenty.
- Laravel 13, PHP 8.3+, Composer. `declare(strict_types=1)`.
- `Hlidac\Db` používá PDO ze spojení Laravelu. SQL webu musí fungovat v SQLite, PostgreSQL i MySQL/MariaDB.
- Schéma měň novými migracemi v `database/migrations/`. Tabulky webu přímo čte Python MCP a ingest, zachovej jejich kontrakt.
- Doménová konfigurace se čte přes `hlidacConfig()`, standardní `config()` patří Laravelu.
- HTTP logiku dávej do pojmenovaných akcí controllerů, HTML do Blade šablon a příkazy do `app/Console/Commands/`. `__invoke()` v controllerech je zakázaný pravidlem PHPStan `controller.invokable`; routy uvádějí třídu i název metody.
- Každá veřejná akce controlleru přijímá jako první parametr vlastní `App\Http\DTO\RequestDTO` s validací. Vstupní vlastnosti DTO jsou `readonly` a obsahují typované ověřené hodnoty; registruj ho v `RequestDTOServiceProvider`. Controller čte pouze jeho vlastnosti. I akce bez parametrů má vlastní DTO bez vstupních polí. Pravidlo `controller.requestDto` v `tools/phpstan/Rules/ControllerRequestDTORule.php` kontroluje tuto povinnost přes `composer analyse`.
- Validaci DTO deklaruj pouze pomocí konkrétních atributů `Spatie\LaravelData\Attributes\Validation`. Mapování vstupních názvů patří do `MapInputName`. `config/data.php` používá jen `AttributesRuleInferrer`. PHPStan pravidlo `RequestDTOValidationAttributesRule` hlídá atributy a zakazuje `rules()`, `withValidator()`, ruční validátory, `WithoutValidation` a obecný `Rule`.
- Synchronizace zachovává pořadí kroků a zámek sdílený s webcronem. Historie `pn_seen`, `alerts`, `post_log` a `meta` zabraňuje opakovanému rozesílání.

Změny, které čtenář uvidí (oprava dat, jiné skóre, nová funkce), zapiš nahoru do `app/Services/Pages/ChangesService.php` (datum, nadpis,
popis srozumitelně pro veřejnost, čísla PR, příznak „mění skóre“).

## Příkazy

```bash
composer test                     # regresní scénáře a PHPUnit HTTP/migrační testy
php artisan hlidac:sync           # synchronizace (import → upozornění → texty PN a tisků → AI → skóre → sítě)
php bin/ai.php --ct=221           # AI rozbor přes claude CLI (předplatné) nad místní DB
HLIDAC_TOKEN=… php bin/ai.php --remote=https://web.cz   # fronta z hostingu (ai_api.php, token v hlavičce X-Hlidac-Token)
php bin/ai.php --verify --all     # 2. kolo: nálezy se závažností ≥ ai.verify_min znovu posoudí Sonnet
php bin/ai.php --resanitize       # pojistky (AiReviewer::sanitize) znovu na uložené rozbory, bez modelu
HLIDAC_CONFIG=/cesta/config.php php artisan serve --port=8099   # lokální náhled
```

Po změně logiky spusť `composer test`. Regresní scénáře vypíší `VŠE OK`, PHPUnit musí skončit bez selhání.

## Architektura

```
SyncController / hlidac:sync → Hlidac\Sync::run()
  1. Importer      stáhne tisky.zip, sd.zip, poslanci.zip, přepíše tabulky tisky / kroky / pn (aktuální období)
  1b. Votes      hlasování k tiskům (hl-{rok}ps.zip, schuze.zip): z kroků historie (hist.id_hlasovani) a podle bodu
                schůze + stejného krátkého názvu; souhrn a rozdělení podle klubů (tabulky hlasovani, hlasovani_kluby).
                Do skóre nevstupuje; chyba nezastaví synchronizaci
  1c. GovPrep     příprava návrhu u vlády z otevřených dat ODok, tabulka priprava.
                  Chyba nezastaví synchronizaci; údaje upřesňují popis bodů, jejich výši nemění.
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
                   AiReliabilityService::statistics() počítá shodu modelů pro /metodika#spolehlivost-ai. Po změně promptu zvyš
                   AiReviewer::PROMPT_VERSION – fronta pak postupně znovu posoudí starší rozbory (ai_prompt)
  7. Scorer        skóre tisků 0–100 + důvody (tisky.reasons JSON), skóre upozornění = skóre tisku + bonus typu

  8. Publisher     pošle na sítě jen upozornění se skóre ≥ notify.min_score (výchozí 50); post_log, max 3 pokusy
```

Náhledy pro sítě: každá stránka má `og:`/`twitter:` značky (`View::header`, 4. parametr), obrázek 1200 × 630
kreslí `og.php` → `app/OgCard.php` (GD + FreeType, písmo `public/assets/fonts`, cache `data/cache/og`, bez GD
`public/assets/og-default.png`). Při změně vzhledu zvyš `OgCard::VERSION`.

Soubory a příkazy jsou popsané v README.md. `data/` zůstává mimo veřejný adresář; cestu mění `HLIDAC_DATA_PATH`.
Původní `.php` URL obsluhují Laravel routy, fyzické vstupní soubory v kořeni byly nahrazeny controllery.
`bin/ai.php`, `bin/texts.php` a `bin/sync.php` jsou kompatibilní vstupy do Artisanu.

Tabulky webu: `tisky`, `kroky`, `pn`, `pn_seen`, `pn_text`, `tisk_text`, `alerts`, `post_log`, `meta`, `hlasovani`, `hlasovani_kluby`.
Python vlastní schémata `steno` a `texty`, Laravel migrace je nemění. Podrobnosti při změnách MCP nebo ingestu čti v `rag/README.md`.

Historie verzí: `text_revize` a `ai_revize` uchovávají každou skutečnou změnu textu nebo rozboru,
oddělenou klíčem `(druh, obdobi, ref_id, verze)`. `Revisions::backfill` doplní původní data na začátku
synchronizace. Při změně volebního období se pracovní texty před vyprázdněním zkopírují do
`tisk_text_archiv` a `pn_text_archiv`; historie se zachovává. Nové sloupce pracovních textů doplň
i do archivní migrace a `Revisions::TISK_COLUMNS` / `PN_COLUMNS`.
Web historii čte přes Eloquent modely a `RevisionService`; `/revize/{druh}/{id}` používá `RevisionRequestDTO`.
Rozdíly textů generuje `Hlidac\Diff`, který escapuje obsah před vložením do HTML.

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

Připravované zákony: `GovPrep` ukládá návrhy před předložením Sněmovně do `pripravovane`, verze seskupuje podle názvu a vyřazuje návrhy již ve Sněmovně. Eloquent model `UpcomingBill`, `UpcomingService`, `UpcomingController` s `UpcomingRequestDTO`, stránka `/pripravovane` a JSON `/api?pripravovane=1`. Do skóre nevstupují.
