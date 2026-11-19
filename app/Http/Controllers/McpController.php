<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Http\DTO\McpRequestDTO;
use App\Services\Pages\McpService;
use Illuminate\Http\Response;

final class McpController
{
    public function index(McpRequestDTO $dto, McpService $mcp): Response
    {
        return response()->view('mcp', $mcp->data(), 200, ['Cache-Control' => 'no-store']);
    }
    public function issue(\App\Http\DTO\McpIssueRequestDTO $dto, McpService $mcp): Response
    {
        return response()->view('mcp', $mcp->data($mcp->issue($dto)), 200, ['Cache-Control' => 'no-store']);
    }
}
