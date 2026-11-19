<?php

declare(strict_types=1);

namespace App\Http\Middleware;

use App\Http\DTO\BillDetailRequestDTO;
use Closure;
use Illuminate\Http\Request;
use Symfony\Component\HttpFoundation\Response;

final class RedirectLegacyUrl
{
    public function handle(Request $request, Closure $next): Response
    {
        $name = match ($request->path()) {
            'index.php' => 'home',
            'zmeny.php' => 'changes',
            'revize.php' => 'revisions',
            'tisky.php' => 'bills',
            'tisk.php' => 'bill',
            'upozorneni.php' => 'alerts',
            'metodika.php' => 'methodology',
            'rss.php' => 'rss',
            'og.php' => 'og',
        };
        $parameters = $request->query();
        if ($name === 'bill') {
            $parameters['ct'] = BillDetailRequestDTO::fromRequest($request)->billNumber;
        }
        if ($name === 'revisions') {
            $dto = \App\Http\DTO\RevisionRequestDTO::fromRequest($request);
            $parameters['druh'] = $dto->kind;
            $parameters['id'] = $dto->documentId;
        }
        return redirect()->to(route($name, $parameters, false), 301);
    }
}
