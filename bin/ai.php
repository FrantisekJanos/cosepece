<?php
// AI rozbor přes claude CLI (předplatné) na stroji s Claude Code.
//   php bin/ai.php [--limit=20] [--ct=221] [--all]                 místní databáze (--all = vše po dávkách)
//   php bin/ai.php --remote=https://web.cz [--token=…] [--limit=20] [--all]  fronta z hostingu, výsledky zpět
//                  (token raději v proměnné HLIDAC_TOKEN než v argumentu)
//   --verify       druhé kolo silnějším modelem (ai.verify_model) nad nálezy se závažností ≥ ai.verify_min
//   --praxe        vysvětlení PN „v praxi“ (app/PnPractice.php) místo procedurálního rozboru; s --verify druhé
//                  kolo silnějším modelem u PN s nálezem. Skóre se nemění.
//   --resanitize   znovu použije pojistky na uložené rozbory (bez volání modelu) a přepočítá skóre

declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    http_response_code(403);
    exit;
}

require dirname(__DIR__) . '/app/bootstrap.php';

use Hlidac\AiReviewer;
use Hlidac\Http;
use Hlidac\PnPractice;

$opt = getopt('', ['limit:', 'ct:', 'remote:', 'token:', 'all', 'verify', 'resanitize', 'praxe']);
$verify = isset($opt['verify']);
$praxe = isset($opt['praxe']);
$limit = (int)($opt['limit'] ?? config('ai.per_run', 20));
$ct = isset($opt['ct']) ? (int)$opt['ct'] : null;

// na tomto stroji vždy přes CLI (předplatné), i když hosting má jiné nastavení
$config = config();
$config['ai']['enabled'] = true;
$config['ai']['backend'] = 'cli';
$reviewer = new AiReviewer(db(), $config);

try {
    // --resanitize model nevolá, jde tedy spustit i bez claude CLI (např. v kontejneru na serveru)
    if (isset($opt['resanitize'])) {
        $n = $reviewer->resanitizeStored();
        $scorer = new Hlidac\Scorer(db(), config());
        $scorer->scoreBills();
        $scorer->scoreAlerts();
        echo "Upraveno $n rozborů\n";
        exit(0);
    }
    if (AiReviewer::cliPath($config) === null) {
        fwrite(STDERR, "claude CLI nenalezeno – nastavte ai.cli_path v config.php\n");
        exit(1);
    }
    if ($verify) {
        echo 'Ověření modelem ' . AiReviewer::verifyModel($config) . "\n";
    }
    if (!isset($opt['remote'])) {
        // --all: po dávkách, dokud je co posuzovat; skóre se přepočítá po každé dávce (web se průběžně plní)
        $scorer = new Hlidac\Scorer(db(), config());
        $practice = new PnPractice(db(), $config);
        $total = ['reviewed' => 0, 'errors' => 0];
        do {
            $stats = $praxe ? $practice->run($limit, $ct, $verify) : $reviewer->run($limit, $ct, $verify);
            $total['reviewed'] += $stats['reviewed'];
            $total['errors'] += $stats['errors'];
            if (!$praxe) {
                // vysvětlení „v praxi“ do skóre nevstupuje
                $scorer->scoreBills();
                $scorer->scoreAlerts();
            }
            echo date('H:i:s') . " dávka: posouzeno {$stats['reviewed']}, chyb {$stats['errors']}; celkem {$total['reviewed']}\n";
            // dávka bez jediného úspěchu = zbývají jen chybové texty, dál nepokračujeme
        } while (isset($opt['all']) && $stats['reviewed'] > 0);
        echo json_encode($total, JSON_UNESCAPED_UNICODE), "\n";
        exit($total['errors'] > 0 ? 1 : 0);
    }

    // vzdálený režim: fronta z hostingu → claude -p → výsledky zpět
    // --all: po dávkách, dokud hosting něco vrací (první spuštění dožene celé období)
    // token v hlavičce, ne v URL (nedostane se do access logů ani do chybových hlášek);
    // z proměnné HLIDAC_TOKEN, aby nebyl vidět v argumentech procesu (--token zůstává pro ruční spuštění)
    $tokenHeader = ['X-Hlidac-Token: ' . (string)($opt['token'] ?? getenv('HLIDAC_TOKEN') ?: '')];
    $base = rtrim((string)$opt['remote'], '/') . '/ai_api.php?v=2';
    $model = $verify ? AiReviewer::verifyModel($config) : AiReviewer::model($config);
    $total = ['reviewed' => 0, 'errors' => 0];
    // pauzy před opakováním odeslání při výpadku webu (celkem do 2 minut)
    $retry = [5, 15, 40, 60];
    do {
        $queue = json_decode(Http::get($base . '&action=queue&limit=' . $limit . ($ct !== null ? '&ct=' . $ct : '') . ($verify ? '&verify=1' : '') . ($praxe ? '&praxe=1' : ''), 20_000_000, $tokenHeader), true);
        if (!is_array($queue) || !isset($queue['items'])) {
            throw new RuntimeException('Neplatná odpověď fronty: ' . json_encode($queue, JSON_UNESCAPED_UNICODE));
        }
        $stats = ['reviewed' => 0, 'errors' => 0];
        foreach ($queue['items'] as $item) {
            if ($praxe && ($item['kind'] ?? '') !== 'praxe') {
                // starší verze webu parametr praxe=1 nezná a vrací běžnou frontu rozboru
                throw new RuntimeException('Web zatím vysvětlení „v praxi“ neumí (ai_api.php vrátil běžnou frontu)');
            }
            $label = $item['kind'] . ' ' . $item['id'] . ' (tisk ' . $item['ct'] . ')';
            try {
                $result = $reviewer->callModel((string)$item['prompt'], $model);
            } catch (Throwable $ex) {
                // rozbor se nepovedl: chybu zapíšeme k textu na hostingu
                try {
                    Http::post($base . '&action=result', $tokenHeader, ['kind' => $item['kind'], 'id' => $item['id'], 'error' => Http::redact($ex->getMessage())], true, 60, $retry);
                } catch (Throwable) {
                    // hosting nedostupný – chyba se jen vypíše, text zůstane ve frontě
                }
                $stats['errors']++;
                echo date('H:i:s') . " CHYBA $label: " . Http::redact($ex->getMessage()) . "\n";
                continue;
            }
            try {
                // výpadek webu při nasazení (502 při výměně kontejneru) přečkáme opakováním, hotový rozbor se neztratí
                $reply = Http::post($base . '&action=result', $tokenHeader, ['kind' => $item['kind'], 'id' => $item['id'], 'result' => $result, 'model' => $model], true, 60, $retry);
                if (($reply['accepted'] ?? true) === false) {
                    // vysvětlení „v praxi“ neprošlo pojistkami na hostingu: důvod je zapsaný u PN, další běh to zkusí
                    // znovu. Není to chyba běhu (pojistky jsou od toho), proto se nepočítá do návratového kódu.
                    $total['rejected'] = ($total['rejected'] ?? 0) + 1;
                    echo date('H:i:s') . " ODMÍTNUTO $label: neprošlo pojistkami\n";
                    continue;
                }
                $stats['reviewed']++;
                echo date('H:i:s') . " OK    $label" . ($praxe ? '' : ': závažnost ' . (int)($result['zavaznost'] ?? 0) . '/10') . "\n";
            } catch (Throwable $ex) {
                // rozbor je v pořádku, jen se nepodařilo ho odeslat: k textu se chyba nezapisuje, fronta ho nabídne znovu
                $stats['errors']++;
                echo date('H:i:s') . " CHYBA $label (odeslání): " . Http::redact($ex->getMessage()) . "\n";
            }
        }
        if ($queue['items'] !== [] && !$praxe) {
            // přepočet skóre na hostingu po každé dávce, web se průběžně plní
            Http::post($base . '&action=done', $tokenHeader, [], true, 120, $retry);
        }
        $total['reviewed'] += $stats['reviewed'];
        $total['errors'] += $stats['errors'];
        // dávka bez jediného úspěchu = zbývají jen chybové texty, dál nepokračujeme
    } while (isset($opt['all']) && $stats['reviewed'] > 0);
    echo date('H:i:s') . ' ' . ($praxe ? 'v praxi ' : '') . ($verify ? 'ověření ' : 'rozbor ') . json_encode($total, JSON_UNESCAPED_UNICODE), "\n";
    exit($total['errors'] > 0 ? 1 : 0);
} catch (Throwable $ex) {
    fwrite(STDERR, 'Chyba: ' . $ex->getMessage() . "\n");
    exit(1);
}
