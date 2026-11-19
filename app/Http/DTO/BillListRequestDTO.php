<?php

declare(strict_types=1);

namespace App\Http\DTO;

use Spatie\LaravelData\Attributes\Validation\Between;
use Spatie\LaravelData\Attributes\Validation\In;
use Spatie\LaravelData\Attributes\Validation\IntegerType;
use Spatie\LaravelData\Attributes\MapInputName;
use Spatie\LaravelData\Attributes\Validation\Max;
use Spatie\LaravelData\Attributes\Validation\Sometimes;
use Spatie\LaravelData\Attributes\Validation\StringType;

final class BillListRequestDTO extends RequestDTO
{
    public function __construct(
        #[MapInputName('f'), Sometimes, StringType, In('', 'flagged', 'mp', 'p90', 'cut', 'surge', 'prilepky', 'zmeny_pn', 'zpetne')]
        public readonly string $filter,
        #[MapInputName('q'), Sometimes, StringType, Max(100)]
        public readonly string $search,
        #[Sometimes, StringType, In('score', 'recent')]
        public readonly string $sort,
        #[MapInputName('min'), Sometimes, IntegerType, Between(0, 100)]
        public readonly int $minimumScore,
    ) {
    }

    protected static function fromValidated(array $data): static
    {
        return new static(
            $data['f'] ?? '', trim($data['q'] ?? ''), $data['sort'] ?? 'score', (int)($data['min'] ?? 0),
        );
    }
}
