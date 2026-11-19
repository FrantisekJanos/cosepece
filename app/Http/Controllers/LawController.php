<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Http\DTO\LawRequestDTO;
use App\Services\Pages\LawService;
use Illuminate\Http\Response;

final class LawController
{
    public function index(LawRequestDTO $dto, LawService $laws): Response
    {
        return response()->view('zakony', $laws->overview());
    }

    public function show(LawRequestDTO $dto, LawService $laws): Response
    {
        return response()->view('zakon', $laws->detail($dto->law));
    }
}
