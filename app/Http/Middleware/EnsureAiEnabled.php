<?php

declare(strict_types=1);

namespace App\Http\Middleware;

use Closure;
use Illuminate\Http\Request;
use Symfony\Component\HttpFoundation\Response;

final class EnsureAiEnabled
{
    public function handle(Request $request, Closure $next): Response
    {
        if (!hlidacConfig('ai.enabled')) {
            return response()->json(['error' => 'AI rozbor je vypnutý (ai.enabled)'], 409);
        }

        return $next($request);
    }
}
