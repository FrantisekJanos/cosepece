<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\Meta;
use Hlidac\SearchIndex;
use Tests\TestCase;

final class SearchTest extends TestCase
{
    public function test_search_redirects_validates_escapes_and_links_to_clean_pages(): void
    {
        $this->importFixtures();
        $this->get('/hledat?q=221')->assertRedirect('/tisky/221');
        $this->get('/hledat?q=st%C5%99et%20z%C3%A1jm%C5%AF')->assertOk()->assertSee('/tisky/221', false);
        $this->get('/hledat?q=' . urlencode('<script>alert(1)</script>'))->assertOk()
            ->assertSee('<script>alert(1)</script>')->assertDontSee('<script>alert(1)</script>', false);
        $this->get('/hledat?q[]=bad')->assertStatus(422);
        $this->get('/hledat?index[]=bad')->assertStatus(422);
        $this->get('/hledat.php?q=221')->assertStatus(301)->assertRedirect('/hledat?q=221');
        foreach (app(SearchIndex::class)->build()['items'] as $item) {
            $this->assertStringNotContainsString('.php', $item['u']);
        }
    }

    public function test_every_amendment_has_an_anchor_including_those_beyond_first_thirty(): void
    {
        $this->importFixtures();
        for ($i = 0; $i < 35; $i++) {
            \App\Models\Amendment::query()->create(['id_dokument' => 990000 + $i, 'cislo' => 990000 + $i,
                'ct' => 221, 'osoba' => 'Test poslanec', 'cas' => '2026-11-19 12:00:00']);
        }
        $page = $this->get('/tisky/221')->assertOk()->assertSee('details class="pn-more"', false);
        for ($i = 0; $i < 35; $i++) {
            $page->assertSee('id="pn-' . (990000 + $i) . '"', false);
        }
    }

    public function test_index_supports_etag_gzip_and_sync_invalidates_cached_form_url(): void
    {
        $this->importFixtures();
        $response = $this->get('/hledat?index=1')->assertOk()->assertJsonStructure(['items', 'sync', 'kinds']);
        $etag = $response->headers->get('ETag');
        $this->withHeader('If-None-Match', $etag)->get('/hledat?index=1')->assertStatus(304);
        $this->withHeader('If-None-Match', 'different')->withHeader('Accept-Encoding', 'gzip')
            ->get('/hledat?index=abcdef')->assertOk()->assertHeader('Content-Encoding', 'gzip');
        $compressed = $this->get('/hledat?index=abcdef');
        $this->assertSame($response->json('items'), json_decode(gzdecode($compressed->getContent()), true)['items']);
        $this->flushHeaders();
        Meta::query()->updateOrCreate(['k' => 'last_sync'], ['v' => '2026-11-19 20:00:00']);
        $this->get('/')->assertOk()->assertSee('/hledat?index=' . substr(md5('2026-11-19 20:00:00'), 0, 10), false);
        $this->assertSame('2026-11-19 20:00:00', $this->get('/hledat?index=1')->json('sync'));
    }
}
