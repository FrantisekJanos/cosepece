<?php

declare(strict_types=1);

namespace Tests\Feature;

use Tests\TestCase;

final class McpPageTest extends TestCase
{
    public function test_mcp_page_uses_configured_endpoint_or_domain_default_and_clean_links(): void
    {
        config(['hlidac.mcp_url' => '']);
        $this->get('/mcp')->assertOk()->assertSee('https://mcp.hlidac.test/mcp')
            ->assertSee('over_citaci')->assertSee('hlasovani_k_tisku')->assertSee('rozprava');
        config(['hlidac.mcp_url' => 'https://tools.example.test/mcp']);
        $this->get('/mcp')->assertOk()->assertSee('https://tools.example.test/mcp');
        $this->get('/mcp.php')->assertStatus(301)->assertRedirect('/mcp');
        $this->get('/data')->assertOk()->assertSee('href="/mcp"', false);
    }
}
