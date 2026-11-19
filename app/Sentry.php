<?php
// Odesílač chyb do Sentry bez závislostí: výjimky, varování a fatální chyby, s filtrem citlivých údajů.

declare(strict_types=1);

namespace Hlidac;

use Throwable;

final class Sentry
{
    private const MAX_EVENTS = 20;       // nejvýš událostí na jeden běh procesu
    // sdílený strop přes všechny procesy (web je proces na každý požadavek, free plán má 5 000 událostí měsíčně)
    private const SHARED_MAX_PER_HOUR = 10;  // nejvýš událostí za hodinu (podle hodin) ze všech procesů
    private const SHARED_FP_SECONDS = 3600;  // stejný otisk nejvýš jednou za hodinu napříč procesy
    private const MAX_STRING = 8000;     // delší řetězce se zkrátí
    private const WARNING_MASK = E_WARNING | E_USER_WARNING | E_USER_ERROR | E_RECOVERABLE_ERROR;
    private const FATAL_MASK = E_ERROR | E_PARSE | E_CORE_ERROR | E_COMPILE_ERROR;

    private static bool $enabled = false;
    private static bool $shutdownRegistered = false;
    private static bool $exceptionHandled = false;
    private static ?array $dsn = null;
    private static string $component = '';
    private static string $environment = '';
    private static string $release = '';
    private static int $sent = 0;
    private static bool $failed = false; // po selhání odeslání se pro zbytek procesu už nic neposílá
    private static array $seen = [];
    private static ?string $throttleDir = null; // adresář sdíleného omezení; null nebo '' = jen omezení v procesu
    /** @var callable|null function (string $url, array $headers, string $body): ?bool; false = selhání */
    private static $transport = null;

    public static function init(array $cfg, string $component): void
    {
        $dsn = self::parseDsn((string)($cfg['dsn'] ?? ''));
        if ($dsn === null) {
            self::$enabled = false;
            return;
        }
        self::$dsn = $dsn;
        self::$component = $component;
        self::$environment = (string)($cfg['environment'] ?? 'production');
        self::$release = (string)(getenv('SENTRY_RELEASE') ?: '');
        self::$throttleDir = array_key_exists('throttle_dir', $cfg)
            ? (string)$cfg['throttle_dir']
            : (defined('APP_ROOT') ? APP_ROOT . '/data/cache/sentry' : null);
        self::$enabled = true;

        set_exception_handler(function (Throwable $ex): void {
            self::$exceptionHandled = true;
            self::capture($ex);
            error_log((string)$ex);
            if (self::$component === 'web') {
                http_response_code(500);
            }
            exit(255);
        });
        set_error_handler(function (int $no, string $str, string $file, int $line): bool {
            if (($no & self::WARNING_MASK) !== 0 && (error_reporting() & $no) !== 0) {
                self::message($str, $no === E_USER_ERROR ? 'error' : 'warning', ['file' => self::relPath($file), 'line' => $line]);
            }
            return false; // PHP chybu dál zaloguje
        });
        if (!self::$shutdownRegistered) {
            self::$shutdownRegistered = true;
            register_shutdown_function(function (): void {
                if (!self::$enabled || self::$exceptionHandled) {
                    return;
                }
                $err = error_get_last();
                if ($err !== null && ($err['type'] & self::FATAL_MASK) !== 0) {
                    self::message($err['message'], 'fatal', ['file' => self::relPath($err['file']), 'line' => $err['line']]);
                }
            });
        }
    }

    public static function enabled(): bool
    {
        return self::$enabled;
    }

    public static function parseDsn(string $dsn): ?array
    {
        $p = parse_url(trim($dsn));
        if (!is_array($p) || !in_array($p['scheme'] ?? '', ['http', 'https'], true) || empty($p['host']) || empty($p['user'])) {
            return null;
        }
        $path = trim((string)($p['path'] ?? ''), '/');
        $parts = $path === '' ? [] : explode('/', $path);
        $project = array_pop($parts);
        if ($project === null || !ctype_digit($project)) {
            return null;
        }
        $prefix = $parts ? '/' . implode('/', $parts) : '';
        $port = isset($p['port']) ? ':' . $p['port'] : '';
        return [
            'endpoint' => $p['scheme'] . '://' . $p['host'] . $port . $prefix . '/api/' . $project . '/envelope/',
            'key' => (string)$p['user'],
        ];
    }

    public static function capture(Throwable $ex, array $extra = []): void
    {
        self::send($ex, 'error', $extra, get_class($ex) . '|' . $ex->getFile() . '|' . $ex->getLine());
    }

    public static function message(string $text, string $level = 'error', array $extra = []): void
    {
        self::send($text, $level, $extra, 'msg|' . $text);
    }

    public static function buildEvent(Throwable|string $what, string $level, array $extra): array
    {
        $event = [
            'event_id' => bin2hex(random_bytes(16)),
            'timestamp' => microtime(true),
            'platform' => 'php',
            'level' => $level,
            'logger' => 'hlidac',
            'environment' => self::$environment,
            'tags' => ['component' => self::$component],
        ];
        if (self::$release !== '') {
            $event['release'] = self::$release;
        }
        if ($what instanceof Throwable) {
            $event['exception'] = ['values' => [[
                'type' => get_class($what),
                'value' => self::scrub($what->getMessage()),
                'stacktrace' => ['frames' => self::frames($what)],
            ]]];
        } else {
            $event['message'] = ['formatted' => self::scrub($what)];
        }
        if (self::$component === 'web' && isset($_SERVER['REQUEST_METHOD'])) {
            $uri = (string)($_SERVER['REQUEST_URI'] ?? '/');
            $query = [];
            parse_str((string)parse_url($uri, PHP_URL_QUERY), $query);
            $event['request'] = [
                'method' => (string)$_SERVER['REQUEST_METHOD'],
                'url' => (string)parse_url($uri, PHP_URL_PATH),
                'query_string' => self::scrub($query),
            ];
        }
        if ($extra) {
            $event['extra'] = self::scrub($extra);
        }
        return $event;
    }

    public static function scrub(mixed $data, ?string $key = null): mixed
    {
        // hodnota pod citlivým klíčem se zahodí celá
        if ($key !== null && preg_match('/token|pass|secret|key|cookie|authorization|auth/i', $key)) {
            return '[Filtered]';
        }
        if (is_array($data)) {
            $out = [];
            foreach ($data as $k => $v) {
                $out[$k] = self::scrub($v, is_string($k) ? $k : null);
            }
            return $out;
        }
        if (!is_string($data)) {
            return $data;
        }
        // regexy běží jen nad začátkem textu (obří řetězce by je zbytečně zdržely), konečné zkrácení až po nich
        $long = mb_strlen($data) > self::MAX_STRING;
        $s = $long ? mb_substr($data, 0, 2 * self::MAX_STRING) : $data;
        $s = Http::redact(self::stripPaths($s));
        if ($s === '' && $data !== '') {
            return '[unscrubbed]'; // Http::redact/stripPaths vrací '' při selhání regexu
        }
        $patterns = [
            // hesla v řetězcích připojení (PDO, libpq "host=db password=…")
            '/\b(password|pass|pwd)=\S+/i' => '$1=***',
            '/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/' => '[email]',
            '/(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])/' => '[ip]',
            // IPv6: se zkratkou "::" nebo aspoň 3 dvojtečky mezi hex skupinami (čas 12:30:45 tak zůstane)
            '/(?<![\w:])(?:[0-9a-f]{1,4}:){1,7}:(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){0,6})?(?![\w:])/i' => '[ip]',
            '/(?<![\w:])::(?:[0-9a-f]{1,4}:){0,6}[0-9a-f]{1,4}(?![\w:])/i' => '[ip]',
            '/(?<![\w:])(?:[0-9a-f]{1,4}:){3,7}[0-9a-f]{1,4}(?![\w:])/i' => '[ip]',
        ];
        foreach ($patterns as $rx => $repl) {
            $r = preg_replace($rx, $repl, $s);
            if ($r === null) {
                return '[unscrubbed]'; // selhání regexu (např. neplatné UTF-8): surový text nikdy neodejde
            }
            $s = $r;
        }
        if ($long || mb_strlen($s) > self::MAX_STRING) {
            $s = mb_substr($s, 0, self::MAX_STRING) . '…';
        }
        return $s;
    }

    public static function setTransport(?callable $fn): void
    {
        self::$transport = $fn;
    }

    public static function reset(): void
    {
        self::$sent = 0;
        self::$seen = [];
        self::$exceptionHandled = false;
        self::$failed = false;
    }

    private static function send(Throwable|string $what, string $level, array $extra, string $fingerprint): void
    {
        if (!self::$enabled || self::$dsn === null || self::$failed) {
            return;
        }
        try {
            if (self::$sent >= self::MAX_EVENTS || isset(self::$seen[$fingerprint])) {
                return;
            }
            self::$seen[$fingerprint] = true;
            if (!self::sharedAllow($fingerprint)) {
                return;
            }
            self::$sent++;
            $event = self::buildEvent($what, $level, $extra);
            $body = json_encode(['event_id' => $event['event_id'], 'sent_at' => gmdate('c')], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)
                . "\n" . '{"type":"event"}' . "\n"
                . json_encode($event, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);
            $headers = [
                'Content-Type: application/x-sentry-envelope',
                'X-Sentry-Auth: Sentry sentry_version=7, sentry_client=hlidac/1.0, sentry_key=' . self::$dsn['key'],
            ];
            $fn = self::$transport ?? [self::class, 'curlTransport'];
            // transport vrací false při selhání (síť, HTTP >= 400); výjimka znamená totéž
            if ($fn(self::$dsn['endpoint'], $headers, $body) === false) {
                self::$failed = true;
            }
        } catch (Throwable) {
            self::$failed = true;
            // hlášení chyb nesmí samo shodit aplikaci
        }
    }

    private static function sharedAllow(string $fingerprint): bool
    {
        // sdílené omezení přes soubory: otisk nejvýš jednou za hodinu a strop událostí za hodinu
        // ze všech procesů; když adresář nejde použít, platí jen omezení v procesu (true)
        $dir = self::$throttleDir;
        if ($dir === null || $dir === '') {
            return true;
        }
        $h = false;
        try {
            if (!is_dir($dir) && !@mkdir($dir, 0775, true) && !is_dir($dir)) {
                return true;
            }
            $now = time();
            $fpFile = $dir . '/fp-' . sha1($fingerprint);
            clearstatcache(true, $fpFile);
            $mtime = @filemtime($fpFile);
            if ($mtime !== false && $now - $mtime < self::SHARED_FP_SECONDS) {
                return false;
            }
            $hourFile = $dir . '/hour-' . gmdate('YmdH', $now);
            $isNew = !is_file($hourFile);
            $h = @fopen($hourFile, 'c+');
            if ($h === false) {
                return true;
            }
            // zámek bez čekání, nejvýš 5 pokusů po 1 ms; obsazený zámek = jen omezení v procesu
            $locked = false;
            for ($i = 0; $i < 5 && !$locked; $i++) {
                $locked = flock($h, LOCK_EX | LOCK_NB);
                if (!$locked) {
                    usleep(1000);
                }
            }
            if (!$locked) {
                return true;
            }
            $count = (int)stream_get_contents($h);
            if ($count >= self::SHARED_MAX_PER_HOUR) {
                return false;
            }
            ftruncate($h, 0);
            rewind($h);
            fwrite($h, (string)($count + 1));
            fflush($h);
            @touch($fpFile, $now);
            if ($isNew) {
                self::cleanThrottleDir($dir, $now);
            }
            return true;
        } catch (Throwable) {
            return true;
        } finally {
            if ($h !== false) {
                @flock($h, LOCK_UN);
                @fclose($h);
            }
        }
    }

    private static function cleanThrottleDir(string $dir, int $now): void
    {
        // staré počítadlo hodin (> 2 h) a prošlé otisky (> 1 h) se mažou příležitostně
        foreach ((array)glob($dir . '/hour-*') as $f) {
            if (is_string($f) && $now - (int)@filemtime($f) > 7200) {
                @unlink($f);
            }
        }
        foreach ((array)glob($dir . '/fp-*') as $f) {
            if (is_string($f) && $now - (int)@filemtime($f) > self::SHARED_FP_SECONDS) {
                @unlink($f);
            }
        }
    }

    private static function curlTransport(string $url, array $headers, string $body): bool
    {
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => $body,
            CURLOPT_HTTPHEADER => $headers,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_CONNECTTIMEOUT => 1,
            CURLOPT_TIMEOUT => 2,
        ]);
        $res = curl_exec($ch);
        $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
        return $res !== false && curl_errno($ch) === 0 && $status < 400;
    }

    private static function stripPaths(string $text): string
    {
        // absolutní cesty serveru se v událostech nesmí objevit
        $text = str_replace('\\', '/', $text);
        $prefixes = ['/var/www/html/', '/var/www/'];
        if (defined('APP_ROOT')) {
            $prefixes[] = str_replace('\\', '/', APP_ROOT) . '/';
        }
        foreach ($prefixes as $prefix) {
            // jen celá cesta od začátku (ne uprostřed jiné cesty, např. /app/ v /var/www/html/app/)
            $text = (string)preg_replace('~(?<![\w./-])' . preg_quote($prefix, '~') . '~', '', $text);
        }
        return $text;
    }

    private static function relPath(string $file): string
    {
        return self::stripPaths($file);
    }

    private static function frames(Throwable $ex): array
    {
        // Sentry chce framy od nejstaršího volání; poslední je místo vyhození
        $frames = [];
        foreach (array_reverse($ex->getTrace()) as $t) {
            if (!isset($t['file'])) {
                continue;
            }
            $frames[] = [
                'filename' => self::relPath($t['file']),
                'lineno' => $t['line'] ?? 0,
                'function' => ($t['class'] ?? '') . ($t['type'] ?? '') . ($t['function'] ?? ''),
            ];
        }
        $frames[] = ['filename' => self::relPath($ex->getFile()), 'lineno' => $ex->getLine()];
        return $frames;
    }
}
