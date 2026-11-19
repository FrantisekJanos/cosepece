<?php
// Společné části HTML: hlavička, patička, štítky.

declare(strict_types=1);

namespace Hlidac;

final class View
{
    public const KIND_LABELS = [
        'newbill' => ['mp', 'Nový návrh'],
        'p90' => ['fast', '§ 90/2'],
        'cut' => ['cut', 'Zkrácená lhůta'],
        'surge' => ['surge', 'Nával PN'],
        'pn' => ['surge', 'Nové PN'],
        'passed' => ['mp', 'Schváleno Sněmovnou'],
        'law' => ['mp', 'Vyhlášeno'],
    ];

    public static function header(string $title, string $active, string $description = '', array $og = []): void
    {
        $repo = new Repo(db());
        $obdobi = $repo->obdobi();
        $sync = $repo->lastSync();
        $nav = [
            'prehled' => ['index.php', 'Přehled'],
            'tisky' => ['tisky.php', 'Tisky'],
            'upozorneni' => ['upozorneni.php', 'Upozornění'],
            'metodika' => ['metodika.php', 'Jak hodnotíme'],
        ];
        $desc = $description !== '' ? $description : 'Pozměňovací návrhy, zrychlená projednávání a zkrácené lhůty v Poslanecké sněmovně.';
        // náhled pro sociální sítě: vlastní obrázek ke každé stránce (og.php), absolutní adresy ze site_url
        $page = basename((string)($_SERVER['SCRIPT_NAME'] ?? 'index.php'));
        $ogUrl = siteUrl((string)($og['url'] ?? ($page === 'index.php' ? '' : $page)));
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
<link rel="alternate" type="application/rss+xml" title="Hlídač zákonů – upozornění" href="rss.php">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&amp;family=Space+Grotesk:wght@500;600;700&amp;display=swap" rel="stylesheet">
<link rel="stylesheet" href="assets/style.css">
<?php $umami = (array)config('umami', []); if (($umami['src'] ?? '') !== '' && ($umami['website_id'] ?? '') !== ''): ?>
<script defer src="<?= e($umami['src']) ?>" data-website-id="<?= e($umami['website_id']) ?>"<?php if (($umami['domains'] ?? '') !== ''): ?> data-domains="<?= e($umami['domains']) ?>"<?php endif; ?> data-do-not-track="true"></script>
<?php endif; ?>
</head>
<body>
<header class="top">
  <div class="top-in">
    <a class="brand" href="index.php">
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 21h18"/><path d="M5 21V10"/><path d="M19 21V10"/><path d="M9 21V10"/><path d="M15 21V10"/><path d="M2 10 12 3l10 7z"/></svg>
      <span class="brand-name">Hlídač zákonů</span>
      <span class="brand-sub">Poslanecká sněmovna · <?= $obdobi ?>. volební období</span>
    </a>
    <nav aria-label="Hlavní menu">
      <?php foreach ($nav as $key => [$href, $label]): ?>
        <a href="<?= $href ?>"<?= $key === $active ? ' class="active" aria-current="page"' : '' ?>><?= $label ?></a>
      <?php endforeach; ?>
      <a href="rss.php">RSS</a>
    </nav>
    <div class="sync"><?= $sync ? 'Data aktualizována ' . e(formatDate($sync, true)) : 'Data zatím nebyla načtena' ?></div>
  </div>
</header>
<main class="wrap">
<?php
    }

    public static function footer(): void
    {
        $issues = (string)config('issues_url', '');
        ?>
<footer class="foot">
  <p>Zdroj: <a href="https://www.psp.cz/sqw/hp.sqw?k=1300">otevřená data Poslanecké sněmovny</a> (tisky, sněmovní dokumenty, poslanci). Počty PN jsou podané písemné pozměňovací návrhy; Sněmovna neeviduje, které z nich byly platně podány ve 2. čtení. Příznaky se počítají automaticky z historie tisku.</p>
  <p><a href="zmeny.php">Co jsme změnili</a><?php if ($issues !== ''): ?> · <a href="<?= e($issues) ?>">Našli jste chybu nebo máte podnět? Napište nám.</a><?php endif; ?></p>
</footer>
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
        $short = [
            'mp_bill' => 'poslanecký návrh', 'senate_bill' => 'senátní návrh', 'region_bill' => 'krajský návrh', 'p90_passed' => 'schváleno v 1. čtení',
            'p90_requested' => 'návrh na § 90/2', 'deadline_cut' => 'zkrácená lhůta', 'short_gap' => 'málo času na PN',
            'late_pn' => 'PN na poslední chvíli', 'surge' => 'nával PN', 'pn_other_law' => 'přílepek', 'pn_large' => 'rozsáhlý PN',
            'retro_deadline' => 'lhůta a zpětné použití', 'pn_many' => 'více sporných PN', 'ai_bill' => 'sporný text návrhu',
            'ai' => 'sporné pozměňovací návrhy', 'topic' => 'citlivá oblast', 'omnibus' => 'souhrnná novela', 'watched' => 'sledovaný zákon',
        ];
        $labels = [];
        foreach (array_slice($reasons, 0, $max) as $r) {
            $labels[] = $short[$r['key']] ?? (string)$r['key'];
        }
        return $labels;
    }

    public static function reasonKeysSummary(array $reasons, int $max = 3): string
    {
        // krátký výčet hlavních důvodů skóre pro kartu na homepage
        return e(implode(', ', self::reasonShortLabels($reasons, $max)));
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

    public static function isPastPeriod(array $alert): bool
    {
        static $current = null;
        $current ??= (int)(db()->getMeta('obdobi_cislo', '0') ?? 0);
        return !empty($alert['obdobi']) && $current > 0 && (int)$alert['obdobi'] !== $current;
    }

    public static function alertUrl(array $alert, bool $absolute = false): string
    {
        // upozornění minulého období vede na psp.cz (tisk se stejným číslem je teď jiný návrh)
        if (self::isPastPeriod($alert)) {
            return self::pspTiskUrl((int)$alert['ct'], (int)$alert['obdobi']);
        }
        $path = 'tisk.php?ct=' . (int)$alert['ct'];
        return $absolute ? siteUrl($path) : $path;
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
              <div class="alert-meta"><?= self::scoreBadge((int)$a['score']) ?><?= self::kindChip($a['kind']) ?><a href="<?= e(self::alertUrl($a)) ?>">tisk <?= (int)$a['ct'] ?><?= self::isPastPeriod($a) ? ' (' . (int)$a['obdobi'] . '. období)' : '' ?></a><?= (int)$a['watched'] ? '<span class="chip chip-watch">Sledovaný</span>' : '' ?></div>
              <h3><a href="<?= e(self::alertUrl($a)) ?>"><?= e($a['title']) ?></a></h3>
              <p><?= e($a['body']) ?></p>
            </div>
          </article>
        <?php endforeach;
    }
}
