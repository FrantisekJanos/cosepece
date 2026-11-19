<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Http\DTO\PublicApiRequestDTO;
use App\Services\PublicApiService;
use Illuminate\Http\JsonResponse;

final class PublicApiController
{
    public function index(PublicApiRequestDTO $dto, PublicApiService $api): JsonResponse
    {
        [$status, $body] = $api->handle($dto);
        return response()->json($body, $status, [
            'Access-Control-Allow-Origin' => '*', 'Cache-Control' => 'public, max-age=300',
            'X-Content-Type-Options' => 'nosniff',
        ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
    }
}
