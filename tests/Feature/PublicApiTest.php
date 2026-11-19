<?php

declare(strict_types=1);

namespace Tests\Feature;

use Tests\TestCase;

final class PublicApiTest extends TestCase
{
    public function test_public_json_detail_and_filters(): void
    {
        $this->importFixtures();
        $this->getJson('/api?tisk=78')->assertOk()->assertHeader('Access-Control-Allow-Origin', '*')
            ->assertJsonPath('verze', 1)->assertJsonPath('tisk.ct', 78)
            ->assertJsonPath('tisk.url', 'https://hlidac.test/tisky/78')->assertJsonCount(4, 'tisk.hlasovani');
        $this->getJson('/api?law=586%2F1992&limit=1')->assertOk()->assertJsonCount(1, 'tisky')->assertJsonPath('tisky.0.ct', 311);
        $this->getJson('/api?since=2999-01-01')->assertOk()->assertJsonCount(0, 'tisky');
        $this->getJson('/api?upozorneni=1&ct=78&min_score=0&limit=2')->assertOk()
            ->assertJsonCount(2, 'upozorneni')->assertJsonPath('upozorneni.0.ct', 78);
        $this->getJson('/api?tisk=999999')->assertNotFound();
        $this->get('/api.php?tisk=78')->assertStatus(301)->assertRedirect('/api?tisk=78');
    }

    public function test_invalid_api_and_rss_filters_return_400(): void
    {
        foreach (['since=2026-02-30', 'since[]=2026-01-01', 'zakon=invalid', 'min_score=101', 'ct[]=78', 'limit=0'] as $query) {
            $this->getJson('/api?' . $query)->assertStatus(400)->assertHeader('Access-Control-Allow-Origin', '*');
            $this->getJson('/rss?' . $query)->assertStatus(400);
        }
        $this->get('/rss?vse=unknown')->assertStatus(400);
    }

    public function test_rss_uses_shared_filters_and_excludes_old_period_when_filtering_by_law(): void
    {
        $this->importFixtures();
        db()->run("INSERT INTO alerts (alert_key, kind, ct, event_at, created_at, title, body, score, obdobi) VALUES ('old311', 'pn', 311, '2026-11-19', '2026-11-19', 'Starý nález', 'Minulé období', 90, 9)");
        $response = $this->get('/rss?zakon=586%2F1992&vse=1&limit=2')->assertOk()->assertDontSee('Starý nález');
        $xml = simplexml_load_string($response->getContent());
        $this->assertGreaterThan(0, count($xml->channel->item));
        $this->assertLessThanOrEqual(2, count($xml->channel->item));
        foreach ($xml->channel->item as $item) {
            $this->assertSame('https://hlidac.test/tisky/311', (string)$item->link);
        }
    }
}
