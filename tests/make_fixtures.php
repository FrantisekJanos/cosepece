<?php
// Sestaví testovací ZIPy ve formátu otevřených dat psp.cz (UNL, kódování windows-1250)
// z reálných řádků staženích 17. 11. 2026 (tisky 90, 311, 221, 78 a jeden tisk 9. období).

declare(strict_types=1);

function buildFixtures(string $outDir): void
{
    $files = [
        'tisky.zip' => [
            'tisky.unl' => <<<'UNL'
43185|2|62|78|0|2||174|6150|Babiš A.|Novela z. o státních svátcích|12.01.2026|13.01.2026|||Návrh poslance Andreje Babiše na vydání zákona, kterým se mění zákon č. 245/2000 Sb., o státních svátcích, o ostatních svátcích, o významných dnech a o dnech pracovního klidu, ve znění pozdějších předpisů|||1|||2026-01-13 09:43|||
43200|1|21|90|0|1||174||min. financí|Vl.n.z., kt. se mění některé z. v oblasti veřejných rozpočtů|27.01.2026|27.01.2026|||Vládní návrh zákona, kterým se mění některé zákony v oblasti veřejných rozpočtů||30|1||1|2026-01-27 15:05|||
43343|2|53|221|0|3||174||Vondráček R. a další|Novela z. o střetu zájmů|08.06.2026|09.06.2026|23:59||Návrh poslanců Radka Vondráčka, Davida Pražáka, Marie Pošarové a Filipa Turka na vydání zákona, kterým se mění zákon č. 159/2006 Sb., o střetu zájmů, ve znění pozdějších předpisů, a další související zákony||||||2026-06-09 10:20|||
43433|1|1104|311|0|1||174||min. financí|Novela z. o daních z příjmů|22.09.2026|22.09.2026|||Vládní návrh zákona, kterým se mění zákon č. 586/1992 Sb., o daních z příjmů, ve znění pozdějších předpisů||30||||2026-09-22 10:22|||
43434|8|0|311|1|0||174||výbor|Usnesení rozpočtového výboru k tisku 311|01.11.2026|01.11.2026|||Usnesení rozpočtového výboru k vládnímu návrhu zákona o daních z příjmů||||||2026-11-01 10:00|||
42069|2|62|1|0|3||173||Vondráček R. a další|N. z. o lobbování|11.10.2021|15.11.2021|||Návrh poslanců Radka Vondráčka a dalších na vydání zákona o lobbování||||||2021-11-15 09:46|||
UNL,
            'tisk_eklep.unl' => <<<'UNL'
KORNDQGFVQKI|52|26|1|43200|0|
KORNDY5H6KU4|718|26|1|43433|0|
UNL,
            'tisky_za.unl' => <<<'UNL'
43433|1|212296|8|Usnesení RV k tisku 311/0|Usnesení rozpočtového výboru č. 172 k vládnímu návrhu zákona o daních z příjmů|2026-10-27 10:00||||||||||
43433|3|212400|10|Pozměňovací a jiné návrhy  k tisku 311/0|Pozměňovací a jiné návrhy k vládnímu návrhu zákona, kterým se mění zákon č. 586/1992 Sb., o daních z příjmů|2026-11-05 10:24||||||||||
UNL,
            'hist.unl' => <<<'UNL'
210677|43185|2026-01-12 00:00||2118|||||||||||
210684|43185|2026-01-12 00:00||2119|||||||||||
210697|43185|2026-01-13 00:00||101|||||||||||
210713|43185|2026-01-20 00:00||102|||||||||||
210714|43185|2026-01-20 00:00||109|||||||||||
210736|43185|2026-01-28 00:00||2058|||25|2089|||||||
210901|43185|2026-03-03 00:00||170|58292|10|92||||||||
210902|43185|2026-03-03 00:00|86763|171|58292|10|92||||||||
210908|43185|2026-03-04 00:00||132|||||||||||
211073|43185|2026-03-26 00:00||135|||394||||||||
211074|43185|2026-03-26 12:25||138|||||||||||
211230|43185|2026-04-15 00:00|87174|139|58617|14|153||||||||
211241|43185|2026-04-17 00:00||143|||||||||||
211325|43185|2026-04-28 00:00||2077|||||||||||
211355|43185|2026-04-28 00:00||2078|||||||||||
211356|43185|2026-05-06 00:00||151|||||||12.05.2026|59|59||
210731|43200|2026-01-27 00:00||2116|||||||||||
210733|43200|2026-01-27 00:00||2117|||||||||||
210739|43200|2026-01-28 00:00||2015|||25|2150|||||||
210903|43200|2026-03-03 00:00||79|58272|10|93||||||||
210904|43200|||2126|||||||||||
210905|43200|2026-03-03 00:00||2013|58272|10|93||||||||
211209|43200|2026-04-15 00:00||2127||14|||||||||
211210|43200|2026-04-15 00:00||22|58540|14|||||||||
211212|43200|2026-04-15 00:00||2029|58540|14|||||||||
211224|43200|2026-04-15 00:00||2031|||151||||||7||
211227|43200|2026-04-16 08:30||2030|||||||||||
211301|43200|2026-04-23 00:00||2034|||||||||||
211440|43200|2026-05-13 00:00||2128||17|||||||||
211441|43200|2026-05-15 00:00|87430|33|58890|17|179||||||||
211462|43200|2026-05-26 00:00||38|||||||||||
211652|43200|2026-06-22 00:00||41|||472||||||||
211653|43200|2026-06-22 09:48||62|||||||||||
211866|43200|2026-07-08 00:00|88105|63|59311|24|248||||||||
211871|43200|2026-07-08 00:00||49|||||||||||
211945|43200|2026-07-22 00:00||51|||||||||||
211946|43200|2026-07-22 16:47||53|||||||||||
212058|43200|2026-08-26 00:00|88416|54|59396|29|266||||||||
212059|43200|2026-08-27 00:00||57|||||||28.08.2026|155|155||
211601|43343|2026-06-08 00:00||2118|||||||||||
211605|43343|2026-06-09 00:00||2119|||||||||||
211606|43343|2026-06-09 00:00||101|||||||||||
211775|43343|2026-06-30 00:00||102|||||||||||
211776|43343|2026-06-30 00:00||109|||||||||||
211803|43343|2026-07-01 00:00||2019|||82|2037|||||||
212169|43433|2026-09-22 00:00||2116|||||||||||
212170|43433|2026-09-22 00:00||2117|||||||||||
212263|43433|2026-10-01 00:00||2011|||98|2125|||||||
212295|43433|2026-10-13 00:00||2126||34|||||||||
212296|43433|2026-10-13 00:00||2013|60038|34|296||||||||
212583|43433|2026-11-13 00:00||2127||36|||||||||
212584|43433|2026-11-13 00:00||22||36|||||||||
212585|43433|2026-11-13 00:00||2029||36|||||||||
212586|43433|2026-11-13 00:00||2031|||355||||||7||
212587|43433|2026-11-13 16:38||2030|||||||||||
100001|42069|2021-11-15 00:00||2118|||||||||||
UNL,
            'prechody.unl' => <<<'UNL'
22|1413|14|23|1|
33|1432|34|22|1|
38|34|16|3|1|
41|16|26|24|1|
49|18|18|14|1|
51|18|19|7|1|
53|19|19|19|1|
54|19|20|20|1|
57|20|21|12|1|
62|26|26|19|1|
63|26|18|20|1|
79|22|12|41|1|
101|50|50|3|1|
102|50|63|30|1|
109|63|51|19|1|
132|70|57|3|1|
135|57|58|24|1|
138|58|58|19|1|
139|58|59|20|1|
143|59|59|14|1|
151|61|62|12|1|
170|64|65|40|1|
171|65|70|22|1|
2011|10|12|2|1|
2013|1412|13|18|1|
2015|10|22|2|1|
2019|51|53|2|1|
2029|14|1102|23|1|
2030|1102|1104|19|1|
2031|1102|1102|59|1|
2034|1104|32|71|1|
2058|51|64|2|1|
2077|59|69|13|1|
2078|69|61|4|1|
2116|0|1301|16|1|
2117|1301|10|19|1|
2118|0|1302|16|1|
2119|1302|50|19|1|
2126|12|1412|238|1|
2127|13|1413|238|1|
2128|32|1432|238|1|
UNL,
            'stavy.unl' => <<<'UNL'
10|7|1|po 10 dnech od rozeslání|10|0|
12|1|1|po 10 dnech od rozeslání poslancům|10|0|
13|3|1|60 dnů od přikázání|60|17|
14|4|1||||
16|12|1|do 30 dnů od doručení|-30|38|
18|8|1|do 15 dnů od postoupení|-15|49|
19|13|1|po 10 dnech od nepodepsání|10|51|
20|11|1||||
21|6|1||||
22|17|1|po 10 dnech od rozeslání poslancům|10|0|
26|14|1|po 10 dnech od rozeslání|10|62|
32|5|1||||
34|64|1||||
50|15|2|do 30 dnů od doručení|-30|101|
51|7|2|po 10 dnech od doručení stan. vlády|10|102|
53|1|2|po 10 dnech od rozesl. stan. vlády|9|109|
57|12|2|do 30 dnů od doručení|-30|132|
58|14|2|po 10 dnech od rozeslání|10|138|
59|8|2|do 15 dnů od postoupení|-15|143|
61|11|2||||
62|6|2||||
63|16|2||||
64|17|2|po 10 dnech od rozeslání poslancům|10|109|
65|18|2||||
69|20|2||||
70|64|2||||
1102|21|1|14 dnů od rozeslání pozměňovacích návrhů|14||
1104|54|1||||
1301|23|1||||
1302|23|2||||
1412|1|1||||
1413|3|1||||
1432|5|1||||
UNL,
            'typ_stavu.unl' => <<<'UNL'
0|START|
1|1. čtení|
2|2. čtení|
3|2. čtení - obecná rozprava|
4|2. čtení - podrobná rozprava|
5|3. čtení|
6|KONEC|
7|Organizační výbor|
8|Prezident|
11|Sbírka zákonů|
12|Senát|
13|Sněmovna (Prezident)|
14|Sněmovna (Senát)|
15|Vláda|
16|Vyjádření vlády|
17|1. čtení (§ 90, odst. 2) - ob. rozprava|
18|1. čtení (§ 90, odst. 2) - podr. rozprava|
20|PČR, PS|
21|Pozměňovací návrhy|
23|Přijímáno do PS|
54|Garanční výbor|
64|Odesíláno do Senátu|
UNL,
            'typ_akce.unl' => <<<'UNL'
2|doporučen|
3|doručen|
4|doručeno|
7|nepodepsal|
12|odeslán|
13|podepsal|
14|postoupen|
16|předložen|
18|přikázán|
19|rozeslán|
20|setrváno|
22|souhlas|
23|ukončena|
24|vrácen|
30|zasláno|
40|souhlas s RJ|
41|nesouhlas s RJ|
59|změna lhůty pozmň. návrhu|
71|projednávání ukončeno|
238|zahájeno|
UNL,
            'druh_tisku.unl' => <<<'UNL'
1|T|Vládní návrh zákona|
2|T|Návrh zákona|
3|T|Státní rozpočet|
8|Z|Usnesení výboru|
UNL,
        ],
        'sd.zip' => [
            'sd_dokument.unl' => <<<'UNL'
29771|174|771|13|||90|6687|2026-04-13 12:00:49|
29785|174|785|13|||90|7042|2026-04-14 08:32:28|
29857|174|857|13|||90|6205|2026-04-15 13:34:30|
30344|174|1344|13|||221|6524|2026-06-11 09:26:36|
30462|174|1462|13|||221|7042|2026-06-26 09:19:52|
31015|174|2015|13|||311|6687|2026-10-15 11:53:35|
31298|174|2298|13|||311|6998|2026-11-13 08:31:14|
31312|174|2312|13|||311|6205|2026-11-13 09:44:24|
31313|174|2313|13|||311|6205|2026-11-13 09:45:15|
31330|174|2330|13|||311|6998|2026-11-13 12:14:17|
29056|174|56|12|Návrh časového harmonogramu|rozpočtový výbor|||2025-11-14 14:14:09|
1|173|1|13|||1|6205|2021-12-01 10:00:00|
UNL,
        ],
        'poslanci.zip' => [
            'osoby.unl' => <<<'UNL'
6205|Ing.|Jurečka|Marian||15.03.1981|M|||
6524|Mgr. Bc.|Rakušan|Vít||16.06.1978|M|||
6687||Jakob|Jan||02.12.1982|M|||
6998|Ing.|Činčila|Benjamin||26.01.1996|M|||
7042|doc. Ing.|Sedmihradská|Lucie|Ph.D.|14.09.1974|Z|||
UNL,
            'organy.unl' => <<<'UNL'
173||11|PSP9|Poslanecká sněmovna|Chamber of Deputies|09.10.2021|08.10.2025||0|
174||11|PSP10|Poslanecká sněmovna|Chamber of Deputies|04.10.2025||174||
1750|174|1|ANO2011|Poslanecký klub ANO 2011|Political group ANO 2011|08.10.2025||1||
1751|174|1|ODS|Poslanecký klub ODS|Political group Civic Democratic Party|08.10.2025||2||
1745|174|1|Piráti|Poslanecký klub Piráti|Political group Pirates|05.10.2025||4|0|
UNL,
            'poslanec.unl' => <<<'UNL'
2137|6150|594|1104|174||||||||||1|
2051|6433|582|1290|174||||||||||1|
1967|4|581|1533|174||||||||||1|
UNL,
            'zarazeni.unl' => <<<'UNL'
6150|1750|0|2025-10-08 00||||
6433|1745|0|2025-10-05 00||||
4|1751|0|2025-10-08 00||||
UNL,
        ],
        // hlasování 10. období a body schůzí (tisk 78; hlasování 87152 je k jinému tisku se stejným číslem bodu)
        'schuze.zip' => [
            'schuze.unl' => <<<'UNL'
861|174|10|2026-03-03 14|2026-03-12 18|2026-03-12 18||
868|174|14|2026-04-14 14|2026-04-24 14|2026-04-24 14||
UNL,
            'bod_schuze.unl' => <<<'UNL'
58292|861|43185|1|5|Návrh poslance Andreje Babiše na vydání zákona, kterým se mění zákon č. 245/2000 Sb.|/sněmovní tisk 78/||0||1||0||p státní svátky|
58617|868|43185|24|1|Návrh zákona, kterým se mění zákon č. 245/2000 Sb.|/sněmovní tisk 78/2/||1|1|0||0||p státní svátky|
58618|868|43185|24|2|Návrh zákona, kterým se mění zákon č. 245/2000 Sb.|/sněmovní tisk 78/2/||0||0||0||p státní svátky|
UNL,
            'bod_stav.unl' => '0|projednán|',
            'schuze_stav.unl' => '',
        ],
        'hl-2025ps.zip' => [
            'hl2025s.unl' => <<<'UNL'
86762|174|10|49|5|03.03.2026|21:01|100|9|9|23|141|71|N|A|Novela z. o státních svátcích**||
86763|174|10|50|5|03.03.2026|21:02|105|7|6|32|150|76|N|A|Novela z. o státních svátcích**||
87152|174|14|49|1|14.04.2026|20:28|100|0|1|0|101|51|N|A|Novela z. o cenách**||
87173|174|14|70|2|15.04.2026|15:58|54|103|10|5|172|87|N|R|Novela z. o státních svátcích**||
87174|174|14|71|2|15.04.2026|16:00|107|34|7|25|173|101|N|A|Novela z. o státních svátcích**||
UNL,
            'hl2025h1.unl' => <<<'UNL'
2137|86762|A|
2137|86763|A|
2137|87152|@|
2137|87173|@|
2137|87174|@|
2051|86762|K|
2051|86763|K|
2051|87152|@|
2051|87173|@|
2051|87174|@|
1967|86762|@|
1967|86763|@|
1967|87152|@|
1967|87173|A|
1967|87174|K|
UNL,
            'hl2025h2.unl' => '',
            'omluvy.unl' => '',
            'zmatecne.unl' => '',
        ],
    ];

    if (!is_dir($outDir)) {
        mkdir($outDir, 0775, true);
    }
    // otevřená data Úřadu vlády (ODok) – zkrácené reálné záznamy tisků 90, 311, 221 a 78
    $r = fn(string $pid, string $type, string $title, string $at, ?string $finish, int $reviewers, string $reason, ?string $gov) => [
        'title' => $title, 'pid' => $pid, 'type' => ['id' => $type, 'name' => $type], 'date_authorized' => $at,
        'author' => ['organization' => ['id' => 'X', 'name' => 'X']], 'status' => ['id' => 'B', 'name' => 'B - signováno'],
        'reason' => $reason, 'notes' => '', 'government_program' => $gov !== null ? ['date' => $gov] : [],
        'review_process' => $finish !== null ? ['date_finish' => $finish, 'reviewers' => array_fill(0, $reviewers, ['id' => 'M', 'name' => 'M'])] : null,
    ];
    @mkdir($outDir . '/odok', 0775, true);
    file_put_contents($outDir . '/odok/requests_2025.json', json_encode(['requests' => [
        $r('KORNDQAAAAAA', 'NZ', 'Vládní návrh zákona, kterým se mění některé zákony v oblasti veřejných rozpočtů', '2025-12-01T09:00:00Z', '2025-12-15T22:59:59Z', 23, 'Plán legislativních prací', null),
    ]], JSON_UNESCAPED_UNICODE));
    file_put_contents($outDir . '/odok/requests_2026.json', json_encode(['requests' => [
        $r('KORNDQGFVQKI', 'NZ', 'Návrh zákona, kterým se mění některé zákony v oblasti veřejných rozpočtů', '2026-01-20T09:00:00Z', null, 0, 'Plán legislativních prací', '2026-01-26T10:00:00Z'),
        $r('KORNDY5H6KU4', 'NZ', 'Návrh zákona, kterým se mění zákon č. 586/1992 Sb., o daních z příjmů', '2026-09-21T12:59:36Z', null, 0, 'Materiál je předkládán mimo Plán legislativních prací vlády na rok 2026 z důvodu mimořádné situace.', '2026-09-21T10:00:00Z'),
        $r('ALBSDUW89Q2L', 'PNZ', 'Návrh poslanců na vydání zákona o střetu zájmů (sněmovní tisk č. 221)', '2026-06-10T08:00:00Z', '2026-06-18T21:59:59Z', 7, 'Žádost o stanovisko', '2026-06-29T10:00:00Z'),
        $r('ALBSDQG99B8P', 'PNZ', 'Návrh poslance na vydání zákona o státních svátcích (sněmovní tisk č. 78)', '2026-01-19T08:00:00Z', null, 0, 'Žádost o stanovisko', null),
        $r('ALBSOLD00001', 'PNZ', 'Návrh poslance z minulého období (sněmovní tisk č. 90)', '2024-03-01T08:00:00Z', '2024-03-10T21:59:59Z', 3, 'Žádost o stanovisko', null),
    ]], JSON_UNESCAPED_UNICODE));
    foreach ($files as $zipName => $entries) {
        $zip = new ZipArchive();
        $path = $outDir . '/' . $zipName;
        @unlink($path);
        $zip->open($path, ZipArchive::CREATE);
        foreach ($entries as $name => $content) {
            // zdroj je ve windows-1250, stejně jako na psp.cz
            $zip->addFromString($name, iconv('UTF-8', 'CP1250', $content . "\n"));
        }
        $zip->close();
    }
}

buildFixtures($argv[1] ?? __DIR__ . '/fixtures/src');
echo "Fixtures OK\n";
