<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Http\DTO\DocumentRequestDTO;
use App\Services\Pages\DocumentReaderService;
use Illuminate\Http\Response;

final class DocumentController
{
    public function show(DocumentRequestDTO $dto, DocumentReaderService $reader): Response
    {
        $data = $reader->data($dto->billNumber, $dto->amendmentId);
        return $data === null
            ? response()->view('missing', ['title' => 'Dokument nenalezen', 'active' => 'tisky'], 404)
            : response()->view('dokumenty', $data);
    }
    public function word(DocumentRequestDTO $dto, DocumentReaderService $reader): Response
    {
        $data = $reader->data($dto->billNumber, $dto->amendmentId);
        $url = $data['document']?->docx_url ?? null;
        if (!is_string($url) || !preg_match('~^https://www\.psp\.cz/(?:sqw|eknih)/~', $url)) {
            abort(404);
        }
        try {
            $binary = \Illuminate\Support\Facades\Cache::remember('word-document:' . hash('sha256', $url), 3600,
                fn () => \Hlidac\Http::get($url));
            if (!str_starts_with($binary, "PK")) {
                abort(502, 'Dokument není platný Word.');
            }
        } catch (\RuntimeException $exception) {
            report($exception);
            abort(502, 'Word se nepodařilo stáhnout ze Sněmovny.');
        }
        return response($binary, 200, [
            'Content-Type' => 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'Content-Disposition' => 'inline; filename="tisk-' . $dto->billNumber . ($dto->amendmentId !== null ? '-pn-' . $dto->amendmentId : '') . '.docx"',
            'X-Content-Type-Options' => 'nosniff',
        ]);
    }

}
