<?php
// Ovladač PHP pro test celého řetězu (tests/test_chain.py): odevzdá výsledek úlohy jako worker,
// tedy PUT video pro každou variantu a nakonec POST result, přímo přes Hlidac\ReelsApi (bez HTTP).
// Všechno čte z prostředí, tajemství neobsahuje:
//   CHAIN_DSN, CHAIN_USER, CHAIN_PASS  testovací databáze (PDO)
//   CHAIN_TOKEN                        token workeru (X-Hlidac-Token)
//   CHAIN_INBOX                        mezisložka (reels.inbox_dir)
//   CHAIN_JOB_ID, CHAIN_CLAIM          vydaná úloha a její claim token (X-Claim-Token)
//   CHAIN_SRC                          složka s videi variant (variant-<n>.mp4)
//   CHAIN_VARIANTS, CHAIN_SKIPPED      JSON: varianty a nevzniklé varianty pro result
// Vypíše JSON [[status, odpověď], …]: odpovědi video v pořadí variant, poslední je result.

declare(strict_types=1);

// bez db(): ta by do testovací databáze migrovala celé schéma webu
require __DIR__ . '/../../app/bootstrap.php';

$db = new Hlidac\Db((string)getenv('CHAIN_DSN'), (string)getenv('CHAIN_USER'), (string)getenv('CHAIN_PASS'));
$api = new Hlidac\ReelsApi($db, [
    'reels' => ['worker_token' => (string)getenv('CHAIN_TOKEN'), 'inbox_dir' => (string)getenv('CHAIN_INBOX')],
    'site_url' => 'https://hlidac.test',
]);
$jobId = (int)getenv('CHAIN_JOB_ID');
$headers = ['X-Hlidac-Token' => (string)getenv('CHAIN_TOKEN'), 'X-Claim-Token' => (string)getenv('CHAIN_CLAIM')];
$variants = json_decode((string)getenv('CHAIN_VARIANTS'), true, 512, JSON_THROW_ON_ERROR);
$skipped = json_decode((string)getenv('CHAIN_SKIPPED') ?: '[]', true, 512, JSON_THROW_ON_ERROR);

$out = [];
foreach ($variants as $v) {
    // video se čte z proudu stejně jako v reels_api.php (php://input)
    $path = getenv('CHAIN_SRC') . '/variant-' . $v['n'] . '.mp4';
    $input = fopen($path, 'rb');
    $out[] = $api->handle('PUT', 'video', ['job_id' => (string)$jobId, 'n' => (string)$v['n']], '',
        $headers + ['Content-Length' => (string)filesize($path)], $input);
    fclose($input);
}
$body = json_encode(['job_id' => $jobId, 'variants' => $variants, 'skipped' => $skipped], JSON_UNESCAPED_UNICODE);
$out[] = $api->handle('POST', 'result', [], $body, $headers);

echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES), "\n";
