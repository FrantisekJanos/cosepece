<?php

declare(strict_types=1);

namespace App\Http\DTO;

use Illuminate\Http\Request;
use Spatie\LaravelData\Attributes\MapInputName;
use Spatie\LaravelData\Attributes\Validation\In;
use Spatie\LaravelData\Attributes\Validation\IntegerType;
use Spatie\LaravelData\Attributes\Validation\Min;
use Spatie\LaravelData\Attributes\Validation\Nullable;
use Spatie\LaravelData\Attributes\Validation\Required;
use Spatie\LaravelData\Attributes\Validation\Sometimes;
use Spatie\LaravelData\Attributes\Validation\StringType;

final class RevisionRequestDTO extends RequestDTO
{
    public function __construct(
        #[MapInputName('druh'), Required, StringType, In('tisk', 'pn')]
        public readonly string $kind,
        #[MapInputName('id'), Required, IntegerType, Min(1)]
        public readonly int $documentId,
        #[MapInputName('o'), Sometimes, Nullable, IntegerType, Min(0)]
        public readonly ?int $period = null,
        #[MapInputName('a'), Sometimes, Nullable, IntegerType, Min(1)]
        public readonly ?int $olderVersion = null,
        #[MapInputName('b'), Sometimes, Nullable, IntegerType, Min(1)]
        public readonly ?int $newerVersion = null,
    ) {
    }

    protected static function validationData(Request $request): array
    {
        return ['druh' => $request->route('druh') ?? $request->query('druh', 'tisk'),
            'id' => $request->route('id') ?? $request->query('id')] + $request->query();
    }

    protected static function fromValidated(array $data): static
    {
        return new static($data['druh'], (int)$data['id'],
            isset($data['o']) ? (int)$data['o'] : null,
            isset($data['a']) ? (int)$data['a'] : null,
            isset($data['b']) ? (int)$data['b'] : null);
    }
}
