<?php

declare(strict_types=1);

namespace App\Http\Middleware;

use Closure;
use Hlidac\RateLimit;
use Illuminate\Http\Request;
use Symfony\Component\HttpFoundation\Response;

final class LimitPublicRequests
{
    public function handle(Request $request, Closure $next): Response
    {
        if ($request->is('cron.php', 'ai_api.php', 'up')) {
            return $next($request);
        }
        $bucket = match ($request->path()) {
            'api', 'api.php' => 'api', 'rss', 'rss.php' => 'rss', 'og', 'og.php' => 'og', default => null,
        };
        $dir = hlidacDataPath('cache/ratelimit');
        $retry = RateLimit::hit('web', RateLimit::limit('web', 240), 60, $request->ip(), $dir);
        if ($retry === null && $bucket !== null) {
            $retry = RateLimit::hit($bucket, RateLimit::limit($bucket, match ($bucket) {
                'api' => 60, 'rss' => 30, 'og' => 120,
            }), 60, $request->ip(), $dir);
            if ($retry !== null && $bucket === 'og') {
                return response()->file(public_path('assets/og-default.png'), [
                    'Content-Type' => 'image/png', 'Cache-Control' => 'no-store',
                ]);
            }
        }
        if ($retry !== null) {
            $message = 'Příliš mnoho dotazů z vaší adresy. Zkuste to znovu za ' . $retry . ' s. Limity: ' . siteUrl('/data#limity');
            $headers = ['Retry-After' => (string)$retry, 'Cache-Control' => 'no-store'];
            return $bucket === 'api'
                ? response()->json(['error' => $message], 429, $headers + ['Access-Control-Allow-Origin' => '*'])
                : response($message, 429, $headers + ['Content-Type' => 'text/plain; charset=utf-8']);
        }
        return $next($request);
    }
}
