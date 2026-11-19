<?php

declare(strict_types=1);

namespace Tests\Feature;

use Hlidac\McpTokens;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;
use Tests\TestCase;

final class McpTokenTest extends TestCase
{
    public function test_post_validates_form_stores_only_hash_and_honors_ip_limit(): void
    {
        Schema::create('mcp_token', function (Blueprint $table): void {
            $table->increments('id');
            $table->text('jmeno');
            $table->string('hash')->unique();
            $table->text('vytvoren');
            $table->text('naposledy')->nullable();
            $table->text('zrusen')->nullable();
        });
        config(['hlidac.mcp' => ['token_table' => 'mcp_token', 'tokens_per_ip' => 1, 'tokens_per_day' => 10]]);
        $tokens = new McpTokens(db(), hlidacConfig());
        $this->get('/mcp')->assertOk()->assertSee('Získat token');
        $this->post('/mcp', ['formular' => []])->assertStatus(422);
        $this->post('/mcp', ['formular' => $tokens->formToken(), 'ucel' => 'test'])->assertOk()
            ->assertSee('příliš rychle');
        $form = ['formular' => $tokens->formToken(time() - 10), 'ucel' => '<test>'];
        $response = $this->post('/mcp', $form)->assertOk()->assertHeader('Cache-Control', 'no-store, private')
            ->assertSee('Váš token');
        preg_match('~hz_[A-Za-z0-9_-]{43}~', $response->getContent(), $match);
        $this->assertNotEmpty($match);
        $this->assertSame(hash('sha256', $match[0]), db()->value('SELECT hash FROM mcp_token'));
        $this->assertSame(0, (int)db()->value('SELECT COUNT(*) FROM mcp_token WHERE hash = ?', [$match[0]]));
        $this->post('/mcp', $form)->assertOk()->assertSee('Z vaší adresy už dnes');
        $this->get('/mcp')->assertOk()->assertDontSee($match[0]);
    }
}
