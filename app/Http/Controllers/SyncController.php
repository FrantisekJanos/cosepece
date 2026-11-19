<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Http\DTO\SyncRequestDTO;
use Hlidac\Http;
use Hlidac\Sync;
use Illuminate\Http\JsonResponse;

final class SyncController
{
    public function run(SyncRequestDTO $dto): JsonResponse
    {
        ignore_user_abort(true);
        set_time_limit(600);
        ini_set('memory_limit', '512M');
        try {
            return response()->json(Sync::run());
        } catch (\Throwable $ex) {
            report($ex);
            return response()->json(['error' => Http::redact($ex->getMessage())], 500);
        }
    }
}
