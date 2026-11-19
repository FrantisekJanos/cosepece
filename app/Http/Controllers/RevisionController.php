<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Http\DTO\RevisionRequestDTO;
use App\Services\Pages\RevisionService;
use Illuminate\Http\Response;

final class RevisionController
{
    public function show(RevisionRequestDTO $dto, RevisionService $revisions): Response
    {
        $data = $revisions->data($dto->kind, $dto->documentId, $dto->period, $dto->olderVersion, $dto->newerVersion);
        return $data === null
            ? response()->view('revision-missing', ['title' => 'Historie nenalezena', 'active' => 'tisky'], 404)
            : response()->view('revize', $data);
    }
}
