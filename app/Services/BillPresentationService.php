<?php

declare(strict_types=1);

namespace App\Services;

use App\Models\Alert;
use App\Models\AmendmentText;
use App\Models\Bill;
use App\Models\BillText;
use Hlidac\AiReviewer;
use Illuminate\Database\Eloquent\Collection;

final class BillPresentationService
{
    public function __construct(private ReviewStatusService $reviews)
    {
    }

    public function present(Bill $bill, ?array $surge = null, ?array $review = null): array
    {
        $data = $bill->getAttributes();
        $data['flags'] = $this->flags($bill, $surge, $review);
        $data['reasons_list'] = $bill->reasons ?: [];
        return $data;
    }

    public function presentMany(Collection $bills, bool $withHeadlines = false): array
    {
        $bills->loadCount('votes');
        $surge = $this->surgeSet();
        $review = $this->reviews->statuses();
        return $bills->map(function (Bill $bill) use ($surge, $review, $withHeadlines): array {
            $data = $this->present($bill, $surge, $review);
            if ($withHeadlines) {
                $data['headline'] = $this->headline($bill->ct);
            }
            return $data;
        })->all();
    }

    public function strongAiFindings(): array
    {
        $minimum = (int)\Hlidac\Scorer::weights(hlidacConfig())['finding_ai_min'];
        $verifier = AiReviewer::verifyModel(hlidacConfig());
        $findings = [];
        foreach ([BillText::class, AmendmentText::class] as $model) {
            foreach ($model::query()->where('ai_score', '>=', $minimum)->where('ai_model', $verifier)->get(['ct', 'ai_score', 'ai_json']) as $text) {
                $ai = $text->ai_json ?: [];
                if (trim((string)(((array)($ai['citace'] ?? []))[0] ?? '')) !== '') {
                    $findings[$text->ct] = max($findings[$text->ct] ?? 0, $text->ai_score);
                }
            }
        }
        return $findings;
    }

    public function headline(int $ct): ?array
    {

        $candidates = [];
        $bill = BillText::query()->whereKey($ct)->whereNotNull('ai_at')->first();
        if ($bill !== null && (int)$bill['ai_score'] >= 4) {
            $candidates[] = ['score' => (int)$bill['ai_score'], 'json' => $bill['ai_json'], 'model' => $bill['ai_model'], 'source' => 'text návrhu'];
        }
        $pn = AmendmentText::query()->where('ct', $ct)->whereNotNull('ai_at')->orderByDesc('ai_score')->first();
        if ($pn !== null && (int)$pn['ai_score'] >= 4) {
            $candidates[] = ['score' => (int)$pn['ai_score'], 'json' => $pn['ai_json'], 'model' => $pn['ai_model'], 'source' => 'pozměňovací návrh č. ' . (int)$pn['cislo']];
        }
        usort($candidates, fn(array $a, array $b): int => $b['score'] <=> $a['score']);
        foreach ($candidates as $c) {
            $ai = $c['json'] ?: [];
            $title = trim((string)($ai['titulek'] ?? ''));
            $quote = trim((string)(((array)($ai['citace'] ?? []))[0] ?? ''));
            if ($title !== '' && $quote !== '') {
                return [
                    'title' => $title,
                    'perex' => trim((string)($ai['perex'] ?? '')),
                    'quote' => $quote,
                    'source' => $c['source'],

                    'verified' => (string)$c['model'] === AiReviewer::verifyModel(hlidacConfig()),
                ];
            }
        }
        return null;
    }

    private function flags(Bill $bill, ?array $surge = null, ?array $review = null): array
    {

        $surge ??= $this->surgeSet();
        $flags = [];
        if ($bill->typ_navrhu === 'poslanecký') {
            $flags[] = ['mp', 'Poslanecký návrh'];
        } elseif ($bill->typ_navrhu === 'senátní') {
            $flags[] = ['mp', 'Senátní návrh'];
        } elseif ($bill->typ_navrhu === 'krajský') {
            $flags[] = ['mp', 'Krajský návrh'];
        }
        if ($bill->p90ok) {
            $flags[] = ['fast', 'Schváleno v 1. čtení'];
        } elseif ($bill->p90) {
            $flags[] = ['fast', 'Navrženo § 90/2'];
        }
        if ($bill->zl) {
            $flags[] = ['cut', 'Zkrácená lhůta'];
        }
        if (isset($surge[$bill->ct])) {
            $flags[] = ['surge', 'Nával PN'];
        }
        $review ??= $this->reviews->statuses($bill->ct);
        $label = ReviewStatusService::label($review[$bill->ct] ?? []);
        if ($label !== null) {
            $flags[] = ['pending', $label];
        }
        return $flags;
    }

    private function surgeSet(): array
    {
        return array_fill_keys(Alert::query()->where('kind', 'surge')->currentPeriod()->distinct()->pluck('ct')->all(), true);
    }
}
