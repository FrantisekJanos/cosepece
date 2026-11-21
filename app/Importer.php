<?php
// Import otevřených dat psp.cz do databáze pro aktuální volební období.

declare(strict_types=1);

namespace Hlidac;

use RuntimeException;

final class Importer
{
    // druhy tisků, které sledujeme: vládní návrh, návrh zákona (poslanecký/senátní/krajský), státní rozpočet
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
        $souhrny = $this->loadSouhrny($tisky);
        $pn = $this->loadPn($obdobiId, $osoby);
        // kroky a PN ze stránek historie tisku (HistoryHtml), které noční otevřená data ještě nemají
        [$kroky, $pn, $html] = $this->mergeHtml($tisky, $kroky, $pn, $osoby);

        // agregace PN podle čísla tisku
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
                'souhrn_pn' => $souhrny[$tisk['id_tisk']]['cislo_za'] ?? null,
                'souhrn_pn_at' => $souhrny[$tisk['id_tisk']]['datum'] ?? null,
            ];
        }

        // neúplný nebo poškozený export nesmí přepsat poslední úplná data (#2)
        $problem = $this->validate($obdobiId, $tiskyRows, $kroky, $pn);
        if ($problem !== null) {
            $this->db->setMeta('import_error', date('Y-m-d H:i:s') . ' ' . $problem);
            throw new RuntimeException('Import zastaven, data se nepřepsala: ' . $problem);
        }

        // přepis tabulek se zdrojovými daty v jedné transakci
        $oldId = (int)$this->db->getMeta('obdobi_id', '0');
        $oldCislo = (int)$this->db->getMeta('obdobi_cislo', '0');
        $this->db->pdo->beginTransaction();
        try {
            if ($oldCislo > 0) {
                // upozornění bez období patří k období předchozího importu
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
                'vyhlaseno', 'lhuta', 'souhrn_pn', 'souhrn_pn_at',
            ], $tiskyRows);
            $this->db->insertMany('kroky', [
                'id_hist', 'id_tisk', 'ct', 'datum', 'typ_stavu', 'stav', 'akce_id', 'akce', 'id_hlasovani',
            ], $kroky);
            $this->db->insertMany('pn', ['id_dokument', 'cislo', 'ct', 'id_osoba', 'osoba', 'cas'], $pn);
            $this->db->setMeta('obdobi_id', (string)$obdobiId);
            $this->db->setMeta('obdobi_cislo', (string)$obdobiCislo);
            $this->db->setMeta('last_sync', date('Y-m-d H:i:s'));
            $this->db->setMeta('import_error', '');
            // záznamy ze stránek historie, které už přišly v otevřených datech, a neověřené starší než týden
            if ($html['confirmed_steps'] !== []) {
                $this->db->run('DELETE FROM html_kroky WHERE id_hist IN (' . implode(',', $html['confirmed_steps']) . ')');
            }
            if ($html['confirmed_pn'] !== []) {
                $this->db->run('DELETE FROM html_pn WHERE id_dokument IN (' . implode(',', $html['confirmed_pn']) . ')');
            }
            $stale = date('Y-m-d H:i:s', time() - HistoryHtml::KEEP_DAYS * 86400);
            $this->db->run('DELETE FROM html_kroky WHERE seen_at < ?', [$stale]);
            $this->db->run('DELETE FROM html_pn WHERE seen_at < ?', [$stale]);
            $this->db->pdo->commit();
        } catch (\Throwable $ex) {
            $this->db->pdo->rollBack();
            throw $ex;
        }

        return ['obdobi' => $obdobiCislo, 'tisky' => count($tiskyRows), 'kroky' => count($kroky), 'pn' => count($pn),
            'html_kroky' => $html['steps'], 'html_pn' => $html['pn']];
    }

    /**
     * Přimíchá kroky a PN ze stránek historie tisku (tabulky html_kroky, html_pn), které v otevřených datech
     * ještě nejsou; otevřená data mají přednost. Vrací [kroky, PN, údaje pro úklid a výsledek].
     */
    private function mergeHtml(array $tisky, array $kroky, array $pn, array $osoby): array
    {
        $info = ['steps' => 0, 'pn' => 0, 'confirmed_steps' => [], 'confirmed_pn' => []];
        // neověřené záznamy starší než týden se nepoužijí (a po zápisu se smažou)
        $stale = date('Y-m-d H:i:s', time() - HistoryHtml::KEEP_DAYS * 86400);
        $htmlSteps = $this->db->all('SELECT * FROM html_kroky WHERE seen_at >= ?', [$stale]);
        $htmlPn = $this->db->all('SELECT * FROM html_pn WHERE seen_at >= ?', [$stale]);
        if ($htmlSteps === [] && $htmlPn === []) {
            return [$kroky, $pn, $info];
        }
        $known = array_fill_keys(array_column($kroky, 'id_hist'), true);
        $map = $this->stepMapper();
        foreach ($htmlSteps as $h) {
            $id = (int)$h['id_hist'];
            if (isset($known[$id])) {
                $info['confirmed_steps'][] = $id;
                continue;
            }
            $idTisk = (int)$h['id_tisk'];
            $row = isset($tisky[$idTisk]) ? $map((int)$h['id_prechod']) : null;
            if ($row === null) {
                continue;
            }
            $kroky[] = ['id_hist' => $id, 'id_tisk' => $idTisk, 'ct' => $tisky[$idTisk]['ct'],
                'datum' => $h['datum'] ?: $h['seen_at'], 'id_hlasovani' => null, '_sb' => null, '_vyhl' => null] + $row;
            $info['steps']++;
        }
        usort($kroky, fn(array $a, array $b): int => $a['id_hist'] <=> $b['id_hist']);
        $knownPn = array_fill_keys(array_column($pn, 'id_dokument'), true);
        $knownNo = array_fill_keys(array_map(fn(array $p): string => $p['ct'] . '|' . $p['cislo'], $pn), true);
        $cts = array_fill_keys(array_column($tisky, 'ct'), true);
        foreach ($htmlPn as $h) {
            $id = (int)$h['id_dokument'];
            if (isset($knownPn[$id]) || isset($knownNo[$h['ct'] . '|' . $h['cislo']])) {
                $info['confirmed_pn'][] = $id;
                continue;
            }
            if (!isset($cts[(int)$h['ct']])) {
                continue;
            }
            $idOsoba = (int)$h['id_osoba'];
            $pn[] = ['id_dokument' => $id, 'cislo' => (int)$h['cislo'], 'ct' => (int)$h['ct'], 'id_osoba' => $idOsoba ?: null,
                'osoba' => $osoby[$idOsoba] ?? (string)$h['osoba'], 'cas' => $h['cas'] ?: $h['seen_at']];
            $info['pn']++;
        }
        return [$kroky, $pn, $info];
    }

    /** Přechod historie → typ stavu, stav, akce (stejně jako u kroků z hist.unl); neznámý přechod = null. */
    private function stepMapper(): \Closure
    {
        $prechody = [];
        foreach ($this->source->rows('tisky', 'prechody.unl') as $c) {
            $prechody[(int)$c[0]] = ['kam' => (int)$c[2], 'akce' => (int)$c[3]];
        }
        $stavToTyp = $this->source->map('tisky', 'stavy.unl', 0, 1);
        $typy = $this->source->map('tisky', 'typ_stavu.unl', 0, 1);
        $akce = $this->source->map('tisky', 'typ_akce.unl', 0, 1);
        return function (int $idPrechod) use ($prechody, $stavToTyp, $typy, $akce): ?array {
            $prechod = $prechody[$idPrechod] ?? null;
            if ($prechod === null) {
                return null;
            }
            $typ = (int)($stavToTyp[$prechod['kam']] ?? 0);
            return ['typ_stavu' => $typ, 'stav' => $typy[$typ] ?? '', 'akce_id' => $prechod['akce'],
                'akce' => $akce[$prechod['akce']] ?? ''];
        };
    }

    private function loadSouhrny(array $tisky): array
    {
        // souhrn pozměňovacích a jiných návrhů po 2. čtení (tisky_za.unl, druh 10 = sněmovní tisk X/n):
        // soupis platně podaných PN s přesným časem rozeslání; bez souhrnu (zkrácené jednání) se PN
        // jako sněmovní tisk nerozesílaly. Bere se první souhrn k tisku.
        $out = [];
        try {
            foreach ($this->source->rows('tisky', 'tisky_za.unl') as $c) {
                $idTisk = (int)($c[0] ?? 0);
                if (!isset($tisky[$idTisk]) || (int)($c[3] ?? 0) !== 10) {
                    continue;
                }
                $at = substr(trim($c[6] ?? ''), 0, 16);
                if ($at !== '' && (!isset($out[$idTisk]) || $at < $out[$idTisk]['datum'])) {
                    $out[$idTisk] = ['cislo_za' => (int)$c[1], 'datum' => $at];
                }
            }
        } catch (RuntimeException) {
            // starší export bez tisky_za.unl: bez souhrnů
        }
        return $out;
    }

    private function switchPeriod(int $oldCislo): void
    {
        // nové volební období: čísla tisků začínají znovu od 1, takže tisk 40 je jiný návrh než tisk 40
        // minulého období (#2 bod 2). Stará upozornění zůstanou v historii pod klíčem s číslem období
        // (a odkazem na psp.cz). Texty a rozbory starých tisků se nemažou: zůstanou v historii verzí
        // (text_revize, ai_revize s číslem období) a celé řádky v archivu (tisk_text_archiv, pn_text_archiv).
        // Z pracovních tabulek zmizí, aby je detail tisku, skóre ani fronty nepoužily pro nový tisk stejného čísla.
        // Nové období začne bootstrapem.
        foreach ($this->db->all('SELECT alert_key FROM alerts WHERE obdobi = ?', [$oldCislo]) as $a) {
            $key = (string)$a['alert_key'];
            if (str_starts_with($key, $oldCislo . '/')) {
                continue;
            }
            $this->db->run('UPDATE alerts SET alert_key = ? WHERE alert_key = ?', [$oldCislo . '/' . $key, $key]);
            $this->db->run('UPDATE post_log SET alert_key = ? WHERE alert_key = ?', [$oldCislo . '/' . $key, $key]);
        }
        $this->db->run('UPDATE alerts SET posted = 3 WHERE posted = 0');
        Revisions::archivePeriod($this->db, $oldCislo);
        $this->db->run('DELETE FROM tisk_text');
        $this->db->run('DELETE FROM pn_text');
        $this->db->run("DELETE FROM meta WHERE k = 'bootstrapped'");
    }

    private function validate(int $obdobiId, array $tisky, array $kroky, array $pn): ?string
    {
        // vrací důvod, proč import nepoužít, nebo null
        $samePeriod = (int)$this->db->getMeta('obdobi_id', '0') === $obdobiId;
        $before = [
            'tisky' => (int)$this->db->value('SELECT COUNT(*) FROM tisky'),
            'kroky' => (int)$this->db->value('SELECT COUNT(*) FROM kroky'),
            'pn' => (int)$this->db->value('SELECT COUNT(*) FROM pn'),
        ];
        $now = ['tisky' => count($tisky), 'kroky' => count($kroky), 'pn' => count($pn)];
        if ($samePeriod) {
            // ve stejném období data jen přibývají; výrazný propad = chybějící nebo useknutý soubor
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
            // PN se musí aspoň zčásti vázat na tisky stejného období
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
        // volební období Sněmovny = orgán typu 11 se zkratkou PSPn; aktuální nemá datum konce
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
        // id_osoba => „Jméno Příjmení“
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
            // jen hlavní tisky (cislo_za = 0) aktuálního období a sledovaných druhů
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
                // rj = 1: předkladatel navrhl projednání podle § 90 odst. 2 (eviduje se dřív než krok v historii)
                'rj' => trim($c[18] ?? '') === '1',
                // lhůta pro výbory ve dnech (výchozí 60, Sněmovna ji může zkrátit)
                'lhuta' => (int)($c[17] ?? 0) ?: null,
            ];
        }
        return $out;
    }

    private function loadKroky(array $tisky): array
    {
        $map = $this->stepMapper();
        $out = [];
        foreach ($this->source->rows('tisky', 'hist.unl') as $c) {
            $idTisk = (int)($c[1] ?? 0);
            if (!isset($tisky[$idTisk])) {
                continue;
            }
            $step = $map((int)($c[4] ?? 0));
            if ($step === null) {
                continue;
            }
            $out[] = [
                'id_hist' => (int)$c[0],
                'id_tisk' => $idTisk,
                'ct' => $tisky[$idTisk]['ct'],
                'datum' => $this->isoDate($c[2] ?? ''),
                'typ_stavu' => $step['typ_stavu'],
                'stav' => $step['stav'],
                'akce_id' => $step['akce_id'],
                'akce' => $step['akce'],
                // hlasování, kterým krok skončil (např. závěrečné hlasování ve 3. čtení)
                'id_hlasovani' => (int)($c[3] ?? 0) ?: null,
                // číslo ve Sbírce zákonů (sloupce zaver_sb_cislo a datum vyhlášení)
                '_sb' => trim($c[13] ?? '') !== '' ? trim($c[13]) . '/' . substr($this->czDate($c[11] ?? '') ?? '', 0, 4) : null,
                // den vyhlášení ve Sbírce (zaver_publik); datum kroku je jen doručení nebo odeslání k publikaci
                '_vyhl' => $this->czDate($c[11] ?? ''),
            ];
        }
        // chronologicky podle id historie (některé kroky nemají datum)
        usort($out, fn(array $a, array $b): int => $a['id_hist'] <=> $b['id_hist']);
        return $out;
    }

    private function loadPn(int $obdobiId, array $osoby): array
    {
        $out = [];
        foreach ($this->source->rows('sd', 'sd_dokument.unl') as $c) {
            // typ 13 = písemné pozměňovací návrhy
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
        // datum vyhlášení z posledního kroku, který ho uvádí
        foreach (array_reverse($kroky) as $krok) {
            if (!empty($krok['_vyhl'])) {
                return $krok['_vyhl'];
            }
        }
        return null;
    }

    private function czDate(string $value): ?string
    {
        // „27.01.2026“ → „2026-01-27“
        $value = trim($value);
        if (preg_match('~^(\d{2})\.(\d{2})\.(\d{4})$~', $value, $m)) {
            return "$m[3]-$m[2]-$m[1]";
        }
        return null;
    }

    private function isoDate(string $value): ?string
    {
        // „2026-11-13 16:38“ i „2026-11-13 16:38:00“ → „2026-11-13 16:38:00“
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
