<?php
// Mapa webu pro vyhledávače (/sitemap.xml, sitemap.php): hlavní stránky, všechny tisky aktuálního období
// a zákony, které tisky nebo připravované návrhy mění. Datum poslední změny je den posledního dění u tisku
// (krok v projednávání nebo nový pozměňovací návrh), aby vyhledávač věděl, co má projít znovu.

declare(strict_types=1);

namespace Hlidac;

final class Sitemap
{
    // stránky z tabulky Url::PAGES, které do mapy patří (hledání a revize jsou výsledky dotazu, ne obsah)
    private const PAGES = ['index', 'tisky', 'zakon', 'upozorneni', 'aktualizace', 'pripravovane', 'metodika', 'data', 'mcp', 'zmeny'];
    // stránky, které se mění s každou synchronizací
    private const LIVE = ['index', 'tisky', 'zakon', 'upozorneni', 'aktualizace', 'pripravovane'];

    /**
     * Položky mapy: absolutní adresa a den poslední změny (YYYY-MM-DD) nebo null, když ho neznáme.
     *
     * @return list<array{loc: string, lastmod: ?string}>
     */
    public static function entries(Db $db): array
    {
        $day = static fn(?string $value): ?string => preg_match('~^\d{4}-\d{2}-\d{2}~', (string)$value, $m) ? $m[0] : null;
        $synced = $day($db->getMeta('last_sync'));
        $out = [];
        foreach (self::PAGES as $page) {
            $out[] = ['loc' => absUrl($page), 'lastmod' => in_array($page, self::LIVE, true) ? $synced : null];
        }
        // tisky a u každého zákona nejnovější dění některého z tisků, které ho mění
        $lawDay = [];
        foreach ($db->all('SELECT ct, posledni, pn_last, zakony FROM tisky ORDER BY ct') as $t) {
            $changed = max((string)$day((string)$t['posledni']), (string)$day((string)$t['pn_last'])) ?: null;
            $out[] = ['loc' => absUrl('tisk', ['ct' => (int)$t['ct']]), 'lastmod' => $changed];
            foreach (array_filter(array_map('trim', explode(',', (string)$t['zakony']))) as $law) {
                $lawDay[$law] = max($lawDay[$law] ?? '', (string)$changed);
            }
        }
        foreach (array_keys((new Laws($db))->overview()) as $law) {
            $law = (string)$law;
            if (Laws::valid($law)) {
                $out[] = ['loc' => absUrl('zakon', ['z' => $law]), 'lastmod' => ($lawDay[$law] ?? '') !== '' ? $lawDay[$law] : null];
            }
        }
        return $out;
    }

    /** Mapa webu jako XML podle protokolu sitemaps.org. */
    public static function xml(array $entries): string
    {
        $xml = '<?xml version="1.0" encoding="UTF-8"?>' . "\n" . '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' . "\n";
        foreach ($entries as $entry) {
            $xml .= '  <url><loc>' . htmlspecialchars((string)$entry['loc'], ENT_XML1 | ENT_QUOTES, 'UTF-8') . '</loc>'
                . ($entry['lastmod'] !== null ? '<lastmod>' . $entry['lastmod'] . '</lastmod>' : '') . "</url>\n";
        }
        return $xml . "</urlset>\n";
    }

    /**
     * Text souboru robots.txt: procházet se smí vše a řádek Sitemap ukazuje na mapu webu.
     * Řádek Sitemap musí mít celou adresu s https://; bez ní (nenastavené site_url) se vynechá.
     */
    public static function robots(?string $sitemapUrl): string
    {
        $text = "User-agent: *\nAllow: /\n";
        // adresa na jednom řádku, jinak by šlo do souboru vložit další pokyn
        if ($sitemapUrl !== null && preg_match('~^https?://[^\s]+$~D', $sitemapUrl)) {
            $text .= "\nSitemap: " . $sitemapUrl . "\n";
        }
        return $text;
    }
}
