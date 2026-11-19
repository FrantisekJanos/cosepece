<?php

declare(strict_types=1);

namespace App\Services\Pages;

use App\Models\Bill;
use App\Services\BillPresentationService;
use Hlidac\Laws;

final class LawService
{
    public function __construct(private Laws $laws, private BillPresentationService $presentation)
    {
    }

    public function overview(): array
    {
        return ['all' => $this->laws->overview(), 'watched' => hlidacConfig('watch_laws', []),
            'law' => '', 'title' => 'Zákony', 'active' => 'zakony',
            'description' => 'Které zákony se v tomto volebním období mění: návrhy ve Sněmovně i připravované u vlády.'];
    }

    public function detail(string $law): array
    {
        $name = $this->laws->name($law);
        $rows = $this->laws->bills($law);
        $models = Bill::query()->whereIn('ct', array_column($rows, 'ct'))->orderByDesc('posledni')->orderByDesc('ct')->get();
        $bills = $this->presentation->presentMany($models);
        $amendments = $this->laws->amendments($law);
        $upcoming = $this->laws->upcoming($law);
        $watched = array_map('strval', (array)hlidacConfig('watch_laws', []));
        $title = 'Zákon č. ' . $law . ' Sb.' . ($name !== null ? ', ' . $name : '');
        $phase = ['pripominky' => 'v připomínkovém řízení', 'po_pripominkach' => 'po připomínkovém řízení',
            'vlada' => 'předloženo vládě', 'schvaleno' => 'schváleno vládou'];
        return compact('law', 'name', 'bills', 'amendments', 'upcoming', 'watched', 'title', 'phase') + [
            'active' => 'zakony', 'description' => 'Co v tomto volebním období mění ' . $title . '.',
            'og' => ['url' => Laws::url($law)],
        ];
    }
}
