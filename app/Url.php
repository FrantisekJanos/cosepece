<?php
// Adresy stránek webu bez přípony .php: /tisk/221, /tisk/221/aktualizace, /zakon/159/2006, /tisky, /metodika…
// Jedna tabulka pro všechny směry: skládání odkazů (path, to), rozpoznání příchozí adresy (route; na Apachi
// dělá totéž .htaccess, při místním náhledu bin/router.php) a přesměrování starých adres (redirectTarget).
// Strojové adresy (api.php, rss.php, og.php, ai_api.php, reels_api.php, cron.php) zůstávají beze změny,
// aby se nerozbily čtečky RSS a cizí nástroje. Mapa webu je na /sitemap.xml (sitemap.php),
// pokyny pro vyhledávače na /robots.txt (robots.php).

declare(strict_types=1);

namespace Hlidac;

final class Url
{
    /**
     * Stránky s hezkou adresou: jméno stránky (skript bez .php) => cesta od kořene webu.
     * Dokumentace dat nemůže být na /data: v kořeni je adresář data/ a .htaccess ho zakazuje.
     */
    public const PAGES = [
        'index' => '',
        'tisky' => 'tisky',
        'tisk' => 'tisk',
        'tisk-aktualizace' => 'tisk-aktualizace',
        'zakon' => 'zakony',
        'upozorneni' => 'upozorneni',
        'aktualizace' => 'aktualizace',
        'pripravovane' => 'pripravovane',
        'metodika' => 'metodika',
        'data' => 'data-a-api',
        'mcp' => 'mcp',
        'zmeny' => 'zmeny',
        'hledat' => 'hledat',
        'revize' => 'revize',
        'hlaseni' => 'hlaseni',
    ];

    /** null = podle config.php → pretty_urls (výchozí zapnuto); testy si hodnotu přepínají */
    public static ?bool $pretty = null;

    /** Hezké adresy potřebují mod_rewrite; na hostingu bez něj se vypnou (pretty_urls = false) a web používá adresy s .php. */
    public static function pretty(): bool
    {
        return self::$pretty ?? (bool)config('pretty_urls', true);
    }

    /**
     * Adresa stránky od kořene webu bez úvodního lomítka: path('tisk', ['ct' => 221], 'pn-5') = 'tisk/221#pn-5'.
     * Číslo tisku a číslo zákona jdou do cesty, ostatní parametry zůstávají v dotazu. Neznámé jméno
     * (rss, api, og…) je skript s příponou: path('rss', ['ct' => 221]) = 'rss.php?ct=221'.
     */
    public static function path(string $page, array $query = [], string $fragment = ''): string
    {
        if ($page === 'index') {
            $path = '';
        } elseif (!self::pretty() || !isset(self::PAGES[$page])) {
            $path = $page . '.php';
        } else {
            $path = self::PAGES[$page];
            $ct = $query['ct'] ?? null;
            $law = $query['z'] ?? null;
            if (in_array($page, ['tisk', 'tisk-aktualizace'], true) && (is_int($ct) || is_string($ct)) && preg_match('~^\d{1,9}$~', (string)$ct) && (int)$ct > 0) {
                // časová osa změn tisku je pod adresou tisku: tisk/221/aktualizace
                $path = 'tisk/' . (int)$ct . ($page === 'tisk' ? '' : '/aktualizace');
                unset($query['ct']);
            } elseif ($page === 'zakon' && is_string($law) && Laws::valid($law)) {
                $path = 'zakon/' . $law;
                unset($query['z']);
            }
        }
        $qs = http_build_query($query, '', '&', PHP_QUERY_RFC3986);
        return $path . ($qs !== '' ? '?' . $qs : '') . ($fragment !== '' ? '#' . $fragment : '');
    }

    /**
     * Kořen webu v adrese: '' v kořeni domény, '/hlidac' v podadresáři. Na webu podle umístění skriptu,
     * v příkazové řádce (synchronizace, testy) podle site_url.
     */
    public static function base(): string
    {
        if (PHP_SAPI === 'cli') {
            return rtrim((string)parse_url((string)config('site_url', ''), PHP_URL_PATH), '/');
        }
        $dir = str_replace('\\', '/', dirname((string)($_SERVER['SCRIPT_NAME'] ?? '/')));
        return $dir === '/' || $dir === '.' ? '' : rtrim($dir, '/');
    }

    /**
     * Adresa od kořene domény pro odkazy v HTML: to('tisk', ['ct' => 221]) = '/tisk/221'. Odkazy nesmějí být
     * relativní k aktuální stránce: na /tisk/221 by „tisky“ vedlo na /tisk/tisky.
     */
    public static function to(string $page = 'index', array $query = [], string $fragment = ''): string
    {
        return self::base() . '/' . self::path($page, $query, $fragment);
    }

    /**
     * Opačný směr k path(): cesta z adresy (bez kořene webu a bez dotazu) => [skript, parametry], nebo null.
     * Stejná pravidla má .htaccess (shodu hlídá test); používá to bin/router.php pro místní náhled.
     */
    public static function route(string $path): ?array
    {
        // jedno lomítko na konci se toleruje, kanonickou adresu pak vynutí přesměrování
        if (ltrim($path, '/') === 'sitemap.xml') {
            // mapa webu pro vyhledávače (strojová adresa, bez tolerance lomítka na konci)
            return ['sitemap.php', []];
        }
        if (ltrim($path, '/') === 'robots.txt') {
            // pokyny pro vyhledávače s odkazem na mapu webu (strojová adresa, bez tolerance lomítka na konci)
            return ['robots.php', []];
        }
        $path = preg_replace('~/$~', '', ltrim($path, '/')) ?? '';
        if ($path === '') {
            return ['index.php', []];
        }
        if (preg_match('~^tisk/(\d+)$~', $path, $m)) {
            return ['tisk.php', ['ct' => $m[1]]];
        }
        if (preg_match('~^tisk/(\d+)/aktualizace$~', $path, $m)) {
            return ['tisk-aktualizace.php', ['ct' => $m[1]]];
        }
        if (preg_match('~^zakon/(\d{1,4})/(\d{4})$~', $path, $m)) {
            return ['zakon.php', ['z' => $m[1] . '/' . $m[2]]];
        }
        $page = array_search($path, self::PAGES, true);
        return is_string($page) && $page !== 'index' ? [$page . '.php', []] : null;
    }

    /**
     * Kam přesměrovat požadavek, který nepřišel na kanonickou adresu stránky (stará adresa s .php,
     * lomítko na konci, číslo tisku v dotazu místo v cestě); null = adresa je v pořádku nebo nejde o stránku
     * z tabulky. Kanonická adresa závisí jen na skriptu a parametrech, takže po přesměrování vyjde stejně
     * a smyčka nevznikne.
     */
    public static function redirectTarget(string $script, string $requestUri, array $get, string $base): ?string
    {
        $page = str_ends_with($script, '.php') ? substr($script, 0, -4) : '';
        if ($requestUri === '' || !self::pretty() || !isset(self::PAGES[$page])) {
            return null;
        }
        $requestPath = explode('#', explode('?', $requestUri, 2)[0], 2)[0];
        if ($base !== '' && !str_starts_with($requestPath, $base . '/')) {
            return null;
        }
        $current = ltrim(substr($requestPath, strlen($base)), '/');
        $target = self::path($page, $get);
        return $current === explode('?', $target, 2)[0] ? null : $base . '/' . $target;
    }
}
