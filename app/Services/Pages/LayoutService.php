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
            'prehled' => ['index.php', 'Přehled'],
            'tisky' => ['tisky.php', 'Tisky'],
            'upozorneni' => ['upozorneni.php', 'Upozornění'],
            'metodika' => ['metodika.php', 'Jak hodnotíme'],
        ];
        $desc = $description !== '' ? $description : 'Pozměňovací návrhy, zrychlená projednávání a zkrácené lhůty v Poslanecké sněmovně.';
        $ogUrl = siteUrl((string)($og['url'] ?? ($page === 'index.php' ? '' : $page)));
        $ogImage = siteUrl((string)($og['image'] ?? 'og.php?p=' . rawurlencode($active)));
        $ogTitle = (string)($og['title'] ?? $title);
        $ogAlt = (string)($og['alt'] ?? $ogTitle . ' – Hlídač zákonů');

        return compact('obdobi', 'sync', 'nav', 'desc', 'ogUrl', 'ogImage', 'ogTitle', 'ogAlt', 'og');
    }
}
