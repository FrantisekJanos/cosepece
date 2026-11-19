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
            $import = (new Importer(db(), $source))->run();
            try {
                $votes = (new Votes(db(), $source))->run();
            } catch (\Throwable $ex) {
                // hlasování jsou doplňková: chyba se zapíše, synchronizace pokračuje
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
            $posts = (new Publisher(db(), $config))->run();
            $result = [
                'import' => $import,
                'votes' => $votes,
                'alerts_new' => $alerts,
                'pn_text' => $pnText,
                'tisk_text' => $tiskText,
                'ai' => $ai,
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
