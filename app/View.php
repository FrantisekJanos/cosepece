<?php


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

    public static function timelineDotClass(array $step): string
    {
        $type = (int)$step['typ_stavu'];
        if (in_array($type, Analyzer::STAV_P90, true)) {
            return 'dot-fast';
        }
        if (in_array((int)$step['akce_id'], Analyzer::AKCE_ZKRACENI, true)) {
            return 'dot-cut';
        }
        if (in_array($type, [64, 11, 8, 12], true)) {
            return 'dot-main';
        }
        return '';
    }

    public static function scoreBadge(int $score): string
    {

        $level = Scorer::level($score, hlidacConfig());
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

        if (($x['ai_model'] ?? null) === null || $x['ai_score'] === null) {
            return '';
        }
        if ((string)$x['ai_model'] !== AiReviewer::verifyModel(hlidacConfig())) {
            return '<span class="muted small">Rozbor: ' . e((string)$x['ai_model']) . '</span>';
        }
        $prev = $x['ai_prev_score'] !== null ? ' (první kolo ' . e((string)$x['ai_prev_model']) . ': ' . (int)$x['ai_prev_score'] . '/10)' : '';
        return '<span class="muted small">Ověřeno druhým modelem ' . e((string)$x['ai_model']) . $prev . '</span>';
    }

    public static function isPastPeriod(array $alert): bool
    {
        return (bool)($alert['is_past_period'] ?? false);
    }

    public static function alertUrl(array $alert, bool $absolute = false): string
    {

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

}
