<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\ArchivedBillText;
use App\Models\TextRevision;
use Hlidac\Revisions;
use Hlidac\TiskText;
use Illuminate\Support\Facades\Artisan;
use Tests\TestCase;

final class RevisionTest extends TestCase
{
    public function test_clean_history_route_diff_and_legacy_redirect(): void
    {
        $this->importFixtures();
        $saver = new TiskText(db(), hlidacConfig());
        $saver->save(221, 'https://www.psp.cz/?idd=1001', "Lhůta 10 let.\n<script>alert(1)</script>", null);
        $saver->save(221, 'https://www.psp.cz/?idd=1002', "Lhůta 4 roky.\n<script>alert(1)</script>", null);
        $this->get('/tisky/221')->assertOk()->assertSee('href="/revize/tisk/221"', false);
        $this->get('/revize/tisk/221?a=1&b=2&id=90')->assertOk()->assertSee('Verze textu')
            ->assertSee('<del>', false)->assertSee('idd 1002')->assertSee('&lt;script&gt;', false)
            ->assertDontSee('<script>alert(1)</script>', false)
            ->assertSee('action="/revize/tisk/221"', false);
        $this->get('/revize.php?druh=tisk&id=221&a=1&b=2')->assertStatus(301)
            ->assertRedirect('/revize/tisk/221?a=1&b=2');
        $this->get('/revize/tisk/999999')->assertNotFound()->assertSee('Historie nenalezena');
    }

    public function test_history_inputs_use_dto_validation(): void
    {
        foreach (['/revize/tisk/221?a=bad', '/revize/tisk/221?b[]=2', '/revize/tisk/221?o=-1',
            '/revize.php?druh=unknown&id=221', '/revize.php?druh=tisk&id[]=221'] as $url) {
            $this->getJson($url)->assertUnprocessable();
        }
        $this->get('/revize/unknown/221')->assertNotFound();
        $this->get('/revize/tisk/not-a-number')->assertNotFound();
    }

    public function test_archived_history_is_kept_separate_and_adopted_without_data_loss(): void
    {
        $this->importFixtures();
        $saver = new TiskText(db(), hlidacConfig());
        $saver->save(221, null, 'Archivovaný text.', null);
        Revisions::archivePeriod(db(), 9);
        $this->assertSame('Archivovaný text.', ArchivedBillText::query()->where('obdobi', 9)->where('ct', 221)->value('text'));
        $this->get('/revize/tisk/221?o=9')->assertOk()->assertSee('9. volební období')
            ->assertSee('historie.sqw?o=9&amp;T=221', false);
        $before = TextRevision::query()->get()->map->getAttributes()->all();
        db()->run("DELETE FROM migrations WHERE migration = '2026_11_19_140000_add_text_and_ai_revisions'");
        $this->assertSame(0, Artisan::call('migrate', ['--force' => true]));
        $this->assertSame($before, TextRevision::query()->get()->map->getAttributes()->all());
        $this->assertSame('Archivovaný text.', ArchivedBillText::query()->where('obdobi', 9)->where('ct', 221)->value('text'));
        $record = TextRevision::query()->firstOrFail();
        $this->expectException(\LogicException::class);
        $record->delete();
    }
}
