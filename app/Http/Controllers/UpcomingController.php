<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Http\DTO\UpcomingRequestDTO;
use App\Services\Pages\UpcomingService;
use Illuminate\Http\Response;

final class UpcomingController
{
    public function index(UpcomingRequestDTO $dto, UpcomingService $upcoming): Response
    {
        return response()->view('pripravovane', $upcoming->data());
    }
}
