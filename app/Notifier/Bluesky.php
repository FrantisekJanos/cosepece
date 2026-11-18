<?php
// Bluesky (AT Protocol): přihlášení heslem aplikace a vytvoření příspěvku s klikatelným odkazem.

declare(strict_types=1);

namespace Hlidac\Notifier;

use Hlidac\Http;

final class Bluesky implements Notifier
{
    private ?array $session = null;

    public function __construct(private array $cfg)
    {
    }

    public function name(): string
    {
        return 'bluesky';
    }

    public function maxLength(): int
    {
        return 300;
    }

    public function send(string $text, string $url): void
    {
        $service = rtrim($this->cfg['service'] ?? 'https://bsky.social', '/');
        $session = $this->login($service);
        $full = $text . "\n" . $url;

        // facet s odkazem; pozice se počítají v bajtech UTF-8
        $start = strlen($text . "\n");
        $record = [
            '$type' => 'app.bsky.feed.post',
            'text' => $full,
            'createdAt' => gmdate('Y-m-d\TH:i:s.v\Z'),
            'langs' => ['cs'],
            'facets' => [[
                'index' => ['byteStart' => $start, 'byteEnd' => $start + strlen($url)],
                'features' => [['$type' => 'app.bsky.richtext.facet#link', 'uri' => $url]],
            ]],
        ];
        Http::post($service . '/xrpc/com.atproto.repo.createRecord', [
            'Authorization: Bearer ' . $session['accessJwt'],
        ], [
            'repo' => $session['did'],
            'collection' => 'app.bsky.feed.post',
            'record' => $record,
        ]);
    }

    private function login(string $service): array
    {
        if ($this->session === null) {
            $this->session = Http::post($service . '/xrpc/com.atproto.server.createSession', [], [
                'identifier' => $this->cfg['handle'],
                'password' => $this->cfg['app_password'],
            ]);
        }
        return $this->session;
    }
}
