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
        // struktura databáze se kontroluje jen po změně schématu (nová verze), ne při každém požadavku
        $db->migrateIfNeeded();
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

/**
 * Adresa stránky od kořene domény pro odkazy v HTML: url('tisk', ['ct' => 221], 'pn-5') = '/tisk/221#pn-5'.
 * Jména stránek a pravidla jsou v app/Url.php; jiné jméno je skript s příponou: url('rss') = '/rss.php'.
 */
function url(string $page = 'index', array $query = [], string $fragment = ''): string
{
    return Hlidac\Url::to($page, $query, $fragment);
}

/** Totéž jako url(), ale celá adresa se site_url (příspěvky na sítě, RSS, API, náhledy pro sdílení). */
function absUrl(string $page = 'index', array $query = [], string $fragment = ''): string
{
    return siteUrl(Hlidac\Url::path($page, $query, $fragment));
}

/** Adresa souboru webu (styl, skript, ikona) od kořene domény: asset('assets/style.css') = '/assets/style.css'. */
function asset(string $file): string
{
    return Hlidac\Url::base() . '/' . ltrim($file, '/');
}

/**
 * Parametry z adresy v bezpečné podobě: vždy jednoduchý text (pole ?q[]=x by v kódu dělala chyby a varování)
 * a žádné číslo mimo rozsah sloupce INTEGER, které by skončilo chybou databáze (500); tak velké číslo tisku
 * ani stránky není. Porovnává se hodnota, kterou z textu udělá přetypování na číslo, tedy i zápisy „1e12“,
 * „+99999999999“ nebo „1e12abc“ (PHP bere číselný začátek). Hledaný výraz (q) je volný text a nemění se.
 */
function normalizeQuery(array $query): array
{
    foreach ($query as $key => $value) {
        if (!is_string($value)) {
            unset($query[$key]);
        } elseif ($key !== 'q' && abs((float)$value) > 999999999) {
            $query[$key] = '999999999';
        }
    }
    return $query;
}

// hlášení chyb do Sentry (prázdný klíč sentry.dsn = vypnuto, nic se neodesílá)
\Hlidac\Sentry::init((array)config('sentry', []), PHP_SAPI === 'cli' ? 'sync' : 'web');

if (PHP_SAPI !== 'cli') {
    // chyby jen do logu, nikdy do stránky (vypsaná cesta a kód pomáhají útočníkovi)
    ini_set('display_errors', '0');
    ini_set('log_errors', '1');
    // jen čtení: GET a HEAD; POST přijímá jen formulář tokenů (mcp.php) a rozhraní AI (ai_api.php),
    // PUT (nahrání videa) a POST jen rozhraní workeru Reels (reels_api.php)
    $script = basename((string)($_SERVER['SCRIPT_NAME'] ?? ''));
    $method = strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET'));
    $allowed = match (true) {
        $script === 'reels_api.php' => ['GET', 'HEAD', 'POST', 'PUT'],
        in_array($script, ['mcp.php', 'ai_api.php'], true) => ['GET', 'HEAD', 'POST'],
        default => ['GET', 'HEAD'],
    };
    if (!in_array($method, $allowed, true)) {
        http_response_code($method === 'OPTIONS' ? 204 : 405);
        header('Allow: ' . implode(', ', $allowed));
        exit;
    }
    $_GET = normalizeQuery($_GET);
    // staré adresy stránek s .php (i lomítko na konci nebo číslo tisku v dotazu) přesměrujeme na hezké;
    // jen při čtení a jen u stránek z tabulky Url::PAGES, strojové adresy (api.php, rss.php…) se nemění
    if (in_array($method, ['GET', 'HEAD'], true)) {
        $canonical = \Hlidac\Url::redirectTarget($script, (string)($_SERVER['REQUEST_URI'] ?? ''), $_GET, \Hlidac\Url::base());
        if ($canonical !== null) {
            header('Location: ' . $canonical, true, 301);
            exit;
        }
    }
}

// limit dotazů z jedné IP na všechny veřejné stránky (přísnější limity mají api.php, rss.php a og.php);
// cron.php, ai_api.php a reels_api.php chrání token a fronta AI posílá výsledky v rychlém sledu
if (PHP_SAPI !== 'cli' && !in_array(basename((string)($_SERVER['SCRIPT_NAME'] ?? '')), ['cron.php', 'ai_api.php', 'reels_api.php'], true)) {
    \Hlidac\RateLimit::guard('web', 240, false);
}
