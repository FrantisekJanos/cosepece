<?php

declare(strict_types=1);

namespace App\Services\Pages;

use App\Models\Amendment;
use App\Models\AmendmentText;
use App\Models\Bill;
use App\Models\BillText;
use App\Models\Meta;
use App\Services\BillPresentationService;
use Hlidac\View;

final class DocumentReaderService
{
    public function __construct(private BillPresentationService $presentation)
    {
    }

    public function data(int $ct, ?int $amendmentId): ?array
    {
        $bill = Bill::query()->withCount('votes')->with(['text', 'amendments' => fn ($q) => $q->with('text')->orderByDesc('cas')])->where('ct', $ct)->first();
        if ($bill === null) {
            return null;
        }
        $selected = $amendmentId === null ? null : $bill->amendments->firstWhere('id_dokument', $amendmentId);
        if ($amendmentId !== null && $selected === null) {
            return null;
        }
        $document = $selected === null ? $bill->text : $selected->text;
        $period = (int)(Meta::query()->whereKey('obdobi_cislo')->value('v') ?? 10);

        $entries = [$this->entry(null, $bill->text)];
        foreach ($bill->amendments as $amendment) {
            $entries[] = $this->entry($amendment, $amendment->text);
        }
        $position = 0;
        foreach ($entries as $index => $entry) {
            if ($entry['pn'] === $amendmentId) {
                $position = $index;
            }
        }

        return [
            'title' => ($selected === null ? 'Návrh zákona ' . $ct . '/0' : 'Pozměňovací návrh č. ' . $selected->cislo) . ' – tisk ' . $ct,
            'active' => 'tisky',
            'bill' => $bill,
            't' => $this->presentation->present($bill),
            'selected' => $selected,
            'document' => $document,
            'analysis' => $document === null ? null : $this->analysis($document, $selected === null),
            'entries' => $entries,
            'previous' => $position > 0 ? $entries[$position - 1] : null,
            'next' => $entries[$position + 1] ?? null,
            'period' => $period,
        ];
    }

    private function entry(?Amendment $amendment, BillText|AmendmentText|null $text): array
    {
        $analysis = $text === null ? null : $this->analysis($text, $amendment === null);
        return [
            'pn' => $amendment?->id_dokument,
            'number' => $amendment?->cislo,
            'author' => $amendment?->osoba,
            'time' => $amendment?->cas?->format('Y-m-d H:i:s'),
            'word' => (bool)($text?->docx_url),
            'badges' => $analysis['badges'] ?? [],
            'ai_score' => $analysis['ai_score'] ?? null,
        ];
    }

    private function analysis(BillText|AmendmentText $text, bool $isBill): array
    {
        $ai = $text->ai_json ?: null;
        $signals = $text->signals ?: [];
        $aiScore = $text->ai_score;
        $badges = [];
        if ($ai !== null && $aiScore !== null) {
            $badges[] = [$aiScore >= 6 ? 'cut' : 'surge', 'AI ' . $aiScore . '/10'];
        }
        if (!empty($ai['zpetna_ucinnost']) || !empty($signals['retro']) || !empty($signals['retro_deadline'])) {
            $badges[] = ['cut', 'Zpětné použití'];
        }
        if (!empty($ai['zkraceni_lhut']) || !empty($signals['deadline'])) {
            $badges[] = ['cut', 'Změna lhůt'];
        }
        if (!empty($ai['prospech_uzke_skupiny'])) {
            $badges[] = ['cut', 'Prospěch úzké skupiny'];
        }
        if (isset($ai['souvisi_s_predmetem']) && $ai['souvisi_s_predmetem'] === false) {
            $badges[] = ['cut', 'Nesouvisí s předmětem'];
        }
        if (!empty($signals['other_laws']) && is_array($signals['other_laws'])) {
            $badges[] = ['cut', 'Přílepek: ' . implode(', ', array_map('strval', $signals['other_laws']))];
        }
        if (!empty($signals['large'])) {
            $badges[] = ['surge', 'Rozsáhlý'];
        }

        $quotes = array_values(array_filter(array_map(
            fn ($quote): string => is_string($quote) ? trim($quote) : '',
            (array)(!empty($ai['citace']) ? $ai['citace'] : array_merge((array)($signals['deadline'] ?? []), (array)($signals['retro'] ?? []))),
        )));

        return [
            'ai' => $ai,
            'ai_score' => $ai !== null ? $aiScore : null,
            'ai_round' => $ai !== null ? View::aiRounds($text->getAttributes()) : '',
            'badges' => $badges,
            'quotes' => array_slice($quotes, 0, 4),
            'quote_source' => !empty($ai['citace']) ? 'ai' : 'rules',
            'fetched_at' => $text->fetched_at?->format('Y-m-d H:i:s'),
            'is_bill' => $isBill,
        ];
    }
}
