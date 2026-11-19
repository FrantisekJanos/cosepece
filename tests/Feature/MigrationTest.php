<?php

declare(strict_types=1);

namespace Tests\Feature;

use Illuminate\Support\Facades\Artisan;
use Tests\TestCase;

final class MigrationTest extends TestCase
{
    public function test_existing_database_is_adopted_without_changing_records(): void
    {
        $this->importFixtures();
        db()->run('INSERT INTO tisk_text (ct, text, ai_json, ai_score, ai_model, ai_prompt) VALUES (221, ?, ?, 8, ?, 2)', ['Původní text', '{"citace":["Původní text"]}', 'test-model']);
        db()->run("INSERT INTO post_log (alert_key, network, status, at) VALUES ('existing', 'mastodon', 'ok', '2026-11-18 12:00:00')");
        $tables = ['tisky', 'kroky', 'pn', 'pn_seen', 'pn_text', 'tisk_text', 'alerts', 'post_log', 'meta'];
        $records = [];
        foreach ($tables as $table) {
            $records[$table] = db()->all('SELECT * FROM ' . $table);
            db()->run('DROP TABLE ' . $table);
        }
        db()->pdo->exec(file_get_contents(dirname(__DIR__) . '/Support/legacy-schema.sql'));
        foreach ($records as $table => $rows) {
            if ($rows !== []) {
                db()->insertMany($table, array_keys($rows[0]), $rows);
            }
        }
        db()->run('DELETE FROM migrations');
        $this->assertSame(0, Artisan::call('migrate', ['--force' => true]));
        foreach ($records as $table => $rows) {
            $this->assertSame($rows, db()->all('SELECT * FROM ' . $table), $table);
        }
        $this->assertSame(0, Artisan::call('migrate', ['--force' => true]));
        $this->assertSame(1, (int)db()->value('SELECT COUNT(*) FROM migrations'));
    }

    public function test_older_schema_gets_missing_ai_columns_without_losing_data(): void
    {
        db()->run('INSERT INTO tisk_text (ct, text) VALUES (221, ?)', ['Uložený text']);
        foreach (['ai_model', 'ai_prompt'] as $column) {
            db()->run('ALTER TABLE tisk_text DROP COLUMN ' . $column);
            db()->run('ALTER TABLE pn_text DROP COLUMN ' . $column);
        }
        db()->run('DELETE FROM migrations');
        $this->assertSame(0, Artisan::call('migrate', ['--force' => true]));
        $row = db()->one('SELECT * FROM tisk_text WHERE ct=221');
        $this->assertSame('Uložený text', $row['text']);
        $this->assertNull($row['ai_model']);
        $this->assertNull($row['ai_prompt']);
    }

    public function test_artisan_and_compatibility_commands_are_registered(): void
    {
        $this->artisan('hlidac:ai', ['--resanitize' => true])->assertExitCode(0);
        $this->artisan('hlidac:texts', ['--reanalyze' => true])->assertExitCode(0);
        $this->artisan('hlidac:ai', ['--limit' => 0])->assertExitCode(1);
        $this->importFixtures();
        $this->artisan('hlidac:sync')->assertExitCode(0);
    }
}
