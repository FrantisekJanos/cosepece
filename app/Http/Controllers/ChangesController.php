<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Http\DTO\ChangesRequestDTO;
use App\Services\Pages\ChangesService;
use Illuminate\Http\Response;

final class ChangesController
{
    public function index(ChangesRequestDTO $dto, ChangesService $changes): Response
    {
        return response()->view('zmeny', $changes->data());
    }
}
