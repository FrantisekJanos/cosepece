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
            'pripravovane.php' => 'upcoming',
            'data.php' => 'api-docs',
            'hledat.php' => 'search',
            'zakon.php' => 'laws',
            'index.php' => 'home',
            'zmeny.php' => 'changes',
            'api.php' => 'public-api',
            'revize.php' => 'revisions',
            'tisky.php' => 'bills',
            'tisk.php' => 'bill',
            'upozorneni.php' => 'alerts',
            'metodika.php' => 'methodology',
            'rss.php' => 'rss',
            'og.php' => 'og',
        };
        $parameters = $request->query();
        if ($name === 'laws' && isset($parameters['z'])) {
            $dto = \App\Http\DTO\LawRequestDTO::fromRequest($request);
            if ($dto->law !== '') {
                [$number, $year] = explode('/', $dto->law);
                $name = 'law';
                unset($parameters['z']);
                $parameters += compact('number', 'year');
            }
        }
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
