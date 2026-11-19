<?php

$connections = [
    'sqlite' => [
        'driver' => 'sqlite', 'database' => env('DB_DATABASE', base_path('data/hlidac.sqlite')),
        'prefix' => '', 'foreign_key_constraints' => false, 'busy_timeout' => 5000,
        'journal_mode' => 'WAL',
    ],
    'pgsql' => [
        'driver' => 'pgsql', 'host' => env('DB_HOST', '127.0.0.1'), 'port' => env('DB_PORT', 5432),
        'database' => env('DB_DATABASE', 'hlidac'), 'username' => env('DB_USERNAME', 'hlidac'),
        'password' => env('DB_PASSWORD', ''), 'charset' => 'utf8', 'prefix' => '',
        'search_path' => 'public', 'sslmode' => env('DB_SSLMODE', 'prefer'),
    ],
    'mysql' => [
        'driver' => 'mysql', 'host' => env('DB_HOST', '127.0.0.1'), 'port' => env('DB_PORT', 3306),
        'database' => env('DB_DATABASE', 'hlidac'), 'username' => env('DB_USERNAME', 'hlidac'),
        'password' => env('DB_PASSWORD', ''), 'unix_socket' => env('DB_SOCKET', ''),
        'charset' => 'utf8mb4', 'collation' => 'utf8mb4_czech_ci', 'prefix' => '',
        'strict' => true,
    ],
];
$default = env('DB_CONNECTION', 'sqlite');

$path = env('HLIDAC_CONFIG', base_path('config.php'));
$legacy = is_file($path) ? require $path : [];
if (env('DB_CONNECTION') === null && isset($legacy['db_dsn'])) {
    [$driver, $dsn] = explode(':', $legacy['db_dsn'], 2);
    if (!isset($connections[$driver])) {
        throw new RuntimeException('Nepodporovaný databázový ovladač: ' . $driver);
    }
    $default = $driver;
    if ($driver === 'sqlite') {
        $connections[$driver]['database'] = $dsn;
    } else {
        foreach (explode(';', $dsn) as $part) {
            if (!str_contains($part, '=')) {
                continue;
            }
            [$key, $value] = explode('=', $part, 2);
            $key = $key === 'dbname' ? 'database' : $key;
            $connections[$driver][$key] = $value;
        }
        $connections[$driver]['username'] = $legacy['db_user'] ?? null;
        $connections[$driver]['password'] = $legacy['db_pass'] ?? null;
    }
}
return [
    'default' => $default,
    'connections' => $connections,
    'migrations' => ['table' => 'migrations', 'update_date_on_publish' => true],
];
