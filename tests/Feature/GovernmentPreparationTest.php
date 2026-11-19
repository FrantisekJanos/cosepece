<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\Bill;
use Illuminate\Support\Facades\Artisan;
use Tests\TestCase;

final class GovernmentPreparationTest extends TestCase
{
    public function test_preparation_relationship_rendering_and_older_schema_adoption(): void
    {
        $this->importFixtures();
        $bill = Bill::query()->where('ct', 90)->with('preparation.bill')->firstOrFail();
        $prep = $bill->preparation;
        $this->assertSame(90, $prep->getKey());
        $this->assertSame(14, $prep->dni);
        $this->assertSame(23, $prep->mist);
        $this->assertTrue($prep->bill->is($bill));
        $this->get('/tisky/90')->assertOk()->assertSee('Příprava návrhu u vlády')
            ->assertSee('Připomínky 14 dní (obvykle 4 týdny)')
            ->assertSee('odok.gov.cz/portal/veklep/material/KORNDQGFVQKI/', false);
        db()->run('ALTER TABLE priprava DROP COLUMN znovu');
        db()->run("DELETE FROM migrations WHERE migration = '2026_11_19_180000_add_government_preparation'");
        $this->assertSame(0, Artisan::call('migrate', ['--force' => true]));
        $this->assertSame('KORNDQGFVQKI', $prep->fresh()->pid);
        $this->assertFalse($prep->fresh()->znovu);
    }
}
