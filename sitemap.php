<?php
// Mapa webu pro vyhledávače, na adrese /sitemap.xml (pravidlo v .htaccess) i /sitemap.php.
// Obsah skládá Hlidac\Sitemap: hlavní stránky, tisky aktuálního období a zákony.

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\Sitemap;

Hlidac\RateLimit::guard('rss', 30, false);

// v CLI testu už výstup běží, hlavičku pak nelze poslat
if (!headers_sent()) {
    header('Content-Type: application/xml; charset=utf-8');
    header('Cache-Control: public, max-age=3600');
}
echo Sitemap::xml(Sitemap::entries(db()));
