<?php


declare(strict_types=1);

namespace Hlidac;

use App\Models\SeenAmendment;

final class AlertBuilder
{
    public function __construct(private Db $db, private array $config)
    {
    }

    public function run(): int
    {

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

        if ($bootstrap) {
            $this->db->setMeta('bootstrapped', date('Y-m-d H:i:s'));
        }
        return $created;
    }

    private function fromBills(array $tisky, int $posted): int
    {

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

        foreach ($tisky as $ct => $t) {
            if ((int)$t['p90'] && !isset($seen["p90:$ct"])) {
                $n += $this->add("p90:$ct", 'p90', $t, ($t['predlozeno'] ?: date('Y-m-d')) . ' 00:00:00',
                    'Navrženo schválit už v 1. čtení: ' . $t['nazev'],
                    'Předkladatel navrhuje postup podle § 90 odst. 2 jednacího řádu: zákon může projít bez 2. a 3. čtení. Zablokovat to může námitka dvou poslaneckých klubů nebo 50 poslanců.',
                    $posted);
            }
        }
        return $n + $this->fromPublication($tisky, $posted);
    }

    private function fromPublication(array $tisky, int $posted): int
    {

        $n = 0;
        foreach ($tisky as $ct => $t) {
            $day = (string)($t['vyhlaseno'] ?? '');
            if ($day === '') {

                $this->db->run("DELETE FROM alerts WHERE alert_key = ? AND kind = 'law' AND posted <> 1", ["law:$ct"]);
                continue;
            }
            $eventAt = $day . ' 00:00:00';
            $body = $t['sbirka']
                ? 'Zákon vyšel jako č. ' . $t['sbirka'] . ' Sb. (vyhlášen ' . date('j. n. Y', (int)strtotime($day)) . ').'
                : 'Zákon byl vyhlášen ve Sbírce zákonů ' . date('j. n. Y', (int)strtotime($day)) . '.';
            $added = $this->add("law:$ct", 'law', $t, $eventAt, 'Vyhlášeno ve Sbírce zákonů: ' . $t['nazev'], $body, $posted);
            if ($added === 0) {

                $this->db->run("UPDATE alerts SET event_at = ?, body = ? WHERE alert_key = ? AND kind = 'law' AND (event_at <> ? OR body <> ?)",
                    [$eventAt, $body, "law:$ct", $eventAt, $body]);
            }
            $n += $added;
        }
        return $n;
    }

    private function fromAmendments(array $tisky, bool $bootstrap, int $posted): int
    {

        $rows = $this->db->all('SELECT p.* FROM pn p LEFT JOIN pn_seen s ON s.id_dokument = p.id_dokument
                                WHERE s.id_dokument IS NULL ORDER BY p.cas');
        if ($rows === []) {
            return 0;
        }

        $groups = [];
        foreach ($rows as $r) {
            $key = $bootstrap ? $r['ct'] . '|' . substr((string)$r['cas'], 0, 10) : (string)$r['ct'];
            $groups[$key][] = $r;
        }

        $threshold = (int)($this->config['surge_threshold'] ?? 10);


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

                continue;
            }
            if ($t !== null) {
                $count = count($list);

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
                SeenAmendment::query()->create(['id_dokument' => $id]);
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
        'prep_velmi' => 50,
        'prep_kratke' => 30,
        'prep_plan' => 30,
        'prep_vlada' => 0,
    ];

    private function fromUpcoming(int $posted): int
    {


        if ($this->db->getMeta('prep_alerts_bootstrapped') === null) {
            $posted = 2;
            $this->db->setMeta('prep_alerts_bootstrapped', date('Y-m-d H:i:s'));
        }
        $phase = ['pripominky' => 'je v připomínkovém řízení', 'po_pripominkach' => 'je po připomínkovém řízení',
            'vlada' => 'čeká na projednání vládou', 'schvaleno' => 'vláda ho schválila'];
        $n = 0;
        foreach ($this->db->all('SELECT * FROM pripravovane') as $u) {
            $pid = (string)$u['pid'];
            $name = self::shortTitle((string)$u['nazev']);

            $t = ['ct' => 0, 'zakony' => implode(',', Laws::numbersIn((string)$u['nazev']))];
            $where = (string)$u['resort'] . ' · návrh ' . ($phase[$u['faze']] ?? $u['faze']) . '.';
            if ($u['dni'] !== null && (int)$u['dni'] < 21) {
                $days = (int)$u['dni'];
                $n += $this->add('prep_review:' . $pid, $days < 10 ? 'prep_velmi' : 'prep_kratke', $t, $u['pripominky_od'] . ' 00:00:00',
                    'Připomínky k návrhu jen ' . $days . ' dní: ' . $name,
                    'Připomínkové řízení ' . formatDate($u['pripominky_od']) . ' – ' . formatDate($u['pripominky_do']) . ' (' . $days . ' dní, '
                    . (int)$u['mist'] . ' připomínkových míst); obvykle trvá 4 týdny. ' . $where, $posted);
            }
            if ((int)$u['mimo_plan'] === 1) {
                $reason = trim((string)$u['duvod']);
                $n += $this->add('prep_plan:' . $pid, 'prep_plan', $t, ($u['zalozeno'] ?: date('Y-m-d')) . ' 00:00:00',
                    'Mimo plán legislativních prací vlády: ' . $name,
                    $where . ($reason !== '' ? ' Důvod podle předkladatele: ' . mb_substr($reason, 0, 300) : ''), $posted);
            }
            if ($u['faze'] === 'schvaleno' && !empty($u['vlada'])) {
                $n += $this->add('prep_vlada:' . $pid, 'prep_vlada', $t, $u['vlada'] . ' 00:00:00',
                    'Vláda schválila návrh: ' . $name,
                    (string)$u['resort'] . '. Návrh míří do Sněmovny, kde dostane číslo sněmovního tisku.', $posted);
            }
        }
        return $n;
    }

    public static function shortTitle(string $title): string
    {

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
