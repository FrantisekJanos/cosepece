<?php

declare(strict_types=1);

namespace App\Console\Commands;

use Illuminate\Console\Command;
use Hlidac\AiReviewer;
use Hlidac\Http;

final class AiCommand extends Command
{
    protected $signature = 'hlidac:ai {--limit=} {--ct=} {--remote=} {--token=} {--all} {--verify} {--resanitize}';
    protected $description = 'Rozbor textů přes Claude CLI, místně nebo vzdáleně';

    public function handle(): int
    {
        $opt = array_filter($this->options(), fn ($value) => $value !== null && $value !== false);
        $verify = isset($opt['verify']);
        $limit = (int)($opt['limit'] ?? hlidacConfig('ai.per_run', 20));
        $ct = isset($opt['ct']) ? (int)$opt['ct'] : null;
        if ($limit < 1 || $limit > 50 || ($ct !== null && $ct < 1)) {
            $this->error('Limit musí být 1–50 a číslo tisku kladné.');
            return self::FAILURE;
        }

        $config = hlidacConfig();
        $config['ai']['enabled'] = true;
        $config['ai']['backend'] = 'cli';
        $reviewer = new AiReviewer(db(), $config);

        try {

            if (isset($opt['resanitize'])) {
                $n = $reviewer->resanitizeStored();
                $scorer = new \Hlidac\Scorer(db(), hlidacConfig());
                $scorer->scoreBills();
                $scorer->scoreAlerts();
                $this->output->write("Upraveno $n rozborů\n");
                return 0;
            }
            if (AiReviewer::cliPath($config) === null) {
                $this->output->write("claude CLI nenalezeno – nastavte ai.cli_path v config.php\n");
                return 1;
            }
            if ($verify) {
                $this->output->write('Ověření modelem ' . AiReviewer::verifyModel($config) . "\n");
            }
            if (!isset($opt['remote'])) {

                $scorer = new \Hlidac\Scorer(db(), hlidacConfig());
                $total = ['reviewed' => 0, 'errors' => 0];
                do {
                    $stats = $reviewer->run($limit, $ct, $verify);
                    $total['reviewed'] += $stats['reviewed'];
                    $total['errors'] += $stats['errors'];
                    $scorer->scoreBills();
                    $scorer->scoreAlerts();
                    $this->output->write(date('H:i:s') . " dávka: posouzeno {$stats['reviewed']}, chyb {$stats['errors']}; celkem {$total['reviewed']}\n");

                } while (isset($opt['all']) && $stats['reviewed'] > 0);
                $this->output->write(json_encode($total, JSON_UNESCAPED_UNICODE) . "\n");
                return $total['errors'] > 0 ? 1 : 0;
            }

            $tokenHeader = ['X-Hlidac-Token: ' . (string)($opt['token'] ?? getenv('HLIDAC_TOKEN') ?: '')];
            $base = rtrim((string)$opt['remote'], '/') . '/ai_api.php?v=2';
            $model = $verify ? AiReviewer::verifyModel($config) : AiReviewer::model($config);
            $total = ['reviewed' => 0, 'errors' => 0];
            do {
                $queue = json_decode(Http::get($base . '&action=queue&limit=' . $limit . ($ct !== null ? '&ct=' . $ct : '') . ($verify ? '&verify=1' : ''), 20_000_000, $tokenHeader), true);
                if (!is_array($queue) || !isset($queue['items'])) {
                    throw new \RuntimeException('Neplatná odpověď fronty: ' . json_encode($queue, JSON_UNESCAPED_UNICODE));
                }
                $stats = ['reviewed' => 0, 'errors' => 0];
                foreach ($queue['items'] as $item) {
                    $label = $item['kind'] . ' ' . $item['id'] . ' (tisk ' . $item['ct'] . ')';
                    try {
                        $result = $reviewer->callModel((string)$item['prompt'], $model);
                        Http::post($base . '&action=result', $tokenHeader, ['kind' => $item['kind'], 'id' => $item['id'], 'result' => $result, 'model' => $model], true, 60);
                        $stats['reviewed']++;
                        $this->output->write(date('H:i:s') . " OK    $label: závažnost " . (int)($result['zavaznost'] ?? 0) . "/10\n");
                    } catch (\Throwable $ex) {
                        try {
                            Http::post($base . '&action=result', $tokenHeader, ['kind' => $item['kind'], 'id' => $item['id'], 'error' => Http::redact($ex->getMessage())], true, 60);
                        } catch (\Throwable) {

                        }
                        $stats['errors']++;
                        $this->output->write(date('H:i:s') . " CHYBA $label: " . Http::redact($ex->getMessage()) . "\n");
                    }
                }
                if ($queue['items'] !== []) {

                    Http::post($base . '&action=done', $tokenHeader, [], true, 120);
                }
                $total['reviewed'] += $stats['reviewed'];
                $total['errors'] += $stats['errors'];

            } while (isset($opt['all']) && $stats['reviewed'] > 0);
            $this->output->write(date('H:i:s') . ' ' . ($verify ? 'ověření ' : 'rozbor ') . json_encode($total, JSON_UNESCAPED_UNICODE) . "\n");
            return $total['errors'] > 0 ? 1 : 0;
        } catch (\Throwable $ex) {
            $this->output->write('Chyba: ' . $ex->getMessage() . "\n");
            return 1;
        }
    }
}
