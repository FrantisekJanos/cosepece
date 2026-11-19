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
                $columns = array_column(db()->all('PRAGMA table_info(' . $table . ')'), 'name');
                $records[$table] = array_map(fn (array $row): array => array_intersect_key($row, array_flip($columns)), $rows);
                db()->insertMany($table, array_keys($records[$table][0]), $records[$table]);
            }
        }
        db()->run('DELETE FROM migrations');
        $this->assertSame(0, Artisan::call('migrate', ['--force' => true]));
        foreach ($records as $table => $rows) {
            $actual = db()->all('SELECT * FROM ' . $table);
            if ($rows !== []) {
                $actual = array_map(fn (array $row): array => array_intersect_key($row, $rows[0]), $actual);
            }
            $this->assertSame($rows, $actual, $table);
        }
        $this->assertSame(0, Artisan::call('migrate', ['--force' => true]));
        $this->assertSame(8, (int)db()->value('SELECT COUNT(*) FROM migrations'));
    }

    public function test_upstream_columns_preserve_existing_ai_results(): void
    {
        db()->run("INSERT INTO tisk_text (ct, text, ai_json, ai_score, ai_model) VALUES (221, 'Text', '{}', 7, 'original')");
        db()->run("DELETE FROM migrations WHERE migration = '2026_11_19_100000_adopt_upstream_improvements'");
        foreach (['tisk_text' => ['checked_at', 'ai_prev_json', 'ai_prev_score', 'ai_prev_model'],
            'pn_text' => ['ai_prev_json', 'ai_prev_score', 'ai_prev_model'], 'tisky' => ['lhuta'], 'alerts' => ['obdobi']] as $table => $columns) {
            foreach ($columns as $column) {
                db()->run('ALTER TABLE ' . $table . ' DROP COLUMN ' . $column);
            }
        }
        $this->assertSame(0, Artisan::call('migrate', ['--force' => true]));
        $row = db()->one('SELECT * FROM tisk_text WHERE ct = 221');
        $this->assertSame('Text', $row['text']);
        $this->assertSame(7, (int)$row['ai_score']);
        $this->assertSame('original', $row['ai_model']);
        $this->assertNull($row['ai_prev_score']);
        $this->assertNull($row['checked_at']);
        $this->assertSame(0, Artisan::call('migrate', ['--force' => true]));
    }

    public function test_existing_votes_survive_adoption_and_repeated_migrations(): void
    {
        $this->importFixtures();
        $votes = db()->all('SELECT * FROM hlasovani ORDER BY id_hlasovani');
        $clubs = db()->all('SELECT * FROM hlasovani_kluby ORDER BY id_hlasovani, klub');
        $this->assertCount(4, $votes);
        db()->run("DELETE FROM migrations WHERE migration = '2026_11_19_120000_add_bill_votes'");
        $this->assertSame(0, Artisan::call('migrate', ['--force' => true]));
        $this->assertSame($votes, db()->all('SELECT * FROM hlasovani ORDER BY id_hlasovani'));
        $this->assertSame($clubs, db()->all('SELECT * FROM hlasovani_kluby ORDER BY id_hlasovani, klub'));
        $this->assertSame(0, Artisan::call('migrate', ['--force' => true]));
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
