<?php







declare(strict_types=1);

namespace Hlidac;

final class McpTokens
{
    public const PREFIX = 'web: ';

    private string $table;

    public function __construct(private Db $db, private array $config)
    {

        $this->table = (string)($config['mcp']['token_table'] ?? 'mcp.token');
    }

    public function limits(): array
    {
        $cfg = $this->config['mcp'] ?? [];
        return ['per_ip' => (int)($cfg['tokens_per_ip'] ?? 3), 'per_day' => (int)($cfg['tokens_per_day'] ?? 200)];
    }

    /** Lze tokeny vydávat? (zapnuto a tabulka existuje – jen při PostgreSQL se službou rag/) */
    public function available(): bool
    {
        if (isset($this->config['mcp']['web_tokens']) && !$this->config['mcp']['web_tokens']) {
            return false;
        }
        if ($this->table !== 'mcp.token') {
            return true;
        }
        try {
            return $this->db->driver === 'pgsql' && \Illuminate\Support\Facades\Schema::hasTable('mcp.token');
        } catch (\Throwable) {
            return false;
        }
    }

    /**
     * Vydá token. Vrací ['token' => …] nebo ['error' => čitelný důvod].
     * @param array $form odeslaný formulář (ucel, formular, web = skryté pole)
     */
    public function issue(array $form, string $ip): array
    {
        if (!$this->available()) {
            return ['error' => 'Vydávání tokenů je teď vypnuté.'];
        }
        if (trim((string)($form['web'] ?? '')) !== '') {
            return ['error' => 'Formulář se nepodařilo ověřit. Načtěte stránku znovu.'];
        }
        $check = self::verifyFormToken((string)($form['formular'] ?? ''), $this->secret());
        if ($check !== null) {
            return ['error' => $check];
        }
        $limits = $this->limits();
        if (RateLimit::hit('mcp_token', $limits['per_ip'], 86400, $ip, hlidacDataPath('cache/ratelimit'), false) !== null) {
            return ['error' => 'Z vaší adresy už dnes vzniklo nejvíc tokenů, kolik lze (' . $limits['per_ip'] . '). Zkuste to zítra, nebo použijte některý z nich.'];
        }

        $since = gmdate('Y-m-d H:i:s', time() - 86400) . '+00:00';
        $today = (new \App\Models\McpToken())->setTable($this->table)->newQuery()->where('jmeno', 'like', self::PREFIX . '%')->where('vytvoren', '>=', $since)->count();
        if ($today >= $limits['per_day']) {
            return ['error' => 'Dnešní počet nových tokenů je vyčerpaný. Zkuste to zítra.'];
        }
        $token = self::generate();
        (new \App\Models\McpToken())->setTable($this->table)->newQuery()->create([
            'jmeno' => self::label((string)($form['ucel'] ?? '')), 'hash' => hash('sha256', $token),
            'vytvoren' => gmdate('Y-m-d H:i:s') . '+00:00',
        ]);
        RateLimit::hit('mcp_token', $limits['per_ip'], 86400, $ip, hlidacDataPath('cache/ratelimit'));
        return ['token' => $token];
    }

    /** Stejný tvar jako secrets.token_urlsafe(32) v hlidac_rag.tokens. */
    public static function generate(): string
    {
        return 'hz_' . rtrim(strtr(base64_encode(random_bytes(32)), '+/', '-_'), '=');
    }

    /** Jméno tokenu v tabulce: „web: “ + účel bez řídicích znaků, nejvýš 80 znaků. */
    public static function label(string $purpose): string
    {
        $p = trim((string)preg_replace('~[\p{C}\s]+~u', ' ', $purpose));
        return self::PREFIX . date('Y-m-d') . ($p !== '' ? ' · ' . mb_substr($p, 0, 80) : '');
    }

    /** Podepsaný údaj formuláře: čas vytvoření a HMAC. */
    public function formToken(?int $time = null): string
    {
        $time ??= time();
        return $time . '.' . substr(hash_hmac('sha256', 'mcp-token|' . $time, $this->secret()), 0, 32);
    }

    /** null = v pořádku, jinak čitelná chyba. Formulář musí být starý aspoň 3 s a nejvýš hodinu. */
    public static function verifyFormToken(string $value, string $secret, ?int $now = null): ?string
    {
        $now ??= time();
        if (!preg_match('~^(\d{9,11})\.([0-9a-f]{32})$~', $value, $m)
            || !hash_equals(substr(hash_hmac('sha256', 'mcp-token|' . $m[1], $secret), 0, 32), $m[2])) {
            return 'Formulář se nepodařilo ověřit. Načtěte stránku znovu.';
        }
        $age = $now - (int)$m[1];
        if ($age < 3) {
            return 'Formulář byl odeslán příliš rychle. Zkuste to prosím znovu.';
        }
        if ($age > 3600) {
            return 'Formulář vypršel. Načtěte stránku znovu.';
        }
        return null;
    }

    private function secret(): string
    {

        $base = (string)($this->config['cron_token'] ?? '');
        if ($base === '' || str_starts_with($base, 'ZMENTE')) {
            $base = (string)$this->db->getMeta('form_secret', '');
            if ($base === '') {
                $base = bin2hex(random_bytes(32));
                $this->db->setMeta('form_secret', $base);
            }
        }
        return hash('sha256', 'hlidac-form|' . $base);
    }
}
