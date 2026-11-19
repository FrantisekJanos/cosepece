<?php


declare(strict_types=1);

namespace Hlidac;

final class Scorer
{
    public const LEVEL_MAJOR = 'major';
    public const LEVEL_WATCH = 'watch';
    public const LEVEL_NORMAL = 'normal';

    private const KIND_BONUS = ['p90' => 10, 'cut' => 10, 'passed' => 5, 'surge' => 5, 'law' => 0, 'newbill' => 0, 'pn' => 0];

    public function __construct(private Db $db, private array $config)
    {
    }

    public static function defaults(): array
    {

        return [
            'mp_bill' => 15,

            'mp_bill_early' => 5,
            'senate_bill' => 5,
            'region_bill' => 5,
            'p90_requested' => 10,
            'p90_passed' => 25,
            'deadline_cut' => 15,
            'short_gap' => 15,
            'short_gap_days' => 7,
            'late_pn' => 10,
            'late_pn_min' => 3,

            'late_pn_share' => 0.5,
            'surge' => 5,
            'omnibus' => 3,

            'topic' => 5,
            'watched' => 10,
            'pn_other_law' => 20,
            'pn_large' => 10,
            'pn_large_chars' => 12000,
            'pn_many_flagged' => 5,
            'retro_deadline' => 15,

            'cap_procedure' => 40,
            'cap_content' => 45,
            'cap_context' => 25,
            'ai_multiplier' => 3,
            'level_major' => 50,
            'level_watch' => 25,
            'topics' => [
                'střet zájmů' => 'střet[ua]? zájmů',
                'dotace a SZIF' => 'dotac|zemědělsk\w* intervenční|SZIF',
                'veřejné zakázky' => 'veřejn\w+ zakáz',
                'kontrolní instituce' => 'Nejvyšším kontrolním|NKÚ|státním zastupitelství|soudech a soudcích|registru smluv|svobodném přístupu k informacím',

                'policie a zpravodajské služby' => 'Policii České republiky|zpravodajských služb',
                'volby a parlament' => 'volb|jednacím řádu|politických stran',
                'veřejnoprávní média' => 'České televizi|Českém rozhlasu|mediálních služeb',
                'promlčení a lhůty vymáhání' => 'promlč|prekluz',
                'platy ústavních činitelů' => 'platu a dalších náležitostech',
                'hospodářská soutěž a veřejná podpora' => 'hospodářské soutěže|veřejné podpo',
            ],
        ];
    }

    public static function weights(array $config): array
    {
        $w = array_replace(self::defaults(), $config['scoring'] ?? []);
        if (isset($config['scoring']['topics'])) {
            $w['topics'] = $config['scoring']['topics'];
        }
        return $w;
    }

    public static function level(int $score, array $config): string
    {
        $w = self::weights($config);
        if ($score >= (int)$w['level_major']) {
            return self::LEVEL_MAJOR;
        }
        return $score >= (int)$w['level_watch'] ? self::LEVEL_WATCH : self::LEVEL_NORMAL;
    }

    public function scoreBills(): int
    {
        $w = self::weights($this->config);
        $kroky = [];
        foreach ($this->db->all('SELECT ct, datum, typ_stavu, akce_id FROM kroky ORDER BY id_hist') as $k) {
            $kroky[(int)$k['ct']][] = $k;
        }
        $pnDates = [];
        foreach ($this->db->all('SELECT ct, cas FROM pn') as $p) {
            $pnDates[(int)$p['ct']][] = substr((string)$p['cas'], 0, 10);
        }
        $pnText = [];
        foreach ($this->db->all('SELECT x.ct, x.cislo, x.rule_score, x.ai_score, x.ai_model, x.signals, x.ai_json, p.osoba FROM pn_text x JOIN pn p ON p.id_dokument = x.id_dokument') as $x) {
            $pnText[(int)$x['ct']][] = $x;
        }
        $billText = [];
        foreach ($this->db->all('SELECT ct, signals, ai_score, ai_json FROM tisk_text') as $x) {
            $billText[(int)$x['ct']] = $x;
        }
        $surge = [];
        foreach ($this->db->all("SELECT DISTINCT ct FROM alerts WHERE kind = 'surge' AND (obdobi IS NULL OR obdobi = ?)",
            [(int)$this->db->getMeta('obdobi_cislo', '0')]) as $r) {
            $surge[(int)$r['ct']] = true;
        }
        $watchLaws = $this->config['watch_laws'] ?? [];
        $watchTisky = array_map('intval', $this->config['watch_tisky'] ?? []);

        $count = 0;
        $this->db->pdo->beginTransaction();
        foreach ($this->db->all('SELECT * FROM tisky') as $t) {
            $ct = (int)$t['ct'];
            $reasons = $this->billReasons($t, $kroky[$ct] ?? [], $pnDates[$ct] ?? [], $pnText[$ct] ?? [],
                $billText[$ct] ?? null, isset($surge[$ct]), $watchLaws, $watchTisky, $w);
            $score = min(100, array_sum(array_column($reasons, 'points')));
            $this->db->run('UPDATE tisky SET score = ?, reasons = ? WHERE id_tisk = ?',
                [$score, json_encode($reasons, JSON_UNESCAPED_UNICODE), (int)$t['id_tisk']]);
            $count++;
        }
        $this->db->pdo->commit();
        return $count;
    }

    public function scoreAlerts(): void
    {

        $scores = [];
        foreach ($this->db->all('SELECT ct, score FROM tisky') as $t) {
            $scores[(int)$t['ct']] = (int)$t['score'];
        }
        $this->db->pdo->beginTransaction();

        $obdobi = (int)$this->db->getMeta('obdobi_cislo', '0');
        foreach ($this->db->all('SELECT alert_key, kind, ct FROM alerts WHERE obdobi IS NULL OR obdobi = ?', [$obdobi]) as $a) {
            $score = min(100, ($scores[(int)$a['ct']] ?? 0) + (self::KIND_BONUS[$a['kind']] ?? 0));
            $this->db->run('UPDATE alerts SET score = ? WHERE alert_key = ?', [$score, $a['alert_key']]);
        }
        $this->db->pdo->commit();
    }

    private function billReasons(array $t, array $kroky, array $pnDates, array $pnText, ?array $billText, bool $surge,
                                 array $watchLaws, array $watchTisky, array $w): array
    {
        $r = [];
        $add = function (string $key, int $points, string $label) use (&$r): void {
            if ($points > 0) {
                $r[] = ['key' => $key, 'points' => $points, 'label' => $label];
            }
        };



        $progressed = (int)$t['p90ok'] === 1;
        foreach ($kroky as $k) {
            $typ = (int)$k['typ_stavu'];
            if (in_array($typ, Analyzer::STAV_2_3_CTENI, true) || in_array($typ, Analyzer::STAV_PO_SNEMOVNE, true) || $typ === 21) {
                $progressed = true;
                break;
            }
        }
        $closed = in_array((string)$t['faze'], ['Vzat zpět', 'Zamítnuto', 'Vráceno předkladateli'], true);
        if ($t['typ_navrhu'] === 'poslanecký') {
            if ($progressed) {
                $add('mp_bill', (int)$w['mp_bill'], 'Poslanecký návrh postupuje projednáváním – neprošel meziresortním připomínkovým řízením');
            } else {
                $add('mp_bill', (int)$w['mp_bill_early'], 'Poslanecký návrh – neprošel meziresortním připomínkovým řízením (zatím nepostoupil do 2. čtení)');
            }
        } elseif ($t['typ_navrhu'] === 'senátní') {
            $add('senate_bill', (int)$w['senate_bill'], 'Senátní návrh – neprošel meziresortním připomínkovým řízením');
        } elseif ($t['typ_navrhu'] === 'krajský') {
            $add('region_bill', (int)$w['region_bill'], 'Návrh krajského zastupitelstva – neprošel meziresortním připomínkovým řízením');
        }

        if ((int)$t['p90ok']) {
            $add('p90_passed', (int)$w['p90_passed'], 'Schváleno už v 1. čtení (§ 90 odst. 2) bez 2. a 3. čtení');
        } elseif ((int)$t['p90'] && !$closed) {

            $add('p90_requested', (int)$w['p90_requested'], 'Navrženo schválit už v 1. čtení (§ 90 odst. 2)');
        }
        if ((int)$t['zl']) {
            $lhuta = isset($t['lhuta']) ? (int)$t['lhuta'] : 0;
            $add('deadline_cut', (int)$w['deadline_cut'], Analyzer::committeeCut($lhuta)
                ? 'Zkrácená lhůta pro výbory: ' . $lhuta . ' dní místo ' . Analyzer::LHUTA_VYBORY
                : 'Zkrácená lhůta při projednávání');
        }

        $pnDay = null;
        $thirdDay = null;
        $detailEnd = null;
        foreach ($kroky as $k) {
            $typ = (int)$k['typ_stavu'];
            $day = $k['datum'] ? substr((string)$k['datum'], 0, 10) : null;
            if ($typ === 21 && $pnDay === null && $day) {
                $pnDay = $day;
            }
            if ($typ === 4 && (int)$k['akce_id'] === 23 && $day) {
                $detailEnd = $day;
            }

            if ($typ === 5 && in_array((int)$k['akce_id'], [238, 71, 23], true) && $pnDay !== null && $day
                && $day >= $pnDay && ($thirdDay === null || $day < $thirdDay)) {
                $thirdDay = $day;
            }
        }
        if ($pnDay && $thirdDay) {
            $gap = (int)round((strtotime($thirdDay) - strtotime($pnDay)) / 86400);
            if ($gap < (int)$w['short_gap_days']) {
                $add('short_gap', (int)$w['short_gap'], $gap <= 0
                    ? 'Pozměňovací návrhy a 3. čtení ve stejný den'
                    : "Mezi pozměňovacími návrhy a 3. čtením jen $gap " . ($gap === 1 ? 'den' : ($gap < 5 ? 'dny' : 'dní')));
            }
        }

        if ($detailEnd !== null) {
            $late = count(array_filter($pnDates, fn(string $d): bool => $d === $detailEnd));

            if ($late >= (int)$w['late_pn_min'] && $late >= (float)$w['late_pn_share'] * count($pnDates)) {
                $add('late_pn', (int)$w['late_pn'], "$late z " . count($pnDates) . ' pozměňovacích návrhů podáno v den konce podrobné rozpravy');
            }
        }
        if ($surge) {
            $add('surge', (int)$w['surge'], 'Nával pozměňovacích návrhů');
        }

        $anyLarge = false;
        $maxAi = 0;
        $flagged = 0;
        $otherLaws = [];
        $otherLawsConfirmed = false;
        $aiWorst = null;
        $retroWhere = null;
        $verifier = AiReviewer::verifyModel($this->config);
        $verifyMin = (int)($this->config['ai']['verify_min'] ?? 6);

        $billLawsInText = $billText !== null ? (array)((json_decode((string)$billText['signals'], true) ?: [])['laws'] ?? []) : [];
        $billSignals = $billText !== null ? (json_decode((string)$billText['signals'], true) ?: []) : [];


        $aiConfirms = function (?string $json, $score): bool {
            $ai = json_decode((string)$json, true) ?: [];
            return $score !== null && (int)$score >= 4 && (!empty($ai['zpetna_ucinnost']) || !empty($ai['zkraceni_lhut']));
        };
        if (!empty($billSignals['retro_deadline']) && !empty($billSignals['deadline'])
            && !$aiConfirms($billText['ai_json'] ?? null, $billText['ai_score'] ?? null)) {
            $retroWhere = 'Text návrhu';
        }
        foreach ($pnText as $x) {
            $signals = json_decode((string)$x['signals'], true) ?: [];

            $aiJson = json_decode((string)($x['ai_json'] ?? ''), true) ?: [];
            $related = ($aiJson['souvisi_s_predmetem'] ?? null) === true;

            $large = !empty($signals['large']);
            $anyLarge = $anyLarge || $large;
            $ruleFlag = $large || (!empty($signals['other_laws']) && !$related);
            $signals['other_laws'] = array_values(array_diff((array)($signals['other_laws'] ?? []), $billLawsInText));
            if ($signals['other_laws'] === []) {
                unset($signals['other_laws']);
            }
            $ruleFlag = $large || (!empty($signals['other_laws']) && !$related);
            if (!$related) {
                foreach ($signals['other_laws'] ?? [] as $law) {
                    $otherLaws[$law] = true;
                }
                if (!empty($signals['other_laws']) && ($aiJson['souvisi_s_predmetem'] ?? null) === false) {
                    $otherLawsConfirmed = true;
                }
            }
            if ($retroWhere === null && !empty($signals['retro_deadline']) && !empty($signals['deadline'])
                && !$aiConfirms($x['ai_json'] ?? null, $x['ai_score'])) {
                $retroWhere = 'Pozměňovací návrh č. ' . $x['cislo'];
            }
            $ai = $x['ai_score'] !== null ? (int)$x['ai_score'] : 0;


            if ($ai >= $verifyMin && (string)($x['ai_model'] ?? '') !== $verifier) {
                $ai = $verifyMin - 1;
                $x['ai_score'] = $ai;
            }
            if ($ruleFlag || $ai >= 6) {
                $flagged++;
            }
            if ($x['ai_score'] !== null && $ai > $maxAi) {
                $maxAi = $ai;
                $aiWorst = $x;
            }
        }
        if ($otherLaws !== []) {
            $add('pn_other_law', (int)$w['pn_other_law'], 'Přílepek: pozměňovací návrh mění i jiný zákon (' . implode(', ', array_map(fn($l) => $l . ' Sb.', array_keys($otherLaws))) . ')'
                . ($otherLawsConfirmed ? '; rozbor textu potvrdil, že s předmětem návrhu nesouvisí' : ''));
        } elseif ($anyLarge) {
            $add('pn_large', (int)$w['pn_large'], 'Rozsáhlá nová úprava zavedená pozměňovacím návrhem');
        }
        if ($retroWhere !== null) {
            $add('retro_deadline', (int)$w['retro_deadline'], $retroWhere . ' mění lhůtu pro vymáhání nebo kontrolu a obsahuje ustanovení, které se výslovně vztahuje i na případy z doby před účinností');
        }
        if ($flagged >= 3) {
            $add('pn_many', (int)$w['pn_many_flagged'], "$flagged problematických pozměňovacích návrhů");
        }
        if ($billText !== null && $billText['ai_score'] !== null && (int)$billText['ai_score'] >= 4) {
            $ai = json_decode((string)$billText['ai_json'], true) ?: [];
            $why = trim((string)($ai['duvod'] ?? ''));
            $add('ai_bill', (int)$billText['ai_score'] * (int)$w['ai_multiplier'], 'Rozbor textu návrhu: závažnost ' . (int)$billText['ai_score'] . '/10' . ($why !== '' ? ' – ' . $why : ''));
        }
        if ($aiWorst !== null && $maxAi >= 4) {
            $ai = json_decode((string)$aiWorst['ai_json'], true) ?: [];
            $why = trim((string)($ai['duvod'] ?? ''));
            $add('ai', $maxAi * (int)$w['ai_multiplier'], 'Rozbor PN č. ' . $aiWorst['cislo'] . ' (' . $aiWorst['osoba'] . '): závažnost ' . $maxAi . '/10' . ($why !== '' ? ' – ' . $why : ''));
        }

        $title = $t['nazev'] . ' ' . $t['uplny_nazev'];
        foreach ($w['topics'] as $label => $regex) {
            if (@preg_match('~' . $regex . '~iu', $title)) {
                $add('topic', (int)$w['topic'], 'Citlivá oblast: ' . $label);
                break;
            }
        }
        if (preg_match('~mění některé zákony|mění a zrušují|v souvislosti s přijetím~u', (string)$t['uplny_nazev'])) {
            $add('omnibus', (int)$w['omnibus'], 'Souhrnná novela více zákonů');
        }
        $laws = array_filter(explode(',', (string)$t['zakony']));
        if (array_intersect($laws, $watchLaws) !== [] || in_array((int)$t['ct'], $watchTisky, true)) {
            $add('watched', (int)$w['watched'], 'Sledovaný zákon');
        }

        return self::applyCaps($r, $w);
    }

    public static function applyCaps(array $reasons, array $w): array
    {

        $groups = [
            'procedure' => ['mp_bill', 'senate_bill', 'region_bill', 'p90_passed', 'p90_requested', 'deadline_cut', 'short_gap', 'late_pn', 'surge'],
            'content' => ['pn_other_law', 'pn_large', 'retro_deadline', 'pn_many', 'ai_bill', 'ai'],
            'context' => ['topic', 'omnibus', 'watched'],
        ];
        usort($reasons, fn(array $a, array $b): int => $b['points'] <=> $a['points']);
        $left = [];
        foreach ($groups as $group => $keys) {
            $left[$group] = (int)($w['cap_' . $group] ?? 100);
        }
        foreach ($reasons as &$reason) {
            foreach ($groups as $group => $keys) {
                if (in_array($reason['key'], $keys, true)) {
                    $points = min((int)$reason['points'], $left[$group]);
                    if ($points < (int)$reason['points']) {
                        $reason['capped'] = true;
                    }
                    $reason['points'] = $points;
                    $left[$group] -= $points;
                    break;
                }
            }
        }
        unset($reason);
        usort($reasons, fn(array $a, array $b): int => $b['points'] <=> $a['points']);
        return $reasons;
    }
}
