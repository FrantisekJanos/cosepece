<?php

declare(strict_types=1);

namespace App\Services;

use Hlidac\AiReviewer;
use Hlidac\Scorer;

final class AiReviewService
{
    public function __construct(private AiReviewer $reviewer, private Scorer $scorer)
    {
    }

    public function queue(int $limit, ?int $billNumber, bool $verify): array
    {
        return $this->reviewer->queue(max(1, min(50, $limit)), $billNumber, $verify);
    }

    public function storeResult(string $kind, int $id, ?array $result, ?string $model, ?string $error): void
    {
        if ($result !== null) {
            $this->reviewer->saveResult($kind, $id, $result, $model);
            return;
        }

        $this->reviewer->saveError($kind, $id, $error ?? 'Neznámá chyba');
    }

    public function recalculateScores(): void
    {
        $this->scorer->scoreBills();
        $this->scorer->scoreAlerts();
    }
}
