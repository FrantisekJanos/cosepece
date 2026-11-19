<?php

declare(strict_types=1);

namespace App\Services;

use App\Models\Alert;
use Hlidac\Scorer;

final class FeedService
{
    public function alerts(bool $includeAll): array
    {
        $minimumScore = $includeAll ? 0 : (int)Scorer::weights(hlidacConfig())['level_watch'];

        return Alert::query()->where('score', '>=', $minimumScore)->orderByDesc('event_at')
            ->limit(50)->get()->map->getAttributes()->all();
    }
}
