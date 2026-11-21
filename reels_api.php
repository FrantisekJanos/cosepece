<?php
// Rozhraní pro worker generátoru Reels na PC: výdej úlohy, nahrání videí, výsledek, chyba, heartbeat.
// Logika a popis akcí v app/ReelsApi.php; tady jen převod HTTP požadavku a odpovědi.

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

$method = strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET'));
$headers = [
    'X-Hlidac-Token' => (string)($_SERVER['HTTP_X_HLIDAC_TOKEN'] ?? ''),
    'X-Claim-Token' => (string)($_SERVER['HTTP_X_CLAIM_TOKEN'] ?? ''),
];
if (isset($_SERVER['CONTENT_LENGTH']) && $_SERVER['CONTENT_LENGTH'] !== '') {
    $headers['Content-Length'] = (string)$_SERVER['CONTENT_LENGTH'];
}

try {
    $api = new Hlidac\ReelsApi(db(), config());
    if ($method === 'PUT') {
        // video se čte po částech rovnou z php://input (post_max_size se na PUT nevztahuje)
        $input = fopen('php://input', 'rb');
        [$status, $data] = $api->handle($method, (string)($_GET['action'] ?? ''), $_GET, '', $headers, $input ?: null);
    } else {
        $body = $method === 'POST'
            ? (string)file_get_contents('php://input', false, null, 0, Hlidac\ReelsApi::MAX_JSON_BYTES + 1)
            : '';
        [$status, $data] = $api->handle($method, (string)($_GET['action'] ?? ''), $_GET, $body, $headers);
    }
} catch (Throwable $ex) {
    error_log('reels_api.php: ' . $ex->getMessage());
    Hlidac\Sentry::capture($ex, ['kde' => 'reels_api.php']);
    [$status, $data] = [500, ['error' => Hlidac\Http::redact($ex->getMessage())]];
}

http_response_code($status);
echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
