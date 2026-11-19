<?php

declare(strict_types=1);

namespace App\Services\Pages;

use Hlidac\Scorer;

final class MethodologyService
{
    public function data(): array
    {
        $w = Scorer::weights(hlidacConfig());
        $ai = (array)hlidacConfig('ai', []);
        $aiOn = !empty($ai['enabled']);
        $verifyMin = (int)($ai['verify_min'] ?? 6);

        $groups = [
            ['Postup projednávání', 'cap_procedure', [
                [$w['mp_bill'], 'Poslanecký návrh: neprošel meziresortním připomínkovým řízením (ministerstva, legislativní rada vlády, odborná veřejnost).'],
                [$w['senate_bill'], 'Senátní návrh: také bez meziresortního připomínkového řízení.'],
                [$w['region_bill'], 'Návrh krajského zastupitelstva nebo hlavního města Prahy: také bez meziresortního připomínkového řízení.'],
                [$w['p90_requested'], 'Navrženo schválit už v 1. čtení podle § 90 odst. 2 jednacího řádu.'],
                [$w['p90_passed'], 'Schváleno už v 1. čtení podle § 90 odst. 2, bez 2. a 3. čtení (místo bodů za pouhý návrh).'],
                [$w['deadline_cut'], 'Zkrácená lhůta při projednávání: lhůta pro výbory kratší než 60 dní, nebo Sněmovna lhůtu změnila během projednávání.'],
                [$w['short_gap'], 'Mezi pozměňovacími návrhy a zahájením 3. čtení méně než ' . (int)$w['short_gap_days'] . ' dní.'],
                [$w['late_pn'], 'Aspoň ' . (int)$w['late_pn_min'] . ' pozměňovací návrhy podané v den konce podrobné rozpravy.'],
                [$w['surge'], 'Nával pozměňovacích návrhů: najednou jich přibylo hodně.'],
            ]],
            ['Obsah textu návrhu a pozměňovacích návrhů', 'cap_content', [
                [$w['pn_other_law'], 'Přílepek: pozměňovací návrh mění jiný zákon, než který se projednává.'],
                [$w['pn_large'], 'Rozsáhlá nová úprava zavedená pozměňovacím návrhem.'],
                [$w['retro_deadline'], 'Text mění lhůtu pro vymáhání nebo kontrolu a výslovně ji použije i na případy z doby před účinností.'],
                [$w['pn_many_flagged'], 'Aspoň 3 problematické pozměňovací návrhy.'],
                ['0–' . (10 * (int)$w['ai_multiplier']), 'Rozbor textu návrhu zákona umělou inteligencí: závažnost 0–10 × ' . (int)$w['ai_multiplier'] . ' (počítá se od závažnosti 4).'],
                ['0–' . (10 * (int)$w['ai_multiplier']), 'Rozbor nejzávažnějšího pozměňovacího návrhu umělou inteligencí: závažnost 0–10 × ' . (int)$w['ai_multiplier'] . ' (počítá se od závažnosti 4).'],
            ]],
            ['Kontext', 'cap_context', [
                [$w['topic'], 'Citlivá oblast: ' . implode(', ', array_keys((array)$w['topics'])) . '.'],
                [$w['omnibus'], 'Souhrnná novela měnící více zákonů najednou.'],
                [$w['watched'], 'Zákon, který web výslovně sleduje.'],
            ]],
        ];

        $title = 'Jak hodnotíme';
        $active = 'metodika';
        $description = 'Jak se počítá skóre závažnosti legislativního postupu: signály, body, stropy a úrovně.';

        return compact('title', 'active', 'description', 'w', 'ai', 'aiOn', 'verifyMin', 'groups');
    }
}
