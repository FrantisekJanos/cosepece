<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\Alert;
use App\Models\Bill;
use App\Models\Meta;
use Carbon\CarbonImmutable;
use Tests\TestCase;

final class ModelsTest extends TestCase
{
    public function test_source_identifiers_and_relationships_work_with_imported_data(): void
    {
        $this->importFixtures();
        $bill = Bill::query()->where('ct', 221)->firstOrFail();
        $loaded = Bill::query()->with(['steps.bill', 'amendments.bill', 'amendments.seen.amendment', 'alerts.bill'])
            ->findOrFail($bill->id_tisk);
        $this->assertTrue($loaded->is($bill));
        $this->assertNotEmpty($loaded->steps);
        foreach ($loaded->steps as $step) {
            $this->assertTrue($step->bill->is($bill));
            $this->assertSame($step->id_hist, $step->getKey());
        }
        $this->assertNotEmpty($loaded->amendments);
        foreach ($loaded->amendments as $amendment) {
            $this->assertTrue($amendment->bill->is($bill));
            $this->assertNotNull($amendment->seen);
            $this->assertTrue($amendment->seen->amendment->is($amendment));
        }
        $this->assertNotEmpty($loaded->alerts);
        foreach ($loaded->alerts as $alert) {
            $this->assertTrue($alert->bill->is($bill));
            $this->assertSame($alert->alert_key, $alert->getKey());
            $this->assertIsInt($alert->posted); // Stav 2/3 není boolean.
        }
    }

    public function test_text_models_round_trip_json_dates_and_nullable_scores_without_schema_changes(): void
    {
        $this->importFixtures();
        $bill = Bill::query()->where('ct', 221)->firstOrFail();
        $text = $bill->text()->create([
            'text' => 'Text zákona', 'chars' => 11, 'signals' => ['retro' => true],
            'ai_json' => ['citace' => ['Text zákona']], 'ai_score' => null,
            'fetched_at' => '2026-11-19 10:30:00',
        ]);
        $fresh = $text->fresh();
        $this->assertSame(221, $fresh->getKey());
        $this->assertSame(['retro' => true], $fresh->signals);
        $this->assertSame(['citace' => ['Text zákona']], $fresh->ai_json);
        $this->assertNull($fresh->ai_score);
        $this->assertInstanceOf(CarbonImmutable::class, $fresh->fetched_at);
        $this->assertTrue($fresh->bill->is($bill));
        $this->assertSame('{"retro":true}', db()->value('SELECT signals FROM tisk_text WHERE ct=221'));
        $this->assertSame('2026-11-19 10:30:00', db()->value('SELECT fetched_at FROM tisk_text WHERE ct=221'));

        $amendment = $bill->amendments()->firstOrFail();
        $pnText = $amendment->text()->create([
            'ct' => $bill->ct, 'cislo' => $amendment->cislo, 'signals' => [], 'ai_json' => null,
        ]);
        $this->assertTrue($pnText->amendment->is($amendment));
        $this->assertTrue($pnText->bill->is($bill));
        $this->assertTrue($bill->amendmentTexts()->firstOrFail()->is($pnText));
        $fresh->ai_score = 8;
        $fresh->save();
        $this->assertSame(8, $bill->text()->firstOrFail()->ai_score);
    }

    public function test_string_metadata_keys_and_source_keys_can_be_updated_without_id_or_timestamps(): void
    {
        Meta::query()->create(['k' => 'model-test', 'v' => 'původní']);
        $meta = Meta::query()->findOrFail('model-test');
        $meta->v = 'nová';
        $meta->save();
        $this->assertSame('nová', db()->getMeta('model-test'));

        $this->importFixtures();
        $bill = Bill::query()->where('ct', 90)->firstOrFail();
        $bill->reasons = [['key' => 'example', 'points' => 5]];
        $bill->save();
        $this->assertSame([['key' => 'example', 'points' => 5]], $bill->fresh()->reasons);
        $this->assertIsBool($bill->p90);
        $this->assertInstanceOf(CarbonImmutable::class, $bill->predlozeno);
    }

    public function test_post_log_keeps_multiple_attempts_and_forbids_ambiguous_instance_updates(): void
    {
        $this->importFixtures();
        $alert = Alert::query()->firstOrFail();
        foreach (['error', 'ok'] as $status) {
            $alert->postLogs()->create(['network' => 'mastodon', 'status' => $status, 'at' => '2026-11-19 11:00:00']);
        }
        $this->assertSame(2, $alert->postLogs()->count());
        $attempt = $alert->postLogs()->where('status', 'error')->firstOrFail();
        $this->assertTrue($attempt->alert->is($alert));
        $this->assertInstanceOf(CarbonImmutable::class, $attempt->at);
        $attempt->status = 'ok';
        try {
            $attempt->save();
            $this->fail('Záznam bez klíče se nesmí aktualizovat.');
        } catch (\LogicException) {
            $this->assertSame(1, $alert->postLogs()->where('status', 'error')->count());
        }
        $this->expectException(\LogicException::class);
        $attempt->delete();
    }
}
