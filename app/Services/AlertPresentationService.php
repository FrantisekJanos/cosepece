<?php

declare(strict_types=1);

namespace App\Services;

use App\Models\Meta;
use Illuminate\Database\Eloquent\Collection;

final class AlertPresentationService
{
    public function upcomingUrl(array $alert, bool $absolute = false): string
    {
        $pid = (string)substr((string)$alert['alert_key'], strpos((string)$alert['alert_key'], ':') + 1);
        if (!\App\Models\UpcomingBill::query()->whereKey($pid)->exists()) {
            return \Hlidac\GovPrep::url($pid);
        }
        $path = route('upcoming', [], false) . '#prep-' . rawurlencode($pid);
        return $absolute ? siteUrl($path) : $path;
    }

    public function presentMany(Collection $alerts): array
    {
        $current = (int)(Meta::query()->whereKey('obdobi_cislo')->value('v') ?? 0);
        return $alerts->map(function ($alert) use ($current): array {
            $data = $alert->getAttributes();
            $data['is_past_period'] = !empty($data['obdobi']) && $current > 0 && (int)$data['obdobi'] !== $current;
            return $data;
        })->all();
    }
}
