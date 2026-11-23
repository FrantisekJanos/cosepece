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
    // limity dotazů z jedné IP adresy za minutu (app/RateLimit.php; 0 = bez limitu); popis pro uživatele na data.php#limity
    'rate_limit' => ['web' => 240, 'api' => 60, 'rss' => 30, 'og' => 120],

    // adresa MCP serveru pro AI asistenty (stránka mcp.php); prázdné = https://mcp.<doména webu>/mcp
    'mcp_url' => '',
    // vydávání tokenů na stránce mcp.php (jen s PostgreSQL a službou rag/): zapnuto, limity na IP a den
    // token z webu se zruší, když ho nikdo nepoužije do unused_days dní nebo ho nepoužívá déle než idle_days dní
    'mcp' => ['web_tokens' => true, 'tokens_per_ip' => 3, 'tokens_per_day' => 200, 'unused_days' => 7, 'idle_days' => 90],

    'issues_url' => 'https://github.com/FrantisekJanos/cosepece/issues',

    // stahování textů pozměňovacích návrhů (Word) a pravidlová analýza přílepků a rozsahu
    // které pozměňovací návrhy prošly: stahování souhrnů PN po 2. čtení (párování s hlasováním jen se službou rag/)
    'pn_outcome' => ['enabled' => true, 'per_run' => 10, 'retry_hours' => 24],

    'pn_text' => [
        'enabled' => true,
        'per_run' => 40, // kolik PN zpracovat za jeden běh (první běh dožene historii postupně)
        'retry_hours' => 24, // neúspěšné stažení (síť, chybějící Word) zkusit znovu po tolika hodinách
    ],

    // nové kroky a PN ze stránek historie tisků (historie.sqw), než je přinesou noční otevřená data psp.cz:
    // tisky se změnou v posledních `days` dnech a tisky na pořadu běžící nebo příští schůze
    'history_html' => [
        'enabled' => true,
        'per_run' => 30, // nejvýš tolik stránek za běh synchronizace (hodinu)
        'days' => 14,
        'delay_ms' => 1000, // pauza mezi požadavky na psp.cz
    ],

    // stahování textů samotných návrhů zákonů (Word); zásadní změny bývají přímo v poslaneckých návrzích
    // příprava návrhu u vlády z otevřených dat Úřadu vlády (ODok/VeKLEP); jen zobrazení u tisku
    'odok' => [
        'enabled' => true,
    ],

    'tisk_text' => [
        'enabled' => true,
        'per_run' => 15, // přednost mají poslanecké a senátní návrhy, § 90/2 a zkrácené lhůty
        'retry_hours' => 24,
        'refresh_days' => 7, // jednou za týden ověřit, jestli psp.cz nevyměnilo dokument (pak nové znění a nový rozbor)
        'refresh_per_run' => 5,
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
        // 'cli_prefix' => [], // spouštěč před cli_path, např. [PHP_BINARY] pro skript .php (Windows, testy)
        'remote_token' => '',   // token pro ai_api.php; prázdné = cron_token
        'api_key' => '',
        'model' => 'claude-haiku-5-5',
        // druhé kolo: nálezy se závažností ≥ verify_min znovu posoudí silnější model (php bin/ai.php --verify)
        'verify_model' => 'claude-sonnet-5-5',
        'verify_min' => 6,
        // spolehlivost (#7 bod 7): hraniční hodnocení repeat_min–repeat_max posoudí rychlý model repeat_runs× a platí medián;
        // každý sample_every-tý text s hodnocením pod sample_below namátkou ověří i silnější model
        'repeat_runs' => 3, 'repeat_min' => 3, 'repeat_max' => 7, 'sample_every' => 10, 'sample_below' => 4,
        'per_run' => 20,       // kolik textů (tisky + PN) posoudit za běh
        'max_chars' => 150000, // delší texty se zkrátí
        'timeout' => 300,      // s na jeden rozbor přes CLI
    ],

    // generátor Reels: worker na PC si přes reels_api.php bere úlohy a nahrává videa (token v hlavičce X-Hlidac-Token).
    // Prázdný token nebo ZMENTE… = rozhraní vypnuté (403). inbox_dir = mezisložka pro bota (volume reelsinbox).
    'reels' => [
        'worker_token' => '',
        'inbox_dir' => '/srv/reels-inbox',
    ],

    // váhy skóre závažnosti; co neuvedete, vezme se z Hlidac\Scorer::defaults()
    'scoring' => [
        // 'mp_bill' => 15, 'p90_passed' => 25, 'deadline_cut' => 15, 'short_gap' => 15,
        // 'pn_other_law' => 20, 'topic' => 15, 'level_major' => 50, 'level_watch' => 25,
        // víc problematických PN (#7 bod 6): 'pn_many_min' => 3, 'pn_many_share' => 0.2, 'pn_many_abs' => 10,
        // 'pn_flag_ai_min' => 6 (od této závažnosti AI je PN problematický),
        // body AI u PN: 'ai_pn_full_min' => 6, 'ai_pn_full_count' => 2, 'ai_pn_full_share' => 0.25, 'ai_pn_single_factor' => 0.5,
    ],

    // Sentry: hlášení neošetřených chyb. Prázdné dsn = vypnuto (nic se neodesílá).
    'sentry' => [
        'dsn' => '',
        'environment' => 'production',
    ],

    'notify' => [
        // true = nic se neposílá, příspěvky se zapisují do data/posts.log
        'dry_run' => true,
        'max_posts_per_run' => 10,
        // co se posílá: 'all' = vše nad prahem, 'watch' = jen sledované zákony, 'off' = nic
        // (vypnutá upozornění zůstanou jen na webu a v RSS a po zapnutí se zpětně neposílají)
        'post' => 'all',
        'hashtags' => '#Sněmovna',

        // na sítě jdou jen upozornění se skóre od tohoto prahu (0–100); ostatní zůstanou na webu
        'min_score' => 50,
        // upozornění pod prahem čeká tolik hodin na doběhnutí rozboru textů, pak se znovu posoudí skóre
        'hold_hours' => 48,
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
