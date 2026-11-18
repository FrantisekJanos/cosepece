<?php
// Tenká vrstva nad PDO. Schéma funguje v SQLite, MySQL/MariaDB i PostgreSQL.

declare(strict_types=1);

namespace Hlidac;

use PDO;
use PDOStatement;

final class Db
{
    public PDO $pdo;
    public string $driver;

    public function __construct(string $dsn, ?string $user = null, ?string $pass = null)
    {
        if (str_starts_with($dsn, 'sqlite:')) {
            // u SQLite zajistíme existenci adresáře pro soubor databáze
            $file = substr($dsn, 7);
            $dir = dirname($file);
            if ($dir !== '' && !is_dir($dir)) {
                mkdir($dir, 0775, true);
            }
        }
        $this->pdo = new PDO($dsn, $user, $pass, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
        $this->driver = (string)$this->pdo->getAttribute(PDO::ATTR_DRIVER_NAME);
        if ($this->driver === 'sqlite') {
            $this->pdo->exec('PRAGMA journal_mode = WAL');
            $this->pdo->exec('PRAGMA foreign_keys = OFF');
        } elseif ($this->driver === 'mysql') {
            $this->pdo->exec("SET NAMES utf8mb4");
        } elseif ($this->driver === 'pgsql') {
            $this->pdo->exec("SET client_encoding TO 'UTF8'");
        }
    }

    public function migrate(): void
    {
        // tabulky se vytvoří jen pokud neexistují; data z psp.cz se při každém importu přepíší
        $text = $this->driver === 'sqlite' ? 'TEXT' : 'TEXT';
        $key = $this->driver === 'sqlite' ? 'TEXT' : 'VARCHAR(190)';
        $suffix = $this->driver !== 'mysql' ? '' : ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_czech_ci';

        $this->pdo->exec("CREATE TABLE IF NOT EXISTS tisky (
            id_tisk INTEGER PRIMARY KEY,
            ct INTEGER NOT NULL,
            druh INTEGER NOT NULL,
            typ_navrhu VARCHAR(20) NOT NULL,
            navrhovatel VARCHAR(255),
            nazev VARCHAR(255),
            uplny_nazev $text,
            predlozeno VARCHAR(19),
            faze VARCHAR(60),
            posledni VARCHAR(19),
            p90 INTEGER NOT NULL DEFAULT 0,
            p90ok INTEGER NOT NULL DEFAULT 0,
            zl INTEGER NOT NULL DEFAULT 0,
            pn_count INTEGER NOT NULL DEFAULT 0,
            pn_last VARCHAR(19),
            zakony $text,
            sbirka VARCHAR(20),
            score INTEGER NOT NULL DEFAULT 0,
            reasons $text
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS kroky (
            id_hist INTEGER PRIMARY KEY,
            id_tisk INTEGER NOT NULL,
            ct INTEGER NOT NULL,
            datum VARCHAR(19),
            typ_stavu INTEGER,
            stav VARCHAR(120),
            akce_id INTEGER,
            akce VARCHAR(120)
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS pn (
            id_dokument INTEGER PRIMARY KEY,
            cislo INTEGER NOT NULL,
            ct INTEGER NOT NULL,
            id_osoba INTEGER,
            osoba VARCHAR(120),
            cas VARCHAR(19)
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS pn_seen (
            id_dokument INTEGER PRIMARY KEY
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS alerts (
            alert_key $key PRIMARY KEY,
            kind VARCHAR(20) NOT NULL,
            ct INTEGER NOT NULL,
            event_at VARCHAR(19) NOT NULL,
            created_at VARCHAR(19) NOT NULL,
            title VARCHAR(255) NOT NULL,
            body $text NOT NULL,
            watched INTEGER NOT NULL DEFAULT 0,
            posted INTEGER NOT NULL DEFAULT 0,
            score INTEGER NOT NULL DEFAULT 0
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS pn_text (
            id_dokument INTEGER PRIMARY KEY,
            ct INTEGER NOT NULL,
            cislo INTEGER NOT NULL,
            docx_url VARCHAR(255),
            text $text,
            chars INTEGER NOT NULL DEFAULT 0,
            laws $text,
            signals $text,
            rule_score INTEGER NOT NULL DEFAULT 0,
            ai_json $text,
            ai_score INTEGER,
            fetched_at VARCHAR(19),
            ai_at VARCHAR(19),
            error $text
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS tisk_text (
            ct INTEGER PRIMARY KEY,
            docx_url VARCHAR(255),
            text $text,
            chars INTEGER NOT NULL DEFAULT 0,
            signals $text,
            ai_json $text,
            ai_score INTEGER,
            fetched_at VARCHAR(19),
            ai_at VARCHAR(19),
            error $text
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS post_log (
            alert_key $key NOT NULL,
            network VARCHAR(20) NOT NULL,
            status VARCHAR(10) NOT NULL,
            at VARCHAR(19) NOT NULL,
            error $text
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS meta (
            k $key PRIMARY KEY,
            v $text
        )$suffix");

        // doplnění sloupců do databází založených starší verzí
        $this->addColumn('tisky', 'score', 'INTEGER NOT NULL DEFAULT 0');
        $this->addColumn('tisky', 'reasons', $text);
        $this->addColumn('alerts', 'score', 'INTEGER NOT NULL DEFAULT 0');
        // model, který rozbor naposledy udělal (druhé kolo silnějším modelem se pozná podle něj)
        $this->addColumn('tisk_text', 'ai_model', 'VARCHAR(60)');
        $this->addColumn('pn_text', 'ai_model', 'VARCHAR(60)');

        $this->createIndex('kroky', 'idx_kroky_tisk', 'id_tisk');
        $this->createIndex('pn_text', 'idx_pn_text_ct', 'ct');
        $this->createIndex('pn', 'idx_pn_ct', 'ct');
        $this->createIndex('alerts', 'idx_alerts_event', 'event_at');
        $this->createIndex('tisky', 'idx_tisky_ct', 'ct');
    }

    private function addColumn(string $table, string $column, string $definition): void
    {
        // ALTER TABLE jen pokud sloupec chybí (funguje v SQLite i MySQL)
        $columns = match ($this->driver) {
            'sqlite' => array_column($this->all("PRAGMA table_info($table)"), 'name'),
            'pgsql' => array_column($this->all(
                'SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ?',
                [$table]
            ), 'column_name'),
            default => array_column($this->all("SHOW COLUMNS FROM $table"), 'Field'),
        };
        if (!in_array($column, $columns, true)) {
            $this->pdo->exec("ALTER TABLE $table ADD COLUMN $column $definition");
        }
    }

    private function createIndex(string $table, string $name, string $column): void
    {
        // MySQL nezná CREATE INDEX IF NOT EXISTS, proto chybu „už existuje“ ignorujeme
        try {
            $sql = $this->driver !== 'mysql'
                ? "CREATE INDEX IF NOT EXISTS $name ON $table ($column)"
                : "CREATE INDEX $name ON $table ($column)";
            $this->pdo->exec($sql);
        } catch (\PDOException) {
        }
    }

    /** Operátor pro hledání bez ohledu na velikost písmen (PostgreSQL má LIKE citlivé). */
    public function ilike(): string
    {
        return $this->driver === 'pgsql' ? 'ILIKE' : 'LIKE';
    }

    /** Převod čísla na text pro porovnání s řetězcem (CAST AS CHAR je v PostgreSQL char(1)). */
    public function castText(string $expr): string
    {
        return $this->driver === 'mysql' ? "CAST($expr AS CHAR)" : "CAST($expr AS VARCHAR)";
    }

    public function run(string $sql, array $params = []): PDOStatement
    {
        $stmt = $this->pdo->prepare($sql);
        $stmt->execute($params);
        return $stmt;
    }

    public function all(string $sql, array $params = []): array
    {
        return $this->run($sql, $params)->fetchAll();
    }

    public function one(string $sql, array $params = []): ?array
    {
        $row = $this->run($sql, $params)->fetch();
        return $row === false ? null : $row;
    }

    public function value(string $sql, array $params = []): mixed
    {
        $value = $this->run($sql, $params)->fetchColumn();
        return $value === false ? null : $value;
    }

    public function insertMany(string $table, array $columns, array $rows): void
    {
        // dávkové vkládání v transakci volajícího, po 200 řádcích kvůli limitům parametrů
        if ($rows === []) {
            return;
        }
        $placeholders = '(' . implode(',', array_fill(0, count($columns), '?')) . ')';
        foreach (array_chunk($rows, 200) as $chunk) {
            $sql = "INSERT INTO $table (" . implode(',', $columns) . ') VALUES '
                . implode(',', array_fill(0, count($chunk), $placeholders));
            $params = [];
            foreach ($chunk as $row) {
                foreach ($columns as $column) {
                    $params[] = $row[$column] ?? null;
                }
            }
            $this->run($sql, $params);
        }
    }

    public function getMeta(string $key, ?string $default = null): ?string
    {
        $value = $this->value('SELECT v FROM meta WHERE k = ?', [$key]);
        return $value === null ? $default : (string)$value;
    }

    public function setMeta(string $key, string $value): void
    {
        $this->run('DELETE FROM meta WHERE k = ?', [$key]);
        $this->run('INSERT INTO meta (k, v) VALUES (?, ?)', [$key, $value]);
    }
}
