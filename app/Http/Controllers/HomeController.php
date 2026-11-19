<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Services\Pages\OverviewService;
use App\Http\DTO\OverviewRequestDTO;
use Illuminate\Http\Response;

final class HomeController
{
    public function index(OverviewRequestDTO $dto, OverviewService $overview): Response
    {
        return response()->view('index', $overview->data($dto->findingsShown));
    }
}
