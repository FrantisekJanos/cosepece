<?php
// Historie verzí textů (návrh zákona, pozměňovací návrh) a rozborů AI (#2 bod 2).
// Záznam přibude jen při skutečné změně (hash textu, resp. hash rozboru s modelem a verzí promptu);
// nic se nemaže ani nepřepisuje. Na konci volebního období se pracovní tabulky textů zkopírují do archivu.

declare(strict_types=1);

namespace Hlidac;

final class Revisions
{
    // sloupce pracovních tabulek kopírované do archivu (při novém sloupci v tisk_text/pn_text doplnit i sem a do Db::migrate)
    public const TISK_COLUMNS = ['ct', 'docx_url', 'text', 'chars', 'signals', 'ai_json', 'ai_score', 'fetched_at', 'ai_at',
        'error', 'checked_at', 'ai_model', 'ai_prompt', 'ai_prev_json', 'ai_prev_score', 'ai_prev_model'];
    public const PN_COLUMNS = ['id_dokument', 'ct', 'cislo', 'docx_url', 'text', 'chars', 'laws', 'signals', 'rule_score',
        'ai_json', 'ai_score', 'fetched_at', 'ai_at', 'error', 'ai_model', 'ai_prompt', 'ai_prev_json', 'ai_prev_score',
        'ai_prev_model'];

    // popisky zdroje záznamu pro web
    public const SOURCES = [
        'stazeni' => 'staženo z psp.cz',
        'zaloha' => 'stav před zavedením historie',
        'rozbor' => 'rozbor AI',
        'pojistky' => 'automatická korekce',
    ];

    public static function period(Db $db): int
    {
        return (int)$db->getMeta('obdobi_cislo', '0');
    }

    public static function target(string $kind): array
    {
        return match ($kind) {
            'tisk' => ['tisk_text', 'ct'],
            'pn' => ['pn_text', 'id_dokument'],
            default => throw new \InvalidArgumentException('Neznámý druh textu: ' . $kind),
        };
    }

    public static function textHash(string $text): string
    {
        return sha1($text);
    }

    private static function last(Db $db, string $table, string $kind, int $obdobi, int $id): ?array
    {
        return $db->one("SELECT verze, hash FROM $table WHERE druh = ? AND obdobi = ? AND ref_id = ? ORDER BY verze DESC LIMIT 1",
            [$kind, $obdobi, $id]);
    }

    public static function recordText(Db $db, string $kind, int $id, string $source = 'stazeni', ?int $obdobi = null): bool
    {
        // uloží současný text, pokud se liší od poslední uložené verze; prázdný text (chyba stažení) se neukládá
        [$table, $column] = self::target($kind);
        $cislo = $kind === 'pn' ? 'cislo' : 'NULL AS cislo';
        $row = $db->one("SELECT ct, $cislo, docx_url, text, fetched_at FROM $table WHERE $column = ?", [$id]);
        $text = (string)($row['text'] ?? '');
        if ($row === null || trim($text) === '') {
            return false;
        }
        $obdobi ??= self::period($db);
        $hash = self::textHash($text);
        $last = self::last($db, 'text_revize', $kind, $obdobi, $id);
        if ($last !== null && (string)$last['hash'] === $hash) {
            return false;
        }
        $db->run('INSERT INTO text_revize (druh, obdobi, ref_id, verze, ct, cislo, docx_url, text, chars, hash, fetched_at, created_at, zdroj)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', [
            $kind, $obdobi, $id, (int)($last['verze'] ?? 0) + 1, (int)$row['ct'],
            $row['cislo'] !== null ? (int)$row['cislo'] : null, $row['docx_url'], $text, mb_strlen($text), $hash,
            $row['fetched_at'], date('Y-m-d H:i:s'), $source,
        ]);
        return true;
    }

    public static function recordAi(Db $db, string $kind, int $id, string $source = 'rozbor', ?int $obdobi = null): bool
    {
        // uloží současný rozbor AI, pokud se liší od posledního uloženého (obsah, model, verze promptu).
        // Když položka ještě nemá žádný záznam a má uschované 1. kolo (ai_prev_json), uloží se nejdřív to.
        [$table, $column] = self::target($kind);
        $row = $db->one("SELECT ct, text, ai_json, ai_score, ai_model, ai_prompt, ai_at, ai_prev_json, ai_prev_score, ai_prev_model
            FROM $table WHERE $column = ?", [$id]);
        if ($row === null || $row['ai_at'] === null || $row['ai_json'] === null) {
            return false;
        }
        $obdobi ??= self::period($db);
        $textHash = self::textHash((string)$row['text']);
        $last = self::last($db, 'ai_revize', $kind, $obdobi, $id);
        $insert = function (int $verze, ?string $json, $score, $model, $prompt, $at, string $hash, string $src) use ($db, $kind, $obdobi, $id, $row, $textHash): void {
            $db->run('INSERT INTO ai_revize (druh, obdobi, ref_id, verze, ct, ai_json, ai_score, ai_model, ai_prompt, ai_at, text_hash, hash, created_at, zdroj)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', [
                $kind, $obdobi, $id, $verze, (int)$row['ct'], $json, $score !== null ? (int)$score : null, $model,
                $prompt !== null ? (int)$prompt : null, $at, $textHash, $hash, date('Y-m-d H:i:s'), $src,
            ]);
        };
        $verze = (int)($last['verze'] ?? 0);
        $lastHash = $last !== null ? (string)$last['hash'] : null;
        if ($last === null && $row['ai_prev_json'] !== null) {
            // výsledek 1. kola před ověřením silnějším modelem (čas a verze promptu 1. kola se neuchovávaly)
            $prevHash = self::aiHash((string)$row['ai_prev_json'], $row['ai_prev_model'], $row['ai_prompt']);
            $insert(++$verze, (string)$row['ai_prev_json'], $row['ai_prev_score'], $row['ai_prev_model'], $row['ai_prompt'], null, $prevHash, 'zaloha');
            $lastHash = $prevHash;
        }
        $hash = self::aiHash((string)$row['ai_json'], $row['ai_model'], $row['ai_prompt']);
        if ($lastHash === $hash) {
            return false;
        }
        $insert(++$verze, (string)$row['ai_json'], $row['ai_score'], $row['ai_model'], $row['ai_prompt'], $row['ai_at'], $hash, $source);
        return true;
    }

    private static function aiHash(string $json, mixed $model, mixed $prompt): string
    {
        return sha1((string)$model . '|' . (string)$prompt . '|' . $json);
    }

    public static function snapshotAll(Db $db, string $source, ?int $obdobi = null): int
    {
        // zapíše současný stav všech textů a rozborů, které ještě v historii nejsou (bez změny pracovních tabulek)
        $n = 0;
        foreach (['tisk', 'pn'] as $kind) {
            [$table, $column] = self::target($kind);
            foreach (array_column($db->all("SELECT $column FROM $table ORDER BY $column"), $column) as $id) {
                $n += (int)self::recordText($db, $kind, (int)$id, $source, $obdobi);
                $n += (int)self::recordAi($db, $kind, (int)$id, $source, $obdobi);
            }
        }
        return $n;
    }

    public static function backfill(Db $db): int
    {
        // jednorázově: dosavadní texty a rozbory jako první verze historie (meta revize_backfill, opakování nic nezmění)
        if ($db->getMeta('revize_backfill') === '1') {
            return 0;
        }
        $own = !$db->pdo->inTransaction();
        if ($own) {
            $db->pdo->beginTransaction();
        }
        try {
            $n = self::snapshotAll($db, 'zaloha');
            $db->setMeta('revize_backfill', '1');
            if ($own) {
                $db->pdo->commit();
            }
        } catch (\Throwable $ex) {
            if ($own) {
                $db->pdo->rollBack();
            }
            throw $ex;
        }
        return $n;
    }

    public static function archivePeriod(Db $db, int $obdobi): array
    {
        // konec volebního období: stav textů a rozborů do historie, celé řádky do archivních tabulek.
        // Pracovní tabulky pak smí volající vyprázdnit; už archivované řádky se nepřepisují.
        self::snapshotAll($db, 'zaloha', $obdobi);
        // hodnoty ve výběru vkládáme přímo (celé číslo a citovaný čas): PostgreSQL neumí odvodit typ parametru v SELECT
        $now = $db->pdo->quote(date('Y-m-d H:i:s'));
        $out = [];
        foreach (['tisk_text' => [self::TISK_COLUMNS, 'ct'], 'pn_text' => [self::PN_COLUMNS, 'id_dokument']] as $table => [$cols, $key]) {
            $list = implode(', ', $cols);
            $select = implode(', ', array_map(fn(string $c): string => 'x.' . $c, $cols));
            $db->run("INSERT INTO {$table}_archiv (obdobi, archived_at, $list)
                SELECT $obdobi, $now, $select FROM $table x
                WHERE NOT EXISTS (SELECT 1 FROM {$table}_archiv a WHERE a.obdobi = ? AND a.$key = x.$key)", [$obdobi]);
            $out[$table] = (int)$db->value("SELECT COUNT(*) FROM {$table}_archiv WHERE obdobi = ?", [$obdobi]);
        }
        return $out;
    }

    public static function counts(Db $db, string $kind, ?int $obdobi = null): array
    {
        // počet verzí textu a rozborů na položku: [ref_id => ['text' => n, 'ai' => m]]
        $obdobi ??= self::period($db);
        $out = [];
        foreach (['text' => 'text_revize', 'ai' => 'ai_revize'] as $type => $table) {
            foreach ($db->all("SELECT ref_id, COUNT(*) AS n FROM $table WHERE druh = ? AND obdobi = ? GROUP BY ref_id", [$kind, $obdobi]) as $r) {
                $out[(int)$r['ref_id']] = ($out[(int)$r['ref_id']] ?? ['text' => 0, 'ai' => 0]);
                $out[(int)$r['ref_id']][$type] = (int)$r['n'];
            }
        }
        return $out;
    }

    public static function hasHistory(array $count): bool
    {
        // odkaz „Historie“ má smysl, až když je víc než jedna verze textu nebo rozboru
        return ($count['text'] ?? 0) > 1 || ($count['ai'] ?? 0) > 1;
    }

    public static function textVersions(Db $db, string $kind, int $id, int $obdobi): array
    {
        // seznam verzí textu bez samotného textu
        return $db->all('SELECT verze, ct, cislo, docx_url, chars, hash, fetched_at, created_at, zdroj FROM text_revize
            WHERE druh = ? AND obdobi = ? AND ref_id = ? ORDER BY verze', [$kind, $obdobi, $id]);
    }

    public static function textVersion(Db $db, string $kind, int $id, int $obdobi, int $verze): ?string
    {
        $text = $db->value('SELECT text FROM text_revize WHERE druh = ? AND obdobi = ? AND ref_id = ? AND verze = ?',
            [$kind, $obdobi, $id, $verze]);
        return $text === null ? null : (string)$text;
    }

    public static function aiVersions(Db $db, string $kind, int $id, int $obdobi): array
    {
        $rows = $db->all('SELECT verze, ct, ai_json, ai_score, ai_model, ai_prompt, ai_at, text_hash, created_at, zdroj FROM ai_revize
            WHERE druh = ? AND obdobi = ? AND ref_id = ? ORDER BY verze', [$kind, $obdobi, $id]);
        foreach ($rows as &$r) {
            $r['ai'] = json_decode((string)$r['ai_json'], true) ?: [];
        }
        return $rows;
    }

    public static function versionsLabel(int $n): string
    {
        // „2 verze“, „5 verzí“
        return $n . ' ' . ($n >= 2 && $n <= 4 ? 'verze' : ($n === 1 ? 'verze' : 'verzí'));
    }

    public static function url(string $kind, int $id): string
    {
        return 'revize.php?druh=' . $kind . '&id=' . $id;
    }
}
