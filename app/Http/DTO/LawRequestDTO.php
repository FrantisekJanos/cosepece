<?php

declare(strict_types=1);

namespace App\Http\DTO;

use Illuminate\Http\Request;
use Spatie\LaravelData\Attributes\MapInputName;
use Spatie\LaravelData\Attributes\Validation\Regex;
use Spatie\LaravelData\Attributes\Validation\Sometimes;
use Spatie\LaravelData\Attributes\Validation\StringType;

final class LawRequestDTO extends RequestDTO
{
    public function __construct(
        #[MapInputName('z'), Sometimes, StringType, Regex('~^(?:\d{1,4}/\d{4})?$~')]
        public readonly string $law = '',
    ) {
    }

    protected static function validationData(Request $request): array
    {
        $input = $request->query();
        if ($request->route('year') !== null && $request->route('number') !== null) {
            $input['z'] = $request->route('number') . '/' . $request->route('year');
        }
        return $input;
    }

    protected static function fromValidated(array $data): static
    {
        return new static($data['z'] ?? '');
    }
}
