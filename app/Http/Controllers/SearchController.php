<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Http\DTO\SearchRequestDTO;
use App\Services\Pages\SearchService;
use Symfony\Component\HttpFoundation\Response;

final class SearchController
{
    public function index(SearchRequestDTO $dto, SearchService $search): Response
    {
        return $search->response($dto);
    }
}
