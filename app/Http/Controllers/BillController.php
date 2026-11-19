<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Services\Pages\BillDetailService;
use App\Services\Pages\BillListingService;
use App\Http\DTO\BillListRequestDTO;
use App\Http\DTO\BillDetailRequestDTO;
use Illuminate\Http\Response;

final class BillController
{
    public function index(BillListRequestDTO $dto, BillListingService $listing): Response
    {
        return response()->view('tisky', $listing->data(
            $dto->filter,
            $dto->search,
            $dto->sort,
            $dto->minimumScore,
        ));
    }

    public function show(BillDetailRequestDTO $dto, BillDetailService $details): Response
    {
        $data = $details->data($dto->billNumber);
        if ($data === null) {
            return response()->view('missing', ['title' => 'Tisk nenalezen', 'active' => 'tisky'], 404);
        }

        return response()->view('tisk', $data);
    }
}
