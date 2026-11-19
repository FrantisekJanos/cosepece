<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\AmendmentText;
use App\Models\Bill;
use App\Models\BillText;
use App\Services\Pages\OverviewService;
use Hlidac\AiReviewer;
use Tests\TestCase;

final class StrongFindingsTest extends TestCase
{
    public function test_only_verified_quoted_findings_include_low_score_bills_and_old_closed_bills_stay_hidden(): void
    {
        $this->importFixtures();
        Bill::query()->where('ct', 221)->update(['score' => 10, 'faze' => '1. čtení', 'posledni' => date('Y-m-d H:i:s')]);
        BillText::query()->create(['ct' => 221, 'ai_score' => 9, 'ai_model' => AiReviewer::model(hlidacConfig()),
            'ai_at' => date('Y-m-d H:i:s'), 'ai_json' => ['titulek' => 'Silný nález', 'citace' => ['Doslovná citace.']]]);
        $finding = fn (): ?array => array_values(array_filter(app(OverviewService::class)->topFindings(50, 60),
            fn (array $bill): bool => (int)$bill['ct'] === 221))[0] ?? null;
        $this->assertNull($finding());
        BillText::query()->where('ct', 221)->update(['ai_model' => AiReviewer::verifyModel(hlidacConfig())]);
        $this->assertSame(9, $finding()['strong_ai']);
        Bill::query()->where('ct', 221)->update(['score' => 55]);
        $this->assertSame(9, $finding()['strong_ai']);
        Bill::query()->where('ct', 221)->update(['score' => 10]);
        $data = app(OverviewService::class)->data(9);
        $this->assertSame(9, $data['findingsShown']);
        $this->assertSame(45, $data['w']['finding_min']);
        $this->get('/')->assertOk()->assertSee('Ověřený nález AI 9/10')->assertSee('href="/tisky/221"', false);
        BillText::query()->where('ct', 221)->update(['ai_json' => '{"citace":[]}']);
        $this->assertNull($finding());
        BillText::query()->where('ct', 221)->delete();
        AmendmentText::query()->create(['id_dokument' => 999999, 'ct' => 221, 'cislo' => 1, 'ai_score' => 8,
            'ai_model' => AiReviewer::verifyModel(hlidacConfig()), 'ai_at' => date('Y-m-d H:i:s'),
            'ai_json' => ['titulek' => 'Silný nález PN', 'citace' => ['Doslovná citace PN.']]]);
        $this->assertSame(8, $finding()['strong_ai']);
        Bill::query()->where('ct', 221)->update(['faze' => 'Vyhlášeno', 'posledni' => date('Y-m-d', strtotime('-200 days'))]);
        $this->assertNull($finding());
    }
}
