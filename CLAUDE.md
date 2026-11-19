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
php bin/reanalyze.php             # pravidlové signály PnText/TiskText znovu na stažené texty, bez sítě a AI
HLIDAC_CONFIG=/cesta/config.php php -S 127.0.0.1:8099   # lokální náhled
```

Test vypisuje `VŠE OK`; po každé změně logiky ho spusť a případně rozšiř.

## Architektura

```
cron.php / bin/sync.php → Hlidac\Sync::run()
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
  7. Scorer        skóre tisků 0–100 + důvody (tisky.reasons JSON), skóre upozornění = skóre tisku + bonus typu
  1b. Votes      hlasování k tiskům (hl-{rok}ps.zip, schuze.zip): z kroků historie (hist.id_hlasovani) a podle bodu
                schůze + stejného krátkého názvu; souhrn a rozdělení podle klubů (tabulky hlasovani, hlasovani_kluby).
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
jméno „web: datum · účel“), jen při PostgreSQL. Ochrana: podepsaný údaj formuláře (3 s – 1 h), skryté pole,
3 tokeny/IP/den (RateLimit 'mcp_token', okno 86400 s), 200 tokenů/den celkem (`config.php → mcp`).

Upozornění na připravované: `AlertBuilder::fromUpcoming` (ct = 0, klíč `prep_review|prep_plan|prep_vlada:<pid>`,
skóre = `AlertBuilder::UPCOMING_KINDS`: < 10 dní 50, < 21 dní 30, mimo plán 30, schváleno vládou 0); první běh
bootstrap (meta `prep_alerts_bootstrapped`). `View::alertUrl` vede na `pripravovane.php#prep-<pid>`, jinak do VeKLEP.

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
`text_revize`, `ai_revize` (historie verzí), `tisk_text_archiv`, `pn_text_archiv` (texty minulých období).

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
