<?php
// Historie běhů synchronizace (tabulka sync_beh): začátek, konec, ok, výsledek JSON, chyba.
// Obdoba steno.beh služby rag/. Zapisuje jen Sync::run pod zámkem, proto stačí id = MAX(id) + 1
// (funguje stejně v SQLite, MySQL i PostgreSQL bez autoinkrementu). Časy v místním čase (Europe/Prague).

declare(strict_types=1);

namespace Hlidac;

final class SyncRuns
{
    /** Text chyby běhu, který skončil zvenku (restart kontejneru, kill) bez zápisu výsledku. */
    public const UNFINISHED = 'nedokončeno: proces skončil bez zápisu výsledku';
    /** Jak dlouho se běhy uchovávají. */
    public const KEEP_DAYS = 90;

    /**
     * Založí řádek běhu. Předtím označí dřívější nedokončené běhy (konec prázdný) a smaže běhy starší
     * než KEEP_DAYS. Vrací id běhu.
     */
    public static function start(Db $db): int
    {
        $now = date('Y-m-d H:i:s');
        $db->run('UPDATE sync_beh SET konec = ?, ok = 0, chyba = ? WHERE konec IS NULL', [$now, self::UNFINISHED]);
        $id = (int)$db->value('SELECT MAX(id) FROM sync_beh') + 1;
        $db->run('INSERT INTO sync_beh (id, zacatek) VALUES (?, ?)', [$id, $now]);
        // úklid až po vložení, ať se id smazaných běhů znovu nepoužije
        $db->run('DELETE FROM sync_beh WHERE zacatek < ?', [date('Y-m-d H:i:s', time() - self::KEEP_DAYS * 86400)]);
        return $id;
    }

    /** Úspěšný konec běhu s výsledkem (dílčí chyby kroků jsou uvnitř výsledku). */
    public static function finish(Db $db, int $id, array $result): void
    {
        $db->run('UPDATE sync_beh SET konec = ?, ok = 1, vysledek = ? WHERE id = ?',
            [date('Y-m-d H:i:s'), json_encode($result, JSON_UNESCAPED_UNICODE), $id]);
    }

    /** Běh skončil výjimkou: ok = 0 a text chyby bez tokenů. */
    public static function fail(Db $db, int $id, \Throwable $ex): void
    {
        $db->run('UPDATE sync_beh SET konec = ?, ok = 0, chyba = ? WHERE id = ?',
            [date('Y-m-d H:i:s'), mb_substr(Http::redact($ex->getMessage()), 0, 500), $id]);
    }

    /** Poslední úspěšně dokončený běh (null = žádný). */
    public static function lastOk(Db $db): ?array
    {
        return $db->one('SELECT * FROM sync_beh WHERE ok = 1 ORDER BY id DESC LIMIT 1');
    }
}
