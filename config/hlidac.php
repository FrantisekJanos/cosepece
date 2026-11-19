<?php


$defaults = [

    'site_url' => 'https://hlidac.example.cz',

    'db_dsn' => 'sqlite:' . base_path('data/hlidac.sqlite'),

    'db_user' => null,
    'db_pass' => null,

    'source_url' => 'https://www.psp.cz/eknih/cdrom/opendata/',
    'source_dir' => null,

    'cron_token' => 'ZMENTE-NA-DLOUHY-NAHODNY-RETEZEC',

    'watch_laws' => ['159/2006', '586/1992'],
    'watch_tisky' => [],

    'surge_threshold' => 10,

    'umami' => [
        'src' => '',
        'website_id' => '',
        'domains' => '',
    ],

    'issues_url' => 'https://github.com/moodixmarket/hlidac-zakonu-podnety/issues',

    'pn_text' => [
        'enabled' => true,
        'per_run' => 40,
        'retry_hours' => 24,
    ],

    'tisk_text' => [
        'enabled' => true,
        'per_run' => 15,
        'retry_hours' => 24,
        'refresh_days' => 7,
        'refresh_per_run' => 5,
    ],

    'ai' => [
        'enabled' => false,
        'backend' => 'cli',
        'cli_path' => 'claude',
        'cli_prefix' => [],
        'remote_token' => '',
        'api_key' => '',
        'model' => 'claude-haiku-5-5',

        'verify_model' => 'claude-sonnet-5-5',
        'verify_min' => 6,
        'per_run' => 20,
        'max_chars' => 150000,
        'timeout' => 300,
    ],

    'scoring' => [

    ],

    'notify' => [

        'dry_run' => true,
        'max_posts_per_run' => 10,
        'hold_hours' => 48,

        'post' => 'all',
        'hashtags' => '#Sněmovna',

        'min_score' => 50,

        'kinds_off' => [],

        'bluesky' => [
            'enabled' => false,
            'service' => 'https://bsky.social',
            'handle' => 'hlidac-zakonu.bsky.social',
            'app_password' => '',
        ],

        'mastodon' => [
            'enabled' => false,
            'instance' => 'https://mastodon.social',
            'token' => '',
            'visibility' => 'public',
        ],

        'telegram' => [
            'enabled' => false,
            'bot_token' => '',
            'chat_id' => '@hlidac_zakonu',
        ],

        'x' => [
            'enabled' => false,
            'api_key' => '',
            'api_secret' => '',
            'access_token' => '',
            'access_secret' => '',
        ],
    ],
];

$legacyPath = env('HLIDAC_CONFIG', base_path('config.php'));
$legacy = is_file($legacyPath) ? require $legacyPath : [];
if (!is_array($legacy)) {
    throw new RuntimeException('Konfigurace musí vracet pole.');
}

$merge = function (array $base, array $overrides) use (&$merge): array {
    foreach ($overrides as $key => $value) {
        $base[$key] = is_array($value) && !array_is_list($value) && is_array($base[$key] ?? null)
            ? $merge($base[$key], $value)
            : $value;
    }
    return $base;
};
$settings = $merge($defaults, $legacy);
$settings['data_path'] = env('HLIDAC_DATA_PATH', base_path('data'));
$overrides = [
    'site_url' => 'APP_URL', 'cron_token' => 'HLIDAC_CRON_TOKEN',
    'source_url' => 'HLIDAC_SOURCE_URL', 'source_dir' => 'HLIDAC_SOURCE_DIR',
    'ai.enabled' => 'HLIDAC_AI_ENABLED', 'ai.backend' => 'HLIDAC_AI_BACKEND',
    'ai.cli_path' => 'HLIDAC_AI_CLI_PATH', 'ai.remote_token' => 'HLIDAC_AI_REMOTE_TOKEN',
    'ai.api_key' => 'ANTHROPIC_API_KEY', 'ai.model' => 'HLIDAC_AI_MODEL',
    'ai.verify_model' => 'HLIDAC_AI_VERIFY_MODEL', 'notify.dry_run' => 'HLIDAC_NOTIFY_DRY_RUN',
    'notify.post' => 'HLIDAC_NOTIFY_POST',
    'notify.bluesky.enabled' => 'BLUESKY_ENABLED', 'notify.bluesky.handle' => 'BLUESKY_HANDLE',
    'notify.bluesky.app_password' => 'BLUESKY_APP_PASSWORD',
    'notify.mastodon.enabled' => 'MASTODON_ENABLED', 'notify.mastodon.instance' => 'MASTODON_INSTANCE',
    'notify.mastodon.token' => 'MASTODON_TOKEN', 'notify.telegram.enabled' => 'TELEGRAM_ENABLED',
    'notify.telegram.bot_token' => 'TELEGRAM_BOT_TOKEN', 'notify.telegram.chat_id' => 'TELEGRAM_CHAT_ID',
    'notify.x.enabled' => 'X_ENABLED', 'notify.x.api_key' => 'X_API_KEY',
    'notify.x.api_secret' => 'X_API_SECRET', 'notify.x.access_token' => 'X_ACCESS_TOKEN',
    'notify.x.access_secret' => 'X_ACCESS_SECRET',
];
foreach ($overrides as $key => $variable) {
    if (env($variable) !== null) {
        data_set($settings, $key, env($variable));
    }
}
return $settings;
