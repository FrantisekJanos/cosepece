<?php

declare(strict_types=1);

namespace App\Services\Pages;

use App\Models\Bill;
use App\Services\BillPresentationService;

final class BillListingService
{
    public function __construct(private BillPresentationService $presentation, private \App\Services\FindingsService $findings)
    {
    }

    public function data(string $filter, string $query, string $sort, int $minimumScore): array
    {
        $f = $filter;
        $filters = [
            '' => 'Všechny tisky',
            'flagged' => 'Všechny příznaky',
            'mp' => 'Poslanecké, senátní a krajské návrhy',
            'p90' => 'Zrychlené čtení',
            'cut' => 'Zkrácená lhůta',
            'surge' => 'Nával PN',
        ];
        foreach (['prilepky', 'zmeny_pn', 'zpetne'] as $key) {
            $filters[$key] = \App\Services\FindingsService::PROBLEM_GROUPS[$key][0];
        }
        if (!array_key_exists($f, $filters)) {
            $f = '';
        }
        $q = trim($query);
        $q = mb_substr($q, 0, 100);
        $sort = $sort === 'recent' ? 'recent' : 'score';
        $min = max(0, min(100, $minimumScore));

        $bills = $this->bills($f, $q, 500, $sort, $min);

        $title = 'Tisky';
        $active = 'tisky';

        return compact('title', 'active', 'filters', 'f', 'q', 'sort', 'min', 'bills');
    }
    public static function fold(?string $text): string
    {

        static $map = [
            'á' => 'a', 'č' => 'c', 'ď' => 'd', 'é' => 'e', 'ě' => 'e', 'í' => 'i', 'ň' => 'n', 'ó' => 'o',
            'ř' => 'r', 'š' => 's', 'ť' => 't', 'ú' => 'u', 'ů' => 'u', 'ý' => 'y', 'ž' => 'z',
            'ä' => 'a', 'ö' => 'o', 'ü' => 'u', 'ľ' => 'l', 'ĺ' => 'l', 'ŕ' => 'r', 'ô' => 'o',
        ];
        return strtr(mb_strtolower(trim((string)$text)), $map);
    }

    public function bills(string $filter, string $search, int $limit, string $sort = 'score', int $minimumScore = 0): array
    {
        $query = Bill::query()->procedureFilter($filter)->ranked($sort);
        if ($minimumScore > 0) {
            $query->where('score', '>=', $minimumScore);
        }
        $byGroup = isset(\App\Services\FindingsService::PROBLEM_GROUPS[$filter]);
        $bills = $search === '' && !$byGroup ? $query->limit($limit)->get() : $query->get();
        if ($byGroup) {
            $ai = $this->findings->aiSignals();
            $short = $this->findings->shortReview();
            $bills = $bills->filter(fn (Bill $bill): bool => in_array($filter,
                $this->findings->problemGroups($bill->getAttributes(), $ai[(int)$bill->ct] ?? null, isset($short[(int)$bill->ct])), true));
        }
        if ($search !== '') {
            $bills = $bills->filter(fn (Bill $bill): bool => (string)$bill->ct === trim($search)
                || str_contains(self::fold($bill->nazev . ' ' . $bill->uplny_nazev . ' ' . $bill->navrhovatel . ' ' . $bill->zakony), self::fold($search)));
        }
        $bills = $bills->take($limit)->values();
        return $this->presentation->presentMany($bills);
    }
}
