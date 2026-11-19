<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Http\DTO\ApiDocumentationRequestDTO;
use App\Services\Pages\ApiDocumentationService;
use Illuminate\Http\Response;

final class ApiDocumentationController
{
    public function index(ApiDocumentationRequestDTO $dto, ApiDocumentationService $documentation): Response
    {
        return response()->view('data', $documentation->data());
    }
}
