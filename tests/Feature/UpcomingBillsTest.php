<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\UpcomingBill;
use Illuminate\Support\Facades\Artisan;
use Tests\TestCase;

final class UpcomingBillsTest extends TestCase
{
    public function test_upcoming_page_api_validation_and_adoption_preserve_data(): void
    {
        $this->importFixtures();
        $bill = UpcomingBill::query()->findOrFail('KORNUP000003');
        $this->assertSame(7, $bill->dni);
        $this->assertSame(2, $bill->verzi);
        $this->assertTrue($bill->mimo_plan);
        $this->get('/pripravovane')->assertOk()->assertSee('Schváleno vládou')
            ->assertSee('Připomínky 7 dní (obvykle 4 týdny)');
        $this->get('/pripravovane.php')->assertStatus(301)->assertRedirect('/pripravovane');
        $this->get('/api?pripravovane=1')->assertOk()->assertJsonCount(3, 'pripravovane')
            ->assertJsonPath('pripravovane.0.pid', 'KORNUP000003')
            ->assertJsonPath('pripravovane.0.pripominky.dni', 7)
            ->assertHeader('Access-Control-Allow-Origin', '*');
        $this->get('/api?pripravovane=invalid')->assertStatus(400);
        $bill->update(['nazev' => '<script>alert(1)</script>']);
        $this->get('/pripravovane')->assertOk()->assertSee('<script>alert(1)</script>')
            ->assertDontSee('<script>alert(1)</script>', false);
        db()->run("DELETE FROM migrations WHERE migration = '2026_11_19_200000_add_upcoming_bills'");
        $this->assertSame(0, Artisan::call('migrate', ['--force' => true]));
        $this->assertSame('<script>alert(1)</script>', $bill->fresh()->nazev);
    }
}
