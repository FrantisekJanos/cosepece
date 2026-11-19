<?php

declare(strict_types=1);

namespace App\Http\DTO;

use Spatie\LaravelData\Attributes\Validation\BooleanType;
use Spatie\LaravelData\Attributes\Validation\IntegerType;
use Spatie\LaravelData\Attributes\MapInputName;
use Spatie\LaravelData\Attributes\Validation\Min;
use Spatie\LaravelData\Attributes\Validation\Sometimes;

final class AlertListRequestDTO extends RequestDTO
{
    public function __construct(
        #[MapInputName('p'), Sometimes, IntegerType, Min(1)]
        public readonly int $page,
        #[MapInputName('vse'), Sometimes, BooleanType]
        public readonly bool $includeAll,
    ) {
    }

    protected static function fromValidated(array $data): static
    {
        return new static((int)($data['p'] ?? 1), (bool)($data['vse'] ?? false));
    }
}
