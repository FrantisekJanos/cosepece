<?php
// AI rozbor přes claude CLI (předplatné) na stroji s Claude Code.
//   php bin/ai.php [--limit=20] [--ct=221] [--all]                 místní databáze (--all = vše po dávkách)
//   php bin/ai.php --remote=https://web.cz --token=… [--limit=20] [--all]  fronta z hostingu, výsledky zpět
//   --verify       druhé kolo silnějším modelem (ai.verify_model) nad nálezy se závažností ≥ ai.verify_min
//   --resanitize   znovu použije pojistky na uložené rozbory (bez volání modelu) a přepočítá skóre

declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    http_response_code(403);
    exit;
}

require dirname(__DIR__) . '/app/bootstrap.php';

use Hlidac\AiReviewer;
use Hlidac\Http;

$opt = getopt('', ['limit:', 'ct:', 'remote:', 'token:', 'all', 'verify', 'resanitize']);
$verify = isset($opt['verify']);
$limit = (int)($opt['limit'] ?? config('ai.per_run', 20));
$ct = isset($opt['ct']) ? (int)$opt['ct'] : null;

// na tomto stroji vždy přes CLI (předplatné), i když hosting má jiné nastavení
$config = config();
$config['ai']['enabled'] = true;
$config['ai']['backend'] = 'cli';
$reviewer = new AiReviewer(db(), $config);
if (AiReviewer::cliPath($config) === null) {
    fwrite(STDERR, "claude CLI nenalezeno – nastavte ai.cli_path v config.php\n");
    exit(1);
}

try {
    if (isset($opt['resanitize'])) {
        $n = $reviewer->resanitizeStored();
        $scorer = new Hlidac\Scorer(db(), config());
        $scorer->scoreBills();
        $scorer->scoreAlerts();
        echo "Upraveno $n rozborů\n";
        exit(0);
    }
    if ($verify) {
        echo 'Ověření modelem ' . AiReviewer::verifyModel($config) . "\n";
    }
    if (!isset($opt['remote'])) {
        // --all: po dávkách, dokud je co posuzovat; skóre se přepočítá po každé dávce (web se průběžně plní)
        $scorer = new Hlidac\Scorer(db(), config());
        $total = ['reviewed' => 0, 'errors' => 0];
        do {
            $stats = $reviewer->run($limit, $ct, $verify);
            $total['reviewed'] += $stats['reviewed'];
            $total['errors'] += $stats['errors'];
            $scorer->scoreBills();
            $scorer->scoreAlerts();
            echo date('H:i:s') . " dávka: posouzeno {$stats['reviewed']}, chyb {$stats['errors']}; celkem {$total['reviewed']}\n";
            // dávka bez jediného úspěchu = zbývají jen chybové texty, dál nepokračujeme
        } while (isset($opt['all']) && $stats['reviewed'] > 0);
        echo json_encode($total, JSON_UNESCAPED_UNICODE), "\n";
        exit($total['errors'] > 0 ? 1 : 0);
    }

    // vzdálený režim: fronta z hostingu → claude -p → výsledky zpět
    // --all: po dávkách, dokud hosting něco vrací (první spuštění dožene celé období)
    $base = rtrim((string)$opt['remote'], '/') . '/ai_api.php?token=' . rawurlencode((string)($opt['token'] ?? ''));
    $model = $verify ? AiReviewer::verifyModel($config) : AiReviewer::model($config);
    $total = ['reviewed' => 0, 'errors' => 0];
    do {
        $queue = json_decode(Http::get($base . '&action=queue&limit=' . $limit . ($ct !== null ? '&ct=' . $ct : '') . ($verify ? '&verify=1' : '')), true);
        if (!is_array($queue) || !isset($queue['items'])) {
            throw new RuntimeException('Neplatná odpověď fronty: ' . json_encode($queue, JSON_UNESCAPED_UNICODE));
        }
        $stats = ['reviewed' => 0, 'errors' => 0];
        foreach ($queue['items'] as $item) {
            $label = $item['kind'] . ' ' . $item['id'] . ' (tisk ' . $item['ct'] . ')';
            try {
                $result = $reviewer->callModel((string)$item['prompt'], $model);
                Http::post($base . '&action=result', [], ['kind' => $item['kind'], 'id' => $item['id'], 'result' => $result, 'model' => $model], true, 60);
                $stats['reviewed']++;
                echo date('H:i:s') . " OK    $label: závažnost " . (int)($result['zavaznost'] ?? 0) . "/10\n";
            } catch (Throwable $ex) {
                try {
                    Http::post($base . '&action=result', [], ['kind' => $item['kind'], 'id' => $item['id'], 'error' => $ex->getMessage()], true, 60);
                } catch (Throwable) {
                    // hosting nedostupný – chyba se jen vypíše, text zůstane ve frontě
                }
                $stats['errors']++;
                echo date('H:i:s') . " CHYBA $label: " . $ex->getMessage() . "\n";
            }
        }
        if ($queue['items'] !== []) {
            // přepočet skóre na hostingu po každé dávce, web se průběžně plní
            Http::post($base . '&action=done', [], [], true, 120);
        }
        $total['reviewed'] += $stats['reviewed'];
        $total['errors'] += $stats['errors'];
        // dávka bez jediného úspěchu = zbývají jen chybové texty, dál nepokračujeme
    } while (isset($opt['all']) && $stats['reviewed'] > 0);
    echo date('H:i:s') . ' ' . ($verify ? 'ověření ' : 'rozbor ') . json_encode($total, JSON_UNESCAPED_UNICODE), "\n";
    exit($total['errors'] > 0 ? 1 : 0);
} catch (Throwable $ex) {
    fwrite(STDERR, 'Chyba: ' . $ex->getMessage() . "\n");
    exit(1);
}
