<?php

declare(strict_types=1);

namespace App\Services;

use App\Models\Alert;
use Hlidac\Scorer;

final class FeedService
{
    public function __construct(private \App\Services\AlertPresentationService $alertPresentation)
    {
    }

    public function alerts(bool $includeAll): array
    {
        $minimumScore = $includeAll ? 0 : (int)Scorer::weights(hlidacConfig())['level_watch'];

        return $this->alertPresentation->presentMany(Alert::query()->where('score', '>=', $minimumScore)->orderByDesc('event_at')->orderByDesc('created_at')->orderByDesc('alert_key')
            ->limit(50)->get());
    }
}
