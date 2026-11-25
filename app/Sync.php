<?php
// Celý běh synchronizace: import dat, tvorba upozornění, rozeslání. Chráněno zámkem proti souběhu.

declare(strict_types=1);

namespace Hlidac;

use RuntimeException;

final class Sync
{
    /** Stáří reel_state.bot_seen_at (s), od kterého synchronizace hlásí do Sentry, že bot Reels neběží. */
    public const BOT_STALE_S = 900;

    public static function run(): array
    {
        $lockFile = APP_ROOT . '/data/sync.lock';
        $lock = fopen($lockFile, 'c');
        if ($lock === false || !flock($lock, LOCK_EX | LOCK_NB)) {
            throw new RuntimeException('Synchronizace už běží.');
        }
        try {
            // historie běhů (sync_beh): řádek až pod zámkem, souběžný pokus se nezapisuje
            $runId = SyncRuns::start(db());
            try {
                $result = self::steps();
            } catch (\Throwable $ex) {
                SyncRuns::fail(db(), $runId, $ex);
                throw $ex;
            }
            SyncRuns::finish(db(), $runId, $result);
            return $result;
        } finally {
            flock($lock, LOCK_UN);
            fclose($lock);
        }
    }

    /** Jednotlivé kroky synchronizace; vrací výsledek běhu (zapíše ho i do meta last_result). */
    private static function steps(): array
    {
        $started = microtime(true);
        $config = config();
        $source = new Source(
            (string)$config['source_url'],
            APP_ROOT . '/data/cache',
            $config['source_dir'] ?? null
        );
        // jednorázově: dosavadní texty a rozbory jako první verze historie (meta revize_backfill)
        $backfill = Revisions::backfill(db());
        try {
            // nové kroky a PN ze stránek historie tisků (rychleji než noční otevřená data); Importer je přimíchá
            $history = (new HistoryHtml(db(), $source, $config))->run();
        } catch (\Throwable $ex) {
            Sentry::capture($ex, ['kde' => 'Sync/HistoryHtml']);
            $history = ['error' => Http::redact($ex->getMessage())];
        }
        $import = (new Importer(db(), $source))->run();
        try {
            $prep = (new GovPrep(db(), $source, $config))->run();
        } catch (\Throwable $ex) {
            // doplňkové údaje Úřadu vlády: chyba se zapíše, synchronizace pokračuje
            Sentry::capture($ex, ['kde' => 'Sync/GovPrep']);
            $prep = ['error' => Http::redact($ex->getMessage())];
        }
        try {
            $votes = (new Votes(db(), $source))->run();
        } catch (\Throwable $ex) {
            // hlasování jsou doplňková: chyba se zapíše, synchronizace pokračuje
            Sentry::capture($ex, ['kde' => 'Sync/Votes']);
            $votes = ['error' => Http::redact($ex->getMessage())];
        }
        // nejdřív upozornění (kvůli „návalu“), pak předběžné skóre (pořadí fronty textů),
        // texty PN a tisků, rozbor AI, konečné skóre, nakonec rozeslání
        $alerts = (new AlertBuilder(db(), $config))->run();
        $scorer = new Scorer(db(), $config);
        $scorer->scoreBills();
        $pnText = (new PnText(db(), $config))->run();
        try {
            // které pozměňovací návrhy prošly (souhrn PN + stenozáznam 3. čtení); doplňkové, chyba synchronizaci nezastaví
            $pnOutcome = (new PnOutcome(db(), $config))->run();
        } catch (\Throwable $ex) {
            Sentry::capture($ex, ['kde' => 'Sync/PnOutcome']);
            $pnOutcome = ['error' => Http::redact($ex->getMessage())];
        }
        $tiskText = (new TiskText(db(), $config))->run();
        $ai = (new AiReviewer(db(), $config))->run();
        $scorer->scoreBills();
        $scorer->scoreAlerts();
        $reels = self::reelsSnapshot($config);
        try {
            $reelsBot = self::reelsBot(db(), time());
        } catch (\Throwable $ex) {
            Sentry::capture($ex, ['kde' => 'Sync/ReelsBot']);
            $reelsBot = ['error' => Http::redact($ex->getMessage())];
        }
        // úklid: prošlé záznamy limitů dotazů a nepoužívané tokeny MCP vydané na webu
        RateLimit::cleanup();
        try {
            $revoked = (new McpTokens(db(), $config))->revokeStale();
        } catch (\Throwable $ex) {
            $revoked = ['error' => Http::redact($ex->getMessage())];
        }
        try {
            $searchItems = (new SearchIndex(db()))->rebuild();
        } catch (\Throwable $ex) {
            $searchItems = ['error' => $ex->getMessage()];
        }
        $posts = (new Publisher(db(), $config))->run();
        $result = [
            'import' => $import,
            'historie_html' => $history,
            'votes' => $votes,
            'priprava' => $prep,
            'alerts_new' => $alerts,
            'pn_text' => $pnText,
            'pn_vysledek' => $pnOutcome,
            'tisk_text' => $tiskText,
            'ai' => $ai,
            'revize_backfill' => $backfill,
            'search_index' => $searchItems,
            'mcp_tokens_revoked' => $revoked,
            'posts' => $posts,
            'reels_snapshot' => $reels,
            'reels_bot' => $reelsBot,
            'seconds' => round(microtime(true) - $started, 1),
        ];
        db()->setMeta('last_result', json_encode($result, JSON_UNESCAPED_UNICODE));
        return $result;
    }

    /**
     * Snímek hlavních nálezů pro generátor Reels (reel_state.top_findings). Jen v PostgreSQL a když tabulku
     * reel_state založila služba reels/; chyba se zapíše do Sentry a synchronizace pokračuje.
     */
    private static function reelsSnapshot(array $config): int|array|null
    {
        try {
            if (db()->driver !== 'pgsql' || db()->value("SELECT to_regclass('reel_state')::text") === null) {
                return null;
            }
            $level = (int)Scorer::weights($config)['level_major'];
            return ReelsSnapshot::write(db(), new Repo(db()), $level);
        } catch (\Throwable $ex) {
            Sentry::capture($ex, ['kde' => 'Sync/ReelsSnapshot']);
            return ['error' => Http::redact($ex->getMessage())];
        }
    }

    /**
     * Živost bota Reels: bot zapisuje reel_state.bot_seen_at (ISO 8601 s pásmem) každou minutu, svůj pád ale nenahlásí.
     * Starší než BOT_STALE_S nebo nečitelná hodnota = zpráva do Sentry (stálý text, sdílené omezení drží jednu
     * událost za hodinu). Bez tabulky reel_state (web bez služby reels/) nebo bez klíče null.
     */
    public static function reelsBot(Db $db, int $now): ?array
    {
        $key = $db->driver === 'mysql' ? '`key`' : 'key';
        try {
            $value = $db->value("SELECT value FROM reel_state WHERE $key = ?", ['bot_seen_at']);
        } catch (\PDOException) {
            return null;
        }
        if ($value === null) {
            return null;
        }
        $value = (string)$value;
        $seen = strtotime($value);
        $minutes = $seen === false ? null : intdiv($now - $seen, 60);
        $stale = $seen === false || $now - $seen > self::BOT_STALE_S;
        if ($stale) {
            Sentry::message('Reels bot neodpovídá: bot_seen_at je starší než 15 min', 'error',
                ['bot_seen_at' => $value, 'minut' => $minutes]);
        }
        return ['seen_at' => $value, 'stale' => $stale];
    }
}
