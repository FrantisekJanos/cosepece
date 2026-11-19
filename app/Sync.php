<?php


declare(strict_types=1);

namespace Hlidac;

use RuntimeException;

final class Sync
{
    public static function run(): array
    {
        $lockFile = hlidacDataPath('sync.lock');
        $lock = fopen($lockFile, 'c');
        if ($lock === false || !flock($lock, LOCK_EX | LOCK_NB)) {
            throw new RuntimeException('Synchronizace už běží.');
        }
        try {
            $started = microtime(true);
            $config = hlidacConfig();
            $source = new Source(
                (string)$config['source_url'],
                hlidacDataPath('cache'),
                $config['source_dir'] ?? null
            );

            $backfill = Revisions::backfill(db());
            $import = (new Importer(db(), $source))->run();
            try {
                $prep = (new GovPrep(db(), $source, $config))->run();
            } catch (\Throwable $ex) {

                $prep = ['error' => Http::redact($ex->getMessage())];
            }
            try {
                $votes = (new Votes(db(), $source))->run();
            } catch (\Throwable $ex) {

                $votes = ['error' => Http::redact($ex->getMessage())];
            }


            $alerts = (new AlertBuilder(db(), $config))->run();
            $scorer = new Scorer(db(), $config);
            $scorer->scoreBills();
            $pnText = (new PnText(db(), $config))->run();
            $tiskText = (new TiskText(db(), $config))->run();
            $ai = (new AiReviewer(db(), $config))->run();
            $scorer->scoreBills();
            $scorer->scoreAlerts();
            try {
                $searchItems = app(SearchIndex::class)->rebuild();
            } catch (\Throwable $ex) {
                $searchItems = ['error' => Http::redact($ex->getMessage())];
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
