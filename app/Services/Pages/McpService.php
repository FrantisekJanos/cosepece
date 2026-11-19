<?php

declare(strict_types=1);

namespace App\Services\Pages;

final class McpService
{
    public function data(): array
    {
        $mcpUrl = (string)hlidacConfig('mcp_url', '');
        if ($mcpUrl === '') {
            $host = (string)parse_url(siteUrl(), PHP_URL_HOST);
            $mcpUrl = $host !== '' ? 'https://mcp.' . preg_replace('~^www\.~', '', $host) . '/mcp' : '';
        }
        $issues = (string)hlidacConfig('issues_url', '');


        $tools = [
            'Návrhy zákonů a zákony' => [
                ['get_tisk', 'detail sněmovního tisku: předkladatel, fáze, skóre s důvody, měněné zákony, číslo ve Sbírce'],
                ['seznam_tisku', 'seznam návrhů s hledáním a řazením podle skóre nebo data'],
                ['tisky_menici_zakon', 'tisky a pozměňovací návrhy, které mění daný zákon (např. 586/1992)'],
                ['tisk_podle_sbirky', 'tisk, který byl vyhlášen ve Sbírce pod daným číslem'],
            ],
            'Texty návrhů a pozměňovacích návrhů' => [
                ['osnova_textu', 'osnova textu (části, články, paragrafy, body), původní i schválené znění'],
                ['get_cel', 'jeden celek textu, např. „§ 17c“ nebo „Čl. I“'],
                ['hledej_v_textech', 'fulltext v textech návrhů a PN, i podle měněného zákona'],
            ],
            'Pozměňovací návrhy' => [
                ['pn_k_tisku', 'pozměňovací návrhy k tisku s autory a hodnocením AI'],
                ['get_pn', 'detail pozměňovacího návrhu včetně projevů, které ho zmiňují'],
            ],
            'Rozprava a projevy (stenoprotokoly)' => [
                ['rozprava', 'celá rozprava k tisku v pořadí, jak zazněla, s reakcemi a zmíněnými PN'],
                ['projevy_k_tisku', 'rejstřík projevů k tisku s filtry podle řečníka, klubu a data'],
                ['projevy_recnika', 'projevy jedné osoby, i s hledanými slovy'],
                ['get_projev', 'celý text projevu s historií verzí a stavem autorizace'],
                ['hledej_v_projevech', 'fulltextové hledání ve všech projevech'],
                ['zmeny_projevu', 'projevy, jejichž text se po zveřejnění změnil (autorizace, korektury)'],
                ['najdi_osobu', 'poslanec nebo člen vlády podle jména'],
                ['program_schuze', 'body pořadu schůze Sněmovny'],
            ],
            'Hlasování' => [
                ['hlasovani_k_tisku', 'všechna hlasování k tisku s výsledky a rozdělením podle klubů'],
                ['get_hlasovani', 'detail jednoho hlasování a projevy, které se k němu vztahují'],
            ],
            'Ověření a stav' => [
                ['over_citaci', 'ověří, že doslovná citace opravdu zazněla v projevu nebo stojí v textu zákona či PN'],
                ['stav_dat', 'stav dat a poslední synchronizace'],
            ],
        ];
        $toolCount = array_sum(array_map('count', $tools));

        return compact('mcpUrl', 'issues', 'tools', 'toolCount') + [
            'title' => 'Pro AI asistenty (MCP)', 'active' => 'mcp',
            'description' => 'Napojení AI asistentů na data Co se peče přes MCP: návrhy zákonů, texty, pozměňovací návrhy, rozpravy a hlasování.',
        ];
    }
}
