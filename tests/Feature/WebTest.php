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
