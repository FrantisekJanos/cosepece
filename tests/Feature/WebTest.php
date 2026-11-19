<?php

declare(strict_types=1);

namespace Tests\Feature;

use Tests\TestCase;

final class WebTest extends TestCase
{
    public function test_public_pages_and_rss_keep_existing_addresses(): void
    {
        $this->importFixtures();
        foreach (['/', '/index.php', '/tisky.php?f=p90', '/tisky.php?q=221&sort=recent', '/tisk.php?ct=90', '/upozorneni.php?vse=1', '/metodika.php'] as $url) {
            $this->get($url)->assertOk()->assertSee('Hlídač zákonů')->assertSee('assets/style.css', false);
        }
        $this->get('/tisk.php?ct=90')->assertSee('https://hlidac.test/tisk.php?ct=90', false);
        $this->get('/tisky.php')->assertSee('https://hlidac.test/tisky.php', false);
        $this->get('/metodika.php')->assertSee('<title>Jak hodnotíme – Hlídač zákonů</title>', false);
        $rss = $this->get('/rss.php?vse=1')->assertOk()->assertHeader('Content-Type', 'application/rss+xml; charset=utf-8');
        $xml = simplexml_load_string($rss->getContent());
        $this->assertNotFalse($xml);
        $this->assertGreaterThan(0, count($xml->channel->item));
    }

    public function test_upstream_search_history_and_ai_rounds_are_rendered(): void
    {
        $this->importFixtures();
        $this->get('/tisky.php?q=STRETU+ZAJMU&sort=recent&min=1')->assertOk()
            ->assertSee('tisk.php?ct=221', false)->assertSee('name="sort" value="recent"', false)
            ->assertSee('name="min" value="1"', false);
        db()->run("INSERT INTO alerts (alert_key, kind, ct, event_at, created_at, title, body, score, obdobi) VALUES ('9/old', 'pn', 90, '2026-11-19 12:00:00', '2026-11-19 12:00:00', 'Historický nález', 'Minulé období', 80, 9)");
        $this->get('/upozorneni.php?vse=1')->assertOk()->assertSee('Historický nález')
            ->assertSee('https://www.psp.cz/sqw/historie.sqw?o=9&amp;T=90', false)->assertSee('9. období');
        $rss = $this->get('/rss.php?vse=1')->assertOk();
        $xml = simplexml_load_string($rss->getContent());
        $old = $xml->xpath('//item[title="Historický nález"]');
        $this->assertSame('https://www.psp.cz/sqw/historie.sqw?o=9&T=90', (string)$old[0]->link);
        $this->get('/tisk.php?ct=90')->assertOk()->assertDontSee('Historický nález');
        db()->run("INSERT INTO tisk_text (ct, text, ai_json, ai_score, ai_model, ai_at, ai_prev_score, ai_prev_model) VALUES (90, 'Text', ?, 6, ?, '2026-11-19 12:00:00', 9, 'first-model')",
            ['{"shrnuti":"Ověřený rozbor","citace":["Text"]}', \Hlidac\AiReviewer::verifyModel(hlidacConfig())]);
        $this->get('/tisk.php?ct=90')->assertOk()->assertSee('první kolo first-model: 9/10');
    }

    public function test_missing_bill_and_unknown_paths_return_404(): void
    {
        $this->get('/tisk.php?ct=999999')->assertNotFound()->assertSee('Tisk nenalezen');
        $this->get('/does-not-exist')->assertNotFound();
        foreach (['/config.php', '/.env', '/data/hlidac.sqlite', '/app/Db.php', '/rag/README.md'] as $url) {
            $this->get($url)->assertNotFound();
        }
    }

    public function test_untrusted_content_is_escaped_once(): void
    {
        $this->importFixtures();
        db()->run('UPDATE tisky SET nazev = ?, uplny_nazev = ?, navrhovatel = ? WHERE ct = 90', ['A & B <script>alert(1)</script>', 'A & B <script>alert(1)</script>', 'C & D']);
        $this->get('/tisk.php?ct=90')->assertOk()
            ->assertSee('A &amp; B &lt;script&gt;alert(1)&lt;/script&gt;', false)
            ->assertDontSee('<script>alert(1)</script>', false)
            ->assertDontSee('C &amp;amp; D', false);
    }

    public function test_og_endpoint_returns_png_and_cache_headers(): void
    {
        $this->importFixtures();
        $response = $this->get('/og.php?ct=90')->assertOk()->assertHeader('Content-Type', 'image/png');
        $this->assertStringContainsString('max-age=86400', $response->headers->get('Cache-Control'));
        $file = $response->baseResponse->getFile()->getPathname();
        $size = getimagesize($file);
        $this->assertSame([1200, 630], [$size[0], $size[1]]);
    }

    public function test_webcron_requires_token_and_preserves_bootstrap_and_repeat_behavior(): void
    {
        require_once dirname(__DIR__) . '/make_fixtures.php';
        buildFixtures($this->dataDirectory . '/src');
        $this->get('/cron.php')->assertForbidden();
        $this->get('/cron.php?token[]=bad')->assertForbidden();
        $this->withHeader('X-Hlidac-Token', 'test-cron-token')->get('/cron.php')->assertOk()->assertJsonPath('posts.sent', 0);
        $alerts = db()->all('SELECT * FROM alerts ORDER BY alert_key');
        $this->get('/cron.php?token=test-cron-token')->assertOk()->assertJsonPath('alerts_new', 0)->assertJsonPath('posts.sent', 0);
        $this->assertSame($alerts, db()->all('SELECT * FROM alerts ORDER BY alert_key'));
        $this->assertSame(0, (int)db()->value('SELECT COUNT(*) FROM post_log'));
    }
}
