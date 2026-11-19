<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Services\FeedService;
use App\Http\DTO\FeedRequestDTO;
use Illuminate\Http\Response;

final class FeedController
{
    public function index(FeedRequestDTO $dto, FeedService $feed): Response
    {
        return response()->view('rss', ['alerts' => $feed->alerts($dto->includeAll)], 200, [
            'Content-Type' => 'application/rss+xml; charset=utf-8',
        ]);
    }
}
