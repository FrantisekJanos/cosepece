<?php
// Dotazy pro webové stránky.

declare(strict_types=1);

namespace Hlidac;

final class Repo
{
    public function __construct(private Db $db)
    {
    }

    public function obdobi(): int
    {
        return (int)$this->db->getMeta('obdobi_cislo', '10');
    }

    public function lastSync(): ?string
    {
        return $this->db->getMeta('last_sync');
    }

    public function stats(): array
    {
        $since = date('Y-m-d H:i:s', strtotime('-7 days'));
        $zkraceni = implode(',', Analyzer::AKCE_ZKRACENI);
        $top = $this->db->one(
            'SELECT p.ct, COUNT(*) n, t.nazev FROM pn p LEFT JOIN tisky t ON t.ct = p.ct
             WHERE p.cas >= ? GROUP BY p.ct, t.nazev ORDER BY n DESC LIMIT 1', [$since]);
        $lastCut = $this->db->one(
            "SELECT k.ct, k.datum FROM kroky k WHERE k.akce_id IN ($zkraceni) ORDER BY k.datum DESC, k.id_hist DESC LIMIT 1");
        return [
            'pn_total' => (int)$this->db->value('SELECT COUNT(*) FROM pn'),
            'bills_total' => (int)$this->db->value('SELECT COUNT(*) FROM tisky WHERE druh IN (1, 2)'),
            'bills_with_pn' => (int)$this->db->value('SELECT COUNT(*) FROM tisky WHERE pn_count > 0'),
            'pn_7d' => (int)$this->db->value('SELECT COUNT(*) FROM pn WHERE cas >= ?', [$since]),
            'pn_7d_top' => $top,
            'p90' => (int)$this->db->value('SELECT COUNT(*) FROM tisky WHERE p90 = 1'),
            'p90ok' => (int)$this->db->value('SELECT COUNT(*) FROM tisky WHERE p90ok = 1'),
            'zl' => (int)$this->db->value('SELECT COUNT(*) FROM tisky WHERE zl = 1'),
            'zl_last' => $lastCut,
        ];
    }

    public function alerts(int $limit, int $offset = 0, ?int $ct = null, int $minScore = 0): array
    {
        if ($ct !== null) {
            // jen aktuální období: tisk se stejným číslem z minulého období je jiný návrh
            return $this->db->all('SELECT * FROM alerts WHERE ct = ? AND (obdobi IS NULL OR obdobi = ?) ORDER BY event_at DESC, created_at DESC, alert_key DESC LIMIT ' . $limit . ' OFFSET ' . $offset,
                [$ct, (int)$this->db->getMeta('obdobi_cislo', '0')]);
        }
        return $this->db->all('SELECT * FROM alerts WHERE score >= ? ORDER BY event_at DESC, created_at DESC, alert_key DESC LIMIT ' . $limit . ' OFFSET ' . $offset, [$minScore]);
    }

    public function alertsCount(int $minScore = 0): int
    {
        return (int)$this->db->value('SELECT COUNT(*) FROM alerts WHERE score >= ?', [$minScore]);
    }

    // druhy procedurálních problémů pro filtr nálezů a tisků: klíč => [název, klíče důvodů skóre];
    // úzké skupiny (jedna konkrétní věc), aby filtr mezi hlavními nálezy něco odfiltroval
    public const PROBLEM_GROUPS = [
        'prilepky' => ['Přílepky', ['pn_other_law']],
        'zmeny_pn' => ['Rozsáhlé změny v PN', ['pn_large']],
        'lhuty' => ['Zkrácené lhůty', ['deadline_cut', 'short_gap']],
        'par90' => ['§ 90/2 bez 2. čtení', ['p90_passed', 'p90_requested']],
        'pripominky' => ['Obcházení připomínek', ['mp_bill', 'senate_bill', 'region_bill']],
        'zpetne' => ['Zpětné použití', ['retro_deadline']],
    ];
    // řazení hlavních nálezů: klíč => název
    public const FINDING_SORTS = [
        'skore' => 'Celkové skóre',
        'ai' => 'Závažnost textu (AI)',
        'nove' => 'Nejnovější dění',
        'hlasovani' => 'Blíží se hlasování',
    ];

    public function topFindings(int $minScore, int $limit, string $sort = 'skore', string $group = ''): array
    {
        return array_slice($this->findings($minScore, $sort, $group)['rows'], 0, $limit);
    }

    /**
     * Hlavní nálezy: nejzávažnější rozpracované tisky (mohou se kdykoli rozhýbat) a uzavřené tisky
     * s aktivitou za posledních 120 dní. Navíc tisky pod prahem, které mají nález AI se závažností aspoň
     * finding_ai_min, ověřený silnějším modelem a doložený citací (jediný silný nález se ve skóre ztratí
     * mezi ostatními signály, např. tisk 221). Volitelně jen jeden druh problému a jiné řazení.
     *
     * @return array{rows: array, counts: array<string, int>, total: int}
     */
    public function findings(int $minScore, string $sort = 'skore', string $group = ''): array
    {
        $since = date('Y-m-d', strtotime('-120 days'));
        $ai = $this->aiSignals();
        $strongMin = (int)Scorer::weights(config())['finding_ai_min'];
        $strong = array_keys(array_filter($ai, fn(array $a) => $a['score'] >= $strongMin));
        $extra = $strong !== [] ? ' OR ct IN (' . implode(',', array_map('intval', $strong)) . ')' : '';
        $rows = $this->db->all(
            "SELECT * FROM tisky WHERE (score >= ?$extra)
               AND (faze NOT IN ('Vyhlášeno', 'Vzat zpět', 'Zamítnuto', 'Vráceno předkladateli') OR posledni >= ?)
             ORDER BY score DESC, posledni DESC",
            [$minScore, $since]
        );
        $short = $this->shortReview();
        $lastPn = [];
        foreach ($this->db->all('SELECT ct, MAX(cas) AS cas FROM pn GROUP BY ct') as $r) {
            $lastPn[(int)$r['ct']] = (string)$r['cas'];
        }
        $counts = array_fill_keys(array_keys(self::PROBLEM_GROUPS), 0);
        foreach ($rows as &$row) {
            $ct = (int)$row['ct'];
            $row['reasons_list'] = json_decode((string)$row['reasons'], true) ?: [];
            $row['ai_verified'] = $ai[$ct]['score'] ?? null;
            // ověřený silný nález AI (štítek na kartě; pod prahem skóre je to důvod zařazení)
            $row['strong_ai'] = ($ai[$ct]['score'] ?? 0) >= $strongMin ? $ai[$ct]['score'] : null;
            $row['groups'] = $this->problemGroups($row, $ai[$ct] ?? null, isset($short[$ct]));
            $row['activity'] = max((string)$row['posledni'], $lastPn[$ct] ?? '');
            foreach ($row['groups'] as $g) {
                $counts[$g]++;
            }
        }
        unset($row);
        $total = count($rows);
        if ($group !== '' && isset(self::PROBLEM_GROUPS[$group])) {
            $rows = array_values(array_filter($rows, fn(array $r) => in_array($group, $r['groups'], true)));
        }
        usort($rows, match ($sort) {
            // nejdřív ověřené nálezy AI od nejzávažnějšího, při shodě (a bez nálezu) podle skóre
            'ai' => fn(array $a, array $b) => [$b['ai_verified'] ?? -1, (int)$b['score']] <=> [$a['ai_verified'] ?? -1, (int)$a['score']],
            'nove' => fn(array $a, array $b) => [$b['activity'], (int)$b['score']] <=> [$a['activity'], (int)$a['score']],
            // tisky před hlasováním ve Sněmovně: 3. čtení a hlasování po Senátu či vetu, pak 2. a 1. čtení
            'hlasovani' => fn(array $a, array $b) => [self::votePhaseRank((string)$a['faze']), -(int)$a['score']] <=> [self::votePhaseRank((string)$b['faze']), -(int)$b['score']],
            default => fn(array $a, array $b) => [(int)$b['score'], (string)$b['posledni']] <=> [(int)$a['score'], (string)$a['posledni']],
        });
        $surge = $this->surgeSet();
        $review = $this->reviewStatus();
        foreach ($rows as &$row) {
            $row['flags'] = $this->flags($row, $surge, $review);
            $row['headline'] = $this->headline((int)$row['ct']);
        }
        return ['rows' => $rows, 'counts' => $counts, 'total' => $total];
    }

    public static function votePhaseRank(string $faze): int
    {
        return match ($faze) {
            '3. čtení', 'Sněmovna po Senátu', 'Sněmovna po vetu' => 0,
            '2. čtení' => 1,
            '1. čtení', 'Organizační výbor', 'Podáno', 'U vlády', 'Stanovisko vlády' => 2,
            default => 3, // Senát, prezident, vyhlášeno, uzavřeno – Sněmovna už nehlasuje
        };
    }

    public function problemGroups(array $t, ?array $ai = null, bool $shortReview = false): array
    {
        // druhy problémů tisku z důvodů skóre (i těch ustřižených stropem skupiny) a z ověřeného rozboru AI
        $keys = array_column(is_array($t['reasons_list'] ?? null) ? $t['reasons_list'] : (json_decode((string)($t['reasons'] ?? ''), true) ?: []), 'key');
        $out = [];
        foreach (self::PROBLEM_GROUPS as $group => [, $reasonKeys]) {
            if (array_intersect($keys, $reasonKeys) !== []) {
                $out[] = $group;
            }
        }
        // ověřený nález AI: text nesouvisí s předmětem návrhu (přílepek) nebo se použije zpětně
        if (!empty($ai['rider']) && !in_array('prilepky', $out, true)) {
            $out[] = 'prilepky';
        }
        if (!empty($ai['retro']) && !in_array('zpetne', $out, true)) {
            $out[] = 'zpetne';
        }
        // vládní návrh s připomínkovým řízením kratším než 3 týdny
        if ($shortReview && !in_array('pripominky', $out, true)) {
            $out[] = 'pripominky';
        }
        return array_values(array_intersect(array_keys(self::PROBLEM_GROUPS), $out));
    }

    public function shortReview(): array
    {
        // ct => true u vládních návrhů s připomínkovým řízením kratším než 21 dní
        $out = [];
        foreach ($this->db->all("SELECT ct FROM priprava WHERE druh = 'vladni' AND dni IS NOT NULL AND dni < 21") as $r) {
            $out[(int)$r['ct']] = true;
        }
        return $out;
    }

    public function aiSignals(): array
    {
        // ct => nejvyšší ověřená závažnost AI (text návrhu nebo PN) s ověřenou citací a příznaky
        // přílepku / zpětného použití z ověřených rozborů od verify_min (slabší nálezy se nepočítají)
        $verifier = AiReviewer::verifyModel(config());
        $flagMin = (int)(config('ai', [])['verify_min'] ?? 6);
        $out = [];
        foreach (['tisk_text', 'pn_text'] as $table) {
            foreach ($this->db->all("SELECT ct, ai_score, ai_json FROM $table WHERE ai_model = ? AND ai_score IS NOT NULL", [$verifier]) as $r) {
                $ai = json_decode((string)$r['ai_json'], true) ?: [];
                if (trim((string)(((array)($ai['citace'] ?? []))[0] ?? '')) === '') {
                    continue;
                }
                $ct = (int)$r['ct'];
                $score = (int)$r['ai_score'];
                $out[$ct] ??= ['score' => 0, 'rider' => false, 'retro' => false];
                $out[$ct]['score'] = max($out[$ct]['score'], $score);
                if ($score >= $flagMin) {
                    $out[$ct]['rider'] = $out[$ct]['rider'] || (isset($ai['souvisi_s_predmetem']) && $ai['souvisi_s_predmetem'] === false);
                    $out[$ct]['retro'] = $out[$ct]['retro'] || !empty($ai['zpetna_ucinnost']);
                }
            }
        }
        return $out;
    }

    public function strongAiFindings(): array
    {
        // ct => nejvyšší ověřená závažnost AI od finding_ai_min, s ověřenou citací
        $min = (int)Scorer::weights(config())['finding_ai_min'];
        return array_filter(array_map(fn(array $a) => $a['score'], $this->aiSignals()), fn(int $s) => $s >= $min);
    }

    public function headline(int $ct): ?array
    {
        // srozumitelný titulek z rozboru AI: z textu návrhu nebo z PN, podle toho, kde je závažnější nález
        $candidates = [];
        $bill = $this->db->one('SELECT ai_score, ai_json, ai_model FROM tisk_text WHERE ct = ? AND ai_at IS NOT NULL', [$ct]);
        if ($bill !== null && (int)$bill['ai_score'] >= 4) {
            $candidates[] = ['score' => (int)$bill['ai_score'], 'json' => $bill['ai_json'], 'model' => $bill['ai_model'], 'source' => 'text návrhu'];
        }
        $pn = $this->db->one('SELECT x.ai_score, x.ai_json, x.ai_model, x.cislo FROM pn_text x WHERE x.ct = ? AND x.ai_at IS NOT NULL ORDER BY x.ai_score DESC LIMIT 1', [$ct]);
        if ($pn !== null && (int)$pn['ai_score'] >= 4) {
            $candidates[] = ['score' => (int)$pn['ai_score'], 'json' => $pn['ai_json'], 'model' => $pn['ai_model'], 'source' => 'pozměňovací návrh č. ' . (int)$pn['cislo']];
        }
        usort($candidates, fn(array $a, array $b): int => $b['score'] <=> $a['score']);
        foreach ($candidates as $c) {
            $ai = json_decode((string)$c['json'], true) ?: [];
            $title = trim((string)($ai['titulek'] ?? ''));
            // titulek na homepage jen u nálezu doloženého citací, která se ověřila v textu (#2 bod 4)
            $quote = trim((string)(((array)($ai['citace'] ?? []))[0] ?? ''));
            if ($title !== '' && $quote !== '') {
                return [
                    'title' => $title,
                    'perex' => trim((string)($ai['perex'] ?? '')),
                    'quote' => $quote,
                    'source' => $c['source'],
                    // ověřeno druhým kolem silnějším modelem?
                    'verified' => (string)$c['model'] === AiReviewer::verifyModel(config()),
                ];
            }
        }
        return null;
    }

    public function suppressedCount(int $minScore): int
    {
        return (int)$this->db->value('SELECT COUNT(*) FROM alerts WHERE score < ?', [$minScore]);
    }

    public function pnTexts(int $ct): array
    {
        $out = [];
        foreach ($this->db->all('SELECT id_dokument, rule_score, ai_score, signals, ai_json, ai_model, ai_prev_score, ai_prev_model, error FROM pn_text WHERE ct = ?', [$ct]) as $x) {
            $x['signals'] = json_decode((string)$x['signals'], true) ?: [];
            $x['ai'] = json_decode((string)$x['ai_json'], true) ?: null;
            $out[(int)$x['id_dokument']] = $x;
        }
        return $out;
    }

    public function prep(int $ct): ?array
    {
        // příprava návrhu u vlády (připomínkové řízení, schůze vlády, důvod předložení)
        return $this->db->one('SELECT * FROM priprava WHERE ct = ?', [$ct]);
    }

    public function upcoming(): array
    {
        // připravované zákony u vlády: od nejpokročilejší fáze, v ní od poslední změny
        $order = array_flip(GovPrep::PHASES);
        $rows = $this->db->all('SELECT * FROM pripravovane');
        usort($rows, fn(array $a, array $b) => [($order[$a['faze']] ?? 9), (string)$b['zmeneno']] <=> [($order[$b['faze']] ?? 9), (string)$a['zmeneno']]);
        return $rows;
    }

    public function votes(int $ct): array
    {
        // hlasování k tisku od nejnovějšího, s rozdělením podle klubů
        $rows = $this->db->all('SELECT * FROM hlasovani WHERE ct = ? ORDER BY datum DESC, id_hlasovani DESC', [$ct]);
        if ($rows === []) {
            return [];
        }
        $clubs = [];
        $ids = array_map(fn($r) => (int)$r['id_hlasovani'], $rows);
        foreach ($this->db->all('SELECT * FROM hlasovani_kluby WHERE id_hlasovani IN (' . implode(',', $ids) . ') ORDER BY klub') as $c) {
            $clubs[(int)$c['id_hlasovani']][] = $c;
        }
        foreach ($rows as &$r) {
            $r['kluby'] = $clubs[(int)$r['id_hlasovani']] ?? [];
        }
        return $rows;
    }

    public function billText(int $ct): ?array
    {
        // stažený text návrhu zákona s pravidlovými signály a rozborem AI
        $x = $this->db->one('SELECT ct, docx_url, chars, signals, ai_score, ai_json, ai_model, ai_prev_score, ai_prev_model, ai_at, fetched_at, error FROM tisk_text WHERE ct = ?', [$ct]);
        if ($x === null) {
            return null;
        }
        $x['signals'] = json_decode((string)$x['signals'], true) ?: [];
        $x['ai'] = json_decode((string)$x['ai_json'], true) ?: null;
        return $x;
    }

    public function watchlist(array $laws, array $tisky): array
    {
        // filtrováno v PHP, aby dotaz fungoval stejně v SQLite i MySQL
        $out = [];
        $surge = $this->surgeSet();
        $review = $this->reviewStatus();
        foreach ($this->db->all('SELECT * FROM tisky ORDER BY posledni DESC') as $t) {
            $tLaws = array_filter(explode(',', (string)$t['zakony']));
            if (array_intersect($tLaws, $laws) !== [] || in_array((int)$t['ct'], array_map('intval', $tisky), true)) {
                $t['flags'] = $this->flags($t, $surge, $review);
                $out[] = $t;
            }
        }
        return $out;
    }

    public function topAuthors(int $days, int $limit): array
    {
        $since = date('Y-m-d H:i:s', strtotime("-$days days"));
        return $this->db->all('SELECT osoba, COUNT(*) n FROM pn WHERE cas >= ? GROUP BY osoba ORDER BY n DESC, osoba LIMIT ' . $limit, [$since]);
    }

    public static function fold(?string $text): string
    {
        // malá písmena bez diakritiky pro hledání
        static $map = [
            'á' => 'a', 'č' => 'c', 'ď' => 'd', 'é' => 'e', 'ě' => 'e', 'í' => 'i', 'ň' => 'n', 'ó' => 'o',
            'ř' => 'r', 'š' => 's', 'ť' => 't', 'ú' => 'u', 'ů' => 'u', 'ý' => 'y', 'ž' => 'z',
            'ä' => 'a', 'ö' => 'o', 'ü' => 'u', 'ľ' => 'l', 'ĺ' => 'l', 'ŕ' => 'r', 'ô' => 'o',
        ];
        return strtr(mb_strtolower(trim((string)$text)), $map);
    }

    public function bills(string $filter = '', string $q = '', int $limit = 500, string $sort = 'score', int $minScore = 0): array
    {
        $where = ['1 = 1'];
        $params = [];
        switch ($filter) {
            case 'mp':
                $where[] = "typ_navrhu <> 'vládní'";
                break;
            case 'p90':
                $where[] = 'p90 = 1';
                break;
            case 'cut':
                $where[] = 'zl = 1';
                break;
            case 'surge':
                $where[] = "ct IN (SELECT ct FROM alerts WHERE kind = 'surge' AND (obdobi IS NULL OR obdobi = ?))";
                $params[] = (int)$this->db->getMeta('obdobi_cislo', '0');
                break;
            case 'flagged':
                $where[] = "(typ_navrhu <> 'vládní' OR p90 = 1 OR zl = 1 OR pn_count > 0)";
                break;
        }
        if ($minScore > 0) {
            $where[] = 'score >= ?';
            $params[] = $minScore;
        }
        $order = $sort === 'recent' ? 'posledni DESC, ct DESC' : 'score DESC, posledni DESC, ct DESC';
        // hledání bez ohledu na velikost písmen a diakritiku („skolsky“ najde „Školský“) se dělá v PHP,
        // stejně v SQLite, MySQL i PostgreSQL; tisků jednoho období jsou stovky, ne tisíce
        // druh problému (přílepky, zrychlení…) se pozná z důvodů skóre a z rozboru AI, filtruje se v PHP
        $byGroup = isset(self::PROBLEM_GROUPS[$filter]);
        $rows = $this->db->all('SELECT * FROM tisky WHERE ' . implode(' AND ', $where)
            . ' ORDER BY ' . $order . ($q === '' && !$byGroup ? ' LIMIT ' . $limit : ''), $params);
        if ($byGroup) {
            $ai = $this->aiSignals();
            $short = $this->shortReview();
            $rows = array_slice(array_values(array_filter($rows, fn(array $t): bool => in_array($filter,
                $this->problemGroups($t, $ai[(int)$t['ct']] ?? null, isset($short[(int)$t['ct']])), true))), 0, $q === '' ? $limit : PHP_INT_MAX);
        }
        if ($q !== '') {
            $needle = self::fold($q);
            $rows = array_slice(array_values(array_filter($rows, fn(array $t): bool => (string)$t['ct'] === trim($q)
                || str_contains(self::fold($t['nazev'] . ' ' . $t['uplny_nazev'] . ' ' . $t['navrhovatel'] . ' ' . $t['zakony']), $needle))), 0, $limit);
        }
        $surge = $this->surgeSet();
        $review = $this->reviewStatus();
        foreach ($rows as &$row) {
            $row['flags'] = $this->flags($row, $surge, $review);
        }
        return $rows;
    }

    public function bill(int $ct): ?array
    {
        $t = $this->db->one('SELECT * FROM tisky WHERE ct = ?', [$ct]);
        if ($t !== null) {
            $t['flags'] = $this->flags($t);
            $t['reasons_list'] = json_decode((string)$t['reasons'], true) ?: [];
        }
        return $t;
    }

    public function steps(int $ct): array
    {
        return $this->db->all('SELECT * FROM kroky WHERE ct = ? ORDER BY id_hist', [$ct]);
    }

    public function amendments(int $ct): array
    {
        return $this->db->all('SELECT * FROM pn WHERE ct = ? ORDER BY cas DESC', [$ct]);
    }

    public function amendmentAuthors(int $ct): array
    {
        return $this->db->all('SELECT osoba, COUNT(*) n FROM pn WHERE ct = ? GROUP BY osoba ORDER BY n DESC, osoba', [$ct]);
    }

    private function surgeSet(): array
    {
        $out = [];
        foreach ($this->db->all("SELECT DISTINCT ct FROM alerts WHERE kind = 'surge' AND (obdobi IS NULL OR obdobi = ?)",
            [(int)$this->db->getMeta('obdobi_cislo', '0')]) as $r) {
            $out[(int)$r['ct']] = true;
        }
        return $out;
    }

    public function reviewStatus(?int $onlyCt = null): array
    {
        // kolik textů (návrh + PN) je posouzeno AI; neposouzený text má 0 bodů, což není totéž co „bez nálezu“
        $out = [];
        $filter = $onlyCt !== null ? ' WHERE ct = ?' : '';
        $params = $onlyCt !== null ? [$onlyCt] : [];
        foreach ($this->db->all('SELECT ct, COUNT(*) AS n FROM pn' . $filter . ' GROUP BY ct', $params) as $r) {
            $ct = (int)$r['ct'];
            $out[$ct] ??= ['total' => 0, 'done' => 0];
            $out[$ct]['total'] += (int)$r['n'];
        }
        foreach ($this->db->all('SELECT ct, COUNT(*) AS n FROM pn_text' . $filter . ($filter ? ' AND' : ' WHERE') . ' ai_at IS NOT NULL GROUP BY ct', $params) as $r) {
            $ct = (int)$r['ct'];
            $out[$ct] ??= ['total' => 0, 'done' => 0];
            $out[$ct]['done'] += (int)$r['n'];
        }
        foreach ($this->db->all('SELECT ct, ai_at FROM tisk_text' . $filter, $params) as $r) {
            $ct = (int)$r['ct'];
            $out[$ct] ??= ['total' => 0, 'done' => 0];
            $out[$ct]['total']++;
            $out[$ct]['done'] += $r['ai_at'] !== null ? 1 : 0;
        }
        return $out;
    }

    public static function reviewLabel(array $status): ?string
    {
        // null = vše posouzeno (nebo není co posuzovat)
        $total = (int)($status['total'] ?? 0);
        $done = (int)($status['done'] ?? 0);
        if ($total === 0 || $done >= $total) {
            return null;
        }
        return $done === 0 ? 'Text neposouzen' : "Posouzeno $done z $total textů";
    }

    public function flags(array $t, ?array $surge = null, ?array $review = null): array
    {
        // štítky v pořadí, v jakém se zobrazují
        $surge ??= $this->surgeSet();
        $flags = [];
        if ($t['typ_navrhu'] === 'poslanecký') {
            $flags[] = ['mp', 'Poslanecký návrh'];
        } elseif ($t['typ_navrhu'] === 'senátní') {
            $flags[] = ['mp', 'Senátní návrh'];
        } elseif ($t['typ_navrhu'] === 'krajský') {
            $flags[] = ['mp', 'Krajský návrh'];
        }
        if ((int)$t['p90ok']) {
            $flags[] = ['fast', 'Schváleno v 1. čtení'];
        } elseif ((int)$t['p90']) {
            $flags[] = ['fast', 'Navrženo § 90/2'];
        }
        if ((int)$t['zl']) {
            $flags[] = ['cut', 'Zkrácená lhůta'];
        }
        if (isset($surge[(int)$t['ct']])) {
            $flags[] = ['surge', 'Nával PN'];
        }
        $review ??= $this->reviewStatus((int)$t['ct']);
        $label = self::reviewLabel($review[(int)$t['ct']] ?? []);
        if ($label !== null) {
            $flags[] = ['pending', $label];
        }
        return $flags;
    }
}
