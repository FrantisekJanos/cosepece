<?php

declare(strict_types=1);

namespace App\Http\DTO;

use Illuminate\Http\Request;
use Illuminate\Contracts\Validation\Validator as ValidatorContract;
use Illuminate\Http\Exceptions\HttpResponseException;
use Spatie\LaravelData\Attributes\MapInputName;
use Spatie\LaravelData\Attributes\Validation\Between;
use Spatie\LaravelData\Attributes\Validation\BooleanType;
use Spatie\LaravelData\Attributes\Validation\DateFormat;
use Spatie\LaravelData\Attributes\Validation\IntegerType;
use Spatie\LaravelData\Attributes\Validation\Min;
use Spatie\LaravelData\Attributes\Validation\Regex;
use Spatie\LaravelData\Attributes\Validation\Sometimes;
use Spatie\LaravelData\Attributes\Validation\StringType;

final class PublicApiRequestDTO extends RequestDTO
{
    public function __construct(
        #[MapInputName('since'), Sometimes, StringType, DateFormat('Y-m-d')]
        public readonly ?string $since = null,
        #[MapInputName('min_score'), Sometimes, IntegerType, Between(0, 100)]
        public readonly ?int $minimumScore = null,
        #[MapInputName('zakon'), Sometimes, StringType, Regex('~^\\d+/\\d{4}$~')]
        public readonly ?string $law = null,
        #[MapInputName('ct'), Sometimes, IntegerType, Min(1)]
        public readonly ?int $billNumber = null,
        #[MapInputName('limit'), Sometimes, IntegerType, Between(1, 500)]
        public readonly int $limit = 100,
        #[MapInputName('tisk'), Sometimes, IntegerType, Min(1)]
        public readonly ?int $detailBill = null,
        #[MapInputName('upozorneni'), Sometimes, BooleanType]
        public readonly bool $includeAlerts = false,
        #[MapInputName('pripravovane'), Sometimes, BooleanType]
        public readonly bool $includeUpcoming = false,
    ) {
    }

    protected static function validationData(Request $request): array
    {
        $input = $request->query();
        if (!isset($input['zakon']) && isset($input['law'])) {
            $input['zakon'] = $input['law'];
        }
        if (!isset($input['min_score']) && isset($input['min'])) {
            $input['min_score'] = $input['min'];
        }
        return $input;
    }

    protected static function fromValidated(array $data): static
    {
        return new static($data['since'] ?? null,
            isset($data['min_score']) ? (int)$data['min_score'] : null,
            $data['zakon'] ?? null,
            isset($data['ct']) ? (int)$data['ct'] : null,
            (int)($data['limit'] ?? 100), isset($data['tisk']) ? (int)$data['tisk'] : null, (bool)($data['upozorneni'] ?? false), (bool)($data['pripravovane'] ?? false));
    }

    public function filters(): array
    {
        return ['since' => $this->since ?? '', 'min_score' => $this->minimumScore,
            'zakon' => $this->law ?? '', 'tisk' => $this->billNumber, 'limit' => $this->limit];
    }

    protected static function failedValidation(ValidatorContract $validator, array $input): never
    {
        throw new HttpResponseException(response()->json([
            'error' => 'Neplatné filtry.', 'errors' => $validator->errors()->toArray(),
        ], 400, ['Access-Control-Allow-Origin' => '*', 'X-Content-Type-Options' => 'nosniff']));
    }
}
