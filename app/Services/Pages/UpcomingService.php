<?php

declare(strict_types=1);

namespace App\Services\Pages;

use App\Models\UpcomingBill;
use Hlidac\GovPrep;

final class UpcomingService
{
    public function items(): array
    {
        $order = array_flip(GovPrep::PHASES);
        $rows = UpcomingBill::query()->get()->map->getAttributes()->all();
        usort($rows, fn(array $a, array $b) => [($order[$a['faze']] ?? 9), (string)$b['zmeneno']] <=> [($order[$b['faze']] ?? 9), (string)$a['zmeneno']]);
        return $rows;
    }

    public function data(): array
    {
        $items = $this->items();
        $phases = [
            'schvaleno' => ['Schváleno vládou', 'Vláda návrh schválila, do Sněmovny ho zatím nepředložila (obvykle během několika dnů až týdnů).'],
            'vlada' => ['Předloženo vládě', 'Připomínky jsou vypořádané a návrh čeká na projednání vládou (případně Legislativní radou vlády).'],
            'po_pripominkach' => ['Po připomínkovém řízení', 'Připomínky skončily, ministerstvo je vypořádává a připravuje návrh pro vládu.'],
            'pripominky' => ['V připomínkovém řízení', 'K návrhu se teď vyjadřují ministerstva, kraje, obce, odborové a zaměstnavatelské svazy a další připomínková místa.'],
        ];
        $byPhase = array_fill_keys(array_keys($phases), []);
        foreach ($items as $u) {
            $byPhase[$u['faze']][] = $u;
        }
        $shortTitle = fn(string $title): string => trim((string)preg_replace('~,?\s*ve znění pozdějších předpisů~u', '', $title), " ,");
        $short = count(array_filter($items, fn(array $u) => $u['dni'] !== null && (int)$u['dni'] < 21));
        $missing = count(array_filter($items, fn(array $u) => $u['dni'] === null && $u['faze'] !== 'pripominky' && !str_contains(mb_strtolower((string)$u['nazev']), 'státním rozpočtu')));

        return compact('items', 'phases', 'byPhase', 'shortTitle', 'short', 'missing') + [
            'title' => 'Připravované zákony', 'active' => 'pripravovane',
            'description' => 'Návrhy zákonů, které vláda připravuje a do Sněmovny ještě nedorazily.',
        ];
    }
}
