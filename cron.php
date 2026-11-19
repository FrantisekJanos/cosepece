<?php
// Webcron pro hostingy bez přístupu k příkazové řádce: /cron.php?token=...

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

header('Content-Type: application/json; charset=utf-8');

// po 10 chybných tokenech za 15 minut se adresa dočasně odmítá (hádání tokenu)
$blocked = Hlidac\RateLimit::authBlocked();
if ($blocked !== null) {
    Hlidac\RateLimit::deny($blocked);
}
$token = (string)($_GET['token'] ?? '');
$expected = (string)config('cron_token', '');
if ($expected === '' || str_starts_with($expected, 'ZMENTE') || !hash_equals($expected, $token)) {
    Hlidac\RateLimit::authFailed();
    http_response_code(403);
    echo json_encode(['error' => 'Neplatný token']);
    exit;
}

ignore_user_abort(true);
set_time_limit(600);
ini_set('memory_limit', '512M');

try {
    echo json_encode(Hlidac\Sync::run(), JSON_UNESCAPED_UNICODE);
} catch (Throwable $ex) {
    http_response_code(500);
    echo json_encode(['error' => $ex->getMessage()], JSON_UNESCAPED_UNICODE);
}
