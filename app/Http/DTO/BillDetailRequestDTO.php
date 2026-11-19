<?php

declare(strict_types=1);

namespace App\Http\DTO;

use Spatie\LaravelData\Attributes\Validation\IntegerType;
use Spatie\LaravelData\Attributes\MapInputName;
use Spatie\LaravelData\Attributes\Validation\Min;
use Spatie\LaravelData\Attributes\Validation\Required;

final class BillDetailRequestDTO extends RequestDTO
{
    public function __construct(
        #[MapInputName('ct'), Required, IntegerType, Min(1)]
        public readonly int $billNumber,
    ) {
    }

    protected static function validationData(\Illuminate\Http\Request $request): array
    {
        return ['ct' => $request->route('ct') ?? $request->query('ct')];
    }

    protected static function fromValidated(array $data): static
    {
        return new static((int)$data['ct']);
    }
}
