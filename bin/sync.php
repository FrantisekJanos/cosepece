<?php
// Spuštění synchronizace z příkazové řádky / cronu: php bin/sync.php

declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    http_response_code(403);
    exit;
}

require dirname(__DIR__) . '/app/bootstrap.php';

// Po restartu serveru startuje kontejner synchronizace zároveň s databází. Počkáme proto až minutu,
// než začne přijímat spojení; bez toho běh selhal a další přišel až za hodinu. Když se nedočkáme,
// pokračujeme a chybu ohlásí Sync::run() jako dřív. U SQLite se spojení podaří hned.
Hlidac\Db::waitUntilReady(static fn() => new Hlidac\Db(config('db_dsn'), config('db_user'), config('db_pass')));

try {
    $result = Hlidac\Sync::run();
    echo json_encode($result, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT), "\n";
    exit(0);
} catch (Throwable $ex) {
    Hlidac\Sentry::capture($ex, ['kde' => 'bin/sync.php']);
    fwrite(STDERR, 'Chyba: ' . $ex->getMessage() . "\n");
    exit(1);
}
