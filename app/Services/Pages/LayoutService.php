<?php

declare(strict_types=1);

namespace App\Services\Pages;

use App\Models\Meta;

final class LayoutService
{
    public function header(string $title, string $active, string $description, array $og, string $page): array
    {
        $obdobi = (int)(Meta::query()->whereKey('obdobi_cislo')->value('v') ?? '10');
        $sync = Meta::query()->whereKey('last_sync')->value('v');
        $nav = [
            'prehled' => [route('home', [], false), 'Přehled'],
            'tisky' => [route('bills', [], false), 'Tisky'],
            'upozorneni' => [route('alerts', [], false), 'Upozornění'],
            'metodika' => [route('methodology', [], false), 'Jak hodnotíme'],
        ];
        $desc = $description !== '' ? $description : 'Pozměňovací návrhy, zrychlená projednávání a zkrácené lhůty v Poslanecké sněmovně.';
        $ogUrl = siteUrl((string)($og['url'] ?? $page));
        $ogImage = siteUrl((string)($og['image'] ?? route('og', ['p' => $active], false)));
        $ogTitle = (string)($og['title'] ?? $title);
        $ogAlt = (string)($og['alt'] ?? $ogTitle . ' – Co se peče');

        return compact('obdobi', 'sync', 'nav', 'desc', 'ogUrl', 'ogImage', 'ogTitle', 'ogAlt', 'og');
    }
}
