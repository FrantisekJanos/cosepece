<?php
// Rychlejší nové kroky a PN (audit aktualizace dat 2026-11-21, díra 6): psp.cz vydává otevřená data jednou v noci,
// stránka historie tisku (historie.sqw?o=10&t=<ct>) se mění hned. Pro tisky se změnou v posledních dnech a tisky
// na pořadu běžící nebo příští schůze se stránka stáhne (šetrně: nejvýš per_run stránek za běh, pauza mezi požadavky)
// a z ní se vytáhnou:
//  - kroky historie z komentářů <!-- LLLP id_hist id_prechod … --> (id i přechod jsou stejné jako v hist.unl;
//    stránka jich uvádí jen část, ostatní doplní noční data), datum z textu záznamu za <!-- EndR id_hist, … -->,
//  - písemné PN z tabulky „Písemné pozměňovací návrhy“ (číslo, autor detail.sqw?id=<id_osoba>, soubor
//    „<id_dokument>-….docx“ = id v sd_dokument.unl, čas podání).
// Ukládají se jen záznamy, které v otevřených datech ještě nejsou, do html_kroky / html_pn. Importer je při každém
// běhu přimíchá do kroků a PN (otevřená data mají přednost) a jakmile záznam přijde v nočních datech, z tabulek
// html_* ho smaže; nic se tak nezdvojí.

declare(strict_types=1);

namespace Hlidac;

final class HistoryHtml
{
    public const BASE = 'https://www.psp.cz/sqw/historie.sqw';
    // fáze, ve kterých se tisk už nemění
    private const FINAL = ['Vyhlášeno', 'Zamítnuto', 'Vzat zpět', 'Vráceno předkladateli'];
    // po tolika chybách stažení za sebou běh skončí (psp.cz má potíže)
    private const MAX_ERRORS = 3;
    // záznam, který noční data do týdne nepotvrdí, se zahodí
    public const KEEP_DAYS = 7;

    /** @var callable(string): string */
    private $fetch;

    public function __construct(private Db $db, private Source $source, private array $config, ?callable $fetch = null)
    {
        $this->fetch = $fetch ?? fn(string $url): string => Http::get($url, 5_000_000);
    }

    public function run(?int $now = null): array
    {
        $cfg = $this->config['history_html'] ?? [];
        if (empty($cfg['enabled'])) {
            return ['stazeno' => 0];
        }
        $now ??= time();
        $obdobi = (int)$this->db->getMeta('obdobi_cislo', '0');
        if ($obdobi <= 0) {
            return ['stazeno' => 0];
        }
        $delay = max(0, (int)($cfg['delay_ms'] ?? 1000));
        $stats = ['stazeno' => 0, 'kroky' => 0, 'pn' => 0, 'chyby' => 0];
        $errors = 0;
        foreach ($this->candidates((int)($cfg['per_run'] ?? 30), (int)($cfg['days'] ?? 14), $now) as $i => $t) {
            if ($i > 0 && $delay > 0) {
                usleep($delay * 1000); // šetrně k serveru psp.cz
            }
            try {
                $html = ($this->fetch)(self::BASE . '?o=' . $obdobi . '&t=' . $t['ct']);
                $errors = 0;
            } catch (\Throwable $ex) {
                $stats['chyby']++;
                if (++$errors >= self::MAX_ERRORS) {
                    break;
                }
                continue;
            }
            $stats['stazeno']++;
            $parsed = self::parse(iconv('CP1250', 'UTF-8//IGNORE', $html) ?: $html, date('Y-m-d H:i:s', $now));
            [$k, $p] = $this->store($t, $parsed, date('Y-m-d H:i:s', $now));
            $stats['kroky'] += $k;
            $stats['pn'] += $p;
        }
        return $stats;
    }

    /**
     * Tisky ke kontrole: na pořadu běžící nebo příští schůze (do 14 dní), pak se změnou (krok, PN) v posledních
     * $days dnech od nejnovější; bez tisků v konečné fázi.
     *
     * @return list<array{id_tisk: int, ct: int}>
     */
    public function candidates(int $limit, int $days, int $now): array
    {
        if ($limit <= 0) {
            return [];
        }
        $final = implode(',', array_fill(0, count(self::FINAL), '?'));
        $rows = $this->db->all(
            "SELECT id_tisk, ct, posledni, pn_last FROM tisky WHERE faze IS NULL OR faze NOT IN ($final)", self::FINAL);
        $since = date('Y-m-d H:i:s', $now - $days * 86400);
        $program = $this->onProgram($now);
        $picked = [];
        foreach ($rows as $r) {
            $last = max((string)$r['posledni'], (string)$r['pn_last']);
            $onProgram = isset($program[(int)$r['id_tisk']]);
            if ($onProgram || $last >= $since) {
                $picked[] = ['id_tisk' => (int)$r['id_tisk'], 'ct' => (int)$r['ct'], 'p' => $onProgram ? 1 : 0, 'last' => $last];
            }
        }
        usort($picked, fn(array $a, array $b): int => [$b['p'], $b['last'], $b['ct']] <=> [$a['p'], $a['last'], $a['ct']]);
        $out = [];
        foreach (array_slice($picked, 0, $limit) as $p) {
            $out[] = ['id_tisk' => $p['id_tisk'], 'ct' => $p['ct']];
        }
        return $out;
    }

    /** id_tisk na pořadu schůze, která běží nebo začne do 14 dní (schuze.zip); chyba zdroje = nic. */
    private function onProgram(int $now): array
    {
        try {
            $obdobiId = (int)$this->db->getMeta('obdobi_id', '0');
            $today = date('Y-m-d', $now);
            $until = date('Y-m-d', $now + 14 * 86400);
            $schuze = [];
            foreach ($this->source->rows('schuze', 'schuze.unl') as $c) {
                $od = substr(trim($c[3] ?? ''), 0, 10);
                $do = substr(trim($c[4] ?? ''), 0, 10);
                if ((int)($c[1] ?? 0) === $obdobiId && $od !== '' && $od <= $until && ($do === '' || $do >= $today)) {
                    $schuze[(int)$c[0]] = true;
                }
            }
            $out = [];
            foreach ($this->source->rows('schuze', 'bod_schuze.unl') as $c) {
                if (isset($schuze[(int)($c[1] ?? 0)]) && (int)($c[2] ?? 0) > 0) {
                    $out[(int)$c[2]] = true;
                }
            }
            return $out;
        } catch (\Throwable) {
            return [];
        }
    }

    /**
     * Kroky a PN ze stránky historie tisku (HTML už v UTF-8). Datum kroku bez data v textu = $fallback.
     *
     * @return array{kroky: list<array{id_hist: int, id_prechod: int, datum: string}>,
     *               pn: list<array{id_dokument: int, cislo: int, id_osoba: ?int, osoba: string, cas: ?string}>}
     */
    public static function parse(string $html, string $fallback): array
    {
        $kroky = [];
        preg_match_all('~<!--\s*LLLP\s+(\d+)\s+(\d+)\s+\d+\s*-->~', $html, $m, PREG_SET_ORDER);
        foreach ($m as [, $id, $prechod]) {
            if ((int)$prechod > 0 && !isset($kroky[(int)$id])) {
                $kroky[(int)$id] = ['id_hist' => (int)$id, 'id_prechod' => (int)$prechod,
                    'datum' => self::stepDate($html, (int)$id) ?? $fallback];
            }
        }
        $pn = [];
        $start = stripos($html, 'Písemné pozměňovací návrhy');
        if ($start !== false) {
            $table = substr($html, $start, (stripos($html, '</table>', $start) ?: strlen($html)) - $start);
            preg_match_all('~<tr\b[^>]*>(.*?)</tr>~is', $table, $rows);
            foreach ($rows[1] as $row) {
                preg_match_all('~<td\b[^>]*>(.*?)</td>~is', $row, $cells);
                $cells = $cells[1];
                if (count($cells) < 5 || !preg_match('~^\d+$~', self::text($cells[0]))
                    || !preg_match('~(\d+)-\d+\.(?:docx?|pdf|rtf)~i', $cells[2], $file)) {
                    continue;
                }
                $osoba = preg_match('~detail\.sqw\?id=(\d+)~', $cells[1], $o) ? (int)$o[1] : null;
                $pn[] = ['id_dokument' => (int)$file[1], 'cislo' => (int)self::text($cells[0]), 'id_osoba' => $osoba,
                    'osoba' => mb_substr(self::text($cells[1]), 0, 120), 'cas' => self::dateTime(self::text($cells[4]))];
            }
        }
        return ['kroky' => array_values($kroky), 'pn' => $pn];
    }

    /**
     * Datum záznamu: první datum v textu za prvním <!-- EndR id_hist, … --> až po EndR jiného záznamu nebo konec
     * historie (jeden záznam může mít víc značek EndR za sebou).
     */
    private static function stepDate(string $html, int $id): ?string
    {
        preg_match_all('~<!--\s*EndR\s+(\d+),[^>]*-->~', $html, $marks, PREG_SET_ORDER | PREG_OFFSET_CAPTURE);
        $from = null;
        $to = null;
        foreach ($marks as $mark) {
            $markId = (int)$mark[1][0];
            if ($from === null && $markId === $id) {
                $from = $mark[0][1] + strlen($mark[0][0]);
            } elseif ($from !== null && $markId !== $id) {
                $to = $mark[0][1];
                break;
            }
        }
        if ($from === null) {
            return null;
        }
        $logEnd = strpos($html, '<!--/ Document Log', $from);
        $to = min($to ?? strlen($html), $logEnd === false ? strlen($html) : $logEnd);
        $segment = self::text(substr($html, $from, $to - $from));
        return preg_match('~(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})~u', $segment, $d)
            ? sprintf('%04d-%02d-%02d 00:00:00', $d[3], $d[2], $d[1]) : null;
    }

    /** „15. 10. 2026 v 11:53:35“ → „2026-10-15 11:53:35“. */
    private static function dateTime(string $text): ?string
    {
        if (!preg_match('~(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})(?:\s*v\s*(\d{1,2}):(\d{2})(?::(\d{2}))?)?~u', $text, $d)) {
            return null;
        }
        return sprintf('%04d-%02d-%02d %02d:%02d:%02d', $d[3], $d[2], $d[1], $d[4] ?? 0, $d[5] ?? 0, $d[6] ?? 0);
    }

    /** Text buňky bez značek, s mezerami místo &nbsp;. */
    private static function text(string $html): string
    {
        $text = html_entity_decode(strip_tags($html), ENT_QUOTES | ENT_HTML5, 'UTF-8');
        return trim((string)preg_replace('~\s+~u', ' ', str_replace("\u{a0}", ' ', $text)));
    }

    /** Uloží záznamy, které v otevřených datech (kroky, pn) ještě nejsou; vrací [nových kroků, nových PN]. */
    private function store(array $tisk, array $parsed, string $seenAt): array
    {
        $newSteps = 0;
        $newPn = 0;
        foreach ($parsed['kroky'] as $k) {
            if ($this->db->value('SELECT 1 FROM kroky WHERE id_hist = ?', [$k['id_hist']]) !== null) {
                continue;
            }
            $known = $this->db->value('SELECT 1 FROM html_kroky WHERE id_hist = ?', [$k['id_hist']]) !== null;
            $this->db->run('DELETE FROM html_kroky WHERE id_hist = ?', [$k['id_hist']]);
            $this->db->run('INSERT INTO html_kroky (id_hist, id_tisk, ct, id_prechod, datum, seen_at) VALUES (?, ?, ?, ?, ?, ?)',
                [$k['id_hist'], $tisk['id_tisk'], $tisk['ct'], $k['id_prechod'], $k['datum'], $seenAt]);
            $newSteps += $known ? 0 : 1;
        }
        foreach ($parsed['pn'] as $p) {
            if ($this->db->value('SELECT 1 FROM pn WHERE id_dokument = ?', [$p['id_dokument']]) !== null) {
                continue;
            }
            $known = $this->db->value('SELECT 1 FROM html_pn WHERE id_dokument = ?', [$p['id_dokument']]) !== null;
            $this->db->run('DELETE FROM html_pn WHERE id_dokument = ?', [$p['id_dokument']]);
            $this->db->run('INSERT INTO html_pn (id_dokument, cislo, ct, id_osoba, osoba, cas, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                [$p['id_dokument'], $p['cislo'], $tisk['ct'], $p['id_osoba'], $p['osoba'], $p['cas'], $seenAt]);
            $newPn += $known ? 0 : 1;
        }
        return [$newSteps, $newPn];
    }
}
