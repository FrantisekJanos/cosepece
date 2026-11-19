<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Services\OgImageService;
use App\Http\DTO\OgImageRequestDTO;
use Symfony\Component\HttpFoundation\BinaryFileResponse;

final class OgController
{
    public function show(OgImageRequestDTO $dto, OgImageService $images): BinaryFileResponse
    {
        return response()->file($images->path($dto->billNumber, $dto->page), [
            'Content-Type' => 'image/png', 'Cache-Control' => 'public, max-age=86400',
            'X-Content-Type-Options' => 'nosniff',
        ]);
    }
}
