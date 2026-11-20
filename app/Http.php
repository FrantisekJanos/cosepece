<?php
// Jednoduchý HTTP klient nad cURL pro volání API sociálních sítí.

declare(strict_types=1);

namespace Hlidac;

use RuntimeException;

final class Http
{
    public static function get(string $url, int $maxBytes = 20_000_000, array $headers = []): string
    {
        // stažení obsahu (HTML, DOCX) s limitem velikosti
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_HTTPHEADER => $headers,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_FOLLOWLOCATION => true,
            CURLOPT_TIMEOUT => 60,
            CURLOPT_CONNECTTIMEOUT => 20,
            CURLOPT_USERAGENT => 'HlidacZakonu/1.0',
            CURLOPT_MAXFILESIZE => $maxBytes,
        ]);
        $body = curl_exec($ch);
        $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $error = curl_error($ch);
        if ($body === false || $status >= 400) {
            throw new RuntimeException(self::redact("HTTP $status při stahování $url: $error"));
        }
        return (string)$body;
    }

    /**
     * @param float[] $retryDelays pauzy (s) před dalšími pokusy při výpadku serveru: chyba spojení nebo HTTP 502, 503, 504
     *     (např. při výměně kontejneru během nasazení); prázdné = jediný pokus
     */
    public static function post(string $url, array $headers, string|array $body, bool $json = true, int $timeout = 30, array $retryDelays = []): array
    {
        $payload = is_array($body) ? ($json ? json_encode($body, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) : http_build_query($body)) : $body;
        if ($json && is_array($body)) {
            $headers[] = 'Content-Type: application/json';
        }
        for ($attempt = 0; ; $attempt++) {
            $ch = curl_init($url);
            curl_setopt_array($ch, [
                CURLOPT_POST => true,
                CURLOPT_POSTFIELDS => $payload,
                CURLOPT_HTTPHEADER => $headers,
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_TIMEOUT => $timeout,
                CURLOPT_USERAGENT => 'HlidacZakonu/1.0',
            ]);
            $response = curl_exec($ch);
            $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
            $error = curl_error($ch);
            if (!self::transient($response === false ? 0 : $status) || !isset($retryDelays[$attempt])) {
                break;
            }
            usleep((int)round(1_000_000 * (float)$retryDelays[$attempt]));
        }
        if ($response === false) {
            throw new RuntimeException(self::redact("HTTP chyba při volání $url: $error"));
        }
        $data = json_decode((string)$response, true);
        if ($status >= 400) {
            throw new RuntimeException(self::redact("HTTP $status z $url: " . mb_substr((string)$response, 0, 300)));
        }
        return is_array($data) ? $data : ['raw' => $response];
    }

    /** Přechodný výpadek, kdy má smysl zkusit to znovu: bez odpovědi (0) nebo 502/503/504 od proxy před webem. */
    public static function transient(int $status): bool
    {
        return in_array($status, [0, 502, 503, 504], true);
    }

    public static function redact(string $text): string
    {
        // tokeny a hesla z URL a odpovědí nesmí skončit v chybách, logu ani v post_log (balík s databází)
        $text = (string)preg_replace('~(api\.telegram\.org/bot)[^/\s]+~i', '$1***', $text);
        $text = (string)preg_replace('~([?&](?:token|access_token|key|api_key|password|secret)=)[^&\s"\']+~i', '$1***', $text);
        $text = (string)preg_replace('#(Bearer\s+)[A-Za-z0-9._~+/=-]+#', '$1***', $text);
        return (string)preg_replace('~("(?:accessJwt|refreshJwt|access_token|token|password)"\s*:\s*")[^"]*~', '$1***', $text);
    }
}
