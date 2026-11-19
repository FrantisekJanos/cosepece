<?php

declare(strict_types=1);

namespace App\Services\Pages;

use App\Models\Bill;
use App\Services\BillPresentationService;

final class BillListingService
{
    public function __construct(private BillPresentationService $presentation)
    {
    }

    public function data(string $filter, string $query, string $sort, int $minimumScore): array
    {
        $f = $filter;
        $filters = [
            '' => 'Všechny tisky',
            'flagged' => 'Všechny příznaky',
            'mp' => 'Poslanecké a senátní návrhy',
            'p90' => 'Zrychlené čtení',
            'cut' => 'Zkrácená lhůta',
            'surge' => 'Nával PN',
        ];
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
    public function bills(string $filter, string $search, int $limit, string $sort = 'score', int $minimumScore = 0): array
    {
        $query = Bill::query()->procedureFilter($filter)->searchText($search)->ranked($sort);
        if ($minimumScore > 0) {
            $query->where('score', '>=', $minimumScore);
        }
        return $this->presentation->presentMany($query->limit($limit)->get());
    }
}
