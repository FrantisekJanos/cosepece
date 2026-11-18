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

        // vše kromě tisků, které už jsou zákonem (vyhlášeno); od nejnověji předložených
        $rows = $this->db->all(
            "SELECT t.* FROM tisky t
             LEFT JOIN tisk_text x ON x.ct = t.ct
             WHERE x.ct IS NULL AND t.faze <> 'Vyhlášeno'
             ORDER BY t.predlozeno DESC, t.ct DESC
             LIMIT " . $limit
        );

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
        return $stats;
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
