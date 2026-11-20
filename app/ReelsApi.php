<?php
// Rozhraní pro worker generátoru Reels na PC (reels_api.php je jen tenký obal kolem handle()).
// Token workeru v hlavičce X-Hlidac-Token (config reels.worker_token), token vydané úlohy v X-Claim-Token.
//   GET  ?action=claim                      → {"job": {...}} nebo {"job": null}
//   PUT  ?action=video&job_id=N&n=1..3      tělo = MP4 (≤ 30 MB) → <inbox>/<job_id>/variant-<n>.mp4
//   POST ?action=result  {"job_id","variants":[{"n","template","format","caption","card"}]} → manifest.json, úloha done
//   POST ?action=fail    {"job_id","error"}  → {"status": "queued"|"failed"}, složka úlohy se smaže
//   POST ?action=heartbeat                  → {"ok": true}
// Mezisložku čte služba reels (bot): přebírá jen složky s manifest.json, který se proto zapisuje jako poslední
// a atomicky (.part + rename). Soubory i složky jsou zapisovatelné pro všechny, protože bot běží pod jiným
// uživatelem než web a po převzetí je maže (mezisložka je mimo kořen webu, jen ve volume obou kontejnerů).

declare(strict_types=1);

namespace Hlidac;

final class ReelsApi
{
    /** největší video, které worker smí nahrát */
    public const MAX_VIDEO_BYTES = 30 * 1024 * 1024;
    /** největší tělo JSON požadavku (popisky a karty variant) */
    public const MAX_JSON_BYTES = 1_000_000;
    /** nejdelší popisek (limit Instagramu 2200 znaků s rezervou na odkaz) */
    public const CAPTION_LIMIT = 2120;
    /** šablony videa a formáty šablony v3 (reels/hlidac_reels/generator/formats.py FORMATS) */
    public const TEMPLATES = ['template_v2', 'template_v3'];
    public const FORMATS = ['cesta', 'pn', 'zmena', 'rozprava'];
    private const CHUNK = 1024 * 1024;

    private ReelJobs $jobs;

    public function __construct(private Db $db, private array $config, ?callable $now = null)
    {
        $this->jobs = new ReelJobs($db, $config, $now);
    }

    /**
     * Zpracuje požadavek workeru; vrací [HTTP status, data JSON]. Video se čte z $input (proud, obal čte
     * php://input po částech), jinak z $body. Hlavičky v libovolné velikosti písmen.
     *
     * @param resource|null $input
     * @return array{0:int,1:array}
     */
    public function handle(string $method, string $action, array $query, string $body, array $headers, $input = null): array
    {
        $headers = array_change_key_case($headers, CASE_LOWER);
        $method = strtoupper($method);

        // po 10 chybných tokenech za 15 minut se adresa dočasně odmítá (hádání tokenu), jako ai_api.php
        $blocked = RateLimit::authBlocked();
        if ($blocked !== null) {
            return [429, ['error' => 'Příliš mnoho chybných pokusů, zkuste to za ' . $blocked . ' s']];
        }
        $expected = (string)($this->config['reels']['worker_token'] ?? '');
        $token = (string)($headers['x-hlidac-token'] ?? '');
        if ($expected === '' || str_starts_with($expected, 'ZMENTE') || !hash_equals($expected, $token)) {
            RateLimit::authFailed();
            return [403, ['error' => 'Neplatný token']];
        }

        $methods = ['claim' => 'GET', 'video' => 'PUT', 'result' => 'POST', 'fail' => 'POST', 'heartbeat' => 'POST'];
        if (!isset($methods[$action]) || $methods[$action] !== $method) {
            return [400, ['error' => 'Neznámá akce']];
        }
        if ($action === 'claim') {
            $job = $this->jobs->claim();
            // zbytky předchozího pokusu (vypršel bez fail) by se jinak mohly dostat do nové sady
            $inbox = $this->inboxDir();
            if ($job !== null && $inbox !== null) {
                self::removeDir($inbox . '/' . (int)$job['job_id']);
            }
            return [200, ['job' => $job]];
        }
        if ($action === 'heartbeat') {
            $this->jobs->heartbeat();
            return [200, ['ok' => true]];
        }

        $inbox = $this->inboxDir();
        if ($inbox === null) {
            return [500, ['error' => 'Mezisložka reels.inbox_dir neexistuje nebo do ní nejde zapisovat']];
        }
        $claim = (string)($headers['x-claim-token'] ?? '');

        if ($action === 'video') {
            $jobId = self::positiveInt($query['job_id'] ?? null);
            $n = self::positiveInt($query['n'] ?? null);
            if ($jobId === null || $n === null || $n > 3) {
                return [400, ['error' => 'Chybí job_id nebo n (1–3)']];
            }
            // nejdřív ohlášená délka, ať se 30+ MB vůbec nečte
            if (isset($headers['content-length']) && (int)$headers['content-length'] > self::MAX_VIDEO_BYTES) {
                return [413, ['error' => 'Video je větší než 30 MB']];
            }
            if (!$this->jobs->checkClaim($jobId, $claim)) {
                return [409, ['error' => 'Úloha už workeru nepatří (claim vypršel nebo skončila)']];
            }
            if ($input === null) {
                $input = fopen('php://memory', 'w+b');
                fwrite($input, $body);
                rewind($input);
            }
            return $this->storeVideo($inbox, $jobId, $n, $input);
        }

        if (strlen($body) > self::MAX_JSON_BYTES) {
            return [413, ['error' => 'Požadavek je příliš velký']];
        }
        $data = json_decode($body, true);
        $jobId = is_array($data) ? self::positiveInt($data['job_id'] ?? null) : null;
        if ($jobId === null) {
            return [400, ['error' => 'Chybí job_id']];
        }
        // ověření claimu před čímkoli dalším: výsledek pro vypršelou úlohu nesmí nic zapsat
        if (!$this->jobs->checkClaim($jobId, $claim)) {
            return [409, ['error' => 'Úloha už workeru nepatří (claim vypršel nebo skončila)']];
        }
        $jobDir = $inbox . '/' . $jobId;

        if ($action === 'fail') {
            $error = is_string($data['error'] ?? null) && $data['error'] !== '' ? $data['error'] : 'Neznámá chyba';
            $status = $this->jobs->fail($jobId, $claim, $error);
            if ($status === 'conflict') {
                return [409, ['error' => 'Úloha už workeru nepatří (claim vypršel nebo skončila)']];
            }
            self::removeDir($jobDir);
            return [200, ['status' => $status]];
        }

        // result
        $variants = $this->validVariants($data['variants'] ?? null, $jobDir);
        if (is_string($variants)) {
            return [400, ['error' => $variants]];
        }
        $job = $this->db->one('SELECT ct, source FROM reel_jobs WHERE id = ?', [$jobId]);
        $manifest = [
            'job_id' => $jobId,
            'ct' => $job !== null && $job['ct'] !== null ? (int)$job['ct'] : null,
            'source' => $job !== null ? (string)$job['source'] : null,
            'variants' => $variants,
        ];
        $json = json_encode($manifest, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
        if ($json === false || !self::writeAtomic($jobDir . '/manifest.json', $json)) {
            return [500, ['error' => 'manifest.json nejde zapsat']];
        }
        if (!$this->jobs->finish($jobId, $claim)) {
            // souběh: claim vypršel mezi kontrolou a dokončením → bot nesmí sadu převzít
            self::removeDir($jobDir);
            return [409, ['error' => 'Úloha už workeru nepatří (claim vypršel nebo skončila)']];
        }
        return [200, ['ok' => true, 'variants' => count($variants)]];
    }

    /**
     * Uloží video z proudu do <inbox>/<job_id>/variant-<n>.mp4 přes .part + rename. Hlídá velikost během
     * kopírování (tělo může být delší, než hlásí Content-Length) a značku MP4 „ftyp“ na offsetu 4.
     *
     * @param resource $input
     * @return array{0:int,1:array}
     */
    public function storeVideo(string $inbox, int $jobId, int $n, $input): array
    {
        $jobDir = $inbox . '/' . $jobId;
        if (!is_dir($jobDir) && !@mkdir($jobDir, 0777) && !is_dir($jobDir)) {
            return [500, ['error' => 'Složku úlohy nejde vytvořit']];
        }
        @chmod($jobDir, 0777); // umask by ubral zápis pro bota (jiný uživatel), který složku po převzetí maže
        $target = $jobDir . '/variant-' . $n . '.mp4';
        $part = $target . '.part';
        $out = @fopen($part, 'wb');
        if ($out === false) {
            return [500, ['error' => 'Video nejde zapsat']];
        }
        $size = 0;
        $head = '';
        $result = null;
        while (!feof($input)) {
            $chunk = fread($input, self::CHUNK);
            if ($chunk === false) {
                $result = [400, ['error' => 'Tělo požadavku nejde přečíst']];
                break;
            }
            if ($chunk === '') {
                continue;
            }
            $size += strlen($chunk);
            if ($size > self::MAX_VIDEO_BYTES) {
                $result = [413, ['error' => 'Video je větší než 30 MB']];
                break;
            }
            if (strlen($head) < 8) {
                $head .= substr($chunk, 0, 8 - strlen($head));
            }
            if (fwrite($out, $chunk) !== strlen($chunk)) {
                $result = [500, ['error' => 'Video nejde zapsat']];
                break;
            }
        }
        fclose($out);
        if ($result === null && (strlen($head) < 8 || substr($head, 4, 4) !== 'ftyp')) {
            $result = [400, ['error' => 'Tělo není video MP4 (chybí ftyp)']];
        }
        if ($result !== null) {
            @unlink($part);
            return $result;
        }
        @chmod($part, 0666);
        if (!@rename($part, $target)) {
            @unlink($part);
            return [500, ['error' => 'Video nejde uložit']];
        }
        return [200, ['ok' => true, 'bytes' => $size]];
    }

    /** Mezisložka z config reels.inbox_dir, nebo null, když chybí nebo do ní nejde zapisovat. */
    private function inboxDir(): ?string
    {
        $dir = rtrim((string)($this->config['reels']['inbox_dir'] ?? ''), '/\\');
        return $dir !== '' && is_dir($dir) && is_writable($dir) ? $dir : null;
    }

    /**
     * Kontrola variant výsledku: 2–3 různá n 1–3, každé s videem na disku, šablonou (v2 bez formátu,
     * v3 s formátem z FORMATS), popiskem do CAPTION_LIMIT a kartou. Vrací pročištěné varianty (seřazené podle n), nebo text chyby.
     */
    private function validVariants(mixed $variants, string $jobDir): array|string
    {
        if (!is_array($variants) || !array_is_list($variants) || count($variants) < 2 || count($variants) > 3) {
            return 'Sada musí mít 2 nebo 3 varianty';
        }
        $out = [];
        foreach ($variants as $v) {
            $n = is_array($v) ? self::positiveInt($v['n'] ?? null) : null;
            if ($n === null || $n > 3 || isset($out[$n])) {
                return 'Varianta musí mít jedinečné n 1–3';
            }
            // stejná smlouva jako ingester bota (reels/hlidac_reels/inbox.py): v2 bez formátu, v3 s formátem z FORMATS
            $template = $v['template'] ?? null;
            $format = $v['format'] ?? null;
            if (!in_array($template, self::TEMPLATES, true)) {
                return "Varianta $n: šablona musí být " . implode(' nebo ', self::TEMPLATES);
            }
            if ($template === 'template_v2' && $format !== null) {
                return "Varianta $n: template_v2 nesmí mít formát";
            }
            if ($template === 'template_v3' && !in_array($format, self::FORMATS, true)) {
                return "Varianta $n: template_v3 potřebuje formát " . implode(', ', self::FORMATS);
            }
            $caption = $v['caption'] ?? null;
            if (!is_string($caption) || trim($caption) === '' || mb_strlen($caption) > self::CAPTION_LIMIT) {
                return "Varianta $n: popisek chybí nebo je delší než " . self::CAPTION_LIMIT . ' znaků';
            }
            if (!is_array($v['card'] ?? null)) {
                return "Varianta $n: chybí karta";
            }
            if (!is_file($jobDir . '/variant-' . $n . '.mp4')) {
                return "Varianta $n: chybí nahrané video";
            }
            $out[$n] = ['n' => $n, 'template' => $template, 'format' => $format, 'caption' => $caption, 'card' => $v['card']];
        }
        ksort($out);
        return array_values($out);
    }

    /** Kladné celé číslo z dotazu nebo JSON (číslo nebo řetězec číslic), jinak null. */
    private static function positiveInt(mixed $value): ?int
    {
        if (is_int($value)) {
            return $value > 0 ? $value : null;
        }
        if (is_string($value) && preg_match('~^[1-9]\d{0,8}$~', $value)) {
            return (int)$value;
        }
        return null;
    }

    /** Zápis přes .part + rename: čtenář nikdy neuvidí napůl zapsaný soubor. */
    private static function writeAtomic(string $path, string $content): bool
    {
        $part = $path . '.part';
        if (@file_put_contents($part, $content) !== strlen($content)) {
            @unlink($part);
            return false;
        }
        @chmod($part, 0666);
        if (!@rename($part, $path)) {
            @unlink($part);
            return false;
        }
        return true;
    }

    /** Smaže složku úlohy (jen soubory v ní, podsložky mezisložka nemá). */
    private static function removeDir(string $dir): void
    {
        if (!is_dir($dir)) {
            return;
        }
        foreach (scandir($dir) ?: [] as $name) {
            if (is_file($dir . '/' . $name)) {
                @unlink($dir . '/' . $name);
            }
        }
        @rmdir($dir);
    }
}
