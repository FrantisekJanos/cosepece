<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Http\DTO\AiRequestDTO;
use App\Services\AiReviewService;
use Hlidac\Http;
use Illuminate\Http\JsonResponse;

final class AiController
{
    public function handle(AiRequestDTO $dto, AiReviewService $reviews): JsonResponse
    {
        try {
            switch ($dto->action) {
                case 'queue':
                    return response()->json(['items' => $reviews->queue($dto->limit, $dto->billNumber, $dto->verify)]);
                case 'result':
                    $reviews->storeResult($dto->kind, $dto->id, $dto->result, $dto->model, $dto->error);
                    break;
                case 'done':
                    $reviews->recalculateScores();
                    break;
            }
            return response()->json(['ok' => true]);
        } catch (\Throwable $ex) {
            report($ex);
            return response()->json(['error' => Http::redact($ex->getMessage())], 500);
        }
    }
}
