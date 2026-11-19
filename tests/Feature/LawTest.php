<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\Amendment;
use App\Models\AmendmentText;
use App\Models\UpcomingBill;
use Hlidac\Laws;
use Tests\TestCase;

final class LawTest extends TestCase
{
    public function test_law_page_collects_bills_external_amendments_and_upcoming_proposals(): void
    {
        $this->importFixtures();
        foreach ([90, 221] as $ct) {
            Amendment::query()->create(['id_dokument' => 999000 + $ct, 'cislo' => 999000 + $ct,
                'ct' => $ct, 'osoba' => 'Test autor', 'cas' => '2026-11-19 12:00:00']);
            AmendmentText::query()->create(['id_dokument' => 999000 + $ct, 'cislo' => 999000 + $ct,
                'ct' => $ct, 'laws' => '159/2006', 'ai_json' => ['titulek' => '<script>bad</script>']]);
        }
        UpcomingBill::query()->create(['pid' => 'TESTLAW', 'faze' => 'pripominky',
            'nazev' => 'Návrh zákona, kterým se mění zákon č. 159/2006 Sb., o střetu zájmů', 'resort' => 'Ministerstvo test']);
        $laws = app(Laws::class);
        $this->assertSame([90], array_map(fn($a) => (int)$a['ct'], $laws->amendments('159/2006')));
        $this->assertSame('TESTLAW', $laws->upcoming('159/2006')[0]['pid']);
        $this->get('/zakony/2006/159')->assertOk()->assertSee('Zákon č. 159/2006 Sb., o střetu zájmů')
            ->assertSee('/tisky/90#pn-999090', false)->assertSee('Ministerstvo test')
            ->assertSee('/rss?zakon=159%2F2006', false)->assertSee('/api?zakon=159%2F2006', false)
            ->assertDontSee('<script>bad</script>', false);
        $this->get('/zakony')->assertOk()->assertSee('/zakony/2006/159', false);
        $this->get('/tisky/221')->assertOk()->assertSee('/zakony/2006/159', false);
    }

    public function test_legacy_redirect_validation_and_route_parameters(): void
    {
        $this->get('/zakon.php')->assertStatus(301)->assertRedirect('/zakony');
        $this->get('/zakon.php?z=159%2F2006')->assertStatus(301)->assertRedirect('/zakony/2006/159');
        $this->get('/zakon.php?z[]=159')->assertStatus(422);
        $this->get('/zakon.php?z=invalid')->assertStatus(422);
        $this->get('/zakony/2006/159?z=invalid')->assertOk()->assertSee('159/2006');
        $this->get('/zakony/bad/159')->assertNotFound();
    }
}
