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
        $expected = (string)($service === 'ai'
            ? hlidacConfig('ai.remote_token') ?: hlidacConfig('cron_token', '')
            : hlidacConfig('cron_token', ''));
        $token = $request->header('X-Hlidac-Token') ?? $request->query('token', '');
        if (!is_string($token) || $expected === '' || str_starts_with($expected, 'ZMENTE') || !hash_equals($expected, $token)) {
            return response()->json(['error' => 'Neplatný token'], 403);
        }
        return $next($request);
    }
}
