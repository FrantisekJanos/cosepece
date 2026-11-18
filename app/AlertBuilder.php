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
                'Předkladatel: ' . $t['navrhovatel'] . '. Návrh poslanců ani senátorů neprochází meziresortním připomínkovým řízením.',
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
        return $n + $this->fromPublication($tisky, $posted);
    }

    private function fromPublication(array $tisky, int $posted): int
    {
        // „Vyhlášeno“ až podle doloženého data vyhlášení (hist.zaver_publik). Přechod do stavu Sbírka
        // nastává už při doručení nebo odeslání k publikaci, tedy o dny až týdny dřív.
        $n = 0;
        foreach ($tisky as $ct => $t) {
            $day = (string)($t['vyhlaseno'] ?? '');
            if ($day === '') {
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
        $n = 0;
        $this->db->pdo->beginTransaction();
        foreach ($groups as $list) {
            $ct = (int)$list[0]['ct'];
            $t = $tisky[$ct] ?? null;
            $ids = array_map(fn($r) => (int)$r['id_dokument'], $list);
            if ($t !== null) {
                $count = count($list);
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
                $kind = $count >= $threshold ? 'surge' : 'pn';
                $title = ($kind === 'surge' ? 'Nával pozměňovacích návrhů: ' : $this->pnLabel($count) . ' k tisku: ') . $t['nazev'];
                $body = $count . ' ' . $this->pnWord($count) . ' od ' . count($authors) . ' ' . (count($authors) === 1 ? 'poslance' : 'poslanců')
                    . ': ' . implode(', ', $names) . '.';
                $n += $this->add($kind . ':' . $ct . ':' . max($ids), $kind, $t, (string)end($list)['cas'], $title, $body, $posted);
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

    private function add(string $key, string $kind, array $t, string $eventAt, string $title, string $body, int $posted): int
    {
        if ($this->db->value('SELECT 1 FROM alerts WHERE alert_key = ?', [$key]) !== null) {
            return 0;
        }
        $this->db->run(
            'INSERT INTO alerts (alert_key, kind, ct, event_at, created_at, title, body, watched, posted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [$key, $kind, (int)$t['ct'], $eventAt, date('Y-m-d H:i:s'), $title, $body, (int)$this->isWatched($t), $posted]
        );
        return 1;
    }
}
