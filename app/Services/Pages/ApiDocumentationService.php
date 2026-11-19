<?php

declare(strict_types=1);

namespace App\Services\Pages;

use App\Http\DTO\PublicApiRequestDTO;
use App\Models\Bill;
use App\Models\Vote;
use App\Services\PublicApiService;
use Hlidac\Scorer;

final class ApiDocumentationService
{
    public function __construct(private PublicApiService $api)
    {
    }

    public function data(): array
    {
        $api = $this->api;
        $base = siteUrl('/api');
        $w = Scorer::weights(hlidacConfig());


        $trim = function ($value, int $depth = 0) use (&$trim) {
            if (is_string($value)) {
                return mb_strlen($value) > 140 ? mb_substr($value, 0, 137) . '…' : $value;
            }
            if (!is_array($value)) {
                return $value;
            }
            $isList = array_is_list($value);
            $out = [];
            foreach ($value as $k => $v) {
                if ($isList && $k >= ($depth === 0 ? 1 : 2)) {
                    break;
                }
                $out[$k] = $trim($v, $depth + 1);
            }
            return $out;
        };
        $example = function (array $query) use ($api, $trim): string {
            [, $body] = $api->handle(PublicApiRequestDTO::fromRequest(\Illuminate\Http\Request::create('/api', 'GET', $query)));
            foreach (['tisky', 'upozorneni', 'pripravovane'] as $list) {
                if (isset($body[$list])) {
                    $body[$list] = array_slice($body[$list], 0, 1);
                }
            }
            if (isset($body['tisk'])) {
                foreach (['hlasovani', 'upozorneni', 'duvody'] as $list) {
                    $body['tisk'][$list] = array_slice($body['tisk'][$list] ?? [], 0, 1);
                }
                if (isset($body['tisk']['hlasovani'][0]['kluby'])) {
                    $body['tisk']['hlasovani'][0]['kluby'] = array_slice($body['tisk']['hlasovani'][0]['kluby'], 0, 1);
                }
            }
            return json_encode($trim($body, 1), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
        };

        $exampleCt = (int)(Bill::query()->whereHas('steps', fn ($q) => $q->whereIn('id_hlasovani', Vote::query()->select('id_hlasovani')))->orderByDesc('score')->value('ct') ?: 221);

        $endpoints = [
            'tisky' => ['Tisky', '/api', 'Návrhy zákonů aktuálního volebního období seřazené od poslední změny, se skóre a důvody.',
                ['since', 'min_score', 'zakon', 'limit'], ['limit' => '3']],
            'tisk' => ['Detail tisku', '/api?tisk={číslo}', 'Jeden tisk: vše jako v seznamu a navíc hlasování ve Sněmovně (i podle klubů), upozornění a stav rozboru textů.',
                [], ['tisk' => (string)$exampleCt]],
            'upozorneni' => ['Upozornění', '/api?upozorneni=1', 'Upozornění na kroky v projednávání (nové pozměňovací návrhy, § 90/2, zkrácené lhůty, schválení, vyhlášení), od nejnovějších. Bez min_score jen od úrovně „Pozor“ (' . (int)$w['level_watch'] . ' bodů).',
                ['since', 'min_score', 'zakon', 'ct', 'limit'], ['upozorneni' => '1', 'limit' => '3']],
            'pripravovane' => ['Připravované zákony', '/api?pripravovane=1', 'Návrhy zákonů, které připravuje vláda a Sněmovna je ještě nedostala, od nejpokročilejší fáze. Bez filtrů.',
                [], ['pripravovane' => '1']],
        ];
        $filters = [
            'since' => ['YYYY-MM-DD', 'jen tisky změněné / upozornění od tohoto dne (včetně)', 'since=2026-11-01'],
            'min_score' => ['0–100', 'jen se skóre aspoň tolik (alias min)', 'min_score=50'],
            'zakon' => ['číslo/rok', 'jen tisky, které mění daný zákon (alias law)', 'zakon=159/2006'],
            'ct' => ['číslo', 'jen upozornění k tomuto tisku', 'ct=221'],
            'limit' => ['1–500', 'nejvýš tolik položek (výchozí 100)', 'limit=20'],
        ];
        $fields = [
            'Tisk' => [
                ['ct', 'číslo', 'číslo sněmovního tisku (v každém volebním období se čísluje znovu od 1)'],
                ['obdobi', 'číslo', 'volební období (10 = od roku 2025)'],
                ['nazev', 'text', 'krátký název podle psp.cz'],
                ['uplny_nazev', 'text', 'úplný název návrhu'],
                ['typ_navrhu', 'text', 'předkladatel: vládní, poslanecký, senátní, krajský'],
                ['navrhovatel', 'text', 'navrhovatel podle psp.cz'],
                ['faze', 'text', 'fáze projednávání (číselník níže)'],
                ['predlozeno', 'datum', 'den předložení Sněmovně (YYYY-MM-DD)'],
                ['posledni_zmena', 'datum a čas', 'poslední krok v historii tisku (YYYY-MM-DD HH:MM:SS)'],
                ['zakony', 'pole textů', 'čísla zákonů, které návrh mění (např. 159/2006)'],
                ['skore', 'číslo', 'skóre závažnosti postupu 0–100'],
                ['uroven', 'text', 'major (od ' . (int)$w['level_major'] . '), watch (od ' . (int)$w['level_watch'] . '), normal'],
                ['duvody', 'pole objektů', 'body skóre: klic (číselník níže), body, popis (čitelný důvod v češtině)'],
                ['priznaky', 'pole textů', 'štítky jako na webu (Poslanecký návrh, Zkrácená lhůta…)'],
                ['url', 'adresa', 'detail tisku na tomto webu'],
                ['psp_url', 'adresa', 'historie tisku na psp.cz'],
            ],
            'Detail tisku (navíc)' => [
                ['hlasovani', 'pole objektů', 'id, datum, vysledek (přijato / zamítnuto), pro, proti, zdrzel, kvorum, kluby (klub, pro, proti, zdrzel_nehlasoval, nepritomen), psp_url'],
                ['upozorneni', 'pole objektů', 'upozornění k tisku (pole jako níže)'],
                ['rozbor_textu', 'objekt', 'posouzeno / celkem: kolik textů (návrh a pozměňovací návrhy) už posoudil rozbor AI'],
            ],
            'Upozornění' => [
                ['id', 'text', 'jednoznačný klíč upozornění (vhodný k odstranění duplicit)'],
                ['typ', 'text', 'druh upozornění (číselník níže)'],
                ['ct', 'číslo nebo null', 'číslo tisku; u připravovaného zákona null'],
                ['obdobi', 'číslo', 'volební období tisku'],
                ['datum', 'datum a čas', 'kdy událost nastala'],
                ['titulek', 'text', 'nadpis upozornění'],
                ['text', 'text', 'krátký popis'],
                ['skore', 'číslo', 'skóre tisku + příplatek za druh upozornění'],
                ['url', 'adresa', 'odkaz na web'],
            ],
            'Připravovaný zákon' => [
                ['pid', 'text', 'identifikátor materiálu v eKLEP/VeKLEP'],
                ['nazev', 'text', 'název materiálu'],
                ['resort', 'text', 'ministerstvo nebo úřad, který návrh připravuje'],
                ['faze', 'text', 'pripominky, po_pripominkach, vlada, schvaleno'],
                ['stav_odok', 'text', 'původní stav v datech Úřadu vlády'],
                ['zalozeno', 'datum', 'založení první verze materiálu'],
                ['zmeneno', 'datum', 'poslední změna kterékoli verze'],
                ['pripominky', 'objekt nebo null', 'od, do, dni, mist (počet připomínkových míst); null = připomínkové řízení nenalezeno'],
                ['vlada_projednala', 'datum nebo null', 'schůze vlády'],
                ['mimo_plan', 'ano/ne', 'předloženo mimo plán legislativních prací'],
                ['vyjimka', 'ano/ne', 'udělena výjimka (např. z povinnosti hodnocení dopadů)'],
                ['popis', 'text', 'o čem návrh je (od předkladatele)'],
                ['duvod', 'text', 'důvod předložení (od předkladatele)'],
                ['veklep_url', 'adresa', 'materiál ve VeKLEP: texty, připomínky, stanovisko Legislativní rady vlády'],
            ],
        ];
        $alertKinds = [
            'newbill' => 'nový poslanecký nebo senátní návrh',
            'p90' => 'navrženo nebo schváleno podle § 90 odst. 2 (bez 2. a 3. čtení)',
            'cut' => 'zkrácení lhůty pro projednání',
            'pn' => 'nové pozměňovací návrhy',
            'surge' => 'nával pozměňovacích návrhů najednou',
            'passed' => 'schváleno Sněmovnou',
            'law' => 'vyhlášeno ve Sbírce zákonů',
        ];
        foreach (array_keys(\Hlidac\AlertBuilder::UPCOMING_KINDS) as $kind) {
            $alertKinds[$kind] = \Hlidac\View::KIND_LABELS[$kind][1];
        }
        $phases = ['Podáno', 'Organizační výbor', 'U vlády', 'Stanovisko vlády', '1. čtení', '2. čtení', '3. čtení', 'Schváleno Sněmovnou',
            'V Senátu', 'Sněmovna po Senátu', 'U prezidenta', 'Sněmovna po vetu', 'Vyhlášeno', 'Vzat zpět', 'Zamítnuto', 'Vráceno předkladateli'];

        $toc = [
            'zaklady' => 'Základy',
            'adresy' => 'Adresy',
            'filtry' => 'Filtry',
            'pole' => 'Popis polí',
            'ciselniky' => 'Číselníky',
            'chyby' => 'Chyby',
            'limity' => 'Limity a slušné chování',
            'mcp' => 'Pro AI asistenty (MCP)',
            'rss' => 'RSS',
            'priklady' => 'Příklady kódu',
            'podminky' => 'Zdroje a podmínky',
        ];

        return compact('base', 'w', 'example', 'exampleCt', 'endpoints', 'filters', 'fields', 'alertKinds', 'phases', 'toc') + [
            'title' => 'Data a API', 'active' => 'data',
            'description' => 'Jak stáhnout data Co se peče: JSON API a RSS, adresy, parametry, popis polí a příklady.',
        ];
    }
}
