<?php
// Tvorba upozornění z importovaných dat. Každé upozornění má jednoznačný klíč,
// takže opakované spuštění nevytvoří duplicity.

declare(strict_types=1);

namespace Hlidac;

final class AlertBuilder
{
    public function __construct(private Db $db, private array $config)
    {
    }

    public function run(): int
    {
        // při prvním běhu se historie jen zapíše (posted = 2), aby se na sítě neposlalo stovky starých zpráv
        $bootstrap = $this->db->getMeta('bootstrapped') === null;
        $posted = $bootstrap ? 2 : 0;
        $created = 0;

        $tisky = [];
        foreach ($this->db->all('SELECT * FROM tisky') as $t) {
            $tisky[(int)$t['ct']] = $t;
        }

        $created += $this->fromBills($tisky, $posted);
        $created += $this->fromSteps($tisky, $posted);
        $created += $this->fromAmendments($tisky, $bootstrap, $posted);
        $created += $this->fromUpcoming($posted);
        $created += $this->fromRegulations($posted);

        if ($bootstrap) {
            $this->db->setMeta('bootstrapped', date('Y-m-d H:i:s'));
        }
        return $created;
    }

    private function fromBills(array $tisky, int $posted): int
    {
        // nový poslanecký nebo senátní návrh zákona
        $n = 0;
        foreach ($tisky as $t) {
            if ($t['typ_navrhu'] === 'vládní' || (int)$t['druh'] !== 2) {
                continue;
            }
            $n += $this->add(
                'newbill:' . $t['ct'],
                'newbill',
                $t,
                ($t['predlozeno'] ?: date('Y-m-d')) . ' 00:00:00',
                'Nový ' . $t['typ_navrhu'] . ' návrh: ' . $t['nazev'],
                'Předkladatel: ' . $t['navrhovatel'] . '. Návrh poslanců, Senátu ani krajů neprochází meziresortním připomínkovým řízením.',
                $posted
            );
        }
        return $n;
    }

    private function fromSenate(array $tisky, int $posted): int
    {
        // Senát (#6): jiný výsledek než schválení a přehlasování Senátu nebo veta Sněmovnou.
        // Jen popis postupu, bonus ke skóre je 0. První běh dosavadní stav jen zapíše (posted = 2).
        $byCt = [];
        foreach ($this->db->all('SELECT ct, typ_stavu, akce_id, datum FROM kroky ORDER BY id_hist') as $k) {
            $byCt[(int)$k['ct']][] = $k;
        }
        if ($this->db->getMeta('senate_alerts_bootstrapped') === null) {
            $posted = 2;
            $this->db->setMeta('senate_alerts_bootstrapped', date('Y-m-d H:i:s'));
        }
        $titles = ['vracen' => 'Senát vrátil s pozměňovacími návrhy', 'zamitnut' => 'Senát zamítl', 'nezabyval' => 'Senát se odmítl zabývat',
            'necinny' => 'Senát se ve lhůtě nevyjádřil', 'bez_usneseni' => 'Senát projednal bez usnesení'];
        $n = 0;
        foreach ($tisky as $ct => $t) {
            $s = Analyzer::senate($byCt[$ct] ?? []);
            $lines = Analyzer::senateLines($s);
            if ($s['vysledek'] !== null && isset($titles[$s['vysledek']])) {
                $n += $this->add("senate:$ct", 'senate', $t, ($s['rozhodnuto'] ?? date('Y-m-d')) . ' 00:00:00',
                    $titles[$s['vysledek']] . ': ' . $t['nazev'], $lines[0], $posted);
            }
            if ($s['snemovna'] === 'setrvala') {
                $n += $this->add("override:$ct", 'override', $t, ($s['snemovna_datum'] ?? date('Y-m-d')) . ' 00:00:00',
                    'Sněmovna přehlasovala Senát: ' . $t['nazev'], $lines[0] . ' ' . $lines[1], $posted);
            }
            if ($s['veto_prehlasovano'] !== null) {
                $n += $this->add("vetoover:$ct", 'override', $t, $s['veto_prehlasovano'] . ' 00:00:00',
                    'Sněmovna přehlasovala veto prezidenta: ' . $t['nazev'],
                    'Prezident zákon nepodepsal' . ($s['veto'] !== null ? ' (' . formatDate($s['veto']) . ')' : '') . ', Sněmovna na něm setrvala (' . formatDate($s['veto_prehlasovano']) . ').', $posted);
            }
        }
        return $n;
    }

    private function fromSteps(array $tisky, int $posted): int
    {
        $n = 0;
        $seen = [];
        foreach ($this->db->all('SELECT * FROM kroky ORDER BY id_hist') as $k) {
            $ct = (int)$k['ct'];
            $t = $tisky[$ct] ?? null;
            if ($t === null) {
                continue;
            }
            $typ = (int)$k['typ_stavu'];
            $akce = (int)$k['akce_id'];
            $when = $k['datum'] ?: date('Y-m-d H:i:s');

            if (in_array($typ, Analyzer::STAV_P90, true) && !isset($seen["p90:$ct"])) {
                $seen["p90:$ct"] = true;
                $n += $this->add("p90:$ct", 'p90', $t, $when,
                    'Navrženo schválit už v 1. čtení: ' . $t['nazev'],
                    'Postup podle § 90 odst. 2 jednacího řádu: zákon může projít bez 2. a 3. čtení. Zablokovat to může námitka dvou poslaneckých klubů nebo 50 poslanců.',
                    $posted);
            }
            if (in_array($akce, Analyzer::AKCE_ZKRACENI, true)) {
                $n += $this->add('cut:' . $k['id_hist'], 'cut', $t, $when,
                    'Zkrácená lhůta: ' . $t['nazev'],
                    'Sněmovna změnila lhůtu (' . $k['akce'] . '). Na posouzení pozměňovacích návrhů zbývá méně času.',
                    $posted);
            }
            if ($typ === Analyzer::STAV_ODESLANO_SENATU && !isset($seen["passed:$ct"])) {
                $seen["passed:$ct"] = true;
                $n += $this->add("passed:$ct", 'passed', $t, $when,
                    'Sněmovna schválila: ' . $t['nazev'],
                    'Zákon míří do Senátu. Písemných pozměňovacích návrhů: ' . (int)$t['pn_count'] . '.',
                    $posted);
            }
        }
        // návrh na § 90/2 evidovaný u tisku (tisky.rj), ke kterému ještě není krok v historii
        foreach ($tisky as $ct => $t) {
            if ((int)$t['p90'] && !isset($seen["p90:$ct"])) {
                $n += $this->add("p90:$ct", 'p90', $t, ($t['predlozeno'] ?: date('Y-m-d')) . ' 00:00:00',
                    'Navrženo schválit už v 1. čtení: ' . $t['nazev'],
                    'Předkladatel navrhuje postup podle § 90 odst. 2 jednacího řádu: zákon může projít bez 2. a 3. čtení. Zablokovat to může námitka dvou poslaneckých klubů nebo 50 poslanců.',
                    $posted);
            }
        }
        return $n + $this->fromSenate($tisky, $posted) + $this->fromPublication($tisky, $posted);
    }

    private function fromPublication(array $tisky, int $posted): int
    {
        // „Vyhlášeno“ až podle doloženého data vyhlášení (hist.zaver_publik). Přechod do stavu Sbírka
        // nastává už při doručení nebo odeslání k publikaci, tedy o dny až týdny dřív.
        $n = 0;
        foreach ($tisky as $ct => $t) {
            $day = (string)($t['vyhlaseno'] ?? '');
            if ($day === '') {
                // starší verze vytvářela „Vyhlášeno“ už při doručení nebo odeslání k publikaci; bez doloženého
                // data vyhlášení upozornění odstraníme (po vyhlášení vznikne znovu se správným datem).
                // Skutečně odeslané na sítě (posted = 1) necháme kvůli historii.
                $this->db->run("DELETE FROM alerts WHERE alert_key = ? AND kind = 'law' AND posted <> 1", ["law:$ct"]);
                continue;
            }
            $eventAt = $day . ' 00:00:00';
            $body = $t['sbirka']
                ? 'Zákon vyšel jako č. ' . $t['sbirka'] . ' Sb. (vyhlášen ' . date('j. n. Y', (int)strtotime($day)) . ').'
                : 'Zákon byl vyhlášen ve Sbírce zákonů ' . date('j. n. Y', (int)strtotime($day)) . '.';
            $added = $this->add("law:$ct", 'law', $t, $eventAt, 'Vyhlášeno ve Sbírce zákonů: ' . $t['nazev'], $body, $posted);
            if ($added === 0) {
                // dřív vytvořené upozornění mohlo dostat datum doručení nebo odeslání – opravíme datum i text
                $this->db->run("UPDATE alerts SET event_at = ?, body = ? WHERE alert_key = ? AND kind = 'law' AND (event_at <> ? OR body <> ?)",
                    [$eventAt, $body, "law:$ct", $eventAt, $body]);
            }
            $n += $added;
        }
        return $n;
    }

    private function fromAmendments(array $tisky, bool $bootstrap, int $posted): int
    {
        // pozměňovací návrhy, které zatím nebyly v žádném upozornění
        $rows = $this->db->all('SELECT p.* FROM pn p LEFT JOIN pn_seen s ON s.id_dokument = p.id_dokument
                                WHERE s.id_dokument IS NULL ORDER BY p.cas');
        if ($rows === []) {
            return 0;
        }

        // při bootstrapu skupiny po dnech (historie), jinak jedna skupina na tisk za běh
        $groups = [];
        foreach ($rows as $r) {
            $key = $bootstrap ? $r['ct'] . '|' . substr((string)$r['cas'], 0, 10) : (string)$r['ct'];
            $groups[$key][] = $r;
        }

        $threshold = (int)($this->config['surge_threshold'] ?? 10);
        // nával = aspoň $threshold PN k tisku v jednom kalendářním dni podle času podání (sd_dokument),
        // nezávisle na tom, jak často běží synchronizace (#7 bod 9)
        $perDay = [];
        foreach ($this->db->all('SELECT ct, cas FROM pn') as $r) {
            $day = substr((string)$r['cas'], 0, 10);
            $perDay[(int)$r['ct']][$day] = ($perDay[(int)$r['ct']][$day] ?? 0) + 1;
        }
        $n = 0;
        $this->db->pdo->beginTransaction();
        foreach ($groups as $list) {
            $ct = (int)$list[0]['ct'];
            $t = $tisky[$ct] ?? null;
            $ids = array_map(fn($r) => (int)$r['id_dokument'], $list);
            if ($t === null) {
                // PN k tisku, který zatím není v přehledu (dorazil dřív než předloha): počká na další běh (#2 bod 10)
                continue;
            }
            if ($t !== null) {
                $count = count($list);
                // den s návalem, ke kterému ještě nebylo upozornění „Nával“
                $surgeDay = null;
                foreach (array_unique(array_map(fn($r) => substr((string)$r['cas'], 0, 10), $list)) as $day) {
                    if (($perDay[$ct][$day] ?? 0) >= $threshold && $this->db->value(
                        "SELECT 1 FROM alerts WHERE kind = 'surge' AND ct = ? AND event_at LIKE ? AND (obdobi IS NULL OR obdobi = ?)",
                        [$ct, $day . '%', (int)$this->db->getMeta('obdobi_cislo', '0')]) === null) {
                        $surgeDay = $day;
                    }
                }
                $authors = [];
                foreach ($list as $r) {
                    $authors[$r['osoba']] = ($authors[$r['osoba']] ?? 0) + 1;
                }
                arsort($authors);
                $names = [];
                foreach (array_slice($authors, 0, 5, true) as $name => $c) {
                    $names[] = $c > 1 ? "$name ($c)" : $name;
                }
                if (count($authors) > 5) {
                    $names[] = 'a další';
                }
                $kind = $surgeDay !== null ? 'surge' : 'pn';
                $title = ($kind === 'surge' ? 'Nával pozměňovacích návrhů: ' : $this->pnLabel($count) . ' k tisku: ') . $t['nazev'];
                $body = $count . ' ' . $this->pnWord($count) . ' od ' . count($authors) . ' ' . (count($authors) === 1 ? 'poslance' : 'poslanců')
                    . ': ' . implode(', ', $names) . '.';
                if ($surgeDay !== null) {
                    $dayTotal = $perDay[$ct][$surgeDay];
                    $body = 'Dne ' . formatDate($surgeDay) . ' podáno ' . $dayTotal . ' ' . $this->pnWord($dayTotal) . '. ' . $body;
                }
                $when = $surgeDay !== null ? max(array_filter(array_map(fn($r) => (string)$r['cas'], $list),
                    fn(string $c) => str_starts_with($c, $surgeDay))) : (string)end($list)['cas'];
                $n += $this->add($kind . ':' . $ct . ':' . max($ids), $kind, $t, $when, $title, $body, $posted);
            }
            foreach ($ids as $id) {
                $this->db->run('INSERT INTO pn_seen (id_dokument) VALUES (?)', [$id]);
            }
        }
        $this->db->pdo->commit();
        return $n;
    }

    private function pnLabel(int $count): string
    {
        return $count === 1 ? 'Nový pozměňovací návrh' : $count . ' ' . ($count < 5 ? 'nové pozměňovací návrhy' : 'nových pozměňovacích návrhů');
    }

    private function pnWord(int $count): string
    {
        return $count === 1 ? 'pozměňovací návrh' : ($count < 5 ? 'pozměňovací návrhy' : 'pozměňovacích návrhů');
    }

    private function isWatched(array $t): bool
    {
        $laws = array_filter(explode(',', (string)$t['zakony']));
        $watchLaws = $this->config['watch_laws'] ?? [];
        $watchTisky = array_map('intval', $this->config['watch_tisky'] ?? []);
        return array_intersect($laws, $watchLaws) !== [] || in_array((int)$t['ct'], $watchTisky, true);
    }

    /** Úrovně upozornění na připravované zákony: druh => [bonus ke skóre, popis] (skóre tisku tu není, je 0). */
    public const UPCOMING_KINDS = [
        'prep_velmi' => 50, // připomínkové řízení kratší než 10 dní: Závažné (i na sítě)
        'prep_kratke' => 30, // kratší než 3 týdny: Pozor
        'prep_plan' => 30,   // předloženo mimo plán legislativních prací vlády: Pozor
        'prep_vlada' => 0,   // vláda návrh schválila: běžná agenda
        'prep_narizeni' => 30, // nařízení vlády s připomínkami kratšími než 10 dní: Pozor (na sítě nejde)
    ];

    private function fromUpcoming(int $posted): int
    {
        // připravované zákony u vlády (GovPrep): krátké připomínkové řízení, mimo plán, schválení vládou;
        // při prvním běhu této funkce se dosavadní stav jen zapíše (posted = 2), na sítě se neposílá.
        // Bez dat (ODok vypnutý nebo nedostupný) se první běh nepočítá: jinak by se celý dosavadní stav
        // při příštím běhu rozeslal jako novinky.
        $upcoming = $this->db->all('SELECT * FROM pripravovane');
        if ($upcoming === []) {
            return 0;
        }
        if ($this->db->getMeta('prep_alerts_bootstrapped') === null) {
            $posted = 2;
            $this->db->setMeta('prep_alerts_bootstrapped', date('Y-m-d H:i:s'));
        }
        $phase = ['pripominky' => 'je v připomínkovém řízení', 'po_pripominkach' => 'je po připomínkovém řízení',
            'vlada' => 'čeká na projednání vládou', 'schvaleno' => 'vláda ho schválila'];
        // datum z otevřených dat jen ve tvaru RRRR-MM-DD, jinak dnešek (vadný údaj nesmí rozbít výpis ani RSS)
        $day = fn($value): string => preg_match('~^\d{4}-\d{2}-\d{2}$~', (string)$value) && strtotime((string)$value) !== false
            ? (string)$value : date('Y-m-d');
        $n = 0;
        foreach ($upcoming as $u) {
            // PID všech verzí materiálu: upozornění vydané k dřívější verzi se u nové verze neopakuje
            $pids = array_values(array_unique(array_filter(array_merge([(string)$u['pid']], explode(',', (string)($u['pidy'] ?? ''))))));
            $name = self::shortTitle((string)$u['nazev']);
            // „bill“ pro add(): bez čísla tisku, sledovaný podle čísel zákonů v názvu materiálu
            $t = ['ct' => 0, 'zakony' => implode(',', Laws::numbersIn((string)$u['nazev']))];
            $where = (string)$u['resort'] . ' · návrh ' . ($phase[$u['faze']] ?? $u['faze']) . '.';
            if ($u['dni'] !== null && (int)$u['dni'] < 21) {
                $days = (int)$u['dni'];
                $n += $this->addUpcoming('prep_review', $pids, $days < 10 ? 'prep_velmi' : 'prep_kratke', $t, $day($u['pripominky_od']) . ' 00:00:00',
                    'Připomínky k návrhu jen ' . $days . ' dní: ' . $name,
                    'Připomínkové řízení ' . formatDate($u['pripominky_od']) . ' – ' . formatDate($u['pripominky_do']) . ' (' . $days . ' dní, '
                    . (int)$u['mist'] . ' připomínkových míst); obvykle trvá 4 týdny. ' . $where, $posted);
            }
            if ((int)$u['mimo_plan'] === 1) {
                $reason = trim((string)$u['duvod']);
                $n += $this->addUpcoming('prep_plan', $pids, 'prep_plan', $t, $day($u['zalozeno']) . ' 00:00:00',
                    'Mimo plán legislativních prací vlády: ' . $name,
                    $where . ($reason !== '' ? ' Důvod podle předkladatele: ' . mb_substr($reason, 0, 300) : ''), $posted);
            }
            if ($u['faze'] === 'schvaleno' && !empty($u['vlada'])) {
                $n += $this->addUpcoming('prep_vlada', $pids, 'prep_vlada', $t, $day($u['vlada']) . ' 00:00:00',
                    'Vláda schválila návrh: ' . $name,
                    (string)$u['resort'] . '. Návrh míří do Sněmovny, kde dostane číslo sněmovního tisku.', $posted);
            }
        }
        return $n;
    }

    private function fromRegulations(int $posted): int
    {
        // připravovaná nařízení vlády: jen připomínkové řízení kratší než 10 dní (obvyklé jsou 3 týdny);
        // první běh jen zapíše dosavadní stav (posted = 2), bez dat se nepočítá (stejně jako u zákonů)
        $rows = $this->db->all('SELECT * FROM pripravovana_narizeni');
        if ($rows === []) {
            return 0;
        }
        if ($this->db->getMeta('prep_nv_alerts_bootstrapped') === null) {
            $posted = 2;
            $this->db->setMeta('prep_nv_alerts_bootstrapped', date('Y-m-d H:i:s'));
        }
        $n = 0;
        foreach ($rows as $u) {
            if ($u['dni'] === null || (int)$u['dni'] >= 10) {
                continue;
            }
            $days = (int)$u['dni'];
            $pids = array_values(array_unique(array_filter(array_merge([(string)$u['pid']], explode(',', (string)($u['pidy'] ?? ''))))));
            $from = (string)$u['pripominky_od'];
            // nařízení není zákon: sledované zákony se podle čísel v názvu nepárují (čísla nařízení a zákonů se překrývají)
            $n += $this->addUpcoming('prep_nv', $pids, 'prep_narizeni', ['ct' => 0, 'zakony' => ''],
                (preg_match('~^\d{4}-\d{2}-\d{2}$~', $from) ? $from : date('Y-m-d')) . ' 00:00:00',
                'Připomínky k nařízení vlády jen ' . $days . ' dní: ' . self::shortTitle((string)$u['nazev']),
                'Připomínkové řízení ' . formatDate($u['pripominky_od']) . ' – ' . formatDate($u['pripominky_do']) . ' (' . $days . ' dní, '
                . (int)$u['mist'] . ' připomínkových míst); u nařízení vlády obvykle trvá 3 týdny. ' . trim((string)$u['resort']), $posted);
        }
        return $n;
    }

    /**
     * Upozornění na připravovaný zákon: klíč „druh:PID“ nejnovější verze, ale jen když upozornění
     * téhož druhu ještě nevzniklo pod PID kterékoli verze materiálu.
     * @param string[] $pids PID verzí od nejnovější
     */
    private function addUpcoming(string $prefix, array $pids, string $kind, array $t, string $eventAt, string $title, string $body, int $posted): int
    {
        $keys = array_map(fn(string $pid) => $prefix . ':' . $pid, $pids);
        if ($keys === [] || $this->db->value('SELECT 1 FROM alerts WHERE alert_key IN (' . implode(',', array_fill(0, count($keys), '?')) . ')', $keys) !== null) {
            return 0;
        }
        return $this->add($keys[0], $kind, $t, $eventAt, $title, $body, $posted);
    }

    public static function shortTitle(string $title): string
    {
        // „Návrh zákona, kterým se mění zákon č. 90/2024 Sb., o zbraních…, ve znění…“ → bez „ve znění…“, nejvýš 180 znaků
        $t = trim((string)preg_replace(['~,?\s*ve znění pozdějších předpisů~u', '~\s+~u'], ['', ' '], $title));
        return mb_strlen($t) > 180 ? rtrim(mb_substr($t, 0, 179)) . '…' : $t;
    }

    private function add(string $key, string $kind, array $t, string $eventAt, string $title, string $body, int $posted): int
    {
        if ($this->db->value('SELECT 1 FROM alerts WHERE alert_key = ?', [$key]) !== null) {
            return 0;
        }
        $this->db->run(
            'INSERT INTO alerts (alert_key, kind, ct, event_at, created_at, title, body, watched, posted, obdobi) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [$key, $kind, (int)$t['ct'], $eventAt, date('Y-m-d H:i:s'), $title, $body, (int)$this->isWatched($t), $posted,
                (int)$this->db->getMeta('obdobi_cislo', '0') ?: null]
        );
        return 1;
    }
}
