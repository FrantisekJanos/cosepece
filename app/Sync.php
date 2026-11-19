<?php
// Celý běh synchronizace: import dat, tvorba upozornění, rozeslání. Chráněno zámkem proti souběhu.

declare(strict_types=1);

namespace Hlidac;

use RuntimeException;

final class Sync
{
    public static function run(): array
    {
        $lockFile = APP_ROOT . '/data/sync.lock';
        $lock = fopen($lockFile, 'c');
        if ($lock === false || !flock($lock, LOCK_EX | LOCK_NB)) {
            throw new RuntimeException('Synchronizace už běží.');
        }
        try {
            $started = microtime(true);
            $config = config();
            $source = new Source(
                (string)$config['source_url'],
                APP_ROOT . '/data/cache',
                $config['source_dir'] ?? null
            );
            // jednorázově: dosavadní texty a rozbory jako první verze historie (meta revize_backfill)
            $backfill = Revisions::backfill(db());
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
            $tiskText = (new TiskText(db(), $config))->run();
            $ai = (new AiReviewer(db(), $config))->run();
            $scorer->scoreBills();
            $scorer->scoreAlerts();
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
                'votes' => $votes,
                'priprava' => $prep,
                'alerts_new' => $alerts,
                'pn_text' => $pnText,
                'tisk_text' => $tiskText,
                'ai' => $ai,
                'revize_backfill' => $backfill,
                'search_index' => $searchItems,
                'mcp_tokens_revoked' => $revoked,
                'posts' => $posts,
                'seconds' => round(microtime(true) - $started, 1),
            ];
            db()->setMeta('last_result', json_encode($result, JSON_UNESCAPED_UNICODE));
            return $result;
        } finally {
            flock($lock, LOCK_UN);
            fclose($lock);
        }
    }
}
