<?php

declare(strict_types=1);

namespace Tests\Feature;

use Tests\TestCase;

final class PublicProtectionTest extends TestCase
{
    public function test_documentation_has_live_examples_and_clean_links(): void
    {
        $this->importFixtures();
        $this->get('/data')->assertOk()->assertSee('Data a API')->assertSee('/api?tisk=')
            ->assertSee('KORNUP000003')->assertSee('Retry-After');
        $this->get('/data.php')->assertStatus(301)->assertRedirect('/data');
    }

    public function test_api_limits_are_per_ip_return_retry_and_can_be_disabled(): void
    {
        config(['hlidac.rate_limit.api' => 1]);
        $this->get('/api')->assertOk()->assertHeader('X-Frame-Options', 'SAMEORIGIN');
        $this->get('/api')->assertStatus(429)->assertHeader('Retry-After')
            ->assertHeader('Access-Control-Allow-Origin', '*')->assertJsonStructure(['error']);
        $this->withServerVariables(['REMOTE_ADDR' => '203.0.113.44'])->get('/api')->assertOk();
        config(['hlidac.rate_limit.api' => 0]);
        $this->get('/api')->assertOk();
    }

    public function test_rss_web_and_image_limits_have_appropriate_responses(): void
    {
        config(['hlidac.rate_limit.rss' => 1, 'hlidac.rate_limit.og' => 1]);
        $this->get('/rss')->assertOk();
        $this->get('/rss')->assertStatus(429)->assertHeader('Retry-After');
        $this->get('/og?p=prehled')->assertOk();
        $image = $this->get('/og?p=prehled')->assertOk()->assertHeader('Content-Type', 'image/png');
        $this->assertSame(public_path('assets/og-default.png'), $image->baseResponse->getFile()->getPathname());
        config(['hlidac.rate_limit.web' => 1]);
        $this->get('/data')->assertStatus(429)->assertSee('/data#limity');
    }

    public function test_bad_tokens_block_even_valid_tokens_until_the_window_expires(): void
    {
        config(['hlidac.rate_limit.auth' => 2]);
        $this->get('/ai_api.php?action=queue&token=bad')->assertForbidden();
        $this->get('/cron.php?token=bad')->assertForbidden();
        $this->withHeader('X-Hlidac-Token', 'test-ai-token')->get('/ai_api.php?action=queue')
            ->assertStatus(429)->assertHeader('Retry-After');
        foreach (glob($this->dataDirectory . '/cache/ratelimit/auth_*') as $file) {
            file_put_contents($file, json_encode(['start' => time() - 901, 'count' => 2]));
        }
        $this->get('/ai_api.php?action=queue')->assertStatus(409);
    }
}
