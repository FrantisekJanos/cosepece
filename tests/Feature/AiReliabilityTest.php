<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\BillText;
use App\Models\AmendmentText;
use App\Models\ArchivedBillText;
use App\Models\ArchivedAmendmentText;
use App\Services\AiReliabilityService;
use Hlidac\AiReviewer;
use Hlidac\Revisions;
use Illuminate\Support\Facades\Artisan;
use Tests\TestCase;

final class AiReliabilityTest extends TestCase
{
    public function test_repeat_data_survives_adoption_and_period_archival_and_is_visible_in_methodology(): void
    {
        $this->importFixtures();
        $data = ['ct' => 221, 'ai_runs' => [5, 2, 6], 'ai_run_count' => 3,
            'ai_score' => 5, 'ai_prompt' => AiReviewer::PROMPT_VERSION];
        $text = BillText::query()->updateOrCreate(['ct' => 221], $data);
        AmendmentText::query()->create($data + ['id_dokument' => 999998, 'cislo' => 999998]);
        $this->assertSame([5, 2, 6], $text->ai_runs);
        db()->run("DELETE FROM migrations WHERE migration = '2026_11_19_220000_add_ai_repeated_runs'");
        $this->assertSame(0, Artisan::call('migrate', ['--force' => true]));
        $this->assertSame([5, 2, 6], $text->fresh()->ai_runs);
        $stats = app(AiReliabilityService::class)->statistics();
        $this->assertSame(2, $stats['runs']['n']);
        $this->assertSame(4, $stats['runs']['max_spread']);
        $this->get('/metodika')->assertOk()->assertSee('Jak ověřujeme rozbor AI');
        Revisions::archivePeriod(db(), 10);
        $archived = ArchivedBillText::query()->where('ct', 221)->where('obdobi', 10)->firstOrFail();
        $this->assertSame([5, 2, 6], $archived->ai_runs);
        $this->assertSame(3, $archived->ai_run_count);
        $this->assertSame([5, 2, 6], ArchivedAmendmentText::query()->where('id_dokument', 999998)->firstOrFail()->ai_runs);
    }
}
