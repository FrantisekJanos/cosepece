<?php
// Společný start aplikace: konfigurace, autoload tříd, databáze a pomocné funkce.

declare(strict_types=1);

define('APP_ROOT', dirname(__DIR__));

date_default_timezone_set('Europe/Prague');
mb_internal_encoding('UTF-8');

spl_autoload_register(function (string $class): void {
    // třídy jsou v namespace Hlidac\..., soubory v app/
    $prefix = 'Hlidac\\';
    if (strncmp($class, $prefix, strlen($prefix)) !== 0) {
        return;
    }
    $relative = str_replace('\\', '/', substr($class, strlen($prefix)));
    $file = APP_ROOT . '/app/' . $relative . '.php';
    if (is_file($file)) {
        require $file;
    }
});

function loadConfig(): array
{
    // config.php má přednost, jinak se použije vzor (vhodné pro první spuštění a testy)
    $path = getenv('HLIDAC_CONFIG') ?: APP_ROOT . '/config.php';
    if (!is_file($path)) {
        $path = APP_ROOT . '/config.example.php';
    }
    $config = require $path;
    if (!is_array($config)) {
        throw new RuntimeException('Konfigurace musí vracet pole.');
    }
    return $config;
}

function config(?string $key = null, mixed $default = null): mixed
{
    static $config = null;
    if ($config === null) {
        $config = loadConfig();
    }
    if ($key === null) {
        return $config;
    }
    // podpora tečkové notace, např. config('notify.bluesky.enabled')
    $value = $config;
    foreach (explode('.', $key) as $part) {
        if (!is_array($value) || !array_key_exists($part, $value)) {
            return $default;
        }
        $value = $value[$part];
    }
    return $value;
}

function db(): Hlidac\Db
{
    static $db = null;
    if ($db === null) {
        $db = new Hlidac\Db(config('db_dsn'), config('db_user'), config('db_pass'));
        $db->migrate();
    }
    return $db;
}

function e(?string $value): string
{
    // escapování výstupu do HTML
    return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function formatDate(?string $value, bool $withTime = false): string
{
    // převod 'Y-m-d H:i:s' na český zápis '13. 11. 2026'
    if ($value === null || $value === '') {
        return '—';
    }
    $ts = strtotime($value);
    if ($ts === false) {
        return $value;
    }
    $out = date('j. n. Y', $ts);
    if ($withTime && date('H:i', $ts) !== '00:00') {
        $out .= ', ' . date('H:i', $ts);
    }
    return $out;
}

function siteUrl(string $path = ''): string
{
    return rtrim((string)config('site_url', ''), '/') . '/' . ltrim($path, '/');
}

// limit dotazů z jedné IP na všechny veřejné stránky (přísnější limity mají api.php, rss.php a og.php);
// cron.php a ai_api.php chrání token a fronta AI posílá výsledky v rychlém sledu
if (PHP_SAPI !== 'cli' && !in_array(basename((string)($_SERVER['SCRIPT_NAME'] ?? '')), ['cron.php', 'ai_api.php'], true)) {
    \Hlidac\RateLimit::guard('web', 240, false);
}
