<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\Bill;
use Illuminate\Support\Facades\Artisan;
use Tests\TestCase;

final class AmendmentSummaryTest extends TestCase
{
    public function test_summary_is_imported_rendered_and_preserved_by_adoption(): void
    {
        $this->importFixtures();
        $bill = Bill::query()->where('ct', 311)->firstOrFail();
        $this->assertSame(3, $bill->souhrn_pn);
        $this->assertSame('2026-11-05 10:24', $bill->souhrn_pn_at->format('Y-m-d H:i'));
        $this->get('/tisky/311')->assertOk()->assertSee('Souhrn platně podaných pozměňovacích návrhů (tisk 311/3)')
            ->assertSee('tiskt.sqw?o=10&amp;ct=311&amp;ct1=3', false)->assertSee('5. 11. 2026, 10:24');
        db()->run("DELETE FROM migrations WHERE migration = '2026_11_19_160000_add_amendment_summary'");
        $this->assertSame(0, Artisan::call('migrate', ['--force' => true]));
        $this->assertSame(3, $bill->fresh()->souhrn_pn);
    }
}
