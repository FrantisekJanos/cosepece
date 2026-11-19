<?php

declare(strict_types=1);

namespace Hlidac;

final class GovPrep
{
    public const URL = 'https://www.odok.gov.cz/opendata/dataset/odok/requests_%d.json';
    public const VEKLEP = 'https://odok.gov.cz/portal/veklep/material/%s/';

    public const PHASES = ['schvaleno', 'vlada', 'po_pripominkach', 'pripominky'];

    private const DONE_STATES = ['CE', 'D', 'E', 'F', 'K', 'DZV', 'Y'];

    public function __construct(private Db $db, private Source $source, private array $config)
    {
    }

    public function run(): array
    {
        $cfg = $this->config['odok'] ?? [];
        if (isset($cfg['enabled']) && !$cfg['enabled']) {
            return ['skipped' => true];
        }
        $obdobiId = (int)$this->db->getMeta('obdobi_id', '0');
        $start = $this->periodStart($obdobiId);
        if ($start === null) {
            return ['skipped' => true];
        }

        $items = [];
        for ($year = (int)substr($start, 0, 4) - 2; $year <= (int)date('Y'); $year++) {
            foreach ($this->load($year, $cfg) as $m) {

                $m = isset($m['_source']) && is_array($m['_source']) ? $m['_source'] : $m;
                if (!empty($m['pid']) && isset($m['title'], $m['date_authorized'])) {
                    $items[(string)$m['pid']] = $m;
                }
            }
        }
        $byTitle = [];
        foreach ($items as $m) {
            $byTitle[self::normTitle((string)$m['title'])][] = $m;
        }

        $tisky = [];
        foreach ($this->db->all('SELECT id_tisk, ct, typ_navrhu, nazev, uplny_nazev, predlozeno FROM tisky') as $t) {
            $tisky[(int)$t['id_tisk']] = $t;
        }
        $rows = [];
        $eklep = $this->eklepLinks($tisky);

        foreach ($eklep as $ct => $pid) {
            $m = $items[$pid] ?? null;
            if ($m === null) {
                continue;
            }
            $review = null;
            foreach ($byTitle[self::normTitle((string)$m['title'])] ?? [] as $cand) {
                if (!empty($cand['review_process']['date_finish']) && (string)$cand['date_authorized'] <= (string)$m['date_authorized']
                    && ($review === null || (string)$cand['date_authorized'] > (string)$review['date_authorized'])) {
                    $review = $cand;
                }
            }
            $rows[$ct] = $this->row($ct, 'vladni', $m, $review);
        }

        foreach ($items as $m) {
            if (!in_array($m['type']['id'] ?? '', ['PNZ', 'SNZ', 'NKZ'], true) || (string)$m['date_authorized'] < $start
                || !preg_match('~sněmovní tisk č\.\s*(\d+)~u', (string)$m['title'], $mm)) {
                continue;
            }
            $ct = (int)$mm[1];
            if (isset($rows[$ct]) && ($rows[$ct]['druh'] === 'vladni' || (string)$rows[$ct]['_at'] >= (string)$m['date_authorized'])) {
                continue;
            }
            $rows[$ct] = $this->row($ct, 'stanovisko', $m, !empty($m['review_process']['date_finish']) ? $m : null);
        }

        $known = array_flip(array_map(fn(array $t) => (int)$t['ct'], $tisky));
        $rows = array_values(array_filter($rows, fn(array $r) => isset($known[$r['ct']])));
        $upcoming = $this->upcoming($byTitle, $start, $tisky, array_flip($eklep));
        $this->db->pdo->beginTransaction();
        try {
            $this->db->run('DELETE FROM priprava');
            $this->db->insertMany('priprava', ['ct', 'druh', 'pid', 'pripominky_od', 'pripominky_do', 'dni', 'mist',
                'vlada', 'duvod', 'poznamka', 'mimo_plan', 'vyjimka', 'znovu'], $rows);
            $this->db->run('DELETE FROM pripravovane');
            $this->db->insertMany('pripravovane', ['pid', 'nazev', 'resort', 'faze', 'stav', 'zalozeno', 'zmeneno', 'pripominky_od',
                'pripominky_do', 'dni', 'mist', 'vlada', 'duvod', 'popis', 'mimo_plan', 'vyjimka', 'verzi'], $upcoming);
            $this->db->pdo->commit();
        } catch (\Throwable $ex) {
            $this->db->pdo->rollBack();
            throw $ex;
        }
        return ['materialy' => count($items), 'tisky' => count($rows), 'pripravovane' => count($upcoming)];
    }

    public static function phaseOf(string $status): ?string
    {

        $code = strtoupper(trim((string)preg_replace('~\s*[-–].*$~u', '', trim($status))));
        if ($code === '' || in_array($code, self::DONE_STATES, true)) {
            return null;
        }
        return match (true) {
            in_array($code, ['1', '2'], true) => 'pripominky',
            $code === '3' => 'po_pripominkach',
            in_array($code, ['7', '8'], true) => 'vlada',
            default => 'schvaleno',
        };
    }

    private function upcoming(array $byTitle, string $start, array $tisky, array $eklepPids): array
    {

        $tiskTitles = [];
        foreach ($tisky as $t) {
            foreach ([(string)$t['uplny_nazev'], (string)$t['nazev']] as $title) {
                if ($title !== '') {
                    $tiskTitles[self::normTitle($title)] = max($tiskTitles[self::normTitle($title)] ?? '', (string)$t['predlozeno']);
                }
            }
        }
        $out = [];
        foreach ($byTitle as $norm => $versions) {
            $versions = array_values(array_filter($versions, fn(array $m) => ($m['type']['id'] ?? '') === 'NZ'
                && preg_match('~^(vládní\s+)?návrh\s+(ústavního\s+)?zákona~ui', trim((string)$m['title']))));
            if ($versions === []) {
                continue;
            }
            usort($versions, fn(array $a, array $b) => strcmp((string)$b['date_authorized'], (string)$a['date_authorized']));
            $last = $versions[0];
            $first = $versions[count($versions) - 1];
            $phase = self::phaseOf((string)($last['status']['name'] ?? ''));
            if ($phase === null || !empty($last['psp']) || (string)$last['date_authorized'] < $start) {
                continue;
            }

            $since = substr((string)$first['date_authorized'], 0, 10);
            $inPsp = array_filter($versions, fn(array $m) => isset($eklepPids[(string)$m['pid']]) || !empty($m['psp'])) !== [];
            foreach ($tiskTitles as $title => $submitted) {
                if ($submitted >= $since && ($title === $norm || (mb_strlen($title) >= 30 && str_starts_with($norm, $title . ' ')))) {
                    $inPsp = true;
                    break;
                }
            }
            if ($inPsp) {
                continue;
            }
            $review = null;
            $gov = null;
            foreach ($versions as $m) {
                if ($review === null && !empty($m['review_process']['date_finish'])) {
                    $review = $m;
                }

                if (!empty($m['government_program']['date']) && (string)$m['government_program']['date'] >= $start) {
                    $gov = max((string)$gov, substr((string)$m['government_program']['date'], 0, 10));
                }
            }
            $row = $this->row(0, 'vladni', $last, $review);
            $changed = max(array_map(fn(array $m) => substr((string)($m['metadata']['date_modified'] ?? $m['date_authorized']), 0, 10), $versions));
            $out[] = [
                'pid' => (string)$last['pid'],
                'nazev' => trim(preg_replace('~\s+~u', ' ', (string)$last['title']) ?? ''),
                'resort' => (string)($last['author']['organization']['name'] ?? ''),
                'faze' => $phase,
                'stav' => mb_substr((string)($last['status']['name'] ?? ''), 0, 80),
                'zalozeno' => substr((string)$first['date_authorized'], 0, 10),
                'zmeneno' => $changed,
                'pripominky_od' => $row['pripominky_od'],
                'pripominky_do' => $row['pripominky_do'],
                'dni' => $row['dni'],
                'mist' => $row['mist'],

                'vlada' => $gov,
                'duvod' => $row['duvod'],
                'popis' => mb_substr(trim(preg_replace('~\s+~u', ' ', (string)($last['description'] ?? '')) ?? ''), 0, 600),
                'mimo_plan' => max(array_map(fn(array $m) => $this->row(0, 'vladni', $m, null)['mimo_plan'], $versions)),
                'vyjimka' => max(array_map(fn(array $m) => $this->row(0, 'vladni', $m, null)['vyjimka'], $versions)),
                'verzi' => count($versions),
            ];
        }
        return $out;
    }

    public static function normTitle(string $title): string
    {

        $t = mb_strtolower(trim($title));
        $t = preg_replace('~^vládní\s+~u', '', $t) ?? $t;
        return trim(preg_replace('~[^\p{L}\p{N}]+~u', ' ', $t) ?? $t);
    }

    public static function url(string $pid): string
    {
        return sprintf(self::VEKLEP, rawurlencode($pid));
    }

    private function row(int $ct, string $druh, array $m, ?array $review): array
    {
        $from = $review !== null ? substr((string)$review['date_authorized'], 0, 10) : null;
        $to = $review !== null ? substr((string)$review['review_process']['date_finish'], 0, 10) : null;
        $text = trim(($m['reason'] ?? '') . ' ' . ($review['notes'] ?? '') . ' ' . ($m['notes'] ?? ''));
        return [
            'ct' => $ct, 'druh' => $druh, 'pid' => (string)$m['pid'],
            'pripominky_od' => $from, 'pripominky_do' => $to,
            'dni' => $from !== null && $to !== null ? (int)round((strtotime($to) - strtotime($from)) / 86400) : null,
            'mist' => $review !== null ? count((array)($review['review_process']['reviewers'] ?? [])) : 0,
            'vlada' => !empty($m['government_program']['date']) ? substr((string)$m['government_program']['date'], 0, 10) : null,
            'duvod' => mb_substr(trim(preg_replace('~\s+~u', ' ', (string)($m['reason'] ?? '')) ?? ''), 0, 500),
            'poznamka' => mb_substr(trim(preg_replace('~\s+~u', ' ', (string)(($review['notes'] ?? '') ?: ($m['notes'] ?? ''))) ?? ''), 0, 500),
            'mimo_plan' => preg_match('~mimo\s+plán~ui', $text) ? 1 : 0,
            'vyjimka' => preg_match('~výjimk~ui', $text) ? 1 : 0,

            'znovu' => preg_match('~znovu|opětovn|znovupředlož|nedokončen\w* legislativní\w* proces~ui', $text) ? 1 : 0,
            '_at' => (string)$m['date_authorized'],
        ];
    }

    private function load(int $year, array $cfg): array
    {

        if (!empty($cfg['local_dir'])) {
            $path = rtrim((string)$cfg['local_dir'], '/') . "/requests_$year.json";
            return is_file($path) ? (array)(json_decode((string)file_get_contents($path), true)['requests'] ?? []) : [];
        }
        $cache = hlidacDataPath("cache/odok_$year.json");
        if (!is_file($cache) || filemtime($cache) < time() - 20 * 3600) {
            try {
                $body = Http::get(sprintf(self::URL, $year), 30_000_000);
                if (json_decode($body, true) !== null) {
                    file_put_contents($cache, $body);
                }
            } catch (\Throwable) {

            }
        }
        return is_file($cache) ? (array)(json_decode((string)file_get_contents($cache), true)['requests'] ?? []) : [];
    }

    private function eklepLinks(array $tisky): array
    {

        $out = [];
        try {
            foreach ($this->source->rows('tisky', 'tisk_eklep.unl') as $c) {
                $idTisk = (int)($c[4] ?? 0);
                if (isset($tisky[$idTisk]) && trim($c[0] ?? '') !== '') {
                    $out[(int)$tisky[$idTisk]['ct']] = trim($c[0]);
                }
            }
        } catch (\RuntimeException) {

        }
        return $out;
    }

    private function periodStart(int $obdobiId): ?string
    {
        foreach ($this->source->rows('poslanci', 'organy.unl') as $c) {
            if ((int)$c[0] === $obdobiId && preg_match('~^(\d{2})\.(\d{2})\.(\d{4})$~', trim($c[6] ?? ''), $m)) {
                return $m[3] . '-' . $m[2] . '-' . $m[1];
            }
        }
        return null;
    }
}
