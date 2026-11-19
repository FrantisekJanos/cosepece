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

    private function headline(int $ct): ?array
    {

        $candidates = [];
        $bill = BillText::query()->whereKey($ct)->whereNotNull('ai_at')->first();
        if ($bill !== null) {
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
            if ($title !== '') {
                return [
                    'title' => $title,
                    'perex' => trim((string)($ai['perex'] ?? '')),
                    'quote' => (string)(($ai['citace'] ?? [])[0] ?? ''),
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
        return array_fill_keys(Alert::query()->where('kind', 'surge')->distinct()->pluck('ct')->all(), true);
    }
}
