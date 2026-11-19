<?php

declare(strict_types=1);

namespace App\Http\DTO;

use Spatie\LaravelData\Attributes\Validation\In;
use Spatie\LaravelData\Attributes\Validation\IntegerType;
use Spatie\LaravelData\Attributes\MapInputName;
use Spatie\LaravelData\Attributes\Validation\Min;
use Spatie\LaravelData\Attributes\Validation\Regex;
use Spatie\LaravelData\Attributes\Validation\Sometimes;
use Spatie\LaravelData\Attributes\Validation\StringType;

final class OgImageRequestDTO extends RequestDTO
{
    public function __construct(
        #[MapInputName('ct'), Sometimes, IntegerType, Min(1)]
        public readonly int $billNumber,
        #[MapInputName('p'), Sometimes, StringType, In('prehled', 'tisky', 'upozorneni', 'metodika', 'zmeny', 'pripravovane', 'data', 'zakony', 'mcp')]
        public readonly string $page,
        #[MapInputName('v'), Sometimes, StringType, Regex('/^[a-f0-9]{16}$/')]
        public readonly ?string $version,
    ) {
    }

    protected static function fromValidated(array $data): static
    {
        return new static((int)($data['ct'] ?? 0), $data['p'] ?? 'prehled', $data['v'] ?? null);
    }
}
