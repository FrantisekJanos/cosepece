<?php

declare(strict_types=1);

namespace App\Services\Pages;

use Hlidac\Scorer;

final class MethodologyService
{
    public function __construct(private \App\Services\AiReliabilityService $reliability)
    {
    }

    public function data(): array
    {
        $rel = $this->reliability->statistics();
        $rc = \Hlidac\AiReviewer::repeatConfig(hlidacConfig());
        $pct = fn(int $a, int $n): string => $n > 0 ? (string)round(100 * $a / $n) . ' %' : '–';
        $w = Scorer::weights(hlidacConfig());
        $ai = (array)hlidacConfig('ai', []);
        $aiOn = !empty($ai['enabled']);
        $verifyMin = (int)($ai['verify_min'] ?? 6);

        $groups = [
            ['Postup projednávání', 'cap_procedure', [
                [$w['mp_bill'], 'Poslanecký návrh, který postupuje projednáváním (2. čtení a dál nebo schválení v 1. čtení): text nepřipravovalo ministerstvo v meziresortním připomínkovém řízení (ministerstva, Legislativní rada vlády, odborná veřejnost).'],
                [$w['mp_bill_early'], 'Poslanecký návrh, který zatím nepostoupil do 2. čtení (většina takových návrhů nikdy neprojde).'],
                [$w['senate_bill'], 'Senátní návrh: text také nepřipravovalo ministerstvo v připomínkovém řízení.'],
                [$w['region_bill'], 'Návrh krajského zastupitelstva nebo hlavního města Prahy: text také nepřipravovalo ministerstvo v připomínkovém řízení.'],
                [$w['p90_requested'], 'Navrženo schválit už v 1. čtení podle § 90 odst. 2 jednacího řádu (jen u tisku, který se ještě projednává).'],
                [$w['p90_passed'], 'Schváleno už v 1. čtení podle § 90 odst. 2, bez 2. a 3. čtení (místo bodů za pouhý návrh).'],
                [$w['deadline_cut'], 'Zkrácená lhůta při projednávání: lhůta pro výbory kratší než 60 dní, nebo Sněmovna lhůtu změnila během projednávání.'],
                [$w['short_gap'], 'Mezi pozměňovacími návrhy a zahájením 3. čtení méně než ' . (int)$w['short_gap_days'] . ' dní.'],
                [$w['late_pn'], 'V den konce podrobné rozpravy podáno aspoň ' . (int)$w['late_pn_min'] . ' pozměňovacích návrhů a zároveň aspoň ' . (int)round(100 * (float)$w['late_pn_share']) . ' % všech pozměňovacích návrhů k tisku.'],
                [$w['surge'], 'Nával pozměňovacích návrhů: najednou jich přibylo hodně.'],
            ]],
            ['Obsah textu návrhu a pozměňovacích návrhů', 'cap_content', [
                [$w['pn_other_law'], 'Přílepek: pozměňovací návrh mění jiný zákon, než který se projednává.'],
                [$w['pn_large'], 'Rozsáhlá nová úprava zavedená pozměňovacím návrhem.'],
                [$w['retro_deadline'], 'Text mění lhůtu pro vymáhání nebo kontrolu a výslovně ji použije i na případy z doby před účinností.'],
                [$w['pn_many_flagged'], 'Aspoň ' . (int)$w['pn_many_min'] . ' problematické pozměňovací návrhy a zároveň aspoň ' . (int)round(100 * (float)$w['pn_many_share']) . ' % všech pozměňovacích návrhů k tisku (od ' . (int)$w['pn_many_abs'] . ' problematických stačí samotný počet). Problematický je návrh, který splní pravidlo (přílepek, rozsáhlá úprava), nebo ho umělá inteligence ohodnotí aspoň ' . (int)$w['pn_flag_ai_min'] . '/10.'],
                ['0–' . (10 * (int)$w['ai_multiplier']), 'Rozbor textu návrhu zákona umělou inteligencí: závažnost 0–10 × ' . (int)$w['ai_multiplier'] . ' (počítá se od závažnosti 4).'],
                ['0–' . (10 * (int)$w['ai_multiplier']), 'Rozbor nejzávažnějšího pozměňovacího návrhu umělou inteligencí: závažnost 0–10 × ' . (int)$w['ai_multiplier'] . ' (počítá se od závažnosti 4). Čím víc pozměňovacích návrhů, tím větší šance, že jeden vyjde závažně náhodou: plné body jen tehdy, když podobně závažné (aspoň ' . (int)$w['ai_pn_full_min'] . '/10, nebo jako ten nejhorší) jsou aspoň ' . (int)$w['ai_pn_full_count'] . ' návrhy, nebo aspoň ' . (int)round(100 * (float)$w['ai_pn_full_share']) . ' % všech; ojedinělý nález dostane ' . (int)round(100 * (float)$w['ai_pn_single_factor']) . ' % bodů.'],
            ]],
            ['Kontext', 'cap_context', [
                [$w['topic'], 'Citlivá oblast podle názvu tisku: ' . implode(', ', array_keys((array)$w['topics'])) . '. Jde o redakční výběr oblastí, kde mají procedurální zkratky větší dopad; proto jen malý příplatek, ne hodnocení obsahu.'],
                [$w['omnibus'], 'Souhrnná novela měnící více zákonů najednou.'],
                [$w['watched'], 'Zákon, který web výslovně sleduje.'],
            ]],
        ];

        $title = 'Jak hodnotíme';
        $active = 'metodika';
        $description = 'Jak se počítá skóre závažnosti legislativního postupu: signály, body, stropy a úrovně.';

        return compact('rel', 'rc', 'pct', 'title', 'active', 'description', 'w', 'ai', 'aiOn', 'verifyMin', 'groups');
    }
}
