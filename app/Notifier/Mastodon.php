<?php


declare(strict_types=1);

namespace Hlidac\Notifier;

use Hlidac\Http;

final class Mastodon implements Notifier
{
    public function __construct(private array $cfg)
    {
    }

    public function name(): string
    {
        return 'mastodon';
    }

    public function maxLength(): int
    {

        return 500 - 24;
    }

    public function send(string $text, string $url): void
    {
        $instance = rtrim($this->cfg['instance'], '/');
        Http::post($instance . '/api/v1/statuses', [
            'Authorization: Bearer ' . $this->cfg['token'],
            'Idempotency-Key: ' . sha1($text . $url),
        ], [
            'status' => $text . "\n" . $url,
            'language' => 'cs',
            'visibility' => $this->cfg['visibility'] ?? 'public',
        ]);
    }
}
