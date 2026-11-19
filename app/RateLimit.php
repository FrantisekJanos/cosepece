<?php
// Jednoduchý limit počtu dotazů z jedné IP adresy (pevné okno, soubory v data/cache/ratelimit).
// Chrání API, RSS a generování obrázků před přetížením jedním klientem; proti rozsáhlému útoku
// z mnoha adres nepomůže – na to patří ochrana před webem (Cloudflare apod.).
// IP adresa se neukládá, jen její otisk se solí; soubory se po hodině mažou.

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
        $dir ??= APP_ROOT . '/data/cache/ratelimit';
        if (!is_dir($dir) && !@mkdir($dir, 0775, true) && !is_dir($dir)) {
            return null;
        }
        $ip = self::client($ip);
        $salt = (string)config('cron_token', '') . APP_ROOT;
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
            // délka okna v souboru: úklid nesmí smazat záznam s delším oknem (např. den u vydávání tokenů)
            $state['w'] = $window;
            if ($count) {
                $state['count'] = (int)$state['count'] + 1;
                ftruncate($fh, 0);
                rewind($fh);
                fwrite($fh, json_encode($state));
                $over = $state['count'] > $limit ? max(1, (int)$state['start'] + $window - $now) : null;
            } else {
                // jen dotaz, zda je adresa zablokovaná (limit už vyčerpaný)
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

    /**
     * Klient pro počítání limitů: IPv4 adresa, u IPv6 celá síť /56 (domácí přípojky dostávají /56 až /64;
     * jinak by stačilo měnit adresu v rámci vlastní sítě a každá by měla plný limit).
     */
    public static function client(string $ip): string
    {
        if (str_contains($ip, ':')) {
            $packed = @inet_pton($ip);
            if ($packed !== false && strlen($packed) === 16) {
                return bin2hex(substr($packed, 0, 7)) . '::/56';
            }
        }
        return $ip;
    }

    /** Pošle odpověď 429 a ukončí běh (formát podle druhu výstupu). */
    public static function deny(int $retryAfter, bool $json = true): never
    {
        http_response_code(429);
        header('Retry-After: ' . $retryAfter);
        header('Cache-Control: no-store');
        $message = 'Příliš mnoho dotazů z vaší adresy. Zkuste to znovu za ' . $retryAfter . ' s. Limity: ' . siteUrl('data.php#limity');
        if ($json) {
            header('Content-Type: application/json; charset=utf-8');
            echo json_encode(['error' => $message], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES), "\n";
        } else {
            header('Content-Type: text/plain; charset=utf-8');
            echo $message, "\n";
        }
        exit;
    }

    /** Kontrola a případné odmítnutí jedním voláním; limit z config.php → rate_limit.{bucket} (dotazů za minutu). */
    public static function guard(string $bucket, int $default, bool $json = true): void
    {
        $limit = (int)(config('rate_limit', [])[$bucket] ?? $default);
        $retry = self::hit($bucket, $limit);
        if ($retry !== null) {
            self::deny($retry, $json);
        }
    }

    /**
     * Ochrana tokenu před hádáním: po vyčerpání pokusů (výchozí 10 chybných za 15 minut) se adresa
     * odmítne i se správným tokenem, dokud okno neskončí. Volat před kontrolou tokenu, failed() po chybné.
     */
    public static function authBlocked(): ?int
    {
        return self::hit('auth', self::limit('auth', 10), 900, null, null, false);
    }

    public static function authFailed(): void
    {
        self::hit('auth', self::limit('auth', 10), 900);
    }

    public static function limit(string $bucket, int $default): int
    {
        return (int)(config('rate_limit', [])[$bucket] ?? $default);
    }

    /** Úklid prošlých záznamů (volá i synchronizace, aby se adresář nehromadil mezi návštěvami). */
    public static function cleanup(?string $dir = null): void
    {
        // záznamy, jejichž okno skončilo, už nic neomezují (bez údaje o okně: po hodině)
        $dir ??= APP_ROOT . '/data/cache/ratelimit';
        foreach (glob($dir . '/*') ?: [] as $file) {
            if (@filemtime($file) >= time() - 3600) {
                continue;
            }
            $state = json_decode((string)@file_get_contents($file), true);
            $end = is_array($state) ? (int)($state['start'] ?? 0) + (int)($state['w'] ?? 3600) : 0;
            if ($end < time()) {
                @unlink($file);
            }
        }
    }
}
