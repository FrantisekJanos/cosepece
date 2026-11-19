<?php


declare(strict_types=1);

namespace Hlidac;

use App\Models\PostLog;
use Hlidac\Notifier\Bluesky;
use Hlidac\Notifier\Mastodon;
use Hlidac\Notifier\Notifier;
use Hlidac\Notifier\Telegram;
use Hlidac\Notifier\X;

final class Publisher
{
    private const MAX_ERRORS = 3;

    /** @var Notifier[] */
    private array $notifiers = [];

    public function __construct(private Db $db, private array $config)
    {
        $n = $config['notify'] ?? [];
        if (!empty($n['bluesky']['enabled'])) {
            $this->notifiers[] = new Bluesky($n['bluesky']);
        }
        if (!empty($n['mastodon']['enabled'])) {
            $this->notifiers[] = new Mastodon($n['mastodon']);
        }
        if (!empty($n['telegram']['enabled'])) {
            $this->notifiers[] = new Telegram($n['telegram']);
        }
        if (!empty($n['x']['enabled'])) {
            $this->notifiers[] = new X($n['x']);
        }
    }

    public function run(): array
    {
        $minScore = (int)($this->config['notify']['min_score'] ?? 50);
        $kindsOff = $this->config['notify']['kinds_off'] ?? [];
        $limit = (int)($this->config['notify']['max_posts_per_run'] ?? 10);
        $dryRun = !empty($this->config['notify']['dry_run']);

        $mode = strtolower(trim((string)($this->config['notify']['post'] ?? 'all')));
        $stats = ['sent' => 0, 'filtered' => 0, 'errors' => 0];

        $pending = $this->db->all('SELECT * FROM alerts WHERE posted = 0 ORDER BY score DESC, event_at');
        foreach ($pending as $alert) {
            $modeOff = $mode === 'off' || ($mode === 'watch' && !(int)$alert['watched']);
            if ($modeOff || (int)$alert['score'] < $minScore || in_array($alert['kind'], $kindsOff, true)) {
                $this->db->run('UPDATE alerts SET posted = 3 WHERE alert_key = ?', [$alert['alert_key']]);
                $stats['filtered']++;
                continue;
            }
            if ($this->notifiers === [] && !$dryRun) {

                $this->db->run('UPDATE alerts SET posted = 3 WHERE alert_key = ?', [$alert['alert_key']]);
                $stats['filtered']++;
                continue;
            }
            if ($limit-- <= 0) {
                break;
            }
            $done = true;
            foreach ($this->targets($dryRun) as $notifier) {
                $name = $notifier === null ? 'dry_run' : $notifier->name();
                if ($this->isFinished($alert['alert_key'], $name)) {
                    continue;
                }
                $url = siteUrl('tisk.php?ct=' . (int)$alert['ct']);
                $text = $this->compose($alert, $notifier?->maxLength() ?? 300);
                try {
                    if ($notifier === null) {
                        file_put_contents(hlidacDataPath('posts.log'), date('c') . "\n" . $text . "\n" . $url . "\n\n", FILE_APPEND);
                    } else {
                        $notifier->send($text, $url);
                    }
                    $this->log($alert['alert_key'], $name, 'ok', null);
                    $stats['sent']++;
                } catch (\Throwable $ex) {
                    $this->log($alert['alert_key'], $name, 'error', $ex->getMessage());
                    $stats['errors']++;
                    if (!$this->isFinished($alert['alert_key'], $name)) {
                        $done = false;
                    }
                }
            }
            if ($done) {
                $this->db->run('UPDATE alerts SET posted = 1 WHERE alert_key = ?', [$alert['alert_key']]);
            }
        }
        return $stats;
    }

    /** @return array<int, Notifier|null> null = zkušební režim bez odesílání */
    private function targets(bool $dryRun): array
    {
        return $dryRun ? [null] : $this->notifiers;
    }

    private function compose(array $alert, int $max): string
    {

        $text = $alert['title'] . "\n" . $alert['body'];
        $tags = trim((string)($this->config['notify']['hashtags'] ?? ''));
        if ($tags !== '' && mb_strlen($text) + mb_strlen($tags) + 1 <= $max) {
            $text .= "\n" . $tags;
        }
        if (mb_strlen($text) > $max) {
            $text = rtrim(mb_substr($text, 0, $max - 1)) . '…';
        }
        return $text;
    }

    private function isFinished(string $key, string $network): bool
    {

        $attempts = PostLog::query()->where('alert_key', $key)->where('network', $network);
        $ok = (clone $attempts)->where('status', 'ok')->count();
        $errors = (clone $attempts)->where('status', 'error')->count();
        return $ok > 0 || $errors >= self::MAX_ERRORS;
    }

    private function log(string $key, string $network, string $status, ?string $error): void
    {
        PostLog::query()->create([
            'alert_key' => $key, 'network' => $network, 'status' => $status,
            'at' => date('Y-m-d H:i:s'),
            'error' => $error === null ? null : mb_substr(Http::redact($error), 0, 500),
        ]);
    }
}
