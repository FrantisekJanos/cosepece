<?php
// Spuštění synchronizace z příkazové řádky / cronu: php bin/sync.php

declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    http_response_code(403);
    exit;
}

require dirname(__DIR__) . '/app/bootstrap.php';

try {
    $result = Hlidac\Sync::run();
    echo json_encode($result, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT), "\n";
    exit(0);
} catch (Throwable $ex) {
    fwrite(STDERR, 'Chyba: ' . $ex->getMessage() . "\n");
    exit(1);
}
