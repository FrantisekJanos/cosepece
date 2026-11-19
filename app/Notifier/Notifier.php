<?php


declare(strict_types=1);

namespace Hlidac\Notifier;

interface Notifier
{

    public function name(): string;

    public function maxLength(): int;

    public function send(string $text, string $url): void;
}
