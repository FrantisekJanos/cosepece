<?php

declare(strict_types=1);

namespace App\Http\DTO;

use Illuminate\Http\Request;
use Spatie\LaravelData\Attributes\Validation\Max;
use Spatie\LaravelData\Attributes\Validation\Sometimes;
use Spatie\LaravelData\Attributes\Validation\StringType;
use Spatie\LaravelData\Attributes\MapInputName;

final class SearchRequestDTO extends RequestDTO
{
    public function __construct(
        #[MapInputName('q'), Sometimes, StringType, Max(100)]
        public readonly string $query = '',
        #[Sometimes, StringType, Max(64)]
        public readonly string $index = '',
        #[Sometimes, StringType]
        public readonly string $etag = '',
        #[Sometimes, StringType]
        public readonly string $encoding = '',
    ) {
    }

    protected static function validationData(Request $request): array
    {
        return array_merge($request->query(), [
            'etag' => $request->header('If-None-Match', ''),
            'encoding' => $request->header('Accept-Encoding', ''),
        ]);
    }

    protected static function fromValidated(array $data): static
    {
        return new static(trim($data['q'] ?? ''), $data['index'] ?? '', $data['etag'], $data['encoding']);
    }
}
