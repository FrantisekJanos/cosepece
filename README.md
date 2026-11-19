# Hlídač zákonů

Web, který sleduje legislativní proces v Poslanecké sněmovně a upozorňuje na postupy obcházející běžné projednávání:

- **pozměňovací návrhy** – kdo je podal, ke kterému zákonu, kdy (včetně „návalů“ desítek návrhů najednou),
- **poslanecké a senátní návrhy zákonů** – neprocházejí meziresortním připomínkovým řízením,
- **schválení už v 1. čtení** podle § 90 odst. 2 jednacího řádu,
- **zkrácené lhůty**,
- schválení Sněmovnou a vyhlášení ve Sbírce zákonů.

Data se berou z [otevřených dat Poslanecké sněmovny](https://www.psp.cz/sqw/hp.sqw?k=1300). Upozornění jdou na web, do RSS a na sociální sítě (Bluesky, Mastodon, Telegram, X).

Podněty a chyby: <https://github.com/FrantisekJanos/cosepece/issues>

## Požadavky

- PHP 8.1+ s rozšířeními `pdo_sqlite` (nebo `pdo_mysql`), `zip`, `curl`, `iconv`, `mbstring`
- Apache s `.htaccess` (běžný sdílený hosting)
- hosting musí umět stahovat z `https://www.psp.cz` (odchozí HTTPS)
- cron (stačí i externí webcron)

## Instalace přes FTP

Podrobný postup včetně naplánování AI rozboru je v [NASAZENI.md](NASAZENI.md).

1. Nahrajte celý obsah repozitáře do kořene webu (nebo podadresáře).
2. Zkopírujte `config.example.php` jako `config.php` a nastavte:
   - `site_url` – veřejnou adresu webu,
   - `cron_token` – dlouhý náhodný řetězec,
   - `watch_laws` – sledované zákony (např. `'159/2006'`),
   - databázi: výchozí je SQLite v `data/`, pro MySQL změňte `db_dsn`, `db_user`, `db_pass`.
3. Adresář `data/` musí být zapisovatelný pro PHP (práva 775 nebo 777 podle hostingu).
4. Spusťte první synchronizaci v prohlížeči: `https://vas-web.cz/cron.php?token=VAS_TOKEN`
   - první běh načte celé období a historii jen uloží – na sítě nic nepošle,
   - stahuje celé balíky otevřených dat, takže může trvat i desítky sekund.
5. Nastavte pravidelné spouštění, např. každou hodinu:
   - cron na hostingu: `php /cesta/k/webu/bin/sync.php`
   - nebo webcron (např. cron-job.org): `https://vas-web.cz/cron.php?token=VAS_TOKEN`

## Sociální sítě

V `config.php` je u každé sítě `enabled`. Dokud je `notify.dry_run = true`, nic se neposílá a texty příspěvků se zapisují do `data/posts.log` – vhodné pro vyzkoušení.

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
úprava. Váhy jdou přenastavit v `config.php → scoring`.

## AI rozbor přes předplatné Claude

Texty návrhů zákonů a pozměňovacích návrhů může posoudit Claude (levný model Haiku) přes `claude` CLI
a vaše předplatné Claude Code – bez API klíče. Sdílený hosting CLI spustit neumí, proto běží rozbor na vašem počítači:

```bash
HLIDAC_TOKEN=VAS_TOKEN php bin/ai.php --remote=https://vas-web.cz --limit=20
```

Skript stáhne frontu textů z `ai_api.php`, každý posoudí přes `claude -p` a výsledky pošle zpět. Server ověří,
že citace v rozboru jsou doslova v textu návrhu, a přepočítá skóre. V `config.php` na hostingu musí být
`ai.enabled = true`; token je `ai.remote_token`, nebo `cron_token`, pokud je prázdný. Místo předplatného lze
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

## Strojový výstup (JSON) a RSS

`api.php` vrací data v JSON (jen čtení, `Access-Control-Allow-Origin: *`, cache 5 min):

| Adresa | Co vrátí |
|---|---|
| `api.php` | tisky aktuálního období od poslední změny: číslo, název, fáze, skóre, úroveň, důvody, příznaky, odkazy |
| `api.php?tisk=221` | detail tisku + hlasování (i podle klubů) + upozornění + stav rozboru textů |
| `api.php?upozorneni=1` | upozornění (bez `min_score` od úrovně „Pozor“) |

Filtry: `since=YYYY-MM-DD` (poslední změna / datum upozornění), `min_score=0–100`, `zakon=159/2006`,
`ct=221` (u upozornění), `limit` (nejvýš 500). Chybný filtr = HTTP 400, neznámý tisk = 404.

`rss.php` má stejné filtry, např. `rss.php?zakon=159/2006`, `rss.php?min_score=60`, `rss.php?ct=221&vse=1`.

## Struktura

```
index.php, tisky.php, tisk.php, upozorneni.php, rss.php   veřejné stránky
cron.php                                                   webcron (chráněno tokenem)
bin/sync.php                                               synchronizace z příkazové řádky
app/Source.php      stažení a čtení ZIP/UNL z psp.cz
app/Importer.php    import tisků, historie, PN a poslanců
app/Analyzer.php    příznaky a fáze projednávání
app/AlertBuilder.php tvorba upozornění (bez duplicit)
app/Publisher.php   rozeslání na sítě
app/Notifier/       Bluesky, Mastodon, Telegram, X
data/               databáze, cache, logy (není veřejné)
```

## Testy

```
php tests/run.php
```

Test sestaví ZIPy z reálných řádků otevřených dat (tisky 90, 311, 221, 78), spustí tři synchronizace a vyrenderuje všechny stránky.
