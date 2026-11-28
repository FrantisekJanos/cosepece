<?php
// Pokyny pro vyhledávače, na adrese /robots.txt (pravidlo v .htaccess) i /robots.php.
// Web je celý veřejný, nic se nezakazuje; soubor hlavně říká, kde je mapa webu (/sitemap.xml).
// Text skládá Hlidac\Sitemap::robots(). Nesahá do databáze, proto nemá omezení počtu požadavků:
// odpověď 429 na robots.txt by vyhledávač bral jako chybu serveru a přestal by web procházet.

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\Sitemap;
use Hlidac\Url;

// v CLI testu už výstup běží, hlavičku pak nelze poslat
if (!headers_sent()) {
    header('Content-Type: text/plain; charset=utf-8');
    header('Cache-Control: public, max-age=3600');
}
// bez mod_rewrite (pretty_urls = false) adresa /sitemap.xml neexistuje, mapa je jen na /sitemap.php
$hasSite = trim((string)config('site_url', '')) !== '';
echo Sitemap::robots($hasSite ? siteUrl(Url::pretty() ? 'sitemap.xml' : 'sitemap.php') : null);
