<?php

declare(strict_types=1);

namespace App\Services\Pages;

use App\Models\AiRevision;
use App\Models\Meta;
use App\Models\TextRevision;
use Hlidac\Diff;
use Hlidac\View;

final class RevisionService
{
    public function counts(string $kind, int $ct, int $period): array
    {
        $counts = [];
        foreach (['text' => TextRevision::class, 'ai' => AiRevision::class] as $type => $model) {
            foreach ($model::query()->where('druh', $kind)->where('obdobi', $period)->where('ct', $ct)
                ->select('ref_id')->selectRaw('COUNT(*) AS n')->groupBy('ref_id')->get() as $record) {
                $id = (int)$record->ref_id;
                $counts[$id] ??= ['text' => 0, 'ai' => 0];
                $counts[$id][$type] = (int)$record->n;
            }
        }
        return $counts;
    }

    public function data(string $kind, int $id, ?int $period, ?int $older, ?int $newer): ?array
    {
        $current = (int)(Meta::query()->whereKey('obdobi_cislo')->value('v') ?? 0);
        $obdobi = $period ?? $current;
        $textRows = TextRevision::query()->where('druh', $kind)->where('obdobi', $obdobi)->where('ref_id', $id)->orderBy('verze')->get(['verze', 'ct', 'cislo', 'docx_url', 'chars', 'hash', 'fetched_at', 'created_at', 'zdroj']);
        $texts = $textRows->map->getAttributes()->all();
        $analyses = AiRevision::query()->where('druh', $kind)->where('obdobi', $obdobi)->where('ref_id', $id)->orderBy('verze')->get()
            ->map(fn (AiRevision $revision): array => $revision->getAttributes() + ['ai' => $revision->ai_json ?: []])->all();
        if ($texts === [] && $analyses === []) {
            return null;
        }
        $ct = (int)(($texts !== [] ? end($texts) : end($analyses))['ct']);
        $cislo = $texts !== [] ? (int)end($texts)['cislo'] : 0;
        $past = $obdobi !== $current;
        $billUrl = $past ? View::pspTiskUrl($ct, $obdobi) : route('bill', ['ct' => $ct], false);
        $subject = $kind === 'tisk' ? 'textu návrhu zákona' : 'pozměňovacího návrhu' . ($cislo > 0 ? ' č. ' . $cislo : '');
        $numbers = array_map(fn (array $row): int => (int)$row['verze'], $texts);
        $b = $newer ?? (end($numbers) ?: 0);
        $a = $older ?? ($numbers[count($numbers) - 2] ?? 0);
        $diff = null;
        if (count($numbers) >= 2 && in_array($a, $numbers, true) && in_array($b, $numbers, true) && $a !== $b) {
            $versions = TextRevision::query()->where('druh', $kind)->where('obdobi', $obdobi)->where('ref_id', $id)
                ->whereIn('verze', [$a, $b])->get(['verze', 'text'])->keyBy('verze');
            $diff = Diff::render((string)$versions[$a]->text, (string)$versions[$b]->text);
        }
        $versionByHash = [];
        foreach ($texts as $text) {
            $versionByHash[(string)$text['hash']] ??= (int)$text['verze'];
        }
        $title = 'Historie ' . $subject . ' – tisk ' . $ct;
        $active = 'tisky';
        $description = 'Předchozí znění a rozbory ' . $subject . ' k tisku ' . $ct . '.';
        $og = ['url' => route('revisions', ['druh' => $kind, 'id' => $id] + ($past ? ['o' => $obdobi] : []), false)];
        return compact('title', 'active', 'description', 'og', 'kind', 'id', 'obdobi', 'texts', 'analyses', 'ct', 'past', 'billUrl', 'subject', 'numbers', 'a', 'b', 'diff', 'versionByHash');
    }
}
