<?php

declare(strict_types=1);

namespace App\Services\Pages;

use App\Http\DTO\SearchRequestDTO;
use App\Models\Bill;
use Hlidac\SearchIndex;
use Symfony\Component\HttpFoundation\Response;

final class SearchService
{
    public function __construct(private SearchIndex $search)
    {
    }

    public function response(SearchRequestDTO $dto): Response
    {
        if ($dto->index !== '' && $dto->index !== '0') {
            $index = $this->search->load();
            $etag = '"si' . SearchIndex::VERSION . '-' . md5((string)$index['built'] . '|' . ($index['sync'] ?? '')) . '"';
            $headers = ['Content-Type' => 'application/json; charset=utf-8', 'ETag' => $etag,
                'Cache-Control' => 'public, max-age=' . (strlen($dto->index) > 1 ? 86400 : 300),
                'Vary' => 'Accept-Encoding', 'X-Content-Type-Options' => 'nosniff'];
            if (trim($dto->etag) === $etag) {
                return response('', 304, $headers);
            }
            $body = json_encode($index, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
            if (preg_match('~(?:^|,)\s*gzip\s*(?:,|$)~i', $dto->encoding) && function_exists('gzencode')) {
                $body = gzencode($body, 6);
                $headers['Content-Encoding'] = 'gzip';
            }
            return response($body, 200, $headers);
        }
        $q = $dto->query;
        if (preg_match('~^(?:(?:sněmovní\s+)?tisk\s*)?(\d{1,4})$~ui', $q, $m)
            && Bill::query()->where('ct', (int)$m[1])->exists()) {
            return redirect()->to(route('bill', ['ct' => (int)$m[1]], false));
        }
        $groups = $q !== '' ? SearchIndex::search($this->search->load(), $q, 50) : [];
        $total = array_sum(array_map('count', $groups));
        return response()->view('hledat', compact('q', 'groups', 'total') + [
            'title' => $q !== '' ? 'Hledání: ' . $q : 'Hledání', 'active' => 'hledat',
            'description' => 'Hledání v tiscích, pozměňovacích návrzích, zákonech a připravovaných zákonech.',
        ]);
    }
}
