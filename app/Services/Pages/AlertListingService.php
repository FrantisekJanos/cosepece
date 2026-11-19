<?php

declare(strict_types=1);

namespace App\Services\Pages;

use Hlidac\Scorer;
use App\Models\Alert;

final class AlertListingService
{
    public function __construct(private \App\Services\AlertPresentationService $alertPresentation)
    {
    }

    public function data(int $page, bool $includeAll): array
    {
        $all = $includeAll;
        $perPage = 30;
        $page = max(1, $page);
        $watchThreshold = (int)Scorer::weights(hlidacConfig())['level_watch'];
        $min = $all ? 0 : $watchThreshold;
        $total = Alert::query()->where('score', '>=', $min)->count();
        $pages = max(1, (int)ceil($total / $perPage));
        $page = min($page, $pages);
        $alerts = $this->alertPresentation->presentMany(Alert::query()->where('score', '>=', $min)->orderByDesc('event_at')->orderByDesc('created_at')->orderByDesc('alert_key')
            ->limit($perPage)->offset(($page - 1) * $perPage)->get());
        $qs = $all ? '&vse=1' : '';

        $title = 'Upozornění';
        $active = 'upozorneni';

        return compact('title', 'active', 'page', 'all', 'min', 'watchThreshold', 'total', 'pages', 'alerts', 'qs');
    }
}
