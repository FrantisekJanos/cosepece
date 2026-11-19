<?php

declare(strict_types=1);

namespace App\Http\DTO;

use Illuminate\Http\Request;
use Spatie\LaravelData\Attributes\MapInputName;
use Spatie\LaravelData\Attributes\Validation\IP;
use Spatie\LaravelData\Attributes\Validation\Max;
use Spatie\LaravelData\Attributes\Validation\Regex;
use Spatie\LaravelData\Attributes\Validation\Required;
use Spatie\LaravelData\Attributes\Validation\Sometimes;
use Spatie\LaravelData\Attributes\Validation\StringType;

final class McpIssueRequestDTO extends RequestDTO
{
    public function __construct(
        #[MapInputName('ucel'), Sometimes, StringType, Max(80)]
        public readonly string $purpose,
        #[MapInputName('formular'), Required, StringType, Regex('~^\d{9,11}\.[0-9a-f]{32}$~')]
        public readonly string $formToken,
        #[Sometimes, StringType, Max(2048)]
        public readonly string $web,
        #[MapInputName('ip'), Required, StringType, IP]
        public readonly string $clientIp,
    ) {
    }

    protected static function validationData(Request $request): array
    {
        return array_merge($request->request->all(), ['ip' => $request->ip()]);
    }

    protected static function fromValidated(array $data): static
    {
        return new static($data['ucel'] ?? '', $data['formular'], $data['web'] ?? '', $data['ip']);
    }
}
