<?php

declare(strict_types=1);

namespace App\Http\DTO;

use Spatie\LaravelData\Attributes\Validation\Required;
use Spatie\LaravelData\Attributes\Validation\StringType;

final class SyncRequestDTO extends RequestDTO
{
    public function __construct(
        #[Required, StringType]
        public readonly string $token,
    ) {
    }

    protected static function fromValidated(array $data): static
    {
        return new static($data['token']);
    }

    protected static function validationData(\Illuminate\Http\Request $request): array
    {
        return ['token' => $request->header('X-Hlidac-Token') ?? $request->query('token')];
    }
}
