<?php

declare(strict_types=1);

namespace App\Services;

use App\Models\BillText;
use App\Models\AmendmentText;
use Hlidac\AiReviewer;

final class AiReliabilityService
{
    public function statistics(?array $cfg = null): array
    {
        $cfg ??= hlidacConfig();
        $verifier = AiReviewer::verifyModel($cfg);
        $fast = AiReviewer::model($cfg);
        $r = AiReviewer::repeatConfig($cfg);
        $verifyMin = (int)($cfg['ai']['verify_min'] ?? 6);
        $pointsFrom = 4;
        $empty = fn() => ['n' => 0, 'agree' => 0, 'lower' => 0, 'higher' => 0, 'to_points' => 0, 'to_finding' => 0];
        $out = ['high' => $empty(), 'sample' => $empty(), 'runs' => ['n' => 0, 'stable' => 0, 'changed_points' => 0, 'max_spread' => 0]];
        foreach ([BillText::class => 'ct', AmendmentText::class => 'id_dokument'] as $model => $id) {
            $rows = $model::query()->where('ai_model', $verifier)->whereNotNull('ai_prev_score')->where('ai_prev_model', $fast)
                ->where('ai_prompt', AiReviewer::PROMPT_VERSION)->get([$id . ' as id', 'ai_score', 'ai_prev_score'])->map->getAttributes()->all();
            foreach ($rows as $row) {
                $prev = (int)$row['ai_prev_score'];
                $now = (int)$row['ai_score'];
                $group = $prev >= $verifyMin ? 'high'
                    : ($prev < $r['sample_below'] && (int)$row['id'] % $r['sample_every'] === AiReviewer::SAMPLE_REMAINDER ? 'sample' : null);
                if ($group === null) {
                    continue;
                }
                $g = &$out[$group];
                $g['n']++;
                $g['agree'] += abs($now - $prev) <= 1 ? 1 : 0;
                $g['lower'] += $now <= $prev - 2 ? 1 : 0;
                $g['higher'] += $now >= $prev + 2 ? 1 : 0;
                $g['to_points'] += $prev < $pointsFrom && $now >= $pointsFrom ? 1 : 0;
                $g['to_finding'] += $prev < $verifyMin && $now >= $verifyMin ? 1 : 0;
                unset($g);
            }
            foreach ($model::query()->where('ai_run_count', '>', 1)->where('ai_prompt', AiReviewer::PROMPT_VERSION)->get(['ai_runs'])->map->getAttributes()->all() as $row) {
                $runs = array_map('intval', json_decode((string)$row['ai_runs'], true) ?: []);
                if (count($runs) < 2) {
                    continue;
                }
                $spread = max($runs) - min($runs);
                $out['runs']['n']++;
                $out['runs']['stable'] += $spread <= 1 ? 1 : 0;
                $out['runs']['max_spread'] = max($out['runs']['max_spread'], $spread);

                $out['runs']['changed_points'] += ($runs[0] >= $pointsFrom) !== (AiReviewer::median($runs) >= $pointsFrom) ? 1 : 0;
            }
        }
        return $out;
    }

}
