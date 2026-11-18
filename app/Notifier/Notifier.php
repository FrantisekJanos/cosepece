<?php
// Společné rozhraní pro odesílání příspěvků na sociální sítě.

declare(strict_types=1);

namespace Hlidac\Notifier;

interface Notifier
{
    // název sítě pro logování
    public function name(): string;

    // maximální délka příspěvku ve znacích
    public function maxLength(): int;

    // odešle text s odkazem; vyhodí výjimku při chybě
    public function send(string $text, string $url): void;
}
