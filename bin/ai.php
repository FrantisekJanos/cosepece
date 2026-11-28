<?php
// AI rozbor přes claude CLI (předplatné) na stroji s Claude Code.
//   php bin/ai.php [--limit=20] [--ct=221] [--all]                 místní databáze (--all = vše po dávkách)
//   php bin/ai.php --remote=https://web.cz [--token=…] [--limit=20] [--all]  fronta z hostingu, výsledky zpět
//                  (token raději v proměnné HLIDAC_TOKEN než v argumentu)
//   --verify       druhé kolo silnějším modelem (ai.verify_model) nad nálezy se závažností ≥ ai.verify_min
//   --praxe        vysvětlení PN „v praxi“ (app/PnPractice.php) místo procedurálního rozboru; s --verify druhé
//                  kolo silnějším modelem u PN s nálezem. Skóre se nemění.
//   --hlaseni      roztřídí hlášení čtenářů k vysvětlením „v praxi“ (app/Reports.php): platné, spam, nesmysl,
//                  pokus o manipulaci, mimo téma. Rychlým modelem. S --verify silnější model zkontroluje
//                  vysvětlení, ke kterým přišlo platné hlášení (oprava musí projít pojistkami). Skóre se nemění.
//   --resanitize   znovu použije pojistky na uložené rozbory (bez volání modelu) a přepočítá skóre;
//                  s --praxe na uložená vysvětlení „v praxi“ (co neprojde, vrátí se do fronty)
//   --praxe --retry  vrátí do fronty PN, jejichž vysvětlení pojistky odmítly tolikrát, že se už samy nezkusí
//                  (bez volání modelu; další pokus dostane v zadání důvod posledního odmítnutí)

declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    http_response_code(403);
    exit;
}

require dirname(__DIR__) . '/app/bootstrap.php';

use Hlidac\AiReviewer;
use Hlidac\Http;
use Hlidac\PnPractice;
use Hlidac\Reports;

$opt = getopt('', ['limit:', 'ct:', 'remote:', 'token:', 'all', 'verify', 'resanitize', 'praxe', 'retry', 'hlaseni']);
$verify = isset($opt['verify']);
$praxe = isset($opt['praxe']);
$hlaseni = isset($opt['hlaseni']);
$limit = (int)($opt['limit'] ?? config('ai.per_run', 20));
$ct = isset($opt['ct']) ? (int)$opt['ct'] : null;

// na tomto stroji vždy přes CLI (předplatné), i když hosting má jiné nastavení
$config = config();
$config['ai']['enabled'] = true;
$config['ai']['backend'] = 'cli';
$reviewer = new AiReviewer(db(), $config);

try {
    // --resanitize model nevolá, jde tedy spustit i bez claude CLI (např. v kontejneru na serveru)
    if (isset($opt['resanitize']) && $praxe) {
        // --praxe --resanitize: pojistky znovu na uložená vysvětlení „v praxi“; co neprojde, vrátí se do fronty
        echo json_encode((new PnPractice(db(), $config))->resanitizeStored(), JSON_UNESCAPED_UNICODE), "\n";
        exit(0);
    }
    if (isset($opt['retry'])) {
        // --praxe --retry: jen počitadlo pokusů, model se nevolá (jde spustit i v kontejneru na serveru)
        if (!$praxe) {
            fwrite(STDERR, "--retry je jen pro vysvětlení v praxi: php bin/ai.php --praxe --retry\n");
            exit(1);
        }
        echo json_encode(['vraceno_do_fronty' => (new PnPractice(db(), $config))->retryRejected()], JSON_UNESCAPED_UNICODE), "\n";
        exit(0);
    }
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
    if ($hlaseni && !isset($opt['remote'])) {
        // třídění hlášení čtenářů (s --verify kontrola nahlášených vysvětlení) nad místní databází: jedna dávka
        $stats = $verify ? (new Reports(db(), $config))->runChecks($limit) : (new Reports(db(), $config))->run($limit);
        echo date('H:i:s') . ' hlášení ' . ($verify ? 'kontrola ' : '') . json_encode($stats, JSON_UNESCAPED_UNICODE), "\n";
        exit($stats['errors'] > 0 ? 1 : 0);
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
    // hlášení třídí rychlý model, nahlášená vysvětlení kontroluje silnější (--hlaseni --verify)
    $model = $verify ? AiReviewer::verifyModel($config) : AiReviewer::model($config);
    $reportKind = $verify ? 'kontrola' : 'hlaseni';
    $total = ['reviewed' => 0, 'errors' => 0];
    // pauzy před opakováním odeslání při výpadku webu (celkem do 2 minut)
    $retry = [5, 15, 40, 60];
    do {
        $queue = json_decode(Http::get($base . '&action=queue&limit=' . $limit . ($ct !== null ? '&ct=' . $ct : '') . ($hlaseni ? '&hlaseni=1' . ($verify ? '&verify=1' : '') : ($verify ? '&verify=1' : '') . ($praxe ? '&praxe=1' : '')), 20_000_000, $tokenHeader), true);
        if (!is_array($queue) || !isset($queue['items'])) {
            throw new RuntimeException('Neplatná odpověď fronty: ' . json_encode($queue, JSON_UNESCAPED_UNICODE));
        }
        $stats = ['reviewed' => 0, 'errors' => 0];
        foreach ($queue['items'] as $item) {
            if ($hlaseni && ($item['kind'] ?? '') !== $reportKind) {
                // starší verze webu parametr hlaseni=1 (nebo jeho spojení s verify=1) nezná a vrací jinou frontu
                throw new RuntimeException('Web zatím ' . ($verify ? 'kontrolu nahlášených vysvětlení' : 'hlášení čtenářů') . ' neumí (ai_api.php vrátil jinou frontu)');
            }
            if (!$hlaseni && $praxe && ($item['kind'] ?? '') !== 'praxe') {
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
                    echo date('H:i:s') . " ODMÍTNUTO $label: " . ($hlaseni && !$verify ? 'hlášení už je roztříděné' : 'neprošlo pojistkami') . "\n";
                    continue;
                }
                $stats['reviewed']++;
                echo date('H:i:s') . " OK    $label" . ($praxe || $hlaseni ? '' : ': závažnost ' . (int)($result['zavaznost'] ?? 0) . '/10') . "\n";
            } catch (Throwable $ex) {
                // rozbor je v pořádku, jen se nepodařilo ho odeslat: k textu se chyba nezapisuje, fronta ho nabídne znovu
                $stats['errors']++;
                echo date('H:i:s') . " CHYBA $label (odeslání): " . Http::redact($ex->getMessage()) . "\n";
            }
        }
        if ($queue['items'] !== [] && !$praxe && !$hlaseni) {
            // přepočet skóre na hostingu po každé dávce, web se průběžně plní
            Http::post($base . '&action=done', $tokenHeader, [], true, 120, $retry);
        }
        $total['reviewed'] += $stats['reviewed'];
        $total['errors'] += $stats['errors'];
        // dávka bez jediného úspěchu = zbývají jen chybové texty, dál nepokračujeme
    } while (isset($opt['all']) && $stats['reviewed'] > 0);
    echo date('H:i:s') . ' ' . ($hlaseni ? 'hlášení ' . ($verify ? 'kontrola ' : '') : ($praxe ? 'v praxi ' : '') . ($verify ? 'ověření ' : 'rozbor ')) . json_encode($total, JSON_UNESCAPED_UNICODE), "\n";
    exit($total['errors'] > 0 ? 1 : 0);
} catch (Throwable $ex) {
    fwrite(STDERR, 'Chyba: ' . $ex->getMessage() . "\n");
    exit(1);
}
