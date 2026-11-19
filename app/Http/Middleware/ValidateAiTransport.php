<?php

declare(strict_types=1);

namespace App\Http\Middleware;

use Closure;
use Illuminate\Http\Request;
use Symfony\Component\HttpFoundation\Response;

final class ValidateAiTransport
{
    public function handle(Request $request, Closure $next): Response
    {
        $action = $request->query('action');
        $allowed = ($request->isMethod('GET') && $action === 'queue')
            || ($request->isMethod('POST') && in_array($action, ['result', 'done'], true));
        if (!$allowed) {
            return response()->json(['error' => 'Neznámá akce'], 400);
        }
        if ($action === 'result' && strlen($request->getContent()) > 1_000_000) {
            return response()->json(['error' => 'Příliš velký výsledek'], 413);
        }
        return $next($request);
    }
}
