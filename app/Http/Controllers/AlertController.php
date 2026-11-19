<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Services\Pages\AlertListingService;
use App\Http\DTO\AlertListRequestDTO;
use Illuminate\Http\Response;

final class AlertController
{
    public function index(AlertListRequestDTO $dto, AlertListingService $listing): Response
    {
        return response()->view('upozorneni', $listing->data(
            $dto->page,
            $dto->includeAll,
        ));
    }
}
