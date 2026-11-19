<?php
// Stažení textu samotného návrhu zákona (Word ze stránky sněmovního tisku) a pravidlová analýza:
// zpětné použití a změny lhůt pro vymáhání. Zásadní změny bývají přímo v poslaneckých návrzích,
// ne jen v pozměňovacích návrzích (např. tisk 221: lhůta pro vymáhání dotací 4 roky i zpětně).

declare(strict_types=1);

namespace Hlidac;

final class TiskText
{
    public function __construct(private Db $db, private array $config)
    {
    }

    public function run(): array
    {
        $cfg = $this->config['tisk_text'] ?? [];
        if (empty($cfg['enabled'])) {
            return ['fetched' => 0, 'errors' => 0];
        }
        $limit = (int)($cfg['per_run'] ?? 15);
        $obdobi = (int)$this->db->getMeta('obdobi_cislo', '10');

        $rows = self::queue($this->db, $limit, (int)($cfg['retry_hours'] ?? 24));

        $stats = ['fetched' => 0, 'errors' => 0];
        foreach ($rows as $row) {
            $ct = (int)$row['ct'];
            try {
                $docxUrl = $this->findDocxUrl($ct, $obdobi);
                $text = $docxUrl !== null ? PnText::docxToText(Http::get($docxUrl)) : '';
                $this->save($ct, $docxUrl, $text, $docxUrl === null ? 'Tisk nemá verzi ve Wordu' : null);
                $stats['fetched']++;
            } catch (\Throwable $ex) {
                $this->save($ct, null, '', mb_substr($ex->getMessage(), 0, 500));
                $stats['errors']++;
            }
            usleep(300_000); // šetrně k serveru psp.cz
        }

        // stažené texty jednou za refresh_days znovu ověříme: když psp.cz dokument vymění (jiné idd),
        // stáhne se nové znění a rozbor AI se udělá znovu (#2 bod 10)
        $stats['refreshed'] = 0;
        $room = min((int)($cfg['refresh_per_run'] ?? 5), max(0, $limit - count($rows)));
        $stale = self::staleQueue($this->db, $room, (int)($cfg['refresh_days'] ?? 7));
        foreach ($stale as $row) {
            $ct = (int)$row['ct'];
            try {
                $docxUrl = $this->findDocxUrl($ct, $obdobi);
                if ($docxUrl !== null && $docxUrl !== (string)$row['docx_url']) {
                    $this->save($ct, $docxUrl, PnText::docxToText(Http::get($docxUrl)), null);
                    $stats['refreshed']++;
                }
                $this->db->run('UPDATE tisk_text SET checked_at = ? WHERE ct = ?', [date('Y-m-d H:i:s'), $ct]);
            } catch (\Throwable) {
                // dočasná chyba: zkusíme při další kontrole, uložený text zůstává
            }
            usleep(300_000);
        }
        return $stats;
    }

    public static function staleQueue(Db $db, int $limit, int $refreshDays = 7): array
    {
        // stažené texty rozpracovaných tisků, které se nejdéle neověřovaly proti psp.cz
        if ($limit <= 0) {
            return [];
        }
        return $db->all(
            "SELECT x.ct, x.docx_url FROM tisk_text x JOIN tisky t ON t.ct = x.ct
             WHERE t.faze <> 'Vyhlášeno' AND x.docx_url IS NOT NULL AND COALESCE(x.checked_at, x.fetched_at) < ?
             ORDER BY COALESCE(x.checked_at, x.fetched_at) LIMIT " . $limit,
            [date('Y-m-d H:i:s', time() - 86400 * max(1, $refreshDays))]
        );
    }

    public static function queue(Db $db, int $limit, int $retryHours = 24): array
    {
        // vše kromě tisků, které už jsou zákonem (vyhlášeno); od nejnověji předložených.
        // Neúspěšné stažení (chyba sítě, chybějící Word) se zkusí znovu po $retryHours, až po nových textech.
        $retryBefore = date('Y-m-d H:i:s', time() - 3600 * max(1, $retryHours));
        return $db->all(
            "SELECT t.* FROM tisky t
             LEFT JOIN tisk_text x ON x.ct = t.ct
             WHERE t.faze <> 'Vyhlášeno' AND (x.ct IS NULL
                OR (x.error IS NOT NULL AND (x.text IS NULL OR x.text = '') AND x.fetched_at < ?))
             ORDER BY CASE WHEN x.ct IS NULL THEN 0 ELSE 1 END, t.predlozeno DESC, t.ct DESC
             LIMIT " . $limit,
            [$retryBefore]
        );
    }

    private function findDocxUrl(int $ct, int $obdobi): ?string
    {
        // stránka „Soubory sněmovního tisku“: <a href="/sqw/text/orig2.sqw?idd=..." title="Dokument DOCX">
        $base = 'https://www.psp.cz';
        $html = Http::get($base . '/sqw/text/tiskt.sqw?o=' . $obdobi . '&ct=' . $ct . '&ct1=0');
        $html = iconv('CP1250', 'UTF-8//IGNORE', $html) ?: $html;
        if (preg_match('~<a href="(/sqw/text/orig2\.sqw\?idd=\d+)"[^>]*title="Dokument DOCX"~', $html, $m)) {
            return $base . html_entity_decode($m[1]);
        }
        return null;
    }

    public static function analyze(string $text): array
    {
        // normativní část (bez důvodové zprávy) a signály zpětného použití a lhůt
        $normative = PnText::normativePart($text);
        return ['chars' => mb_strlen($normative), 'signals' => PnText::procedureSignals($normative)];
    }

    private function save(int $ct, ?string $docxUrl, string $text, ?string $error): void
    {
        $a = self::analyze($text);
        $this->db->run('DELETE FROM tisk_text WHERE ct = ?', [$ct]);
        $this->db->run(
            'INSERT INTO tisk_text (ct, docx_url, text, chars, signals, fetched_at, error) VALUES (?, ?, ?, ?, ?, ?, ?)',
            [
                $ct, $docxUrl, mb_substr($text, 0, 400000), (int)$a['chars'],
                json_encode($a['signals'], JSON_UNESCAPED_UNICODE), date('Y-m-d H:i:s'), $error,
            ]
        );
    }
}
