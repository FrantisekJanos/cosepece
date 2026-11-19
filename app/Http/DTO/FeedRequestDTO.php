<?php

declare(strict_types=1);

namespace App\Http\DTO;

use Spatie\LaravelData\Attributes\Validation\BooleanType;
use Spatie\LaravelData\Attributes\MapInputName;
use Spatie\LaravelData\Attributes\Validation\Sometimes;

final class FeedRequestDTO extends RequestDTO
{
    public function __construct(
        #[MapInputName('vse'), Sometimes, BooleanType]
        public readonly bool $includeAll,
    ) {
    }

    protected static function fromValidated(array $data): static
    {
        return new static((bool)($data['vse'] ?? false));
    }
}
