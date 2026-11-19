<?php

declare(strict_types=1);

namespace App\Http\Middleware;

use Closure;
use Illuminate\Http\Request;
use Symfony\Component\HttpFoundation\Response;

final class VerifyServiceToken
{
    public function handle(Request $request, Closure $next, string $service): Response
    {
        $dir = hlidacDataPath('cache/ratelimit');
        $retry = \Hlidac\RateLimit::hit('auth', \Hlidac\RateLimit::limit('auth', 10), 900, $request->ip(), $dir, false);
        if ($retry !== null) {
            return response()->json(['error' => 'Příliš mnoho chybných pokusů, zkuste to za ' . $retry . ' s'], 429, [
                'Retry-After' => (string)$retry, 'Cache-Control' => 'no-store',
            ]);
        }
        $expected = (string)($service === 'ai'
            ? hlidacConfig('ai.remote_token') ?: hlidacConfig('cron_token', '')
            : hlidacConfig('cron_token', ''));
        $token = $request->header('X-Hlidac-Token') ?? $request->query('token', '');
        if (!is_string($token) || $expected === '' || str_starts_with($expected, 'ZMENTE') || !hash_equals($expected, $token)) {
            \Hlidac\RateLimit::hit('auth', \Hlidac\RateLimit::limit('auth', 10), 900, $request->ip(), $dir);
            return response()->json(['error' => 'Neplatný token'], 403);
        }
        return $next($request);
    }
}
