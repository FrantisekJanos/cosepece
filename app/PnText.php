<?php


declare(strict_types=1);

namespace Hlidac;

use RuntimeException;
use ZipArchive;

final class PnText
{
    public function __construct(private Db $db, private array $config)
    {
    }

    public function run(): array
    {
        $cfg = $this->config['pn_text'] ?? [];
        if (empty($cfg['enabled'])) {
            return ['fetched' => 0, 'errors' => 0];
        }
        $limit = (int)($cfg['per_run'] ?? 40);
        $obdobi = (int)$this->db->getMeta('obdobi_cislo', '10');



        $retryBefore = date('Y-m-d H:i:s', time() - 3600 * max(1, (int)($cfg['retry_hours'] ?? 24)));
        $rows = $this->db->all(
            "SELECT p.*, t.zakony FROM pn p
             LEFT JOIN pn_text x ON x.id_dokument = p.id_dokument
             LEFT JOIN pn_seen s ON s.id_dokument = p.id_dokument
             JOIN tisky t ON t.ct = p.ct
             WHERE t.faze <> ? AND (x.id_dokument IS NULL
                OR (x.error IS NOT NULL AND (x.text IS NULL OR x.text = '') AND x.fetched_at < ?))
             ORDER BY CASE WHEN x.id_dokument IS NULL THEN 0 ELSE 1 END,
                CASE WHEN s.id_dokument IS NULL THEN 0 ELSE 1 END, p.cas DESC
             LIMIT " . $limit,
            ['Vyhlášeno', $retryBefore]
        );

        $stats = ['fetched' => 0, 'errors' => 0];
        foreach ($rows as $row) {
            try {
                $docxUrl = $this->findDocxUrl((int)$row['cislo'], $obdobi);
                $text = $docxUrl !== null ? self::docxToText(Http::get($docxUrl)) : '';
                $analysis = self::analyze($text, array_filter(explode(',', (string)$row['zakony'])), $this->config);
                $this->save($row, $docxUrl, $text, $analysis, $docxUrl === null ? 'Dokument nemá verzi ve Wordu' : null);
                $stats['fetched']++;
            } catch (\Throwable $ex) {
                $this->save($row, null, '', ['chars' => 0, 'laws' => [], 'signals' => [], 'score' => 0], $ex->getMessage());
                $stats['errors']++;
            }
            usleep(300_000);
        }
        return $stats;
    }

    private function findDocxUrl(int $cislo, int $obdobi): ?string
    {

        $base = 'https://www.psp.cz';
        $html = Http::get($base . '/sqw/sd.sqw?cd=' . $cislo . '&o=' . $obdobi);
        $html = iconv('CP1250', 'UTF-8//IGNORE', $html) ?: $html;
        if (preg_match('~class="file docx"><a href="([^"]+)"~', $html, $m)) {
            return $base . html_entity_decode($m[1]);
        }
        return null;
    }

    public static function docxToText(string $binary): string
    {

        $tmp = tempnam(sys_get_temp_dir(), 'pn');
        file_put_contents($tmp, $binary);
        $zip = new ZipArchive();
        if ($zip->open($tmp) !== true) {
            @unlink($tmp);
            throw new RuntimeException('Soubor není platný DOCX');
        }
        $xml = (string)$zip->getFromName('word/document.xml');
        $zip->close();
        @unlink($tmp);

        $xml = preg_replace('~<w:(del|moveFrom)\b[^>]*/>|<w:(del|moveFrom)\b[^>]*>.*?</w:\2>~s', '', $xml) ?? $xml;
        $xml = preg_replace('~<w:delText\b[^>]*>.*?</w:delText>~s', '', $xml) ?? $xml;
        $xml = str_replace(['</w:p>', '<w:tab/>', '<w:br/>'], ["\n", "\t", "\n"], $xml);
        $text = html_entity_decode(strip_tags($xml), ENT_QUOTES | ENT_XML1, 'UTF-8');
        return trim(preg_replace("~\n{3,}~", "\n\n", $text) ?? $text);
    }

    public static function normativePart(string $text): string
    {

        $parts = preg_split(
            '~^\s*(Odůvodnění|Zdůvodnění|O\s*d\s*ů\s*v\s*o\s*d\s*n\s*ě\s*n\s*í|D\s*ů\s*v\s*o\s*d\s*o\s*v\s*á\s+z\s*p\s*r\s*á\s*v\s*a)\s*:?\s*$~mui',
            $text,
            2
        );
        return $parts[0] ?? $text;
    }

    public static function sentences(string $text): array
    {

        $out = [];
        foreach (preg_split("~\n+~u", $text) ?: [] as $line) {
            $line = trim(preg_replace('~\s+~u', ' ', $line) ?? $line);

            foreach (preg_split('~(?<=[.;“])\s+(?=[„"(]?\p{Lu}|\d+\.\s)~u', $line) ?: [] as $sentence) {
                $sentence = trim($sentence);
                if ($sentence !== '') {
                    $out[] = $sentence;
                }
            }
        }
        return $out;
    }

    public static function procedureSignals(string $normative): array
    {

        $retro = [];
        $deadline = [];
        $retroDeadline = [];
        $deadlineTopic = '~(?:lhůt|promlč|preklu|zánik|vymáh|vrácení|odvod|kontrol|sankc|pokut|porušení\s+rozpočtové\s+kázně)~ui';
        foreach (self::sentences($normative) as $sentence) {
            if (mb_strlen($sentence) > 800) {
                continue;
            }

            $isRetro = preg_match('~\bi\s+(?:tehdy|v\s+případech?|na\s+(?:případy|řízení|nároky|platby|dotace|práva|povinnosti))\b[^.]{0,300}?(?:před(?:e)?\s+dnem\s+nabytí\s+účinnosti|před\s+účinností)~ui', $sentence)
                || (preg_match('~\bzpětn(?:ě|á|é|ou)\s+(?:účinnost|použi)~ui', $sentence) && !self::negatesRetro($sentence));

            $isDeadline = preg_match('~(?:lhůt|promlč|preklu|nejpozději\s+do|zanik(?:á|ne)\s+(?:nárok|právo|odpovědnost))~ui', $sentence)
                && preg_match('~\b\d+\s+(?:let|roků|rok[uy]?|měsíců)\b~u', $sentence)
                && preg_match('~(?:vrácení|odvod|vymáh|kontrol|řízení|sankc|pokut|porušení\s+rozpočtové\s+kázně|přestup|promlč|preklu)~ui', $sentence);
            if ($isRetro) {
                $retro[] = mb_substr($sentence, 0, 500);
                if (preg_match($deadlineTopic, $sentence)) {
                    $retroDeadline[] = mb_substr($sentence, 0, 500);
                }
            }
            if ($isDeadline) {
                $deadline[] = mb_substr($sentence, 0, 500);
            }
        }
        $out = [];
        foreach (['retro' => $retro, 'deadline' => $deadline, 'retro_deadline' => $retroDeadline] as $key => $list) {
            if ($list !== []) {
                $out[$key] = array_slice(array_values(array_unique($list)), 0, 5);
            }
        }
        return $out;
    }

    public static function lawsIn(string $normative): array
    {

        preg_match_all('~V\s+zákoně\s+č\.\s*(\d+/\d{4})\s*Sb\.~u', $normative, $m1);
        preg_match_all('~Zákon\s+č\.\s*(\d+/\d{4})\s*Sb\.,[^\n]{0,400}?se\s+(?:mění|doplňuje)~u', $normative, $m2);
        return array_values(array_unique(array_merge($m1[1], $m2[1])));
    }

    public static function negatesRetro(string $sentence): bool
    {

        return (bool)(preg_match('~\b(?:nemá|nemají|nemůže\s+mít|nemohou\s+mít|není|nejsou|nelze|bez|vylučuje\s+se|vyloučen[aáoéý]?|nepoužij\w*|nepoužívá\w*)\s+(?:\S+\s+){0,2}zpětn~ui', $sentence)
            || preg_match('~\bzpětn\w*\s+(?:účinnost|použití)\s+(?:se\s+)?(?:vylučuje|je\s+vyloučen\w*|není\s+přípustn\w*)~ui', $sentence));
    }

    public static function analyze(string $text, array $billLaws, array $config): array
    {
        $weights = $config['scoring'] ?? [];
        $normative = self::normativePart($text);


        $laws = self::lawsIn($normative);

        $signals = [];
        $score = 0;
        if ($billLaws !== []) {
            $other = array_values(array_diff($laws, $billLaws));
            if ($other !== []) {
                $signals['other_laws'] = $other;
                $score += (int)($weights['pn_other_law'] ?? 20);
            }
        }
        $chars = mb_strlen($normative);
        if ($chars >= (int)($weights['pn_large_chars'] ?? 12000)) {
            $signals['large'] = $chars;
            $score += (int)($weights['pn_large'] ?? 10);
        }
        $newSections = preg_match_all('~vklád(?:á|ají)\s+(?:se\s+)?(?:nov[ýé]\s+)?§~u', $normative);
        if ($newSections > 0) {
            $signals['new_sections'] = $newSections;
        }

        $signals += self::procedureSignals($normative);
        return ['chars' => $chars, 'laws' => $laws, 'signals' => $signals, 'score' => min($score, 30)];
    }

    private function save(array $row, ?string $docxUrl, string $text, array $a, ?string $error): void
    {
        $this->db->run('DELETE FROM pn_text WHERE id_dokument = ?', [(int)$row['id_dokument']]);
        $this->db->run(
            'INSERT INTO pn_text (id_dokument, ct, cislo, docx_url, text, chars, laws, signals, rule_score, fetched_at, error)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [
                (int)$row['id_dokument'], (int)$row['ct'], (int)$row['cislo'], $docxUrl,
                mb_substr($text, 0, 200000), (int)$a['chars'], implode(',', $a['laws']),
                json_encode($a['signals'], JSON_UNESCAPED_UNICODE), (int)$a['score'], date('Y-m-d H:i:s'), $error,
            ]
        );
    }
}
