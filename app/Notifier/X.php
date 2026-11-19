<?php


declare(strict_types=1);

namespace Hlidac\Notifier;

use Hlidac\Http;

final class X implements Notifier
{
    private const ENDPOINT = 'https://api.x.com/2/tweets';

    public function __construct(private array $cfg)
    {
    }

    public function name(): string
    {
        return 'x';
    }

    public function maxLength(): int
    {

        return 280 - 24;
    }

    public function send(string $text, string $url): void
    {
        Http::post(self::ENDPOINT, [
            'Authorization: ' . $this->oauthHeader('POST', self::ENDPOINT),
        ], ['text' => $text . "\n" . $url]);
    }

    private function oauthHeader(string $method, string $url): string
    {

        $params = [
            'oauth_consumer_key' => $this->cfg['api_key'],
            'oauth_nonce' => bin2hex(random_bytes(16)),
            'oauth_signature_method' => 'HMAC-SHA1',
            'oauth_timestamp' => (string)time(),
            'oauth_token' => $this->cfg['access_token'],
            'oauth_version' => '1.0',
        ];
        ksort($params);
        $pairs = [];
        foreach ($params as $k => $v) {
            $pairs[] = rawurlencode($k) . '=' . rawurlencode($v);
        }
        $base = strtoupper($method) . '&' . rawurlencode($url) . '&' . rawurlencode(implode('&', $pairs));
        $key = rawurlencode($this->cfg['api_secret']) . '&' . rawurlencode($this->cfg['access_secret']);
        $params['oauth_signature'] = base64_encode(hash_hmac('sha1', $base, $key, true));

        $header = [];
        foreach ($params as $k => $v) {
            $header[] = rawurlencode($k) . '="' . rawurlencode($v) . '"';
        }
        return 'OAuth ' . implode(', ', $header);
    }
}
