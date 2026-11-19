<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Http\DTO\MethodologyRequestDTO;
use App\Services\Pages\MethodologyService;
use Illuminate\Http\Response;

final class MethodologyController
{
    public function index(MethodologyRequestDTO $dto, MethodologyService $methodology): Response
    {
        return response()->view('metodika', $methodology->data());
    }
}
