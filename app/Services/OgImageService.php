<?php

declare(strict_types=1);

namespace App\Services;

use Hlidac\OgCard;
use App\Models\Bill;
use App\Models\Meta;

final class OgImageService
{
    public function __construct(private BillPresentationService $presentation)
    {
    }

    public function path(int $billNumber, string $page): string
    {
        $fallback = public_path('assets/og-default.png');
        if (!OgCard::available()) {
            return $fallback;
        }

        try {
            $bill = $billNumber > 0 ? Bill::query()->where('ct', $billNumber)->first() : null;
            $card = $bill !== null
                ? OgCard::forBill($this->presentation->present($bill), hlidacConfig())
                : OgCard::forPage($page, hlidacConfig(), (int)(Meta::query()->whereKey('obdobi_cislo')->value('v') ?? '10'));

            return OgCard::cached($card, hlidacDataPath('cache/og'));
        } catch (\Throwable $ex) {
            report($ex);
            return $fallback;
        }
    }
}
