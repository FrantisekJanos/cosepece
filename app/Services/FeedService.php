<?php

declare(strict_types=1);

namespace App\Services;

final class FeedService
{
    public function __construct(private PublicApiService $api)
    {
    }

    public function alerts(bool $includeAll, array $filters): array
    {
        if ($includeAll && $filters['min_score'] === null) {
            $filters['min_score'] = 0;
        }
        $filters['limit'] = min(100, $filters['limit']);
        return $this->api->alertRows($filters);
    }
}
