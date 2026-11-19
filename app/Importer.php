<?php


declare(strict_types=1);

namespace Hlidac;

use RuntimeException;

final class Importer
{

    private const DRUHY = [1, 2, 3];

    public function __construct(private Db $db, private Source $source)
    {
    }

    public function run(): array
    {
        [$obdobiId, $obdobiCislo] = $this->currentPeriod();

        $osoby = $this->loadOsoby();
        $tisky = $this->loadTisky($obdobiId);
        $kroky = $this->loadKroky($tisky);
        $pn = $this->loadPn($obdobiId, $osoby);

        $pnByCt = [];
        foreach ($pn as $row) {
            $ct = $row['ct'];
            $pnByCt[$ct]['count'] = ($pnByCt[$ct]['count'] ?? 0) + 1;
            $pnByCt[$ct]['last'] = max($pnByCt[$ct]['last'] ?? '', (string)$row['cas']);
        }

        $krokyByTisk = [];
        foreach ($kroky as $krok) {
            $krokyByTisk[$krok['id_tisk']][] = $krok;
        }

        $tiskyRows = [];
        foreach ($tisky as $tisk) {
            $list = $krokyByTisk[$tisk['id_tisk']] ?? [];
            $a = Analyzer::analyze($list);
            $tiskyRows[] = $tisk + [
                'faze' => $a['faze'],
                'posledni' => $a['posledni'],
                'p90' => (int)($a['p90'] || $tisk['rj']),
                'p90ok' => (int)$a['p90ok'],
                'zl' => (int)($a['zl'] || Analyzer::committeeCut($tisk['lhuta'])),
                'pn_count' => $pnByCt[$tisk['ct']]['count'] ?? 0,
                'pn_last' => $pnByCt[$tisk['ct']]['last'] ?? null,
                'sbirka' => $this->sbirka($list),
                'vyhlaseno' => $this->vyhlaseno($list),
            ];
        }

        $problem = $this->validate($obdobiId, $tiskyRows, $kroky, $pn);
        if ($problem !== null) {
            $this->db->setMeta('import_error', date('Y-m-d H:i:s') . ' ' . $problem);
            throw new RuntimeException('Import zastaven, data se nepřepsala: ' . $problem);
        }


        $oldId = (int)$this->db->getMeta('obdobi_id', '0');
        $oldCislo = (int)$this->db->getMeta('obdobi_cislo', '0');
        $this->db->pdo->beginTransaction();
        try {
            if ($oldCislo > 0) {

                $this->db->run('UPDATE alerts SET obdobi = ? WHERE obdobi IS NULL', [$oldCislo]);
            }
            if ($oldId > 0 && $oldId !== $obdobiId) {
                $this->switchPeriod($oldCislo);
            }
            $this->db->run('DELETE FROM tisky');
            $this->db->run('DELETE FROM kroky');
            $this->db->run('DELETE FROM pn');
            $this->db->insertMany('tisky', [
                'id_tisk', 'ct', 'druh', 'typ_navrhu', 'navrhovatel', 'nazev', 'uplny_nazev', 'predlozeno',
                'faze', 'posledni', 'p90', 'p90ok', 'zl', 'pn_count', 'pn_last', 'zakony', 'sbirka',
                'vyhlaseno', 'lhuta',
            ], $tiskyRows);
            $this->db->insertMany('kroky', [
                'id_hist', 'id_tisk', 'ct', 'datum', 'typ_stavu', 'stav', 'akce_id', 'akce', 'id_hlasovani',
            ], $kroky);
            $this->db->insertMany('pn', ['id_dokument', 'cislo', 'ct', 'id_osoba', 'osoba', 'cas'], $pn);
            $this->db->setMeta('obdobi_id', (string)$obdobiId);
            $this->db->setMeta('obdobi_cislo', (string)$obdobiCislo);
            $this->db->setMeta('last_sync', date('Y-m-d H:i:s'));
            $this->db->setMeta('import_error', '');
            $this->db->pdo->commit();
        } catch (\Throwable $ex) {
            $this->db->pdo->rollBack();
            throw $ex;
        }

        return ['obdobi' => $obdobiCislo, 'tisky' => count($tiskyRows), 'kroky' => count($kroky), 'pn' => count($pn)];
    }

    private function switchPeriod(int $oldCislo): void
    {



        foreach ($this->db->all('SELECT alert_key FROM alerts WHERE obdobi = ?', [$oldCislo]) as $a) {
            $key = (string)$a['alert_key'];
            if (str_starts_with($key, $oldCislo . '/')) {
                continue;
            }
            $this->db->run('UPDATE alerts SET alert_key = ? WHERE alert_key = ?', [$oldCislo . '/' . $key, $key]);
            $this->db->run('UPDATE post_log SET alert_key = ? WHERE alert_key = ?', [$oldCislo . '/' . $key, $key]);
        }
        $this->db->run('UPDATE alerts SET posted = 3 WHERE posted = 0');
        $this->db->run('DELETE FROM tisk_text');
        $this->db->run('DELETE FROM pn_text');
        $this->db->run("DELETE FROM meta WHERE k = 'bootstrapped'");
    }

    private function validate(int $obdobiId, array $tisky, array $kroky, array $pn): ?string
    {

        $samePeriod = (int)$this->db->getMeta('obdobi_id', '0') === $obdobiId;
        $before = [
            'tisky' => (int)$this->db->value('SELECT COUNT(*) FROM tisky'),
            'kroky' => (int)$this->db->value('SELECT COUNT(*) FROM kroky'),
            'pn' => (int)$this->db->value('SELECT COUNT(*) FROM pn'),
        ];
        $now = ['tisky' => count($tisky), 'kroky' => count($kroky), 'pn' => count($pn)];
        if ($samePeriod) {

            foreach ($now as $table => $n) {
                if ($before[$table] >= 20 && $n < $before[$table] * 0.8) {
                    return "propad počtu řádků $table z {$before[$table]} na $n";
                }
            }
        }
        if ($tisky === [] && $pn !== []) {
            return 'export neobsahuje žádné tisky, ale obsahuje pozměňovací návrhy';
        }
        if ($tisky === [] && $kroky !== []) {
            return 'export obsahuje kroky historie, ale žádné tisky';
        }
        if ($tisky !== [] && $kroky === []) {
            return 'export obsahuje tisky, ale žádné kroky historie';
        }
        if ($pn !== [] && $tisky !== []) {

            $cts = array_flip(array_map(fn(array $t): int => (int)$t['ct'], $tisky));
            $linked = count(array_filter($pn, fn(array $p): bool => isset($cts[(int)$p['ct']])));
            if ($linked === 0) {
                return 'žádný pozměňovací návrh se neváže na importované tisky';
            }
        }
        return null;
    }

    private function currentPeriod(): array
    {

        $best = null;
        foreach ($this->source->rows('poslanci', 'organy.unl') as $c) {
            if (($c[2] ?? '') !== '11' || !preg_match('~^PSP(\d+)$~', $c[3] ?? '', $m)) {
                continue;
            }
            $cislo = (int)$m[1];
            $open = trim($c[7] ?? '') === '';
            if ($best === null || ($open && !$best['open']) || ($open === $best['open'] && $cislo > $best['cislo'])) {
                $best = ['id' => (int)$c[0], 'cislo' => $cislo, 'open' => $open];
            }
        }
        if ($best === null) {
            throw new RuntimeException('Nepodařilo se určit volební období.');
        }
        return [$best['id'], $best['cislo']];
    }

    private function loadOsoby(): array
    {

        $out = [];
        foreach ($this->source->rows('poslanci', 'osoby.unl') as $c) {
            $out[(int)$c[0]] = trim(($c[3] ?? '') . ' ' . ($c[2] ?? ''));
        }
        return $out;
    }

    private function loadTisky(int $obdobiId): array
    {
        $out = [];
        foreach ($this->source->rows('tisky', 'tisky.unl') as $c) {

            if ((int)($c[7] ?? 0) !== $obdobiId || (int)($c[4] ?? 1) !== 0 || !in_array((int)$c[1], self::DRUHY, true)) {
                continue;
            }
            $uplny = trim($c[15] ?? '');
            $out[(int)$c[0]] = [
                'id_tisk' => (int)$c[0],
                'ct' => (int)$c[3],
                'druh' => (int)$c[1],
                'typ_navrhu' => Analyzer::typNavrhu((int)$c[1], $uplny, (int)($c[5] ?? 0)),
                'navrhovatel' => trim($c[9] ?? ''),
                'nazev' => trim($c[10] ?? ''),
                'uplny_nazev' => $uplny,
                'predlozeno' => $this->czDate($c[11] ?? ''),
                'zakony' => implode(',', Analyzer::zakony($uplny)),

                'rj' => trim($c[18] ?? '') === '1',

                'lhuta' => (int)($c[17] ?? 0) ?: null,
            ];
        }
        return $out;
    }

    private function loadKroky(array $tisky): array
    {
        $prechody = [];
        foreach ($this->source->rows('tisky', 'prechody.unl') as $c) {
            $prechody[(int)$c[0]] = ['kam' => (int)$c[2], 'akce' => (int)$c[3]];
        }
        $stavToTyp = $this->source->map('tisky', 'stavy.unl', 0, 1);
        $typy = $this->source->map('tisky', 'typ_stavu.unl', 0, 1);
        $akce = $this->source->map('tisky', 'typ_akce.unl', 0, 1);

        $out = [];
        foreach ($this->source->rows('tisky', 'hist.unl') as $c) {
            $idTisk = (int)($c[1] ?? 0);
            if (!isset($tisky[$idTisk])) {
                continue;
            }
            $prechod = $prechody[(int)($c[4] ?? 0)] ?? null;
            if ($prechod === null) {
                continue;
            }
            $typ = (int)($stavToTyp[$prechod['kam']] ?? 0);
            $out[] = [
                'id_hist' => (int)$c[0],
                'id_tisk' => $idTisk,
                'ct' => $tisky[$idTisk]['ct'],
                'datum' => $this->isoDate($c[2] ?? ''),
                'typ_stavu' => $typ,
                'stav' => $typy[$typ] ?? '',
                'akce_id' => $prechod['akce'],
                'akce' => $akce[$prechod['akce']] ?? '',

                'id_hlasovani' => (int)($c[3] ?? 0) ?: null,

                '_sb' => trim($c[13] ?? '') !== '' ? trim($c[13]) . '/' . substr($this->czDate($c[11] ?? '') ?? '', 0, 4) : null,

                '_vyhl' => $this->czDate($c[11] ?? ''),
            ];
        }

        usort($out, fn(array $a, array $b): int => $a['id_hist'] <=> $b['id_hist']);
        return $out;
    }

    private function loadPn(int $obdobiId, array $osoby): array
    {
        $out = [];
        foreach ($this->source->rows('sd', 'sd_dokument.unl') as $c) {

            if ((int)($c[1] ?? 0) !== $obdobiId || (int)($c[3] ?? 0) !== 13) {
                continue;
            }
            $idOsoba = (int)($c[7] ?? 0);
            $out[] = [
                'id_dokument' => (int)$c[0],
                'cislo' => (int)$c[2],
                'ct' => (int)$c[6],
                'id_osoba' => $idOsoba ?: null,
                'osoba' => $osoby[$idOsoba] ?? trim($c[5] ?? ''),
                'cas' => $this->isoDate($c[8] ?? ''),
            ];
        }
        return $out;
    }

    private function sbirka(array $kroky): ?string
    {
        foreach (array_reverse($kroky) as $krok) {
            if (!empty($krok['_sb']) && !str_ends_with($krok['_sb'], '/')) {
                return $krok['_sb'];
            }
        }
        return null;
    }

    private function vyhlaseno(array $kroky): ?string
    {

        foreach (array_reverse($kroky) as $krok) {
            if (!empty($krok['_vyhl'])) {
                return $krok['_vyhl'];
            }
        }
        return null;
    }

    private function czDate(string $value): ?string
    {

        $value = trim($value);
        if (preg_match('~^(\d{2})\.(\d{2})\.(\d{4})$~', $value, $m)) {
            return "$m[3]-$m[2]-$m[1]";
        }
        return null;
    }

    private function isoDate(string $value): ?string
    {

        $value = trim($value);
        if ($value === '') {
            return null;
        }
        if (preg_match('~^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$~', $value)) {
            return $value . ':00';
        }
        return substr($value, 0, 19);
    }
}
