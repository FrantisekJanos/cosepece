<?php

declare(strict_types=1);

namespace Tests\Feature;

use Hlidac\Sentry;
use Illuminate\Support\Facades\Route;
use Tests\TestCase;

final class SentryIntegrationTest extends TestCase
{
    public function test_laravel_reports_errors_without_replacing_handlers_or_exposing_details(): void
    {
        $events = [];
        $probe = static fn (): bool => false;
        $before = set_error_handler($probe);
        restore_error_handler();
        Sentry::init(['dsn' => 'https://fake@example.invalid/1', 'throttle_dir' => ''], 'web', false);
        $after = set_error_handler($probe);
        restore_error_handler();
        $this->assertSame($before, $after);
        Sentry::setTransport(function ($url, $headers, $body) use (&$events): bool {
            $events[] = $body;
            return true;
        });
        try {
            config(['app.debug' => false]);
            Route::get('/sentry-integration-test', static function (): never {
                throw new \RuntimeException('Connection failed password=not-a-real-password');
            });
            $this->get('/sentry-integration-test')->assertStatus(500)->assertDontSee('not-a-real-password');
            $this->assertCount(1, $events);
            $this->assertStringNotContainsString('not-a-real-password', $events[0]);
            $this->assertStringContainsString('password=***', $events[0]);
            Sentry::init(['dsn' => ''], 'web', false);
            report(new \RuntimeException('disabled sentry'));
            $this->assertCount(1, $events);
        } finally {
            Sentry::init(['dsn' => ''], 'web', false);
            Sentry::setTransport(null);
            Sentry::reset();
        }
    }
}
