<?php

declare(strict_types=1);

namespace Tests\Feature;

use Hlidac\AiReviewer;
use Tests\TestCase;

final class AiApiTest extends TestCase
{
    public function test_authentication_and_disabled_responses_remain_compatible(): void
    {
        $this->get('/ai_api.php?action=queue')->assertForbidden();
        $this->withHeader('X-Hlidac-Token', 'test-cron-token')->get('/ai_api.php?action=queue')->assertForbidden();
        $this->withHeader('X-Hlidac-Token', 'test-ai-token')->get('/ai_api.php?action=queue')->assertStatus(409);
        config(['hlidac.ai.remote_token' => '']);
        $this->withHeader('X-Hlidac-Token', 'test-cron-token')->get('/ai_api.php?action=queue')->assertStatus(409);
        config(['hlidac.cron_token' => '']);
        $this->get('/ai_api.php?action=queue')->assertForbidden();
    }

    public function test_remote_client_can_read_queue_save_result_and_recompute_scores(): void
    {
        $this->importFixtures();
        $text = 'Řízení lze zahájit nejpozději do 4 let od porušení rozpočtové kázně.';
        db()->run('INSERT INTO tisk_text (ct, text, chars, signals) VALUES (221, ?, ?, ?)', [$text, mb_strlen($text), '{}']);
        config(['hlidac.ai.enabled' => true]);
        $this->withHeader('X-Hlidac-Token', 'test-ai-token');
        $this->get('/ai_api.php?action=queue&ct=221&limit=10')->assertOk()
            ->assertJsonPath('items.0.kind', 'tisk')->assertJsonPath('items.0.id', 221)
            ->assertJsonStructure(['items' => [['kind', 'id', 'ct', 'prompt']]]);
        $this->postJson('/ai_api.php?action=result', [
            'kind' => 'tisk', 'id' => 221, 'model' => 'test-model',
            'result' => ['zavaznost' => 8, 'titulek' => 'Zkrácená lhůta', 'citace' => [$text, 'Vymyšlená citace.']],
        ])->assertOk()->assertJson(['ok' => true]);
        $row = db()->one('SELECT * FROM tisk_text WHERE ct = 221');
        $this->assertSame([$text], json_decode($row['ai_json'], true)['citace']);
        $this->assertSame(AiReviewer::PROMPT_VERSION, (int)$row['ai_prompt']);
        $this->assertSame('test-model', $row['ai_model']);
        $this->postJson('/ai_api.php?action=done')->assertOk()->assertJson(['ok' => true]);
        $this->get('/ai_api.php?action=queue&ct=221')->assertOk()->assertExactJson(['items' => []]);
        $this->postJson('/ai_api.php?action=result', ['kind' => 'tisk', 'id' => 221, 'error' => 'https://web.test/?token=tajne'])->assertOk();
        $this->assertStringNotContainsString('tajne', db()->value('SELECT error FROM tisk_text WHERE ct=221'));
    }

    public function test_ai_gate_runs_before_validation_and_service_resolution(): void
    {
        // Vypnutý endpoint se nesmí dotknout služby ani pro chybný vstup.
        $this->app->bind(\App\Services\AiReviewService::class, function () {
            throw new \RuntimeException('Služba se při vypnutém AI nesmí vytvořit.');
        });
        $this->withHeader('X-Hlidac-Token', 'test-ai-token')
            ->postJson('/ai_api.php?action=result', ['kind' => 'wrong'])
            ->assertStatus(409)->assertJson(['error' => 'AI rozbor je vypnutý (ai.enabled)']);
    }

    public function test_payload_size_and_legacy_action_method_errors_remain_compatible(): void
    {
        config(['hlidac.ai.enabled' => true]);
        $this->withHeader('X-Hlidac-Token', 'test-ai-token');
        $this->postJson('/ai_api.php?action=result', ['error' => str_repeat('x', 1_000_001)])
            ->assertStatus(413)->assertJson(['error' => 'Příliš velký výsledek']);
        $this->get('/ai_api.php?action=result')->assertStatus(400)->assertJson(['error' => 'Neznámá akce']);
        $this->postJson('/ai_api.php?action=queue')->assertStatus(400)->assertJson(['error' => 'Neznámá akce']);
    }

    public function test_spatie_attributes_validate_only_fields_for_the_selected_action(): void
    {
        config(['hlidac.ai.enabled' => true]);
        $this->withHeader('X-Hlidac-Token', 'test-ai-token');
        $this->get('/ai_api.php?action=queue&kind[]=ignored&id=wrong')->assertOk()->assertExactJson(['items' => []]);
        db()->run('INSERT INTO tisk_text (ct, text, chars) VALUES (221, ?, 4)', ['Text']);
        $this->postJson('/ai_api.php?action=result', [
            'kind' => 'tisk', 'id' => 221, 'error' => 'Chyba',
            'ct' => 'ignored', 'limit' => [], 'verify' => 'ignored',
        ])->assertOk();
        $this->assertSame('Chyba', db()->value('SELECT error FROM tisk_text WHERE ct=221'));
        // Nullable PHP typ dovoluje výchozí stav DTO, samotný payload result:null zůstává neplatný.
        $this->postJson('/ai_api.php?action=result', ['kind' => 'tisk', 'id' => 221, 'result' => null])->assertStatus(400);
        $this->postJson('/ai_api.php?action=result', ['id' => 221])->assertStatus(400);
    }

    public function test_invalid_payloads_do_not_modify_texts(): void
    {
        config(['hlidac.ai.enabled' => true]);
        $this->withHeader('X-Hlidac-Token', 'test-ai-token');
        foreach ([[], ['kind' => 'wrong', 'id' => 1], ['kind' => 'tisk', 'id' => []], ['kind' => 'pn', 'id' => 1, 'result' => 'text']] as $payload) {
            $this->postJson('/ai_api.php?action=result', $payload)->assertStatus(400);
        }
        $this->get('/ai_api.php?action=queue&ct[]=1')->assertStatus(400);
        $this->postJson('/ai_api.php?action=unknown')->assertStatus(400);
        $this->assertSame(0, (int)db()->value('SELECT COUNT(*) FROM tisk_text'));
    }
}
