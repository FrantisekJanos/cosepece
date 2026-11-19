<?php

declare(strict_types=1);

namespace App\Services;

use App\Models\Amendment;
use App\Models\AmendmentText;
use App\Models\BillText;

final class ReviewStatusService
{
    public function statuses(?int $onlyCt = null): array
    {
        $out = [];
        $amendments = Amendment::query()->select('ct')->selectRaw('COUNT(*) AS n')->groupBy('ct');
        $reviewed = AmendmentText::query()->select('ct')->selectRaw('COUNT(*) AS n')->whereNotNull('ai_at')->groupBy('ct');
        $bills = BillText::query()->select('ct', 'ai_at');
        if ($onlyCt !== null) {
            $amendments->where('ct', $onlyCt);
            $reviewed->where('ct', $onlyCt);
            $bills->where('ct', $onlyCt);
        }
        foreach ($amendments->get() as $row) {
            $ct = (int)$row['ct'];
            $out[$ct] ??= ['total' => 0, 'done' => 0];
            $out[$ct]['total'] += (int)$row['n'];
        }
        foreach ($reviewed->get() as $row) {
            $ct = (int)$row['ct'];
            $out[$ct] ??= ['total' => 0, 'done' => 0];
            $out[$ct]['done'] += (int)$row['n'];
        }
        foreach ($bills->get() as $row) {
            $ct = (int)$row['ct'];
            $out[$ct] ??= ['total' => 0, 'done' => 0];
            $out[$ct]['total']++;
            $out[$ct]['done'] += $row['ai_at'] !== null ? 1 : 0;
        }
        return $out;
    }

    public static function label(array $status): ?string
    {

        $total = (int)($status['total'] ?? 0);
        $done = (int)($status['done'] ?? 0);
        if ($total === 0 || $done >= $total) {
            return null;
        }
        return $done === 0 ? 'Text neposouzen' : "Posouzeno $done z $total textů";
    }
}
