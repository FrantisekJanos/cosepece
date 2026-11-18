<?php
// Rozhraní pro vzdálený AI rozbor: hosting neumí spustit claude CLI, takže fronta textů se stáhne
// na stroj s Claude Code (bin/ai.php --remote), tam se posoudí přes předplatné a výsledky se pošlou zpět.
//   GET  ai_api.php?token=…&action=queue&limit=10[&ct=221][&verify=1]  → {"items": [{kind, id, ct, prompt}]}
//        verify=1: nejzávažnější nálezy k ověření silnějším modelem
//   POST ai_api.php?token=…&action=result  {"kind","id","result","model"} nebo {"kind","id","error"}
//   POST ai_api.php?token=…&action=done    → přepočet skóre

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\AiReviewer;
use Hlidac\Scorer;

header('Content-Type: application/json; charset=utf-8');

function aiApiReply(array $data, int $status = 200): never
{
    http_response_code($status);
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

// vlastní token pro AI, jinak token cronu
$expected = (string)(config('ai.remote_token') ?: config('cron_token', ''));
$token = (string)($_GET['token'] ?? '');
if ($expected === '' || str_starts_with($expected, 'ZMENTE') || !hash_equals($expected, $token)) {
    aiApiReply(['error' => 'Neplatný token'], 403);
}
if (empty(config('ai.enabled'))) {
    aiApiReply(['error' => 'AI rozbor je vypnutý (ai.enabled)'], 409);
}

$reviewer = new AiReviewer(db(), config());
$action = (string)($_GET['action'] ?? '');

try {
    if ($action === 'queue' && $_SERVER['REQUEST_METHOD'] === 'GET') {
        $limit = max(1, min(50, (int)($_GET['limit'] ?? 10)));
        $ct = isset($_GET['ct']) ? (int)$_GET['ct'] : null;
        aiApiReply(['items' => $reviewer->queue($limit, $ct, !empty($_GET['verify']))]);
    }
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        aiApiReply(['error' => 'Neznámá akce'], 400);
    }
    if ($action === 'result') {
        $body = json_decode((string)file_get_contents('php://input', false, null, 0, 1_000_000), true);
        if (!is_array($body) || !isset($body['kind'], $body['id'])) {
            aiApiReply(['error' => 'Chybí kind a id'], 400);
        }
        if (isset($body['result']) && is_array($body['result'])) {
            $model = isset($body['model']) && is_string($body['model']) && preg_match('~^[a-z0-9.\-]{1,60}$~', $body['model']) ? $body['model'] : null;
            $reviewer->saveResult((string)$body['kind'], (int)$body['id'], $body['result'], $model);
        } else {
            $reviewer->saveError((string)$body['kind'], (int)$body['id'], (string)($body['error'] ?? 'Neznámá chyba'));
        }
        aiApiReply(['ok' => true]);
    }
    if ($action === 'done') {
        $scorer = new Scorer(db(), config());
        $scorer->scoreBills();
        $scorer->scoreAlerts();
        aiApiReply(['ok' => true]);
    }
    aiApiReply(['error' => 'Neznámá akce'], 400);
} catch (Throwable $ex) {
    aiApiReply(['error' => $ex->getMessage()], 500);
}
