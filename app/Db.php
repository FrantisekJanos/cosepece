<?php


declare(strict_types=1);

namespace Hlidac;

use PDO;
use PDOStatement;

final class Db
{
    public PDO $pdo;
    public string $driver;

    public function __construct(string|PDO $dsn, ?string $user = null, ?string $pass = null)
    {
        if ($dsn instanceof PDO) {
            $this->pdo = $dsn;
            $this->pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
            $this->driver = (string)$dsn->getAttribute(PDO::ATTR_DRIVER_NAME);
            return;
        }
        if (str_starts_with($dsn, 'sqlite:')) {

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
