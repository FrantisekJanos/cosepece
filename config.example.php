<?php
// Vzor konfigurace. Zkopírujte jako config.php a upravte. config.php se do gitu nenahrává.

return [
    // veřejná adresa webu (bez lomítka na konci) – používá se v odkazech v příspěvcích
    'site_url' => 'https://hlidac.example.cz',

    // databáze: SQLite (výchozí, nic nenastavujete), PostgreSQL nebo MySQL/MariaDB
    'db_dsn' => 'sqlite:' . __DIR__ . '/data/hlidac.sqlite',
    // 'db_dsn' => 'pgsql:host=localhost;dbname=hlidac',
    // 'db_dsn' => 'mysql:host=localhost;dbname=hlidac;charset=utf8mb4',
    'db_user' => null,
    'db_pass' => null,

    // zdroj dat
    'source_url' => 'https://www.psp.cz/eknih/cdrom/opendata/',
    'source_dir' => null, // lokální adresář se ZIPy místo stahování (testy)

    // tajný klíč pro webcron: https://vas-web.cz/cron.php?token=...
    'cron_token' => 'ZMENTE-NA-DLOUHY-NAHODNY-RETEZEC',

    // sledované zákony (číslo/rok) a tisky – dostanou přednost a vlastní upozornění
    'watch_laws' => ['159/2006', '586/1992'],
    'watch_tisky' => [],

    // od kolika PN za jeden běh jde o „nával“
    'surge_threshold' => 10,

    // měření návštěvnosti Umami (bez cookies); prázdné = tracker se nevloží
    'umami' => [
        'src' => '',        // např. https://stat.example.cz/sv.js
        'website_id' => '',
        'domains' => '',    // měřit jen na těchto doménách, např. example.cz
    ],

    // odkaz na veřejné issues (podněty a chyby)
    'issues_url' => 'https://github.com/moodixmarket/hlidac-zakonu-podnety/issues',

    // stahování textů pozměňovacích návrhů (Word) a pravidlová analýza přílepků a rozsahu
    'pn_text' => [
        'enabled' => true,
        'per_run' => 40, // kolik PN zpracovat za jeden běh (první běh dožene historii postupně)
    ],

    // stahování textů samotných návrhů zákonů (Word); zásadní změny bývají přímo v poslaneckých návrzích
    'tisk_text' => [
        'enabled' => true,
        'per_run' => 15, // přednost mají poslanecké a senátní návrhy, § 90/2 a zkrácené lhůty
    ],

    // rozbor textů návrhů zákonů a PN přes Claude (zpětná účinnost, zkrácení lhůt vymáhání, přílepky,
    // prospěch úzké skupiny). Výchozí je levný model Haiku.
    //  - backend 'cli': claude -p přes předplatné Claude Code. Na sdíleném hostingu CLI není – tam spouštějte
    //    z vlastního počítače: php bin/ai.php --remote=https://vas-web.cz --token=REMOTE_TOKEN
    //  - backend 'api': placené Claude API s klíčem (api_key nebo proměnná ANTHROPIC_API_KEY)
    'ai' => [
        'enabled' => false,
        'backend' => 'cli',
        'cli_path' => 'claude', // v cronu bývá potřeba plná cesta, např. /Users/jmeno/.local/bin/claude
        'remote_token' => '',   // token pro ai_api.php; prázdné = cron_token
        'api_key' => '',
        'model' => 'claude-haiku-5-5',
        // druhé kolo: nálezy se závažností ≥ verify_min znovu posoudí silnější model (php bin/ai.php --verify)
        'verify_model' => 'claude-sonnet-5-5',
        'verify_min' => 6,
        'per_run' => 20,       // kolik textů (tisky + PN) posoudit za běh
        'max_chars' => 150000, // delší texty se zkrátí
        'timeout' => 300,      // s na jeden rozbor přes CLI
    ],

    // váhy skóre závažnosti; co neuvedete, vezme se z Hlidac\Scorer::defaults()
    'scoring' => [
        // 'mp_bill' => 15, 'p90_passed' => 25, 'deadline_cut' => 15, 'short_gap' => 15,
        // 'pn_other_law' => 20, 'topic' => 15, 'level_major' => 50, 'level_watch' => 25,
    ],

    'notify' => [
        // true = nic se neposílá, příspěvky se zapisují do data/posts.log
        'dry_run' => true,
        'max_posts_per_run' => 10,
        'hashtags' => '#Sněmovna',

        // na sítě jdou jen upozornění se skóre od tohoto prahu (0–100); ostatní zůstanou na webu
        'min_score' => 50,
        // typy upozornění, které se neposílají nikdy: newbill, p90, cut, surge, passed, law, pn
        'kinds_off' => [],

        // Bluesky: handle a heslo aplikace (Nastavení → Soukromí a zabezpečení → Hesla aplikací)
        'bluesky' => [
            'enabled' => false,
            'service' => 'https://bsky.social',
            'handle' => 'hlidac-zakonu.bsky.social',
            'app_password' => '',
        ],
        // Mastodon: token aplikace s oprávněním write:statuses
        'mastodon' => [
            'enabled' => false,
            'instance' => 'https://mastodon.social',
            'token' => '',
            'visibility' => 'public',
        ],
        // Telegram: bot musí být administrátorem kanálu
        'telegram' => [
            'enabled' => false,
            'bot_token' => '',
            'chat_id' => '@hlidac_zakonu',
        ],
        // X: aplikace s oprávněním Read and write, klíče OAuth 1.0a
        'x' => [
            'enabled' => false,
            'api_key' => '',
            'api_secret' => '',
            'access_token' => '',
            'access_secret' => '',
        ],
    ],
];
