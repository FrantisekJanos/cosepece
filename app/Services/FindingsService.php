<?php

declare(strict_types=1);

namespace App\Services;

use App\Models\Bill;
use App\Models\Amendment;
use App\Models\BillText;
use App\Models\AmendmentText;
use App\Models\GovernmentPreparation;
use Hlidac\AiReviewer;
use Hlidac\Scorer;

final class FindingsService
{
    public function __construct(private BillPresentationService $presentation)
    {
    }

    public const PROBLEM_GROUPS = [
        'prilepky' => ['Přílepky', ['pn_other_law']],
        'zmeny_pn' => ['Rozsáhlé změny v PN', ['pn_large']],
        'lhuty' => ['Zkrácené lhůty', ['deadline_cut', 'short_gap']],
        'par90' => ['§ 90/2 bez 2. čtení', ['p90_passed', 'p90_requested']],
        'pripominky' => ['Obcházení připomínek', ['mp_bill', 'senate_bill', 'region_bill']],
        'zpetne' => ['Zpětné použití', ['retro_deadline']],
    ];

    public const FINDING_SORTS = [
        'skore' => 'Celkové skóre',
        'ai' => 'Závažnost textu (AI)',
        'nove' => 'Nejnovější dění',
        'hlasovani' => 'Blíží se hlasování',
    ];

    public function findings(int $minScore, string $sort = 'skore', string $group = ''): array
    {
        $ai = $this->aiSignals();
        $strongMin = (int)Scorer::weights(hlidacConfig())['finding_ai_min'];
        $strong = array_keys(array_filter($ai, fn(array $a) => $a['score'] >= $strongMin));
        $rows = $this->presentation->presentMany(Bill::query()->activeFindings($minScore, $strong)->ranked('score')->get(), true);
        $short = $this->shortReview();
        $lastPn = [];
        foreach (Amendment::query()->select('ct')->selectRaw('MAX(cas) AS cas')->groupBy('ct')->get()->map->getAttributes()->all() as $r) {
            $lastPn[(int)$r['ct']] = (string)$r['cas'];
        }
        $counts = array_fill_keys(array_keys(self::PROBLEM_GROUPS), 0);
        foreach ($rows as &$row) {
            $ct = (int)$row['ct'];
            $row['reasons_list'] = json_decode((string)$row['reasons'], true) ?: [];
            $row['ai_verified'] = $ai[$ct]['score'] ?? null;

            $row['strong_ai'] = ($ai[$ct]['score'] ?? 0) >= $strongMin ? $ai[$ct]['score'] : null;
            $row['groups'] = $this->problemGroups($row, $ai[$ct] ?? null, isset($short[$ct]));
            $row['activity'] = max((string)$row['posledni'], $lastPn[$ct] ?? '');
            foreach ($row['groups'] as $g) {
                $counts[$g]++;
            }
        }
        unset($row);
        $total = count($rows);
        if ($group !== '' && isset(self::PROBLEM_GROUPS[$group])) {
            $rows = array_values(array_filter($rows, fn(array $r) => in_array($group, $r['groups'], true)));
        }
        usort($rows, match ($sort) {

            'ai' => fn(array $a, array $b) => [$b['ai_verified'] ?? -1, (int)$b['score']] <=> [$a['ai_verified'] ?? -1, (int)$a['score']],
            'nove' => fn(array $a, array $b) => [$b['activity'], (int)$b['score']] <=> [$a['activity'], (int)$a['score']],

            'hlasovani' => fn(array $a, array $b) => [self::votePhaseRank((string)$a['faze']), -(int)$a['score']] <=> [self::votePhaseRank((string)$b['faze']), -(int)$b['score']],
            default => fn(array $a, array $b) => [(int)$b['score'], (string)$b['posledni']] <=> [(int)$a['score'], (string)$a['posledni']],
        });
        return ['rows' => $rows, 'counts' => $counts, 'total' => $total];
    }

    public static function votePhaseRank(string $faze): int
    {
        return match ($faze) {
            '3. čtení', 'Sněmovna po Senátu', 'Sněmovna po vetu' => 0,
            '2. čtení' => 1,
            '1. čtení', 'Organizační výbor', 'Podáno', 'U vlády', 'Stanovisko vlády' => 2,
            default => 3,
        };
    }

    public function problemGroups(array $t, ?array $ai = null, bool $shortReview = false): array
    {

        $keys = array_column(is_array($t['reasons_list'] ?? null) ? $t['reasons_list'] : (json_decode((string)($t['reasons'] ?? ''), true) ?: []), 'key');
        $out = [];
        foreach (self::PROBLEM_GROUPS as $group => [, $reasonKeys]) {
            if (array_intersect($keys, $reasonKeys) !== []) {
                $out[] = $group;
            }
        }

        if (!empty($ai['rider']) && !in_array('prilepky', $out, true)) {
            $out[] = 'prilepky';
        }
        if (!empty($ai['retro']) && !in_array('zpetne', $out, true)) {
            $out[] = 'zpetne';
        }

        if ($shortReview && !in_array('pripominky', $out, true)) {
            $out[] = 'pripominky';
        }
        return array_values(array_intersect(array_keys(self::PROBLEM_GROUPS), $out));
    }

    public function shortReview(): array
    {

        $out = [];
        foreach (GovernmentPreparation::query()->where('druh', 'vladni')->whereNotNull('dni')->where('dni', '<', 21)->pluck('ct') as $ct) {
            $out[(int)$ct] = true;
        }
        return $out;
    }

    public function aiSignals(): array
    {


        $verifier = AiReviewer::verifyModel(hlidacConfig());
        $flagMin = (int)(hlidacConfig('ai', [])['verify_min'] ?? 6);
        $out = [];
        foreach ([BillText::class, AmendmentText::class] as $model) {
            foreach ($model::query()->where('ai_model', $verifier)->whereNotNull('ai_score')->get()->map->getAttributes()->all() as $r) {
                $ai = json_decode((string)$r['ai_json'], true) ?: [];
                if (trim((string)(((array)($ai['citace'] ?? []))[0] ?? '')) === '') {
                    continue;
                }
                $ct = (int)$r['ct'];
                $score = (int)$r['ai_score'];
                $out[$ct] ??= ['score' => 0, 'rider' => false, 'retro' => false];
                $out[$ct]['score'] = max($out[$ct]['score'], $score);
                if ($score >= $flagMin) {
                    $out[$ct]['rider'] = $out[$ct]['rider'] || (isset($ai['souvisi_s_predmetem']) && $ai['souvisi_s_predmetem'] === false);
                    $out[$ct]['retro'] = $out[$ct]['retro'] || !empty($ai['zpetna_ucinnost']);
                }
            }
        }
        return $out;
    }

}
