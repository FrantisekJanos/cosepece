<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\Bill;
use App\Models\BillText;
use App\Services\FindingsService;
use App\Services\Pages\BillListingService;
use App\Services\Pages\OverviewService;
use Hlidac\AiReviewer;
use Tests\TestCase;

final class FindingFiltersTest extends TestCase
{
    public function test_filters_apply_before_limit_and_keep_search_and_sort_in_links(): void
    {
        $this->importFixtures();
        Bill::query()->update(['score' => 90, 'reasons' => '[]']);
        Bill::query()->where('ct', 221)->update(['score' => 50, 'reasons' => json_encode([['key' => 'pn_other_law', 'points' => 0, 'label' => 'Přílepek']])]);
        $rows = app(BillListingService::class)->bills('prilepky', '', 1);
        $this->assertCount(1, $rows);
        $this->assertSame(221, (int)$rows[0]['ct']);
        $this->assertCount(1, app(BillListingService::class)->bills('prilepky', '221', 1));
        $this->assertCount(0, app(BillListingService::class)->bills('prilepky', 'neexistuje', 1));
        $data = app(OverviewService::class)->data(9, 'ai', 'prilepky');
        $this->assertSame('/?razeni=ai&druh=prilepky&n=12#nalezy', $data['findingsUrl'](['n' => 12]));
        $this->get('/?razeni=ai&druh=prilepky')->assertOk()->assertSee('Co hledám');
        $this->get('/tisky?f=prilepky&q=221')->assertOk()->assertSee('221');
        foreach (['razeni[]=ai', 'druh=invalid', 'razeni=invalid'] as $query) {
            $this->get('/?' . $query)->assertStatus(422);
        }
    }

    public function test_ai_problem_flags_require_verified_quoted_findings_of_sufficient_severity(): void
    {
        $this->importFixtures();
        BillText::query()->create(['ct' => 221, 'ai_score' => 8, 'ai_model' => AiReviewer::model(hlidacConfig()),
            'ai_json' => ['citace' => ['Citace'], 'souvisi_s_predmetem' => false, 'zpetna_ucinnost' => true]]);
        $service = app(FindingsService::class);
        $this->assertArrayNotHasKey(221, $service->aiSignals());
        BillText::query()->where('ct', 221)->update(['ai_model' => AiReviewer::verifyModel(hlidacConfig()), 'ai_score' => 5]);
        $this->assertFalse($service->aiSignals()[221]['rider']);
        BillText::query()->where('ct', 221)->update(['ai_score' => 6]);
        $this->assertTrue($service->aiSignals()[221]['rider']);
        $this->assertTrue($service->aiSignals()[221]['retro']);
        BillText::query()->where('ct', 221)->update(['ai_json' => '{"citace":[]}']);
        $this->assertArrayNotHasKey(221, $service->aiSignals());
    }

    public function test_shared_disclaimer_and_icons_use_public_paths(): void
    {
        $this->get('/')->assertOk()->assertSee('foot-disclaimer', false)->assertSee('/assets/favicon.svg', false)
            ->assertSee('/site.webmanifest', false)->assertSee('/metodika#odpovednost', false);
        $this->get('/metodika')->assertOk()->assertSee('id="odpovednost"', false);
        $this->assertSame('Co se peče', json_decode(file_get_contents(public_path('site.webmanifest')), true)['name']);
    }
}
