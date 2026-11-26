<?php
// Reels na X přes složku Pro X (worker na PC): které video zkopírovat, které soubory smazat, a hlášení výsledku.
// Sloupce reel_queue.x_* vlastní služba reels/ (reels/hlidac_reels/schema_reels.sql), PHP je nemigruje.
// Stavy x_status: wanted (vlastník klikl Na X) → ready | missing (hlásí worker) → done | skipped (tlačítka bota).

declare(strict_types=1);

namespace Hlidac;

final class ReelsX
{
    /** výsledky, které smí hlásit worker */
    public const RESULTS = ['ready', 'missing'];
    /** soubory vyřízených položek se mažou ze složky Pro X, dokud je položka mladší než tolik dní */
    public const REMOVE_DAYS = 14;
    /** nejvýš tolik položek ke zkopírování v jedné odpovědi */
    private const WANTED_LIMIT = 50;
    private const REMOVE_LIMIT = 500;

    /** @var callable(): int */
    private $now;

    public function __construct(private Db $db, ?callable $now = null)
    {
        $this->now = $now ?? fn(): int => time();
    }

    /**
     * Pro worker: wanted = položky ke zkopírování [{id, set_id, variant, ct}], remove = id vyřízených položek
     * (done, skipped, missing) za posledních REMOVE_DAYS dní, jejichž soubor má worker ze složky smazat.
     *
     * @return array{wanted: list<array{id:int,set_id:?int,variant:?int,ct:?int}>, remove: list<int>}
     */
    public function pending(): array
    {
        $rows = $this->db->all(
            "SELECT id, set_id, variant, ct FROM reel_queue WHERE x_status = 'wanted' ORDER BY id LIMIT " . self::WANTED_LIMIT
        );
        $since = $this->stamp(($this->now)() - self::REMOVE_DAYS * 86400);
        $remove = $this->db->all(
            "SELECT id FROM reel_queue WHERE x_status IN ('done', 'skipped', 'missing')
              AND COALESCE(x_ready_at, x_requested_at) > ? ORDER BY id LIMIT " . self::REMOVE_LIMIT,
            [$since]
        );
        $int = fn($v): ?int => $v === null ? null : (int)$v;
        return [
            'wanted' => array_map(fn(array $r): array => [
                'id' => (int)$r['id'], 'set_id' => $int($r['set_id']), 'variant' => $int($r['variant']), 'ct' => $int($r['ct']),
            ], $rows),
            'remove' => array_map(fn(array $r): int => (int)$r['id'], $remove),
        ];
    }

    /**
     * Worker hlásí výsledek kopie ($result z RESULTS). Mění jen položku, která pořád čeká (wanted): pozdní hlášení
     * po Zrušit nebo Hotovo nic nepřepíše. True = zapsáno.
     */
    public function ready(int $id, string $result): bool
    {
        if (!in_array($result, self::RESULTS, true)) {
            throw new \InvalidArgumentException('neplatný výsledek');
        }
        return $this->db->run(
            "UPDATE reel_queue SET x_status = ?, x_ready_at = ? WHERE id = ? AND x_status = 'wanted'",
            [$result, $this->stamp(($this->now)()), $id]
        )->rowCount() > 0;
    }

    /** Čas pro sloupce časů (stejně jako ReelJobs::stamp: PostgreSQL s posunem pásma, SQLite text). */
    private function stamp(int $ts): string
    {
        return date($this->db->driver === 'pgsql' ? 'Y-m-d H:i:sP' : 'Y-m-d H:i:s', $ts);
    }
}
