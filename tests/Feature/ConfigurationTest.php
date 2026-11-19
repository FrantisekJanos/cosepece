<?php

declare(strict_types=1);

namespace Tests\Feature;

use Symfony\Component\Process\Process;
use Tests\TestCase;

final class ConfigurationTest extends TestCase
{
    public function test_legacy_settings_and_environment_survive_configuration_cache(): void
    {
        $legacyPath = $this->dataDirectory . '/config.php';
        file_put_contents($legacyPath, '<?php return ' . var_export([
            'site_url' => 'https://legacy.test', 'watch_laws' => ['123/2026'],
            'db_dsn' => 'sqlite:' . $this->dataDirectory . '/legacy.sqlite',
        ], true) . ';');
        $env = [
            'HLIDAC_CONFIG' => $legacyPath,
            'HLIDAC_DATA_PATH' => $this->dataDirectory,
            'APP_CONFIG_CACHE' => $this->dataDirectory . '/cached.php',
            'APP_URL' => 'https://override.test',
            'DB_CONNECTION' => 'sqlite',
            'DB_DATABASE' => $this->dataDirectory . '/override.sqlite',
        ];
        $cache = new Process([PHP_BINARY, 'artisan', 'config:cache'], base_path(), $env);
        $cache->mustRun();
        unlink($legacyPath);
        $read = new Process([PHP_BINARY, '-r', <<<'CODE'
require 'app/bootstrap.php';
echo json_encode([hlidacConfig('site_url'), hlidacConfig('watch_laws'), config('database.connections.sqlite.database')]);
CODE], base_path(), $env);
        $read->mustRun();
        $this->assertSame([
            'https://override.test', ['123/2026'], $this->dataDirectory . '/override.sqlite',
        ], json_decode($read->getOutput(), true));
    }
}
