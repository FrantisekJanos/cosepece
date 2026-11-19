<?php


declare(strict_types=1);

namespace Hlidac\Notifier;

use Hlidac\Http;

final class Telegram implements Notifier
{
    public function __construct(private array $cfg)
    {
    }

    public function name(): string
    {
        return 'telegram';
    }

    public function maxLength(): int
    {
        return 4000;
    }

    public function send(string $text, string $url): void
    {
        Http::post('https://api.telegram.org/bot' . $this->cfg['bot_token'] . '/sendMessage', [], [
            'chat_id' => $this->cfg['chat_id'],
            'text' => $text . "\n" . $url,
            'disable_web_page_preview' => false,
        ]);
    }
}
