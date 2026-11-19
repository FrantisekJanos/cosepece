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
        $text = self::fit($text, $url);
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

    public static function fit(string $text, string $url, int $max = 300): string
    {
        // Bluesky počítá limit 300 grafémů z celého příspěvku včetně odkazu (#2 bod 11)
        $len = fn(string $s): int => function_exists('grapheme_strlen') ? (int)grapheme_strlen($s) : mb_strlen($s);
        $room = $max - $len("\n" . $url);
        if ($len($text) <= $room) {
            return $text;
        }
        $cut = function_exists('grapheme_substr') ? (string)grapheme_substr($text, 0, max(0, $room - 1)) : mb_substr($text, 0, max(0, $room - 1));
        return rtrim($cut) . '…';
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
