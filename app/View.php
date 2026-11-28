<?php
// Společné části HTML: hlavička, patička, štítky.

declare(strict_types=1);

namespace Hlidac;

final class View
{
    /** true = stránka nenačte měřicí skript (odpověď s čerstvě vydaným tokenem nemá číst žádný cizí skript) */
    public static bool $noAnalytics = false;

    public const KIND_LABELS = [
        'newbill' => ['mp', 'Nový návrh'],
        'p90' => ['fast', '§ 90/2'],
        'cut' => ['cut', 'Zkrácená lhůta'],
        'surge' => ['surge', 'Nával PN'],
        'pn' => ['surge', 'Nové PN'],
        'passed' => ['mp', 'Schváleno Sněmovnou'],
        'law' => ['mp', 'Vyhlášeno'],
        'senate' => ['surge', 'Senát'],
        'override' => ['fast', 'Přehlasováno'],
        'prep_velmi' => ['cut', 'Připomínky < 10 dní'],
        'prep_kratke' => ['cut', 'Krátké připomínky'],
        'prep_plan' => ['surge', 'Mimo plán vlády'],
        'prep_vlada' => ['mp', 'Schváleno vládou'],
        'prep_narizeni' => ['cut', 'Nařízení: krátké připomínky'],
    ];

    // krátké názvy důvodů skóre podle klíče (karty, obrázky pro sdílení, dokumentace API)
    public const REASON_SHORT = [
        'mp_bill' => 'poslanecký návrh', 'senate_bill' => 'senátní návrh', 'region_bill' => 'krajský návrh', 'p90_passed' => 'schváleno v 1. čtení',
        'p90_requested' => 'návrh na § 90/2', 'deadline_cut' => 'zkrácená lhůta', 'short_gap' => 'málo času na PN',
        'late_pn' => 'PN na poslední chvíli', 'surge' => 'nával PN', 'pn_other_law' => 'přílepek', 'pn_large' => 'rozsáhlý PN',
        'retro_deadline' => 'lhůta a zpětné použití', 'pn_many' => 'více sporných PN', 'ai_bill' => 'sporný text návrhu',
        'ai' => 'sporné pozměňovací návrhy', 'topic' => 'citlivá oblast', 'omnibus' => 'souhrnná novela', 'watched' => 'sledovaný zákon',
    ];

    // profily projektu na sociálních sítích (ikony vpravo nahoře v hlavičce); config.php → social je může přepsat,
    // prázdná adresa ikonu skryje
    public const SOCIAL = [
        'facebook' => 'https://www.facebook.com/cosepece/',
        'instagram' => 'https://www.instagram.com/cosepece/',
        'x' => 'https://x.com/cosepece',
    ];

    /**
     * Odkazy na profily projektu na sociálních sítích jako ikony (SVG přímo ve stránce, bez cizích skriptů
     * a obrázků, takže sítě o návštěvě nic nevědí, dokud čtenář neklikne). Vrací hotové HTML; bez adres prázdný řetězec.
     */
    public static function socialLinks(): string
    {
        $icons = [
            'facebook' => ['Facebook', '<path fill="currentColor" d="M9.101 23.691v-7.98H6.627v-3.667h2.474v-1.58c0-4.085 1.848-5.978 5.858-5.978.401 0 .955.042 1.468.103a8.68 8.68 0 0 1 1.141.195v3.325a8.623 8.623 0 0 0-.653-.036 26.805 26.805 0 0 0-.733-.009c-.707 0-1.259.096-1.675.309a1.686 1.686 0 0 0-.679.622c-.258.42-.374.995-.374 1.752v1.297h3.919l-.386 2.103-.287 1.564h-3.246v8.245C19.396 23.238 24 18.179 24 12.044c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.628 3.874 10.35 9.101 11.647Z"/>'],
            'instagram' => ['Instagram', '<rect x="2.5" y="2.5" width="19" height="19" rx="5.5" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="12" r="4.3" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="17.4" cy="6.6" r="1.3" fill="currentColor"/>'],
            'x' => ['X', '<path fill="currentColor" d="M18.901 1.153h3.68l-8.04 9.19L24 22.846h-7.406l-5.8-7.584-6.638 7.584H.474l8.6-9.83L0 1.154h7.594l5.243 6.932ZM17.61 20.644h2.039L6.486 3.24H4.298Z"/>'],
        ];
        $links = array_replace(self::SOCIAL, array_intersect_key((array)config('social', []), self::SOCIAL));
        $html = '';
        foreach ($icons as $key => [$name, $svg]) {
            $href = trim((string)($links[$key] ?? ''));
            if (!preg_match('~^https://~', $href)) {
                continue;
            }
            $html .= '<a href="' . e($href) . '" target="_blank" rel="noopener" aria-label="Hlídač zákonů na síti ' . $name . '" title="' . $name . '">'
                . '<svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">' . $svg . '</svg></a>';
        }
        return $html === '' ? '' : '<div class="social">' . $html . '</div>';
    }

    public static function header(string $title, string $active, string $description = '', array $og = []): void
    {
        $repo = new Repo(db());
        $obdobi = $repo->obdobi();
        $sync = $repo->lastSync();
        // hlavní menu: nejdřív obsah (co se ve Sněmovně a u vlády děje), pak stránky o webu a datech
        $nav = [
            'prehled' => ['index', 'Přehled'],
            'tisky' => ['tisky', 'Tisky'],
            'zakony' => ['zakon', 'Zákony'],
            'upozorneni' => ['upozorneni', 'Upozornění'],
            'pripravovane' => ['pripravovane', 'Připravované'],
            'metodika' => ['metodika', 'Jak hodnotíme'],
            'data' => ['data', 'Data a API'],
            'mcp' => ['mcp', 'Pro AI asistenty'],
            'zmeny' => ['zmeny', 'Co jsme změnili'],
        ];
        $desc = $description !== '' ? $description : 'Pozměňovací návrhy, zrychlená projednávání a zkrácené lhůty v Poslanecké sněmovně.';
        // náhled pro sociální sítě: vlastní obrázek ke každé stránce (og.php), absolutní adresy ze site_url;
        // kanonická adresa je hezká adresa stránky (u zákona i s jeho číslem), bez filtrů v dotazu
        $page = basename((string)($_SERVER['SCRIPT_NAME'] ?? 'index.php'), '.php');
        $law = (string)($_GET['z'] ?? '');
        $ogPath = isset(Url::PAGES[$page]) ? Url::path($page, $page === 'zakon' && Laws::valid($law) ? ['z' => $law] : []) : '';
        $ogUrl = siteUrl((string)($og['url'] ?? $ogPath));
        $ogImage = siteUrl((string)($og['image'] ?? 'og.php?p=' . rawurlencode($active)));
        $ogTitle = (string)($og['title'] ?? $title);
        $ogAlt = (string)($og['alt'] ?? $ogTitle . ' – Hlídač zákonů');
        ?>
<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title><?= e($title) ?> – Hlídač zákonů</title>
<meta name="description" content="<?= e($desc) ?>">
<link rel="canonical" href="<?= e($ogUrl) ?>">
<meta property="og:type" content="<?= isset($og['url']) ? 'article' : 'website' ?>">
<meta property="og:site_name" content="Hlídač zákonů">
<meta property="og:locale" content="cs_CZ">
<meta property="og:url" content="<?= e($ogUrl) ?>">
<meta property="og:title" content="<?= e($ogTitle) ?>">
<meta property="og:description" content="<?= e($desc) ?>">
<meta property="og:image" content="<?= e($ogImage) ?>">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="<?= OgCard::WIDTH ?>">
<meta property="og:image:height" content="<?= OgCard::HEIGHT ?>">
<meta property="og:image:alt" content="<?= e($ogAlt) ?>">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="<?= e($ogTitle) ?>">
<meta name="twitter:description" content="<?= e($desc) ?>">
<meta name="twitter:image" content="<?= e($ogImage) ?>">
<meta name="twitter:image:alt" content="<?= e($ogAlt) ?>">
<link rel="icon" href="<?= e(asset('favicon.ico')) ?>" sizes="32x32">
<link rel="icon" href="<?= e(asset('assets/favicon.svg')) ?>" type="image/svg+xml">
<link rel="apple-touch-icon" href="<?= e(asset('assets/apple-touch-icon.png')) ?>">
<link rel="manifest" href="<?= e(asset('site.webmanifest')) ?>">
<meta name="theme-color" content="#15171C">
<link rel="alternate" type="application/rss+xml" title="Hlídač zákonů – upozornění" href="<?= e(url('rss')) ?>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&amp;family=Space+Grotesk:wght@500;600;700&amp;display=swap" rel="stylesheet">
<link rel="stylesheet" href="<?= e(asset('assets/style.css')) ?>?v=<?= (int)@filemtime(APP_ROOT . '/assets/style.css') ?>">
<?php $umami = (array)config('umami', []); if (!self::$noAnalytics && ($umami['src'] ?? '') !== '' && ($umami['website_id'] ?? '') !== ''): ?>
<script defer src="<?= e($umami['src']) ?>" data-website-id="<?= e($umami['website_id']) ?>"<?php if (($umami['domains'] ?? '') !== ''): ?> data-domains="<?= e($umami['domains']) ?>"<?php endif; ?> data-do-not-track="true"></script>
<?php endif; ?>
</head>
<body>
<header class="top">
  <div class="top-in">
    <a class="brand" href="<?= e(url()) ?>">
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 21h18"/><path d="M5 21V10"/><path d="M19 21V10"/><path d="M9 21V10"/><path d="M15 21V10"/><path d="M2 10 12 3l10 7z"/></svg>
      <span class="brand-name">Hlídač zákonů</span>
      <span class="brand-sub">Poslanecká sněmovna · <?= $obdobi ?>. volební období</span>
    </a>
    <?php if (!in_array($active, ['prehled', 'hledat', 'tisky'], true)): ?><?php self::searchForm('', 'top'); ?><?php endif; ?>
    <?= self::socialLinks() ?>
    <div class="top-row2">
      <nav id="site-nav" aria-label="Hlavní menu">
        <?php foreach ($nav as $key => [$page, $label]): ?>
          <a href="<?= e(url($page)) ?>"<?= $key === $active ? ' class="active" aria-current="page"' : '' ?>><?= $label ?></a>
        <?php endforeach; ?>
        <a href="<?= e(url('rss')) ?>">RSS</a>
      </nav>
      <div class="sync"><?= $sync ? 'Data aktualizována ' . e(formatDate($sync, true)) : 'Data zatím nebyla načtena' ?></div>
    </div>
    <?php // na telefonu je menu jeden posuvný řádek: aktuální stránku posuneme do zorného pole (bez JS zůstane menu na začátku) ?>
    <script>(function(){var n=document.getElementById('site-nav'),a=n&&n.querySelector('a.active');if(a&&n.scrollWidth>n.clientWidth){n.scrollLeft=a.offsetLeft-n.offsetLeft-(n.clientWidth-a.offsetWidth)/2;}})();</script>
  </div>
</header>
<main class="wrap">
<?php
    }

    /** Vyhledávací pole s našeptávačem (assets/search.js); bez JavaScriptu vede na stránku výsledků. */
    public static function searchForm(string $q = '', string $variant = 'hero'): void
    {
        static $n = 0;
        $id = 'q' . (++$n);
        ?>
<form class="search search-<?= e($variant) ?>" action="<?= e(url('hledat')) ?>" method="get" role="search" data-search="<?= e(url('hledat', ['index' => SearchIndex::stamp(db())])) ?>" data-base="<?= e(asset('')) ?>">
  <div class="search-box">
    <label for="<?= $id ?>" class="sr-only">Hledat tisky, zákony, pozměňovací návrhy a témata</label>
    <input id="<?= $id ?>" name="q" type="search" value="<?= e($q) ?>" placeholder="Tisk, zákon, téma nebo poslanec" autocomplete="off" spellcheck="false"
      role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="<?= $id ?>-list">
    <ul class="suggest" id="<?= $id ?>-list" role="listbox" aria-label="Návrhy" hidden></ul>
  </div>
  <button type="submit">Hledat</button>
</form>
<?php if ($n === 1): ?><script src="<?= e(asset('assets/search.js')) ?>?v=<?= (int)@filemtime(APP_ROOT . '/assets/search.js') ?>" defer></script><?php endif; ?>
<?php
    }

    /**
     * Cookie lišta a skript pro Google Analytics 4 (assets/consent.js). gtag.js se načte až po souhlasu
     * (Consent Mode v2 basic). Bez platného Measurement ID vrátí prázdný řetězec, takže forky nic neměří.
     */
    public static function ga4Tags(array $ga4): string
    {
        $id = self::ga4Id($ga4);
        if ($id === '') {
            return '';
        }
        $js = asset('assets/consent.js') . '?v=' . (int)@filemtime(APP_ROOT . '/assets/consent.js');
        return '<div id="cookie-bar" class="cookie-bar" role="region" aria-label="Souhlas s cookies" hidden>'
            . '<p>Návštěvnost měříme bez cookies (Umami). Se souhlasem ji měříme i v Google Analytics, které ukládá cookies. '
            . '<a href="' . e(url('metodika', [], 'cookies')) . '">Víc o cookies</a></p>'
            . '<div class="cookie-actions">'
            . '<button type="button" class="btn" data-consent="granted">Přijmout</button>'
            . '<button type="button" class="btn" data-consent="denied">Odmítnout</button>'
            . '</div></div>'
            . "\n" . '<script defer src="' . e($js) . '" data-ga4="' . e($id) . '"></script>' . "\n";
    }

    /** Odkaz do patičky, který znovu otevře cookie lištu; bez JavaScriptu vede na vysvětlení v metodice. */
    public static function cookieSettingsLink(array $ga4): string
    {
        if (self::ga4Id($ga4) === '') {
            return '';
        }
        return '<a href="' . e(url('metodika', [], 'cookies')) . '" data-cookie-settings>Nastavení cookies</a>';
    }

    /** Platné Measurement ID GA4 z konfigurace (G-…), jinak prázdný řetězec. */
    public static function ga4Id(array $ga4): string
    {
        $id = (string)($ga4['measurement_id'] ?? '');
        return preg_match('/^G-[A-Z0-9]+$/', $id) === 1 ? $id : '';
    }

    public static function footer(): void
    {
        $issues = (string)config('issues_url', '');
        $ga4 = self::$noAnalytics ? '' : self::ga4Tags((array)config('ga4', []));
        ?>
<footer class="foot">
  <p>Zdroj: <a href="https://www.psp.cz/sqw/hp.sqw?k=1300">otevřená data Poslanecké sněmovny</a> (tisky, sněmovní dokumenty, poslanci). Počty PN jsou podané písemné pozměňovací návrhy; Sněmovna neeviduje, které z nich byly platně podány ve 2. čtení. Příznaky se počítají automaticky z historie tisku.</p>
  <p class="foot-disclaimer"><strong>Upozornění:</strong> Obsah vzniká automaticky z otevřených dat a část rozborů (shrnutí, titulky a hodnocení závažnosti textů) připravuje umělá inteligence, která se může mýlit. Údaje mají informativní povahu, nejsou právní radou ani oficiálním stanoviskem Sněmovny a před dalším použitím je ověřte v původních zdrojích. <a href="<?= e(url('metodika', [], 'odpovednost')) ?>">Omezení odpovědnosti</a></p>
  <p><a href="<?= e(url('zmeny')) ?>">Co jsme změnili</a> · <a href="<?= e(url('zakon')) ?>">Zákony</a> · <a href="<?= e(url('data')) ?>">Data a API</a> · <a href="<?= e(url('mcp')) ?>">Pro AI asistenty (MCP)</a> · <a href="https://github.com/FrantisekJanos/cosepece">Zdrojový kód (AGPL-3.0)</a><?php if ($ga4 !== ''): ?> · <?= self::cookieSettingsLink((array)config('ga4', [])) ?><?php endif; ?><?php if ($issues !== ''): ?> · <a href="<?= e($issues) ?>">Našli jste chybu nebo máte podnět? Napište nám.</a><?php endif; ?></p>
</footer>
<?= $ga4 ?>
</main>
</body>
</html>
<?php
    }

    public static function scoreBadge(int $score): string
    {
        // barevné skóre: závažné / pozor / běžné
        $level = Scorer::level($score, config());
        $labels = [Scorer::LEVEL_MAJOR => 'Závažné', Scorer::LEVEL_WATCH => 'Pozor', Scorer::LEVEL_NORMAL => 'Běžné'];
        return '<span class="score score-' . $level . '" title="Skóre závažnosti ' . $score . ' ze 100">'
            . '<b>' . $score . '</b> ' . $labels[$level] . '</span>';
    }

    public static function reasons(array $reasons, int $max = 0): string
    {
        $list = $max > 0 ? array_slice($reasons, 0, $max) : $reasons;
        if ($list === []) {
            return '';
        }
        $out = '<ul class="reasons">';
        foreach ($list as $r) {
            $out .= '<li><span class="pts">+' . (int)$r['points'] . '</span>' . e($r['label'])
                . (!empty($r['capped']) ? ' <span class="muted small">(strop skupiny)</span>' : '') . '</li>';
        }
        return $out . '</ul>';
    }

    public static function reasonShortLabels(array $reasons, int $max = 3): array
    {
        // krátké názvy hlavních důvodů skóre (karta na homepage, obrázek pro sdílení); plné důvody jsou v detailu tisku
        $short = self::REASON_SHORT;
        $labels = [];
        // důvod s 0 body (např. celý ustřižený stropem skupiny) skóre nezvýšil, do zkratky nepatří
        foreach ($reasons as $r) {
            if ((int)($r['points'] ?? 1) <= 0) {
                continue;
            }
            $labels[] = $short[$r['key']] ?? (string)$r['key'];
            if (count($labels) >= $max) {
                break;
            }
        }
        return $labels;
    }

    public static function reasonKeysSummary(array $reasons, int $max = 3): string
    {
        // krátký výčet hlavních důvodů skóre pro kartu na homepage
        return e(implode(', ', self::reasonShortLabels($reasons, $max)));
    }

    /**
     * Datum události pro čtenáře: „dnes, 22:21“, „včera“, jinak „13. 11. 2026“. Čas se uvádí jen u dneška
     * a jen když ho záznam má (kroky projednávání mají jen den). $now je pro testy.
     */
    public static function when(?string $value, ?int $now = null): string
    {
        $ts = $value !== null && $value !== '' ? strtotime($value) : false;
        if ($ts === false) {
            return '';
        }
        $now ??= time();
        $day = date('Y-m-d', $ts);
        if ($day === date('Y-m-d', $now)) {
            return 'dnes' . (date('H:i', $ts) !== '00:00' ? ', ' . date('G:i', $ts) : '');
        }
        if ($day === date('Y-m-d', strtotime('-1 day', $now))) {
            return 'včera';
        }
        return date('j. n. Y', $ts);
    }

    /**
     * Jméno poslance s poslaneckým klubem v závorce, např. „Jan Novák (ODS)“. Klub je ke dni podání návrhu
     * (pn.klub); bez klubu jen jméno. Vrací hotové HTML.
     */
    public static function person(?string $name, ?string $club): string
    {
        $label = Clubs::label($club);
        return e((string)$name) . ($label !== '' ? ' <span class="club">(' . e($label) . ')</span>' : '');
    }

    /**
     * Navrhovatel tisku s kluby jmenovaných předkladatelů, např. „Babiš A. (ANO) a další“
     * (tisky.navrhovatel_klub); bez klubů jen text navrhovatele. Vrací hotové HTML.
     */
    public static function proposer(?string $proposer, ?string $clubs): string
    {
        if (trim((string)$clubs) === '') {
            return e(trim((string)$proposer));
        }
        [$parts, $rest] = Clubs::proposerParts($proposer, $clubs);
        return implode(', ', array_map(
            fn(array $p): string => e($p[0]) . ($p[1] !== '' ? ' <span class="club">(' . e($p[1]) . ')</span>' : ''),
            $parts
        )) . e($rest);
    }

    public static function chips(array $flags): string
    {
        $out = '';
        foreach ($flags as [$class, $label]) {
            $out .= '<span class="chip chip-' . e($class) . '">' . e($label) . '</span>';
        }
        return $out;
    }

    public static function kindChip(string $kind): string
    {
        [$class, $label] = self::KIND_LABELS[$kind] ?? ['surge', $kind];
        return '<span class="chip chip-' . e($class) . '">' . e($label) . '</span>';
    }

    public static function aiRounds(array $x): string
    {
        // kdo rozbor udělal; po ověření silnějším modelem i výsledek 1. kola
        if (($x['ai_model'] ?? null) === null || $x['ai_score'] === null) {
            return '';
        }
        if ((string)$x['ai_model'] !== AiReviewer::verifyModel(config())) {
            return '<span class="muted small">Rozbor: ' . e((string)$x['ai_model']) . '</span>';
        }
        $prev = $x['ai_prev_score'] !== null ? ' (první kolo ' . e((string)$x['ai_prev_model']) . ': ' . (int)$x['ai_prev_score'] . '/10)' : '';
        return '<span class="muted small">Ověřeno druhým modelem ' . e((string)$x['ai_model']) . $prev . '</span>';
    }

    /**
     * Blok „Co by se změnilo v praxi“ u pozměňovacího návrhu (pn_text.praxe_json z PnPractice): hlavní věta
     * a dopad jsou vidět hned, stav před a po, dotčené skupiny, příklad a tvrzení předkladatele se rozbalují.
     * Nadpis se řídí výsledkem hlasování ve 3. čtení ($outcome z PnOutcome), text sám zůstává podmiňovací.
     * Nad nadpisem je doslovná citace z textu PN ($quote: citace z rozboru, jinak první citace vysvětlení),
     * aby čtenář viděl znění návrhu a hned pod ním, co znamená. $report (id_dokument, cislo, ct) přidá do rohu
     * praporek pro nahlášení chyby (app/Reports.php); jeho klíč check (kontrola, at) přidá větu o kontrole
     * po hlášení čtenáře. Vrací hotové HTML; bez vysvětlení prázdný řetězec.
     */
    public static function pnPractice(?array $p, ?string $model, ?array $outcome = null, ?string $quote = null, ?array $report = null): string
    {
        if (!is_array($p) || trim((string)($p['co_se_meni'] ?? '')) === '') {
            return '';
        }
        $quote = trim((string)($quote ?? '')) !== '' ? trim((string)$quote) : trim((string)(((array)($p['citace'] ?? []))[0] ?? ''));
        $state = (string)($outcome['stav'] ?? '');
        $head = match ($state) {
            'prijat' => 'Co se tím v praxi mění <span class="muted">(Sněmovna návrh přijala)</span>',
            'zamitnut' => 'Co by se změnilo v praxi <span class="muted">(návrh neprošel)</span>',
            default => 'Co by se změnilo v praxi',
        };
        $html = '<div class="pn-praxe' . ($report !== null ? ' has-report' : '') . '">' . ($quote !== '' ? '<blockquote class="quote small">' . e($quote) . '</blockquote>' : '')
            . '<span class="pn-praxe-head">' . $head . '</span>'
            . '<span class="pn-praxe-what">' . e((string)$p['co_se_meni']) . '</span>'
            . '<span class="small">' . e((string)($p['v_praxi'] ?? '')) . '</span>';
        $more = '';
        $technical = ($p['typ'] ?? '') === 'technicka_uprava';
        if (!$technical) {
            $before = trim((string)($p['pred'] ?? ''));
            $beforeLabel = match ($p['pred_zdroj'] ?? null) {
                'navrh' => 'Podle projednávaného návrhu',
                'zakon' => 'Podle dnešního zákona',
                default => 'Bez tohoto návrhu',
            };
            $more .= '<div class="pn-praxe-grid">'
                . ($before !== '' ? '<div><span class="pn-praxe-label">' . $beforeLabel . '</span>' . e($before) . '</div>' : '')
                . '<div><span class="pn-praxe-label">S tímto návrhem</span>' . e((string)($p['po'] ?? '')) . '</div></div>';
            $who = array_filter(array_map('strval', (array)($p['koho_se_tyka'] ?? [])), fn(string $w): bool => trim($w) !== '');
            if ($who !== []) {
                $more .= '<p class="small"><span class="muted">Koho se to týká:</span> ' . e(implode(', ', $who)) . '</p>';
            }
            if (trim((string)($p['priklad'] ?? '')) !== '') {
                $more .= '<p class="small">' . e((string)$p['priklad']) . ' <span class="muted">(modelový příklad)</span></p>';
            }
        }
        if (trim((string)($p['podle_predkladatele'] ?? '')) !== '') {
            $more .= '<p class="small"><span class="muted">Předkladatel uvádí:</span> ' . e((string)$p['podle_predkladatele']) . '</p>';
        }
        if (trim((string)($p['nejiste'] ?? '')) !== '') {
            $more .= '<p class="small muted">Z textu neplyne: ' . e((string)$p['nejiste']) . '</p>';
        }
        if ($more !== '') {
            $html .= '<details class="pn-praxe-more"><summary>' . ($technical ? 'Co uvádí předkladatel' : 'Před a po, koho se to týká') . '</summary>' . $more . '</details>';
        }
        return $html . '<span class="muted small">Vysvětlení připravil jazykový model' . ($model !== null && $model !== '' ? ' (' . e($model) . ')' : '')
            . ' z textu návrhu a může se mýlit. Skóre neovlivňuje.' . self::reportCheckNote($report['check'] ?? null) . '</span>'
            . ($report !== null ? self::reportLink((int)$report['id_dokument'], (int)$report['cislo'], (int)$report['ct']) : '') . '</div>';
    }

    /** Věta u vysvětlení, které po hlášení čtenáře prošlo kontrolou silnějším modelem ($check: kontrola, at). */
    public static function reportCheckNote(?array $check): string
    {
        $when = e(formatDate((string)($check['at'] ?? '')));
        return match ($check['kontrola'] ?? null) {
            'opraveno' => ' Po hlášení čtenáře opraveno ' . $when . '.',
            'potvrzeno' => ' Po hlášení čtenáře zkontrolováno ' . $when . ', beze změny.',
            default => '',
        };
    }

    /**
     * Praporek „Nahlásit chybu ve vysvětlení“ v rohu bloku vysvětlení. Je to obyčejný odkaz na stránku
     * s formulářem (hlaseni.php); s JavaScriptem ho assets/report.js otevře jako vrstvu nad stránkou.
     */
    public static function reportLink(int $id, int $cislo, int $ct): string
    {
        $title = 'Nahlásit chybu ve vysvětlení';
        return '<a class="report-link" href="' . e(url('hlaseni', ['d' => $id])) . '" rel="nofollow" title="' . $title . '" aria-label="' . $title
            . ' návrhu č. ' . $cislo . '" data-report="' . $id . '" data-report-label="' . e(Reports::label($cislo, $ct)) . '">'
            . '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            . '<path d="M5 21V4"/><path d="M5 4h11l-2 4 2 4H5"/></svg></a>';
    }

    /**
     * Formulář hlášení chyby ve vysvětlení: stejný ve vrstvě na stránce tisku i na samostatné stránce
     * (hlaseni.php, bez JavaScriptu). $id = 0 a prázdný $label vyplní až assets/report.js podle praporku,
     * na který čtenář klikl. $old = dříve odeslané hodnoty (cast, poznamka) při chybě. $publishes = zda se platná
     * hlášení zveřejňují na GitHubu (null = podle config.php); formulář na to pak upozorní.
     */
    public static function reportForm(int $id, string $label, string $cancel, string $error = '', array $old = [], ?bool $publishes = null): string
    {
        $max = Reports::settings(config())['max_note'];
        // se zapnutým zveřejňováním formulář čtenáře upozorní a pošle s hlášením, že upozornění viděl
        $publishes ??= (new Reports(db(), config()))->publishes();
        $html = '<form class="report-form" method="post" action="' . e(url('hlaseni')) . '">'
            . '<p class="report-context muted small" data-report-context>' . e($label) . '</p>'
            . '<input type="hidden" name="d" value="' . ($id > 0 ? $id : '') . '">'
            . ($publishes ? '<input type="hidden" name="zverejneni" value="1">' : '')
            . '<fieldset><legend>Která část nesedí</legend>';
        foreach (Reports::PARTS as $key => $name) {
            $html .= '<label class="report-option"><input type="radio" name="cast" value="' . e($key) . '" required'
                . (($old['cast'] ?? '') === $key ? ' checked' : '') . '> ' . e($name) . '</label>';
        }
        return $html . '</fieldset>'
            . '<label class="report-note"><span>Co je podle vás správně <span class="muted">(nepovinné)</span></span>'
            . '<textarea name="poznamka" rows="4" maxlength="' . $max . '" placeholder="Lhůta v § 12 odst. 3 se prodlužuje na 60 dnů, ne na 90.">'
            . e((string)($old['poznamka'] ?? '')) . '</textarea></label>'
            . '<p class="report-help small"><strong>Nejvíc nám pomůže, když napíšete:</strong> co přesně je špatně, kde to v textu návrhu najdeme (paragraf, odstavec, bod) a jak to má být správně.</p>'
            // past na roboty: pole je skryté stylem, člověk ho nevyplní
            . '<label class="report-web" aria-hidden="true">Web <input type="text" name="web" tabindex="-1" autocomplete="off"></label>'
            . '<p class="report-error small" role="alert"' . ($error === '' ? ' hidden' : '') . '>' . e($error) . '</p>'
            . '<p class="muted small">Hlášení slouží jen k opravě vysvětlení: zajímá nás, zda odpovídá textu návrhu, ne názor na návrh samotný. Nepište osobní údaje. Hlášení nejdřív roztřídí jazykový model a neodpovídáme na ně.'
            . ($publishes ? ' <strong>Hlášení, které třídění označí jako platné, zveřejníme i s vaší poznámkou jako podnět na GitHubu projektu, bez údajů o vás.</strong>' : '') . '</p>'
            . '<div class="report-actions">' . $cancel . '<button type="submit" class="btn">Odeslat hlášení</button></div>'
            . '</form>';
    }

    /** Poděkování po odeslání hlášení (ve vrstvě i na samostatné stránce). */
    public static function reportThanks(): string
    {
        return '<p><strong>Děkujeme, hlášení jsme přijali.</strong></p>'
            . '<p class="small">Vysvětlení podle něj prověříme proti textu návrhu. Když se potvrdí chyba, opravíme ho.</p>';
    }

    /**
     * Vrstva s formulářem hlášení (prvek dialog) a její skript; na stránce jednou. Bez JavaScriptu nebo
     * v prohlížeči bez prvku dialog zůstane skrytá a praporek vede na stránku hlaseni.php.
     */
    public static function reportDialog(): string
    {
        $js = asset('assets/report.js') . '?v=' . (int)@filemtime(APP_ROOT . '/assets/report.js');
        return '<dialog class="report-dialog" id="report-dialog" aria-labelledby="report-title">'
            . '<h2 id="report-title">Nahlásit chybu ve vysvětlení</h2>'
            . '<div data-report-body>' . self::reportForm(0, '', '<button type="button" class="btn btn-ghost" data-report-close>Zrušit</button>') . '</div>'
            . '<div data-report-done hidden>' . self::reportThanks()
            . '<div class="report-actions"><button type="button" class="btn" data-report-close>Zavřít</button></div></div>'
            . '</dialog>' . "
" . '<script src="' . e($js) . '" defer></script>' . "
";
    }

    public static function isPastPeriod(array $alert): bool
    {
        static $current = null;
        $current ??= (int)(db()->getMeta('obdobi_cislo', '0') ?? 0);
        return !empty($alert['obdobi']) && $current > 0 && (int)$alert['obdobi'] !== $current;
    }

    /** @var array<string, array{pid: string, nazev: string, druh: string}>|null PID kterékoli verze materiálu => aktuální řádek */
    private static ?array $upcoming = null;

    /**
     * Připravované zákony podle PID kterékoli verze materiálu (upozornění může nést PID starší verze).
     * Načte se jednou na požadavek; výpis upozornění by se jinak ptal databáze u každé položky.
     */
    public static function upcomingIndex(): array
    {
        if (self::$upcoming === null) {
            self::$upcoming = [];
            foreach (['zakon' => 'pripravovane', 'narizeni' => 'pripravovana_narizeni'] as $kind => $table) {
                foreach (db()->all("SELECT pid, pidy, nazev FROM $table") as $u) {
                    $row = ['pid' => (string)$u['pid'], 'nazev' => (string)$u['nazev'], 'druh' => $kind];
                    foreach (array_filter(array_merge([(string)$u['pid']], explode(',', (string)$u['pidy']))) as $pid) {
                        self::$upcoming[$pid] ??= $row;
                    }
                }
            }
        }
        return self::$upcoming;
    }

    /** Po změně tabulky pripravovane (synchronizace, testy) se tabulka PID načte znovu. */
    public static function forgetUpcoming(): void
    {
        self::$upcoming = null;
    }

    /** Štítek s výsledkem pozměňovacího návrhu ve 3. čtení (PnOutcome) a odkazy na hlasování. */
    public static function pnOutcome(?array $o): string
    {
        if ($o === null) {
            return '';
        }
        $state = (string)$o['stav'];
        $class = match ($state) {
            'prijat', 'castecne' => 'chip chip-fast',
            'zamitnut' => 'chip chip-mp',
            default => 'chip',
        };
        // před 3. čtením stačí písmeno ze souhrnu, výsledek ještě není
        $label = match ($state) {
            'ceka' => 'Zazněl ve 2. čtení',
            default => PnOutcome::STATES[$state] ?? $state,
        };
        $letters = trim((string)$o['pismena']);
        $html = '<span class="' . $class . '">' . e($label) . ($letters !== '' ? ' · ' . e($letters) : '') . '</span>';
        $links = [];
        foreach (array_slice((array)$o['hlasovani'], 0, 8) as $v) {
            $links[] = '<a href="https://www.psp.cz/sqw/hlasy.sqw?g=' . (int)$v['id'] . '">č. ' . (int)$v['cislo']
                . (!empty($v['varianta']) ? ' (var. ' . (int)$v['varianta'] . ')' : '') . ' ' . (($v['vysledek'] ?? '') === 'A' ? 'přijato' : 'zamítnuto') . '</a>';
        }
        $note = ($links !== [] ? 'hlasování ' . implode(', ', $links) : '')
            . (!empty($o['stanovisko']) ? ($links !== [] ? ' · ' : '') . 'stanovisko výboru: ' . e((string)$o['stanovisko']) : '');
        return $html . ($note !== '' ? ' <span class="muted small">' . $note . '</span>' : '');
    }

    public static function alertUrl(array $alert, bool $absolute = false): string
    {
        // připravovaný zákon (bez tisku): na stránku připravovaných, a když už tam není, do VeKLEP
        if (str_starts_with((string)$alert['kind'], 'prep_')) {
            $pid = (string)substr((string)$alert['alert_key'], strpos((string)$alert['alert_key'], ':') + 1);
            $current = self::upcomingIndex()[$pid] ?? null;
            if ($current === null) {
                return GovPrep::url($pid);
            }
            $query = $current['druh'] === 'narizeni' ? ['druh' => 'narizeni'] : [];
            $anchor = 'prep-' . rawurlencode($current['pid']);
            return $absolute ? absUrl('pripravovane', $query, $anchor) : url('pripravovane', $query, $anchor);
        }
        // upozornění minulého období vede na psp.cz (tisk se stejným číslem je teď jiný návrh)
        if (self::isPastPeriod($alert)) {
            return self::pspTiskUrl((int)$alert['ct'], (int)$alert['obdobi']);
        }
        return $absolute ? absUrl('tisk', ['ct' => (int)$alert['ct']]) : url('tisk', ['ct' => (int)$alert['ct']]);
    }

    public static function pspTiskUrl(int $ct, int $obdobi): string
    {
        return 'https://www.psp.cz/sqw/historie.sqw?o=' . $obdobi . '&T=' . $ct;
    }

    public static function pspSdUrl(int $cislo, int $obdobi): string
    {
        return 'https://www.psp.cz/sqw/sd.sqw?cd=' . $cislo . '&o=' . $obdobi;
    }

    public static function alertList(array $alerts): void
    {
        if ($alerts === []) {
            echo '<p class="empty">Zatím žádná upozornění.</p>';
            return;
        }
        foreach ($alerts as $a): ?>
          <article class="alert">
            <div class="alert-when"><?= e(formatDate($a['event_at'], true)) ?></div>
            <div class="alert-body">
              <div class="alert-meta"><?= self::scoreBadge((int)$a['score']) ?><?= self::kindChip($a['kind']) ?><a href="<?= e(self::alertUrl($a)) ?>"><?= (int)$a['ct'] > 0 ? 'tisk ' . (int)$a['ct'] : ($a['kind'] === 'prep_narizeni' ? 'nařízení vlády' : 'připravovaný zákon') ?><?= self::isPastPeriod($a) ? ' (' . (int)$a['obdobi'] . '. období)' : '' ?></a><?= (int)$a['watched'] ? '<span class="chip chip-watch">Sledovaný</span>' : '' ?></div>
              <h3><a href="<?= e(self::alertUrl($a)) ?>"><?= e($a['title']) ?></a></h3>
              <p><?= e($a['body']) ?></p>
            </div>
          </article>
        <?php endforeach;
    }

    /** Skóre jen číslem v barvě úrovně: do řádků, kde by slovo „Závažné“ překáželo. */
    public static function scoreMini(int $score): string
    {
        $labels = [Scorer::LEVEL_MAJOR => 'závažné', Scorer::LEVEL_WATCH => 'pozor', Scorer::LEVEL_NORMAL => 'běžné'];
        $level = Scorer::level($score, config());
        return '<span class="score score-' . $level . '" title="Skóre závažnosti ' . $score . ' ze 100 (' . $labels[$level] . ')"><b>' . $score . '</b></span>';
    }

    /**
     * Souhrn jedné aktualizace (Updates::withEvents) do řádku: co se stalo a odkazy na tisky se skóre,
     * např. „Zkrácená lhůta (26. 11.): 300 · Novela z. - zákoník práce 78, 301 · Vl. n. z. o platformové práci 31“.
     * Datum v závorce je den události, když se liší ode dne, kdy ji web zaznamenal (na stránce tisku je krok
     * pod tímto datem). Co se do řádku nevejde, je za odkazem na časovou osu.
     */
    public static function updateSummary(array $update): string
    {
        $summary = Updates::summary($update['events']);
        $parts = [];
        foreach ($summary['parts'] as $part) {
            $links = [];
            foreach ($part['events'] as $event) {
                // tisk aktuálního období číslem a názvem (nedělitelná mezera, ať číslo nezůstane na konci řádku samo),
                // ostatní (připravovaný zákon, minulé období) jen názvem
                $label = (int)$event['ct'] > 0 && !self::isPastPeriod($event)
                    ? (int)$event['ct'] . "\u{00A0}· " . Updates::shorten((string)$event['name'], 60)
                    : Updates::shorten((string)$event['name']);
                $links[] = '<a href="' . e(self::alertUrl($event)) . '">' . e($label) . '</a> ' . self::scoreMini((int)$event['score']);
            }
            $note = Updates::dayNote((string)$part['day'], (string)$update['from']);
            $head = trim(e($part['what']) . ($note !== '' ? ' <span class="muted" title="Kdy se událost stala">(' . e($note) . ')</span>' : ''));
            $parts[] = ($head !== '' ? $head . ': ' : '') . implode(', ', $links);
        }
        $out = implode(' <span class="muted">·</span> ', $parts);
        if ($summary['rest'] > 0) {
            $rest = $summary['rest'];
            $out .= ' a <a href="' . e(url('aktualizace', ['vse' => 1], (string)$update['id'])) . '">' . $rest . ' '
                . ($rest >= 5 ? 'dalších' : 'další') . '</a>';
        }
        return $out;
    }

    /**
     * Adresa aktualizace na časové ose (kotva u jejího času). Aktualizace jen s běžnou agendou ve výchozím
     * pohledu není, proto odkaz vede do pohledu s běžnou agendou.
     */
    public static function updateUrl(array $update): string
    {
        return url('aktualizace', (int)($update['important'] ?? 0) > 0 ? [] : ['vse' => 1], (string)$update['id']);
    }

    /**
     * Řádek „Poslední aktualizace“ na přehledu: nejnovější aktualizace je vidět hned, po rozbalení (bez JavaScriptu,
     * prvek details) předchozí aktualizace a odkaz na časovou osu. Čas říká, kdy web změnu zaznamenal, a vede
     * na tutéž aktualizaci na časové ose. Bez upozornění v databázi se nevypíše nic.
     *
     * @param list<array> $updates aktualizace od nejnovější (Updates::latest)
     */
    public static function updatesBar(array $updates, ?string $lastSync): void
    {
        if ($updates === []) {
            return;
        }
        $first = array_shift($updates);
        $hint = 'Kdy web změnu zaznamenal. Datum v závorce říká, kdy se stala.';
        ?>
<details class="card updates">
  <summary>
    <span class="updates-line"><strong>Poslední aktualizace <a href="<?= e(self::updateUrl($first)) ?>" title="<?= e($hint) ?>"><?= e(Updates::when((string)$first['from'], true)) ?></a></strong> <span class="muted">·</span> <?= self::updateSummary($first) ?></span>
  </summary>
  <?php if ($updates !== []): ?>
  <ol class="updates-list" aria-label="Předchozí aktualizace">
    <?php foreach ($updates as $update): ?>
    <li><a class="updates-time" href="<?= e(self::updateUrl($update)) ?>" title="<?= e($hint) ?>"><time datetime="<?= e(date('Y-m-d\TH:i', (int)strtotime((string)$update['from']))) ?>"><?= e(Updates::when((string)$update['from'])) ?></time></a><span><?= self::updateSummary($update) ?></span></li>
    <?php endforeach; ?>
  </ol>
  <?php endif; ?>
  <div class="updates-foot">
    <span class="muted small"><?php if ($lastSync !== null && $lastSync !== ''): ?>Data naposledy kontrolována <?= e(Updates::when($lastSync, true)) ?><?php endif; ?></span>
    <a href="<?= e(url('aktualizace')) ?>">Zobrazit všechny aktualizace →</a>
  </div>
</details>
        <?php
    }

    /** Štítky pozměňovacího návrhu z řádku Repo::pnTexts(): přílepek, lhůta se zpětným použitím, rozsáhlý, AI N/10. */
    public static function pnChips(array $x): string
    {
        $out = '';
        if (!empty($x['signals']['other_laws'])) {
            $out .= '<span class="chip chip-cut">Přílepek: ' . e(implode(', ', $x['signals']['other_laws'])) . '</span>';
        }
        if (!empty($x['signals']['retro_deadline']) && !empty($x['signals']['deadline'])) {
            $out .= '<span class="chip chip-cut">Lhůta a zpětné použití</span>';
        }
        if (!empty($x['signals']['large'])) {
            $out .= '<span class="chip chip-surge">Rozsáhlý</span>';
        }
        if (($x['ai_score'] ?? null) !== null) {
            $out .= '<span class="chip ' . ((int)$x['ai_score'] >= 6 ? 'chip-cut' : 'chip-surge') . '">AI ' . (int)$x['ai_score'] . '/10</span>';
        }
        return $out;
    }

    /**
     * Jedna změna tisku (položka z BillUpdates::days) jako HTML s odkazem na místo na stránce tisku: krok vede
     * do průběhu projednávání (kotva krok-N), pozměňovací návrhy na své záznamy (pn-N), souhrn na psp.cz.
     * $short je podoba do jednoho řádku: u pozměňovacích návrhů jen prvních pár čísel, bez doplňků.
     */
    public static function billUpdateItem(array $item, int $ct, bool $short): string
    {
        $title = e((string)$item['title']);
        if ($item['type'] === 'pn') {
            // jediné číslo navíc se vypíše, „a 1 další“ by místo neušetřilo
            $limit = count($item['pn']) <= BillUpdates::PN_LINKS + 1 ? count($item['pn']) : BillUpdates::PN_LINKS;
            $list = $short ? array_slice($item['pn'], 0, $limit) : [];
            $links = [];
            foreach ($list as $p) {
                $links[] = '<a href="' . e(url('tisk', ['ct' => $ct], 'pn-' . (int)$p['cislo'])) . '">' . (int)$p['cislo'] . '</a>';
            }
            $rest = count($item['pn']) - count($list);
            return $short
                ? $title . ': č.' . "\u{00A0}" . implode(', ', $links) . ($rest > 0 ? ' a ' . $rest . ' ' . ($rest >= 5 ? 'dalších' : 'další') : '')
                : '<strong>' . $title . '</strong>';
        }
        if ($item['type'] === 'summary') {
            $link = (string)$item['url'] !== '' ? '<a href="' . e((string)$item['url']) . '">' . $title . '</a>' : $title;
            return $short ? $link : $link . ' <span class="muted">(' . e((string)$item['detail']) . ')</span>';
        }
        // krok projednávání nebo vyhlášení: výrazné kroky jako štítek ve stejných barvách jako tečky v průběhu
        $href = url('tisk', ['ct' => $ct], (int)$item['id'] > 0 ? 'krok-' . (int)$item['id'] : 'prubeh');
        $chip = ['cut' => 'chip-cut', 'fast' => 'chip-fast', 'main' => 'chip-mp'][$item['flag']] ?? '';
        $link = '<a' . ($chip !== '' ? ' class="chip ' . $chip . '"' : '') . ' href="' . e($href) . '">' . $title . '</a>';
        if ($short) {
            return $link;
        }
        $extra = (string)$item['detail'] !== '' ? ' <span class="muted">' . e((string)$item['detail']) . '</span>' : '';
        if ((int)$item['vote'] > 0) {
            $extra .= ' <a class="small" href="https://www.psp.cz/sqw/hlasy.sqw?g=' . (int)$item['vote'] . '">hlasování na psp.cz</a>';
        }
        return $link . $extra;
    }

    /**
     * Souhrn jednoho dne změn tisku do řádku, např. „Zkrácená lhůta · Podán 1 pozměňovací návrh: č. 2589 ·
     * Rozeslán souhrn pozměňovacích návrhů a 4 další“. Co se nevejde, je za odkazem na časovou osu tisku.
     */
    public static function billUpdateSummary(array $day, int $ct): string
    {
        $summary = BillUpdates::summary($day);
        $parts = [];
        foreach ($summary['items'] as $item) {
            $parts[] = self::billUpdateItem($item, $ct, true);
        }
        $out = implode(' <span class="muted">·</span> ', $parts);
        if ($summary['rest'] > 0) {
            $rest = $summary['rest'];
            $out .= ' a <a href="' . e(url('tisk-aktualizace', ['ct' => $ct], BillUpdates::anchor((string)$day['day']))) . '">' . $rest . ' '
                . ($rest >= 5 ? 'dalších' : 'další') . '</a>';
        }
        return $out;
    }

    /**
     * Řádek „Poslední změna“ na stránce tisku: nejnovější den se změnami je vidět hned, po rozbalení (bez
     * JavaScriptu, prvek details) předchozí dny a odkaz na časovou osu tisku. Datum je den, kdy se změna stala,
     * a vede na tentýž den na časové ose. Bez změn se nevypíše nic.
     *
     * @param list<array> $days dny od nejnovějšího (BillUpdates::days)
     */
    public static function billUpdatesBar(array $days, int $ct): void
    {
        if ($days === []) {
            return;
        }
        $total = count($days);
        $first = array_shift($days);
        $hint = 'Den, kdy se změna stala. Krok se v otevřených datech Sněmovny může objevit se zpožděním.';
        $dayUrl = static fn(array $day): string => url('tisk-aktualizace', ['ct' => $ct], BillUpdates::anchor((string)$day['day']));
        ?>
<details class="card updates">
  <summary>
    <span class="updates-line"><strong>Poslední změna <a href="<?= e($dayUrl($first)) ?>" title="<?= e($hint) ?>"><?= e(BillUpdates::dayWhen((string)$first['day'])) ?></a></strong> <span class="muted">·</span> <?= self::billUpdateSummary($first, $ct) ?></span>
  </summary>
  <?php if ($days !== []): ?>
  <ol class="updates-list" aria-label="Předchozí změny">
    <?php foreach (array_slice($days, 0, BillUpdates::ROW_DAYS) as $day): ?>
    <li><a class="updates-time" href="<?= e($dayUrl($day)) ?>" title="<?= e($hint) ?>"><time datetime="<?= e((string)$day['day']) ?>"><?= e(BillUpdates::dayWhen((string)$day['day'])) ?></time></a><span><?= self::billUpdateSummary($day, $ct) ?></span></li>
    <?php endforeach; ?>
  </ol>
  <?php endif; ?>
  <div class="updates-foot">
    <span class="muted small">Celkem <?= e(BillUpdates::daysLabel($total)) ?></span>
    <a href="<?= e(url('tisk-aktualizace', ['ct' => $ct])) ?>">Zobrazit všechny změny tisku →</a>
  </div>
</details>
        <?php
    }
}
