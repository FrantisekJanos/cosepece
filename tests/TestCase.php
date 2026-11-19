<?php

declare(strict_types=1);

namespace Tests;

use Illuminate\Foundation\Testing\TestCase as BaseTestCase;
use Illuminate\Support\Facades\Artisan;

abstract class TestCase extends BaseTestCase
{
    protected string $dataDirectory;

    protected function setUp(): void
    {
        parent::setUp();
        $this->dataDirectory = sys_get_temp_dir() . '/hlidac-http-' . bin2hex(random_bytes(6));
        mkdir($this->dataDirectory, 0775, true);
        config([
            'hlidac.data_path' => $this->dataDirectory,
            'hlidac.source_dir' => $this->dataDirectory . '/src',
            'hlidac.site_url' => 'https://hlidac.test',
            'hlidac.cron_token' => 'test-cron-token',
            'hlidac.ai.remote_token' => 'test-ai-token',
            'hlidac.ai.enabled' => false,
            'hlidac.pn_text.enabled' => false,
            'hlidac.tisk_text.enabled' => false,
            'hlidac.notify.dry_run' => true,
        ]);
        Artisan::call('migrate', ['--force' => true]);
    }

    protected function importFixtures(): void
    {
        require_once dirname(__DIR__) . '/tests/make_fixtures.php';
        buildFixtures($this->dataDirectory . '/src');
        \Hlidac\Sync::run();
    }

    protected function tearDown(): void
    {
        if (isset($this->dataDirectory)) {
            $files = new \RecursiveIteratorIterator(
                new \RecursiveDirectoryIterator($this->dataDirectory, \FilesystemIterator::SKIP_DOTS),
                \RecursiveIteratorIterator::CHILD_FIRST,
            );
            foreach ($files as $file) {
                $file->isDir() ? rmdir($file->getPathname()) : unlink($file->getPathname());
            }
            rmdir($this->dataDirectory);
        }
        parent::tearDown();
    }
}
