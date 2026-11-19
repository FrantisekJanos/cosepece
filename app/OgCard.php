<?php
// Obrázek pro sdílení na sociálních sítích (og:image, 1200 × 630 px): značka webu, o co jde,
// u tisku skóre a hlavní důvody. Kreslí se přes GD s písmem Noto Sans z assets/fonts,
// hotové PNG se ukládá do data/cache/og (klíč = obsah karty, změna skóre = nový soubor).

declare(strict_types=1);

namespace Hlidac;

final class OgCard
{
    public const WIDTH = 1200;
    public const HEIGHT = 630;
    // zvýšit při změně vzhledu, aby se nepoužily staré obrázky z cache
    public const VERSION = 1;

    private const PAD = 72;
    private const COLORS = [
        'ground' => '#F6F6F3', 'ink' => '#15171C', 'ink2' => '#3C4049', 'muted' => '#5B5F68',
        'bar_sub' => '#B8BCC6', 'line' => '#E3E3DE', 'accent' => '#1D4ED8', 'white' => '#FFFFFF',
        'major' => '#B91C1C', 'watch' => '#B45309', 'watch_bg' => '#FEF3E2', 'watch_ink' => '#9A3412',
        'normal' => '#9CA3AF', 'normal_bg' => '#ECECE7',
    ];

    public static function available(): bool
    {
        // GD s FreeType je potřeba pro text; bez něj se použije statický obrázek assets/og-default.png
        return function_exists('imagettftext') && function_exists('imagepng')
            && is_file(self::font('bold')) && is_file(self::font('regular'));
    }

    public static function forBill(array $t, array $config): array
    {
        // karta tisku: kdo a v jaké fázi, celý název, proč takové skóre
        $score = (int)$t['score'];
        $level = Scorer::level($score, $config);
        $reasons = View::reasonShortLabels($t['reasons_list'] ?? [], 4);
        $why = $reasons !== []
            ? 'Proč: ' . implode(', ', $reasons) . '.'
            : 'Běžné projednávání bez procedurálních příznaků.';
        $eyebrow = ['Tisk ' . (int)$t['ct']];
        if (trim((string)$t['typ_navrhu']) !== '') {
            $eyebrow[] = trim((string)$t['typ_navrhu']) . ' návrh';
        }
        if (trim((string)$t['faze']) !== '') {
            $eyebrow[] = trim((string)$t['faze']);
        }
        return [
            'eyebrow' => implode(' · ', $eyebrow),
            'title' => self::cleanTitle((string)($t['uplny_nazev'] ?: $t['nazev'])),
            'text' => $why,
            'score' => $score,
            'level' => $level,
            'domain' => self::domain($config),
        ];
    }

    public static function forPage(string $page, array $config, int $obdobi = 0): array
    {
        // karty běžných stránek: o čem web je a co na stránce najdu
        $period = $obdobi > 0 ? $obdobi . '. volební období' : 'aktuální volební období';
        $pages = [
            'prehled' => [
                'Co se peče ve Sněmovně',
                'Zrychlená projednávání, přílepky v pozměňovacích návrzích a zkrácené lhůty v Poslanecké sněmovně. Každý den z otevřených dat psp.cz.',
            ],
            'tisky' => [
                'Návrhy zákonů seřazené podle závažnosti postupu',
                'Všechny tisky ' . $period . ' se skóre 0–100 a čitelným důvodem každého bodu.',
            ],
            'upozorneni' => [
                'Upozornění na sporné kroky ve Sněmovně',
                'Nové pozměňovací návrhy, schválení podle § 90/2, zkrácené lhůty a vyhlášené zákony. Běžná agenda je potlačená.',
            ],
            'metodika' => [
                'Jak hodnotíme legislativní postup',
                'Signály, body a stropy skóre závažnosti. Hodnotíme postup a formu, nikdy politický obsah nebo stranu.',
            ],
        ];
        [$title, $text] = $pages[$page] ?? $pages['prehled'];
        return [
            'eyebrow' => 'Poslanecká sněmovna · ' . $period,
            'title' => $title,
            'text' => $text,
            'text_lines' => 3,
            'score' => null,
            'level' => null,
            'domain' => self::domain($config),
        ];
    }

    public static function cacheKey(array $card): string
    {
        return substr(sha1(self::VERSION . '|' . json_encode($card, JSON_UNESCAPED_UNICODE)), 0, 16);
    }

    public static function cached(array $card, string $dir): string
    {
        // vrátí cestu k PNG; vykreslí ho jen poprvé
        $path = rtrim($dir, '/') . '/' . self::cacheKey($card) . '.png';
        if (!is_file($path)) {
            if (!is_dir($dir)) {
                @mkdir($dir, 0775, true);
            }
            $tmp = $path . '.' . getmypid() . '.tmp';
            file_put_contents($tmp, self::render($card));
            rename($tmp, $path);
        }
        return $path;
    }

    public static function render(array $card): string
    {
        $w = self::WIDTH;
        $h = self::HEIGHT;
        $im = imagecreatetruecolor($w, $h);
        imagealphablending($im, true);
        imageantialias($im, true);
        $c = [];
        foreach (self::COLORS as $name => $hex) {
            $c[$name] = self::color($im, $hex);
        }
        $bold = self::font('bold');
        $regular = self::font('regular');
        $pad = self::PAD;

        imagefilledrectangle($im, 0, 0, $w, $h, $c['ground']);

        // tmavý pruh se značkou jako na webu
        $barH = 100;
        imagefilledrectangle($im, 0, 0, $w, $barH, $c['ink']);
        self::drawLogo($im, $pad, 30, 40, $c['white']);
        imagettftext($im, 30, 0, $pad + 58, 64, $c['white'], $bold, 'Hlídač zákonů');
        $right = 'Hlídáme postup, ne politiku';
        $rw = self::textWidth($right, 20, $regular);
        imagettftext($im, 20, 0, $w - $pad - $rw, 60, $c['bar_sub'], $regular, $right);

        // barevný pruh vlevo podle úrovně skóre (u stránek modrý)
        $levelColor = match ($card['level']) {
            Scorer::LEVEL_MAJOR => $c['major'],
            Scorer::LEVEL_WATCH => $c['watch'],
            Scorer::LEVEL_NORMAL => $c['normal'],
            default => $c['accent'],
        };
        imagefilledrectangle($im, 0, $barH, 14, $h, $levelColor);

        $maxW = $w - 2 * $pad;
        $y = $barH + 64;

        // nadtitulek: tisk, typ návrhu, fáze
        $eyebrow = mb_strtoupper((string)$card['eyebrow']);
        $eyebrow = self::fit($eyebrow, 20, $bold, $maxW, 1)[0];
        imagettftext($im, 20, 0, $pad, $y, $c['accent'], $bold, $eyebrow);

        // titulek: co nejvíc čitelný, zmenšuje se podle délky, max. 3–4 řádky
        $footerTop = $h - 112;
        $text = trim((string)$card['text']);
        $textSize = 26;
        $textLines = $text !== '' ? self::fit($text, $textSize, $regular, $maxW, (int)($card['text_lines'] ?? 2)) : [];
        $textH = count($textLines) * (int)round($textSize * 1.45);
        $titleTop = $y + 26;
        $titleRoom = $footerTop - 28 - ($textH > 0 ? $textH + 22 : 0) - $titleTop;
        $title = (string)$card['title'];
        $lines = [];
        $size = 56;
        foreach ([56, 50, 44, 40, 36] as $size) {
            $lineH = (int)round($size * 1.2);
            $maxLines = max(1, intdiv($titleRoom, $lineH));
            $lines = self::fit($title, $size, $bold, $maxW, $maxLines, $complete);
            if ($complete) {
                break;
            }
        }
        $lineH = (int)round($size * 1.2);
        $ty = $titleTop;
        foreach ($lines as $line) {
            $ty += $lineH;
            imagettftext($im, $size, 0, $pad, $ty - (int)round($size * 0.22), $c['ink'], $bold, $line);
        }

        // popis nebo důvody skóre
        $ty += 22;
        foreach ($textLines as $line) {
            $ty += (int)round($textSize * 1.45);
            imagettftext($im, $textSize, 0, $pad, $ty - 8, $c['ink2'], $regular, $line);
        }

        // spodní řádek: skóre (u tisku) a adresa webu
        imageline($im, $pad, $footerTop, $w - $pad, $footerTop, $c['line']);
        $by = $footerTop + 30;
        if ($card['score'] !== null) {
            $labels = [Scorer::LEVEL_MAJOR => 'Závažné', Scorer::LEVEL_WATCH => 'Pozor', Scorer::LEVEL_NORMAL => 'Běžné'];
            [$bg, $fg] = match ($card['level']) {
                Scorer::LEVEL_MAJOR => [$c['major'], $c['white']],
                Scorer::LEVEL_WATCH => [$c['watch_bg'], $c['watch_ink']],
                default => [$c['normal_bg'], $c['muted']],
            };
            $num = (string)(int)$card['score'];
            $label = $labels[$card['level']] ?? '';
            $numW = self::textWidth($num, 34, $bold);
            $labW = self::textWidth($label, 24, $bold);
            $pillW = 28 + $numW + 14 + $labW + 28;
            self::roundedRect($im, $pad, $by, $pad + $pillW, $by + 60, 14, $bg);
            imagettftext($im, 34, 0, $pad + 28, $by + 45, $fg, $bold, $num);
            imagettftext($im, 24, 0, $pad + 28 + $numW + 14, $by + 41, $fg, $bold, $label);
            imagettftext($im, 22, 0, $pad + $pillW + 20, $by + 40, $c['muted'], $regular, 'skóre závažnosti postupu ze 100');
        } else {
            imagettftext($im, 22, 0, $pad, $by + 40, $c['muted'], $regular, 'Z otevřených dat Poslanecké sněmovny');
        }
        $domain = (string)($card['domain'] ?? '');
        if ($domain !== '') {
            $dw = self::textWidth($domain, 26, $bold);
            imagettftext($im, 26, 0, $w - $pad - $dw, $by + 41, $c['ink'], $bold, $domain);
        }

        ob_start();
        imagepng($im, null, 6);
        return (string)ob_get_clean();
    }

    public static function fit(string $text, int $size, string $font, int $maxW, int $maxLines, ?bool &$complete = null): array
    {
        // zalomí text do řádků dané šířky; když se nevejde, poslední řádek zkrátí a doplní „…“
        $words = preg_split('/\s+/u', trim($text)) ?: [];
        $lines = [];
        $line = '';
        $complete = true;
        foreach ($words as $i => $word) {
            $try = $line === '' ? $word : $line . ' ' . $word;
            if ($line === '' || self::textWidth($try, $size, $font) <= $maxW) {
                $line = $try;
                continue;
            }
            $lines[] = $line;
            $line = $word;
            if (count($lines) === $maxLines) {
                $complete = false;
                $line = '';
                break;
            }
        }
        if ($line !== '') {
            $lines[] = $line;
        }
        if (count($lines) > $maxLines) {
            $complete = false;
            $lines = array_slice($lines, 0, $maxLines);
        }
        if (!$complete && $lines !== []) {
            $last = array_pop($lines);
            while ($last !== '' && self::textWidth($last . ' …', $size, $font) > $maxW) {
                $cut = mb_strrpos($last, ' ');
                $last = $cut === false ? mb_substr($last, 0, -1) : mb_substr($last, 0, $cut);
            }
            $lines[] = rtrim($last, " ,;:–-") . ' …';
        }
        // i jediné dlouhé slovo musí mít strop šířky
        foreach ($lines as $k => $l) {
            while (mb_strlen($l) > 1 && self::textWidth($l, $size, $font) > $maxW) {
                $l = mb_substr($l, 0, -2) . '…';
            }
            $lines[$k] = $l;
        }
        return $lines;
    }

    public static function cleanTitle(string $title): string
    {
        // úplný název bez úvodu „Návrh poslanců X, Y… na vydání zákona“ a opakujících se formulí,
        // ať je z karty hned vidět, co zákon mění (předkladatel je v nadtitulku a na webu)
        $short = preg_replace_callback(
            '/^(?:vládní\s+|senátní\s+)?návrh\b.*?\b(?:na\s+vydání\s+)?(ústavního\s+)?zákona\b/iu',
            fn(array $m) => ($m[1] ?? '') !== '' ? 'Ústavní zákon' : 'Zákon',
            $title,
            1
        );
        if ($short !== null && mb_strlen($short) >= 20) {
            $title = $short;
        }
        $title = preg_replace('/,?\s*ve znění pozdějších předpisů/u', '', $title) ?? $title;
        $title = preg_replace('/\s+/u', ' ', $title) ?? $title;
        return trim($title, " ,");
    }

    private static function domain(array $config): string
    {
        $host = (string)(parse_url((string)($config['site_url'] ?? ''), PHP_URL_HOST) ?? '');
        return preg_replace('/^www\./', '', $host) ?? $host;
    }

    private static function font(string $weight): string
    {
        return APP_ROOT . '/assets/fonts/NotoSans-' . ($weight === 'bold' ? 'Bold' : 'Regular') . '.ttf';
    }

    private static function textWidth(string $text, int $size, string $font): int
    {
        $box = imagettfbbox($size, 0, $font, $text);
        return $box === false ? 0 : (int)abs($box[2] - $box[0]);
    }

    private static function color(\GdImage $im, string $hex): int
    {
        $hex = ltrim($hex, '#');
        return (int)imagecolorallocate($im, hexdec(substr($hex, 0, 2)), hexdec(substr($hex, 2, 2)), hexdec(substr($hex, 4, 2)));
    }

    private static function roundedRect(\GdImage $im, int $x1, int $y1, int $x2, int $y2, int $r, int $color): void
    {
        imagefilledrectangle($im, $x1 + $r, $y1, $x2 - $r, $y2, $color);
        imagefilledrectangle($im, $x1, $y1 + $r, $x2, $y2 - $r, $color);
        foreach ([[$x1 + $r, $y1 + $r], [$x2 - $r, $y1 + $r], [$x1 + $r, $y2 - $r], [$x2 - $r, $y2 - $r]] as [$cx, $cy]) {
            imagefilledellipse($im, $cx, $cy, 2 * $r, 2 * $r, $color);
        }
    }

    private static function drawLogo(\GdImage $im, int $x, int $y, int $s, int $color): void
    {
        // budova se sloupy jako ikona v hlavičce webu (viewBox 24 × 24)
        $k = $s / 24;
        $p = fn(float $v) => (int)round($v * $k);
        imagesetthickness($im, max(2, $p(1.8)));
        imageline($im, $x + $p(3), $y + $p(21), $x + $p(21), $y + $p(21), $color);
        foreach ([5, 9, 15, 19] as $cx) {
            imageline($im, $x + $p($cx), $y + $p(21), $x + $p($cx), $y + $p(10), $color);
        }
        imagepolygon($im, [$x + $p(2), $y + $p(10), $x + $p(12), $y + $p(3), $x + $p(22), $y + $p(10)], $color);
        imagesetthickness($im, 1);
    }
}
