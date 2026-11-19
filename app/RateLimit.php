<?php





declare(strict_types=1);

namespace Hlidac;

final class RateLimit
{
    /**
     * Započítá dotaz. Vrací null, když je v limitu, jinak počet sekund do konce okna (Retry-After).
     */
    public static function hit(string $bucket, int $limit, int $window = 60, ?string $ip = null, ?string $dir = null, bool $count = true): ?int
    {
        $ip ??= (string)($_SERVER['REMOTE_ADDR'] ?? '');
        if ($limit <= 0 || $ip === '' || PHP_SAPI === 'cli' && $dir === null) {
            return null;
        }
        $dir ??= hlidacDataPath('cache/ratelimit');
        if (!is_dir($dir) && !@mkdir($dir, 0775, true) && !is_dir($dir)) {
            return null;
        }

        if (str_contains($ip, ':')) {
            $packed = @inet_pton($ip);
            if ($packed !== false) {
                $ip = bin2hex(substr($packed, 0, 8)) . '::/64';
            }
        }
        $salt = (string)hlidacConfig('cron_token', '') . APP_ROOT;
        $path = $dir . '/' . $bucket . '_' . substr(hash('sha256', $salt . '|' . $ip), 0, 32);
        $fh = @fopen($path, 'c+');
        if ($fh === false) {
            return null;
        }
        try {
            flock($fh, LOCK_EX);
            $now = time();
            $state = json_decode((string)stream_get_contents($fh), true);
            if (!is_array($state) || (int)($state['start'] ?? 0) <= $now - $window) {
                $state = ['start' => $now, 'count' => 0];
            }
            if ($count) {
                $state['count'] = (int)$state['count'] + 1;
                ftruncate($fh, 0);
                rewind($fh);
                fwrite($fh, json_encode($state));
                $over = $state['count'] > $limit ? max(1, (int)$state['start'] + $window - $now) : null;
            } else {

                $over = $state['count'] >= $limit ? max(1, (int)$state['start'] + $window - $now) : null;
            }
        } finally {
            flock($fh, LOCK_UN);
            fclose($fh);
        }
        if (random_int(1, 200) === 1) {
            self::cleanup($dir);
        }
        return $over;
    }

    public static function limit(string $bucket, int $default): int
    {
        return (int)(hlidacConfig('rate_limit', [])[$bucket] ?? $default);
    }

    private static function cleanup(string $dir): void
    {

        foreach (glob($dir . '/*') ?: [] as $file) {
            if (@filemtime($file) < time() - 3600) {
                @unlink($file);
            }
        }
    }
}
