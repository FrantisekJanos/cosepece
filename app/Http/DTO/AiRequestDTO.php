<?php

declare(strict_types=1);

namespace App\Http\DTO;

use Spatie\LaravelData\Attributes\Validation\ArrayType;
use Spatie\LaravelData\Attributes\Validation\ExcludeUnless;
use Spatie\LaravelData\Attributes\Validation\In;
use Spatie\LaravelData\Attributes\Validation\IntegerType;
use Spatie\LaravelData\Attributes\MapInputName;
use Spatie\LaravelData\Attributes\Validation\Max;
use Spatie\LaravelData\Attributes\Validation\Min;
use Spatie\LaravelData\Attributes\Validation\Nullable;
use Spatie\LaravelData\Attributes\Validation\Regex;
use Spatie\LaravelData\Attributes\Validation\Required;
use Spatie\LaravelData\Attributes\Validation\RequiredIf;
use Spatie\LaravelData\Attributes\Validation\Sometimes;
use Spatie\LaravelData\Attributes\Validation\StringType;

use Illuminate\Contracts\Validation\Validator;
use Illuminate\Http\Exceptions\HttpResponseException;
use Illuminate\Http\Request;

final class AiRequestDTO extends RequestDTO
{
    public function __construct(
        #[Required, In('queue', 'result', 'done')]
        public readonly string $action,
        #[ExcludeUnless('action', 'queue'), Sometimes, IntegerType]
        public readonly int $limit,
        #[MapInputName('ct'), ExcludeUnless('action', 'queue'), Sometimes, IntegerType, Min(1)]
        public readonly ?int $billNumber,
        #[ExcludeUnless('action', 'queue'), Sometimes, In([0, 1])]
        public readonly bool $verify,
        #[ExcludeUnless('action', 'result'), RequiredIf('action', 'result'), StringType, In('tisk', 'pn')]
        public readonly string $kind,
        #[ExcludeUnless('action', 'result'), RequiredIf('action', 'result'), IntegerType, Min(1)]
        public readonly int $id,
        #[ExcludeUnless('action', 'result'), Sometimes, ArrayType]
        public readonly ?array $result,
        #[ExcludeUnless('action', 'result'), Sometimes, Nullable, StringType, Max(60), Regex('~^[a-z0-9.\-]+$~')]
        public readonly ?string $model,
        #[ExcludeUnless('action', 'result'), Sometimes, Nullable, StringType]
        public readonly ?string $error,
    ) {
    }

    protected static function validationData(Request $request): array
    {
        $action = $request->query('action');
        $data = $request->isMethod('GET') ? $request->query() : $request->json()->all();

        return array_merge($data, ['action' => $action]);
    }

    protected static function fromValidated(array $data): static
    {
        return new static(
            $data['action'], max(1, min(50, (int)($data['limit'] ?? 10))),
            isset($data['ct']) ? (int)$data['ct'] : null, (bool)($data['verify'] ?? false),
            $data['kind'] ?? '', (int)($data['id'] ?? 0), $data['result'] ?? null,
            $data['model'] ?? null, $data['error'] ?? null,
        );
    }

    protected static function failedValidation(Validator $validator, array $input): never
    {
        $message = $input['action'] === 'queue' ? 'Neplatné parametry fronty' : 'Neplatný výsledek rozboru';
        throw new HttpResponseException(response()->json(['error' => $message], 400));
    }
}
