<?php
// Test odesílače chyb do Sentry (bez sítě, transport se nahrazuje): php tests/sentry_test.php

declare(strict_types=1);

$tmp = sys_get_temp_dir() . '/hlidac-sentry-test-' . getmypid();
mkdir($tmp, 0775, true);
$base = require dirname(__DIR__) . '/config.example.php';
file_put_contents($tmp . '/config.php', '<?php return ' . var_export($base, true) . ';');
putenv('HLIDAC_CONFIG=' . $tmp . '/config.php');

require dirname(__DIR__) . '/app/bootstrap.php';

use Hlidac\Sentry;

$failures = 0;
function check(string $label, mixed $actual, mixed $expected): void
{
    global $failures;
    $ok = $actual === $expected;
    if (!$ok) {
        $failures++;
    }
    echo ($ok ? '  OK   ' : '  FAIL ') . $label . ($ok ? '' : ' → ' . var_export($actual, true) . ' (čekáno ' . var_export($expected, true) . ')') . "\n";
}

const DSN = 'https://abc123@o1.ingest.de.sentry.io/456';

// po každém bloku vrátíme globální handlery, aby neovlivnily zbytek testu
function cleanup(): void
{
    restore_exception_handler();
    restore_error_handler();
    Sentry::setTransport(null);
    Sentry::reset();
}

echo "1. parseDsn\n";
check('platný DSN', Sentry::parseDsn(DSN), ['endpoint' => 'https://o1.ingest.de.sentry.io/api/456/envelope/', 'key' => 'abc123']);
check('prázdný DSN', Sentry::parseDsn(''), null);
check('nesmysl', Sentry::parseDsn('nesmysl'), null);

echo "2. scrub\n";
check('citlivé klíče', Sentry::scrub(['X-Hlidac-Token' => 'a', 'db_pass' => 'b', 'api_key' => 'c', 'Cookie' => 'd', 'q' => 'x']),
    ['X-Hlidac-Token' => '[Filtered]', 'db_pass' => '[Filtered]', 'api_key' => '[Filtered]', 'Cookie' => '[Filtered]', 'q' => 'x']);
check('e-mail a IP', Sentry::scrub('psal jan.novak@example.cz z 192.168.1.20 a 2001:db8::1'), 'psal [email] z [ip] a [ip]');
check('token v URL', str_contains((string)Sentry::scrub('url ?token=tajne&x=1'), 'token=***'), true);
check('token zmizel', str_contains((string)Sentry::scrub('url ?token=tajne&x=1'), 'tajne'), false);
check('čas beze změny', Sentry::scrub('sync 12:30:45'), 'sync 12:30:45');
check('paragraf beze změny', Sentry::scrub('§ 4.36'), '§ 4.36');
check('verze beze změny', Sentry::scrub('verze 2.3.0'), 'verze 2.3.0');
check('rekurze do polí', Sentry::scrub(['a' => ['b' => 'x@y.cz']]), ['a' => ['b' => '[email]']]);
check('dlouhý řetězec se zkrátí', mb_strlen((string)Sentry::scrub(str_repeat('a', 9000))) <= 8100, true);

echo "3. buildEvent\n";
Sentry::reset();
Sentry::setTransport(function (): void {});
Sentry::init(['dsn' => DSN, 'environment' => 'test'], 'cli');
$ev = Sentry::buildEvent(new RuntimeException('chyba pro a@b.cz'), 'error', []);
check('level', $ev['level'], 'error');
check('platform', $ev['platform'], 'php');
check('typ výjimky', $ev['exception']['values'][0]['type'], 'RuntimeException');
check('hodnota filtrovaná', $ev['exception']['values'][0]['value'], 'chyba pro [email]');
$bad = 0;
foreach ($ev['exception']['values'][0]['stacktrace']['frames'] as $f) {
    $fn = (string)($f['filename'] ?? '');
    if (str_starts_with($fn, '/var/www') || str_starts_with($fn, APP_ROOT)) {
        $bad++;
    }
}
check('filename bez absolutní cesty', $bad, 0);
check('tag component', $ev['tags']['component'], 'cli');
check('event_id', preg_match('/^[0-9a-f]{32}$/', $ev['event_id']), 1);
check('bez server_name a user', isset($ev['server_name']) || isset($ev['user']), false);
$msg = Sentry::buildEvent('text zprávy', 'warning', ['k' => 'v']);
check('zpráva', $msg['message']['formatted'], 'text zprávy');
check('extra', $msg['extra'], ['k' => 'v']);
cleanup();

echo "4. vypnuto při prázdném DSN\n";
$calls = 0;
Sentry::setTransport(function () use (&$calls): void { $calls++; });
Sentry::init(['dsn' => '', 'environment' => 'test'], 'web');
check('enabled', Sentry::enabled(), false);
Sentry::capture(new RuntimeException('x'));
check('transport nezavolán', $calls, 0);
cleanup();

echo "5. deduplikace a limit\n";
$calls = 0;
Sentry::setTransport(function () use (&$calls): void { $calls++; });
Sentry::init(['dsn' => DSN, 'environment' => 'test'], 'cli');
check('enabled', Sentry::enabled(), true);
for ($i = 0; $i < 2; $i++) {
    Sentry::capture(new RuntimeException('stejná')); // stejný řádek
}
check('dvakrát stejná výjimka = 1 odeslání', $calls, 1);
$calls = 0;
Sentry::reset();
for ($i = 0; $i < 25; $i++) {
    Sentry::message('zpráva ' . $i);
}
check('limit 20 událostí', $calls, 20);
cleanup();

echo "6. selhání transportu\n";
Sentry::setTransport(function (): void { throw new RuntimeException('síť spadla'); });
Sentry::init(['dsn' => DSN, 'environment' => 'test'], 'cli');
$thrown = false;
try {
    Sentry::capture(new LogicException('x'));
    Sentry::message('y');
} catch (Throwable) {
    $thrown = true;
}
check('výjimka z transportu se nepropustí', $thrown, false);
cleanup();

echo "7. envelope a hlavička\n";
$captured = [];
Sentry::setTransport(function (string $url, array $headers, string $body) use (&$captured): void {
    $captured = ['url' => $url, 'headers' => $headers, 'body' => $body];
});
Sentry::init(['dsn' => DSN, 'environment' => 'test'], 'cli');
Sentry::message('obálka');
$auth = implode("\n", $captured['headers'] ?? []);
check('url', $captured['url'] ?? null, 'https://o1.ingest.de.sentry.io/api/456/envelope/');
check('sentry_key', str_contains($auth, 'sentry_key=abc123'), true);
check('sentry_version', str_contains($auth, 'sentry_version=7'), true);
$lines = explode("\n", (string)($captured['body'] ?? ''));
$h0 = json_decode($lines[0] ?? '', true);
check('řádek 1 má event_id', is_array($h0) && isset($h0['event_id']), true);
check('řádek 2 typ', $lines[1] ?? null, '{"type":"event"}');
$e = json_decode($lines[2] ?? '', true);
check('řádek 3 událost', $e['message']['formatted'] ?? null, 'obálka');
check('event_id shodné', $e['event_id'] ?? null, $h0['event_id'] ?? 'x');
cleanup();

echo "8. selhání vypne odesílání, cesty v textech
";
foreach (['false' => fn() => false, 'výjimka' => function (): void { throw new RuntimeException('x'); }] as $name => $tr) {
    $calls = 0;
    Sentry::setTransport(function () use (&$calls, $tr) { $calls++; return $tr(); });
    Sentry::init(['dsn' => DSN, 'environment' => 'test'], 'cli');
    Sentry::message('první');
    Sentry::message('druhá');
    Sentry::capture(new RuntimeException('třetí'));
    check("po selhání ($name) se už neodesílá", $calls, 1);
    cleanup();
}
Sentry::setTransport(function (): void {});
Sentry::init(['dsn' => DSN, 'environment' => 'test'], 'cli');
$ev = Sentry::buildEvent(new RuntimeException('open ' . APP_ROOT . '/data/x.sqlite a /var/www/html/app/Db.php'), 'error', []);
check('cesty ve zprávě výjimky', $ev['exception']['values'][0]['value'], 'open data/x.sqlite a app/Db.php');
$ev = Sentry::buildEvent('PDO /var/www/html/data/y.sqlite', 'error', []);
check('cesty ve zprávě', $ev['message']['formatted'], 'PDO data/y.sqlite');
cleanup();

echo $failures === 0 ? "\nVŠE OK\n" : "\nSELHÁNÍ: $failures\n";
exit($failures === 0 ? 0 : 1);
