<?php




declare(strict_types=1);

require dirname(__DIR__) . '/app/bootstrap.php';

$file = $argv[1] ?? '';
if ($file === '' || !is_file($file)) {
    fwrite(STDERR, "Použití: php bin/sqlite2pg.php /cesta/k/hlidac.sqlite\n");
    exit(1);
}

$src = new Hlidac\Db('sqlite:' . $file);
$dst = db();
if ($dst->driver === 'sqlite') {
    fwrite(STDERR, "Cíl v config.php je SQLite, převod nemá smysl.\n");
    exit(1);
}
Illuminate\Support\Facades\Artisan::call('migrate', ['--force' => true]);

$tables = ['tisky', 'kroky', 'pn', 'pn_seen', 'pn_text', 'tisk_text', 'alerts', 'post_log', 'meta', 'hlasovani', 'hlasovani_kluby', 'text_revize', 'ai_revize', 'tisk_text_archiv', 'pn_text_archiv', 'priprava'];
$available = array_column($src->all("SELECT name FROM sqlite_master WHERE type = 'table'"), 'name');
$tables = array_values(array_intersect($tables, $available));


$src->pdo->beginTransaction();
$dst->pdo->beginTransaction();
try {
    foreach ($tables as $table) {
        $srcCols = array_column($src->all("PRAGMA table_info($table)"), 'name');
        $stmt = $dst->run('SELECT * FROM ' . $table . ' LIMIT 0');
        $dstCols = [];
        for ($i = 0; $i < $stmt->columnCount(); $i++) {
            $dstCols[] = $stmt->getColumnMeta($i)['name'];
        }
        $cols = array_values(array_intersect($srcCols, $dstCols));
        $dst->run("DELETE FROM $table");
        $read = $src->run('SELECT ' . implode(',', $cols) . " FROM $table");
        $batch = [];
        while ($row = $read->fetch()) {
            $batch[] = $row;
            if (count($batch) === 200) {
                $dst->insertMany($table, $cols, $batch);
                $batch = [];
            }
        }
        $dst->insertMany($table, $cols, $batch);
        $srcCount = (int)$src->value("SELECT COUNT(*) FROM $table");
        $dstCount = (int)$dst->value("SELECT COUNT(*) FROM $table");
        printf("%-10s %6d řádků%s\n", $table, $dstCount, $dstCount === $srcCount ? '' : "  NESEDÍ (SQLite $srcCount)");
        if ($dstCount !== $srcCount) {
            throw new RuntimeException("Počet řádků v tabulce $table nesedí");
        }
    }
    $dst->pdo->commit();
    $src->pdo->commit();
} catch (Throwable $ex) {
    $dst->pdo->rollBack();
    $src->pdo->rollBack();
    fwrite(STDERR, 'Převod zrušen, cílová databáze se nezměnila: ' . $ex->getMessage() . "\n");
    exit(2);
}
echo "Hotovo.\n";
