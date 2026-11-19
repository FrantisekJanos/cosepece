<?php

declare(strict_types=1);

namespace App\Http\DTO;

use Illuminate\Http\Request;
use Spatie\LaravelData\Attributes\MapInputName;
use Spatie\LaravelData\Attributes\Validation\{Required, IntegerType, Min, Nullable, Sometimes};

final class DocumentRequestDTO extends RequestDTO
{
    public function __construct(
        #[MapInputName('ct'), Required, IntegerType, Min(1)]
        public readonly int $billNumber,
        #[MapInputName('pn'), Sometimes, Nullable, IntegerType, Min(1)]
        public readonly ?int $amendmentId = null,
    ) {}

    protected static function validationData(Request $request): array
    {
        return ['ct' => $request->route('ct'), 'pn' => $request->query('pn')];
    }

    protected static function fromValidated(array $data): static
    {
        return new static((int)$data['ct'], isset($data['pn']) ? (int)$data['pn'] : null);
    }
}
