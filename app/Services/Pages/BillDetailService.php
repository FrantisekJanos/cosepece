<?php

declare(strict_types=1);

namespace App\Services\Pages;

use App\Models\Bill;
use App\Models\BillText;
use App\Models\AmendmentText;
use App\Models\Meta;
use App\Services\BillPresentationService;
use App\Services\ReviewStatusService;
use Hlidac\OgCard;

final class BillDetailService
{
    public function __construct(
        private BillPresentationService $presentation,
        private ReviewStatusService $reviews,
    ) {
    }

    public function data(int $ct): ?array
    {
        $bill = $ct > 0 ? Bill::query()->with([
            'steps' => fn ($query) => $query->orderBy('id_hist'),
            'amendments' => fn ($query) => $query->orderByDesc('cas'),
            'alerts' => fn ($query) => $query->orderByDesc('event_at')->limit(10),
            'text', 'amendmentTexts',
        ])->where('ct', $ct)->first() : null;
        if ($bill === null) {
            return null;
        }

        $t = $this->presentation->present($bill);
        $obdobi = (int)(Meta::query()->whereKey('obdobi_cislo')->value('v') ?? '10');
        $steps = $bill->steps->map->getAttributes()->all();
        $pn = $bill->amendments->map->getAttributes()->all();
        $authors = $bill->amendments()->select('osoba')->selectRaw('COUNT(*) AS n')
            ->groupBy('osoba')->orderByDesc('n')->orderBy('osoba')->get()->map->getAttributes()->all();
        $alerts = $bill->alerts->map->getAttributes()->all();
        $texts = $bill->amendmentTexts->mapWithKeys(fn (AmendmentText $text): array => [
            $text->id_dokument => $this->textData($text),
        ])->all();
        $billText = $bill->text === null ? null : $this->textData($bill->text);
        $review = $this->reviews->statuses($ct)[$ct] ?? ['total' => 0, 'done' => 0];
        $laws = array_filter(explode(',', (string)$t['zakony']));

        $days = null;
        if ($t['predlozeno'] && $t['posledni']) {
            $days = (int)round((strtotime($t['posledni']) - strtotime($t['predlozeno'])) / 86400);
        }

        $ogCard = OgCard::forBill($t, hlidacConfig());
        $ogDesc = 'Skóre závažnosti postupu ' . (int)$t['score'] . '/100. ' . $ogCard['text'] . ' ' . $t['uplny_nazev'];
        $title = 'Tisk ' . $ct . ' – ' . $t['nazev'];
        $active = 'tisky';
        $description = mb_substr($ogDesc, 0, 300);
        $og = [
            'url' => 'tisk.php?ct=' . $ct,
            'image' => 'og.php?ct=' . $ct . '&v=' . OgCard::cacheKey($ogCard),
            'title' => 'Tisk ' . $ct . ': ' . OgCard::cleanTitle((string)($t['uplny_nazev'] ?: $t['nazev'])),
            'alt' => 'Tisk ' . $ct . ', skóre ' . (int)$t['score'] . ' ze 100. ' . $ogCard['text'],
        ];

        return compact('title', 'active', 'description', 'og', 'ct', 't', 'obdobi', 'steps', 'pn', 'authors', 'alerts', 'texts', 'billText', 'review', 'laws', 'days');
    }
    private function textData(BillText|AmendmentText $text): array
    {
        $data = $text->getAttributes();
        $data['signals'] = $text->signals ?: [];
        $data['ai'] = $text->ai_json ?: null;
        return $data;
    }
}
