<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\Alert;
use App\Models\UpcomingBill;
use Hlidac\AlertBuilder;
use Hlidac\Scorer;
use Hlidac\View;
use Tests\TestCase;

final class UpcomingAlertsTest extends TestCase
{
    public function test_upcoming_alerts_render_in_api_rss_and_pages_and_link_to_source_when_removed(): void
    {
        $this->importFixtures();
        $key = 'prep_review:KORNUP000003';
        $alert = Alert::query()->findOrFail($key);
        $this->assertSame(2, (int)$alert->posted);
        $this->assertSame(50, (int)$alert->score);
        $this->get('/api?upozorneni=1&min_score=50')->assertOk()->assertJsonFragment([
            'id' => $key, 'ct' => null, 'typ' => 'prep_velmi', 'skore' => 50,
        ]);
        $this->get('/rss?min_score=50')->assertOk()->assertSee('/pripravovane#prep-KORNUP000003', false);
        $this->get('/upozorneni')->assertOk()->assertSee('připravovaný zákon')->assertDontSee('>tisk 0', false);
        $this->get('/pripravovane')->assertOk()->assertSee('id="prep-KORNUP000003"', false);
        $this->assertSame('/pripravovane#prep-KORNUP000003', View::alertUrl($alert->getAttributes()));
        UpcomingBill::query()->whereKey('KORNUP000003')->delete();
        $this->assertSame('https://odok.gov.cz/portal/veklep/material/KORNUP000003/', View::alertUrl($alert->getAttributes(), true));
    }

    public function test_new_events_after_bootstrap_are_eligible_for_publication_and_idempotent(): void
    {
        $this->importFixtures();
        UpcomingBill::query()->create(['pid' => 'NEWPREP', 'nazev' => 'Nový návrh zákona', 'faze' => 'pripominky',
            'pripominky_od' => '2026-11-19', 'pripominky_do' => '2026-11-24', 'dni' => 5]);
        $builder = new AlertBuilder(db(), hlidacConfig());
        $this->assertSame(1, $builder->run());
        (new Scorer(db(), hlidacConfig()))->scoreAlerts();
        $alert = Alert::query()->findOrFail('prep_review:NEWPREP');
        $this->assertSame(0, (int)$alert->posted);
        $this->assertSame(50, (int)$alert->score);
        $this->assertSame(0, $builder->run());
    }
}
