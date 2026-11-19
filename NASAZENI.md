# Nasazení Hlídače zákonů

Návod pro toho, kdo web nasazuje. Celý projekt je v ZIPu; nic se nekompiluje ani neinstaluje přes Composer.

**ZIP obsahuje i hotovou databázi** `data/hlidac.sqlite` (SQLite): všechny tisky a pozměňovací návrhy
10. volebního období, stažené texty zákonů a PN a hotový AI rozbor (519 textů, nejzávažnější ověřené
silnějším modelem), stav ke dni balení. Nic z toho se nemusí stahovat ani posuzovat znovu – synchronizace
a AI jen doženou to, co přibylo od data balení. Databáze neobsahuje žádné tokeny ani hesla.

## Jak to celé běží

Systém má dvě části na dvou místech:

| Kde | Co tam běží | Jak často |
|---|---|---|
| **Webhosting** (PHP) | web, databáze, stahování dat z psp.cz, skóre, RSS, příspěvky na sítě | cron každou hodinu (`cron.php` nebo `bin/sync.php`) |
| **Počítač s Claude Code** (Mac, případně Linux) | AI rozbor textů zákonů a pozměňovacích návrhů přes předplatné Claude | naplánovaná úloha každou hodinu (`deploy/ai-remote.sh`) |

Proč dvě místa: AI rozbor používá program `claude` (Claude Code) přihlášený k předplatnému Claude.
Ten na sdíleném hostingu spustit nejde. Počítač s Claude Code si proto každou hodinu stáhne z webu
texty čekající na rozbor (`ai_api.php`), posoudí je a výsledky pošle zpět. Web sám ověří, že citace
v rozboru jsou doslova v textu zákona, a přepočítá skóre.

```
hosting:  cron ─► cron.php ─► psp.cz ─► databáze ─► web / RSS / sítě
                                           ▲ │
                         výsledky (POST)   │ ▼ fronta textů (GET)
počítač:  launchd/cron ─► deploy/ai-remote.sh ─► claude -p (Haiku, pak Sonnet na nejzávažnější)
```

Web funguje i bez AI části (skóre z postupu a pravidel), jen bez srozumitelných titulků a rozborů textu.

---

## Část 1: Webhosting

### Požadavky

- PHP 8.1 nebo novější s rozšířeními `pdo_sqlite` (nebo `pdo_mysql`), `zip`, `curl`, `iconv`, `mbstring`
- Apache s povoleným `.htaccess` (běžný sdílený hosting)
- odchozí HTTPS na `https://www.psp.cz` (stahují se otevřená data a Word soubory)
- cron na hostingu, nebo externí webcron (např. cron-job.org)
- `memory_limit` aspoň 256 MB a delší běh skriptu (cron.php si sám nastaví 512 MB a 600 s, pokud to hosting dovolí)

### Postup

1. **Nahrajte obsah ZIPu** přes FTP do kořene webu (nebo do podadresáře). Adresář `deploy/` na hostingu
   není potřeba, ale nevadí (je zakázaný přes `.htaccess`).

2. **Vytvořte `config.php`**: zkopírujte `config.example.php` jako `config.php` a nastavte:
   - `site_url` – veřejná adresa webu bez lomítka na konci, např. `https://hlidaczakonu.cz`
   - `cron_token` – dlouhý náhodný řetězec (vygenerujte např. `openssl rand -hex 32`)
   - `ai.enabled` → `true` (bez toho `ai_api.php` odmítne počítač s AI)
   - `ai.remote_token` – další dlouhý náhodný řetězec pro AI část (prázdné = použije se `cron_token`)
   - `notify.dry_run` nechte zatím `true` – nic se neposílá na sítě, příspěvky se jen zapisují do `data/posts.log`
   - databáze: nechte výchozí SQLite (`db_dsn` beze změny) – jen tak se použije přibalená databáze.
     MySQL/MariaDB (`db_dsn`, `db_user`, `db_pass`) je možná, ale začíná prázdná: stažení textů trvá
     desítky hodinových běhů a AI rozbor celého období několik hodin.

3. **Práva**: adresář `data/` i soubor `data/hlidac.sqlite` musí být zapisovatelné pro PHP
   (adresář 775 / soubor 664, na některých hostinzích 777 / 666). SQLite si vedle databáze zakládá
   pomocné soubory, proto musí jít zapisovat i do adresáře. Soubor nahrávejte přes FTP v **binárním** režimu.

4. **Ověřte ochranu souborů** – tyto adresy musí vrátit chybu 403:
   - `https://vas-web.cz/config.php`
   - `https://vas-web.cz/data/hlidac.sqlite` (po prvním běhu)
   - `https://vas-web.cz/app/Db.php`

   Pokud se některý soubor zobrazí nebo stáhne, hosting ignoruje `.htaccess` – **nepokračujte** a řešte
   s hostingem (jinak je veřejně dostupná konfigurace s tokeny).

5. **První synchronizace** – v prohlížeči otevřete `https://vas-web.cz/cron.php?token=VAS_CRON_TOKEN`.
   - Stáhne aktuální balíky dat Sněmovny (desítky sekund až minuty) a doplní, co přibylo od data balení.
     Výsledkem je JSON s počty.
   - Upozornění z přibalené historie jsou označená jako odeslaná, na sítě se nic zpětně nepošle.
     Nové události od data balení vzniknou jako nová upozornění – dokud je `notify.dry_run = true`, jen se zapíšou.
   - Pokud hosting běh utne (chyba 500/504), otevřete adresu znovu – import je opakovatelný.
   - Web je hned plný: přehled, hlavní nálezy s titulky z AI, detaily tisků.

6. **Pravidelné spouštění každou hodinu**, jednou z možností:
   - cron na hostingu (má-li SSH/PHP CLI): `0 * * * * php /cesta/k/webu/bin/sync.php`
   - webcron: `https://vas-web.cz/cron.php?token=VAS_CRON_TOKEN` každou hodinu, timeout co nejdelší

   Každý běh stáhne nová data a k tomu až 40 textů PN a 15 textů zákonů (Word z psp.cz) – s přibalenou
   databází jde jen o dokumenty přibylé od data balení. (Kdo začíná s prázdnou databází a má SSH, může
   historii stáhnout najednou: `php bin/texts.php`, desítky minut.)

7. **Kontrola**: na webu se objeví přehled, tisky a skóre. Datum „Data aktualizována“ v hlavičce
   ukazuje poslední běh synchronizace.

---

## Část 2: AI rozbor na počítači s Claude Code

Ano, je to **naplánovaná úloha (cron)** – jen ne na hostingu, ale na počítači, kde je nainstalovaný
a přihlášený Claude Code. Na Macu se místo cronu používá launchd (cron na macOS nemá přístup ke klíčence,
kde je uložené přihlášení Claude Code).

### Požadavky

- Mac (nebo Linux), který je většinu času zapnutý. Když je vypnutý nebo spí, nic se nerozbije –
  rozbor se dožene při dalším běhu.
- **Claude Code** přihlášený k předplatnému Claude (Pro nebo Max): https://claude.com/claude-code
- **PHP 8.1+** v příkazové řádce. Na Macu: `brew install php`
- **Žádný `ANTHROPIC_API_KEY`** – skript ho záměrně maže, aby se rozbor účtoval z předplatného, ne z API.

### Postup (macOS)

1. **Rozbalte ZIP** do trvalé složky, např. `~/hlidac_zakonu`. Konfigurace `config.php` tu potřeba není
   a přibalená databáze se tu nepoužívá (rozbor pracuje s databází na hostingu).

2. **Přihlaste Claude Code** (jednou):
   ```bash
   claude
   ```
   V Claude Code zadejte `/login`, zvolte přihlášení předplatným a pak `/exit`.
   Ověření, že funguje bez interakce:
   ```bash
   echo 'Odpověz jen: OK' | claude -p --model claude-haiku-5-5
   ```

3. **Nastavení připojení k webu**:
   ```bash
   cd ~/hlidac_zakonu
   cp deploy/ai-remote.env.example deploy/ai-remote.env
   ```
   V `deploy/ai-remote.env` vyplňte `HLIDAC_URL` (adresa webu) a `HLIDAC_TOKEN` (hodnota `ai.remote_token`
   z `config.php` na hostingu). Soubor obsahuje tajný token – nikam ho nenahrávejte.

4. **Ruční zkušební běh**:
   ```bash
   bash deploy/ai-remote.sh
   tail -n 30 data/ai-remote.log
   ```
   V logu uvidíte řádky `OK tisk 221 (tisk 221): závažnost 8/10`. Chyba `Neplatný token` = špatný token,
   `AI rozbor je vypnutý` = na hostingu chybí `ai.enabled = true`.

   S přibalenou databází posoudí jen texty přibylé od data balení (obvykle minuty; pokud žádné nejsou,
   vypíše `{"reviewed":0,"errors":0}` – to je v pořádku). S prázdnou databází (MySQL) by první běh trval
   ~3 hodiny; Mac pak nesmí usnout: `caffeinate -i bash deploy/ai-remote.sh`. Skript lze přerušit (Ctrl+C),
   další běh naváže.

5. **Naplánování každou hodinu (launchd)**:
   ```bash
   cd ~/hlidac_zakonu
   sed "s#CESTA_K_PROJEKTU#$PWD#g" deploy/cz.hlidac.ai.plist > ~/Library/LaunchAgents/cz.hlidac.ai.plist
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/cz.hlidac.ai.plist
   ```
   Úloha se spustí hned a pak každou hodinu (po probuzení Macu dožene prošvihnutý termín).
   Souběžné běhy hlídá zámek, takže dlouhý první běh se nepřekrývá s dalším.

   Kontrola a správa:
   ```bash
   launchctl print gui/$(id -u)/cz.hlidac.ai | grep -E "state|last exit"   # stav úlohy
   tail -f ~/hlidac_zakonu/data/ai-remote.log                               # průběh
   launchctl kickstart gui/$(id -u)/cz.hlidac.ai                             # spustit hned
   launchctl bootout gui/$(id -u)/cz.hlidac.ai                               # vypnout
   ```

### Postup (Linux)

Body 1–4 stejně (Claude Code přihlášený pod uživatelem, pod kterým poběží cron), pak `crontab -e`:

```
0 * * * * /bin/bash /home/UZIVATEL/hlidac_zakonu/deploy/ai-remote.sh
```

### Co dělá jeden běh

1. `php bin/ai.php --remote=… --all` – texty bez rozboru posoudí levný model **Haiku** (`claude -p`, bez nástrojů,
   bez přístupu k souborům; text jde do modelu jen jako vstup).
2. `php bin/ai.php --remote=… --all --verify` – nálezy se závažností ≥ 6 znovu posoudí silnější model **Sonnet**;
   jeho výsledek nahradí první rozbor (na webu „ověřeno druhým modelem“).
3. Po každé dávce web přepočítá skóre.

Limit předplatného: při běžném provozu (desítky nových textů denně) se nepozná. Když se limit při prvním
dohánění vyčerpá, rozbory skončí chybou a doženou se v dalších hodinách.

---

## Část 3: Sociální sítě (až bude web odladěný)

V `config.php` na hostingu vyplňte klíče sítí (`notify.bluesky`, `mastodon`, `telegram`, `x`), u každé
`enabled => true` a nakonec `notify.dry_run => false`. Posílají se jen upozornění se skóre ≥ `notify.min_score`
(výchozí 50), nejvýš `max_posts_per_run` za běh. Před přepnutím projděte `data/posts.log` (přes FTP),
co by se posílalo. Klíče patří jen do `config.php` na hostingu, nikdy do gitu ani do ZIPu.

---

## Aktualizace

Nahrajte nové soubory přes FTP **kromě** `config.php` a adresáře `data/` – při aktualizaci databázi z balíku
nikdy nepřepisujte, na hostingu je novější než v ZIPu. Nové sloupce databáze se
doplní samy při prvním běhu. Na počítači s AI stačí přepsat soubory ve složce (kromě `deploy/ai-remote.env`
a `data/`).

## Řešení problémů

| Příznak | Příčina / řešení |
|---|---|
| `cron.php` vrací `Neplatný token` | `cron_token` v adrese neodpovídá `config.php` |
| `cron.php` končí 500/504 | hosting utne dlouhý běh; spusťte znovu, případně snižte `pn_text.per_run` a `tisk_text.per_run` |
| na webu „Data zatím nebyla načtena“ | první synchronizace neproběhla; viz Část 1, bod 5 |
| `data/` nelze zapisovat | práva adresáře `data/` (775/777) |
| AI: `claude CLI nenalezeno` | `claude` není v `~/.local/bin`, `/opt/homebrew/bin` ani `/usr/local/bin`; doplňte cestu do PATH ve skriptu |
| AI: `Not logged in` / chyba přihlášení | znovu `claude` → `/login` pod stejným uživatelem, pod kterým běží launchd |
| AI: `AI rozbor je vypnutý` | na hostingu `ai.enabled => true` |
| AI: nic se neděje | `launchctl print gui/$(id -u)/cz.hlidac.ai`, log `data/ai-launchd.log` a `data/ai-remote.log` |
| na webu chybí titulky z AI | AI část ještě neproběhla, nebo tisk nemá text ve Wordu na psp.cz |

## Bezpečnost

- `config.php` a `deploy/ai-remote.env` obsahují tajné tokeny – nesdílet, neposílat e-mailem, nedávat do gitu.
- `ai_api.php` přijímá jen požadavky s tokenem; výsledky AI web ukládá jen tehdy, když citace sedí na text zákona.
- Při podezření na únik tokenu ho změňte v `config.php` (a v `deploy/ai-remote.env`).
- Na hosting nahrávejte **ZIP z `bin/package.sh`**, ne celý pracovní adresář z gitu: balík neobsahuje `.git`,
  testy, `docker/.env`, `deploy/ai-remote.env` ani místní logy.
- Ochrana `config.php` a `data/` stojí na `.htaccess` (bod 4 výše). Pokud to hosting umožňuje, dejte citlivé
  soubory **mimo veřejný kořen webu**: konfiguraci přes proměnnou prostředí, např. v `.htaccess`
  `SetEnv HLIDAC_CONFIG /home/ucet/private/config.php`, a databázi přes `db_dsn`, např.
  `'sqlite:/home/ucet/private/hlidac.sqlite'`. V `data/` pak zůstane jen cache a zámek synchronizace.
- Zápis do `data/` dejte jen účtu, pod kterým běží PHP (775/664), 777/666 jen tam, kde to hosting jinak nedovolí.
