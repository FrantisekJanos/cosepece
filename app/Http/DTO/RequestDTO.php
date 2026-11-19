<?php

declare(strict_types=1);

namespace App\Http\DTO;

use Illuminate\Contracts\Validation\Validator as ValidatorContract;
use Illuminate\Http\Exceptions\HttpResponseException;
use Illuminate\Http\Request;
use Illuminate\Validation\ValidationException;
use Spatie\LaravelData\Data;

abstract class RequestDTO extends Data
{
    final public static function fromRequest(Request $request): static
    {
        $input = static::validationData($request);
        try {
            $validated = static::validate($input);
        } catch (ValidationException $exception) {
            static::failedValidation($exception->validator, $input);
        }

        return static::fromValidated($validated);
    }

    abstract protected static function fromValidated(array $data): static;

    protected static function validationData(Request $request): array
    {
        return $request->query();
    }

    protected static function failedValidation(ValidatorContract $validator, array $input): never
    {
        throw new HttpResponseException(response()->json([
            'message' => 'Neplatné parametry požadavku.',
            'errors' => $validator->errors()->toArray(),
        ], 422));
    }
}
