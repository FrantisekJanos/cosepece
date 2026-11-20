<?php
// Snímek hlavních nálezů pro generátor Reels: synchronizace ho zapíše do reel_state (klíč top_findings)
// a služba reels/ (hlidac_reels/fresh.py) z jeho změn zakládá úlohy fresh a stahuje sady k nálezům,
// které z přehledu vypadly. Tabulku reel_state vlastní Python, PHP ji nemigruje.

declare(strict_types=1);

namespace Hlidac;

final class ReelsSnapshot
{
    public const STATE_KEY = 'top_findings';
    /** kolik hlavních nálezů se bere (stejně jako auto_pick v ReelJobs) */
    public const LIMIT = 60;

    /**
     * Hlavní nálezy použitelné pro video: topFindings(minScore, 60) jen s titulkem z rozboru AI,
     * v pořadí přehledu. Stejný výběr používá auto_pick (ReelJobs::pickFinding).
     */
    public static function findings(Repo $repo, int $minScore): array
    {
        $out = [];
        foreach ($repo->topFindings($minScore, self::LIMIT) as $t) {
            if ($repo->headline((int)$t['ct']) !== null) {
                $out[] = $t;
            }
        }
        return $out;
    }

    /**
     * Zapíše snímek {"at": ISO 8601, "items": {"<ct>": [klíče důvodů podle bodů sestupně]}} a vrátí počet tisků.
     * Bez ON CONFLICT (přenositelnost): když řádek existuje UPDATE, jinak INSERT (rowCount po UPDATE
     * se stejnou hodnotou je v MySQL 0, proto se existence zjišťuje dotazem).
     */
    public static function write(Db $db, Repo $repo, int $minScore): int
    {
        $items = [];
        foreach (self::findings($repo, $minScore) as $t) {
            $reasons = array_values(array_filter((array)(json_decode((string)$t['reasons'], true) ?: []), 'is_array'));
            // podle bodů sestupně, stejné body drží pořadí ze skóre (usort je stabilní)
            usort($reasons, fn(array $a, array $b): int => (int)($b['points'] ?? 0) <=> (int)($a['points'] ?? 0));
            $keys = [];
            foreach ($reasons as $r) {
                $key = (string)($r['key'] ?? '');
                if ($key !== '' && !in_array($key, $keys, true)) {
                    $keys[] = $key;
                }
            }
            $items[(string)(int)$t['ct']] = $keys;
        }
        // vždy jako objekt (prázdné pole nebo řada čísel 0..n by se zakódovaly jako seznam)
        $value = json_encode(['at' => date('c'), 'items' => (object)$items], JSON_UNESCAPED_UNICODE);

        $col = $db->driver === 'mysql' ? '`key`' : 'key';
        if ($db->value("SELECT 1 FROM reel_state WHERE $col = ?", [self::STATE_KEY]) !== null) {
            $db->run("UPDATE reel_state SET value = ? WHERE $col = ?", [$value, self::STATE_KEY]);
        } else {
            $db->run("INSERT INTO reel_state ($col, value) VALUES (?, ?)", [self::STATE_KEY, $value]);
        }
        return count($items);
    }
}
