<?php
// Výdej úloh generátoru Reels pro worker na PC (tabulky reel_jobs, reel_queue, reel_state vlastní služba reels/,
// schéma reels/hlidac_reels/schema_reels.sql; PHP je nemigruje). Každá změna stavu je jeden podmíněný UPDATE,
// takže souběžné požadavky workeru nevydají jednu úlohu dvakrát.

declare(strict_types=1);

namespace Hlidac;

final class ReelJobs
{
    /** fresh úloha starší než tento počet dní se nevydá (expired) */
    public const FRESH_MAX_DAYS = 7;
    /** lhůta vydání po claim (lease_until): bez průběhu nebo výsledku se úloha po tolika minutách vrátí do fronty */
    public const CLAIM_TIMEOUT_MIN = 60;
    /** lhůta vydání po každém hlášení průběhu (progress) */
    public const PROGRESS_LEASE_MIN = 15;
    /** propadlé vydání se vrátí do fronty s tímto odkladem (minuty) */
    public const LEASE_RETRY_MIN = 10;
    /** úloha bez rozboru AI se zkusí znovu za tolik minut */
    public const AI_RETRY_MIN = 30;
    /** na rozbor AI úloha čeká nejvýš tolik hodin od založení, pak failed (data) */
    public const AI_MAX_HOURS = 6;
    /** worker hlásí průběh po tolika sekundách (posílá se v odpovědi claim) */
    public const PROGRESS_INTERVAL_S = 120;
    /** po tolika pokusech úloha skončí jako failed */
    public const MAX_ATTEMPTS = 3;
    /** po tolika návratech do fronty kvůli prostředí (fail env/infra) úloha skončí jako failed */
    public const ENV_MAX_RETRIES = 5;
    /** odklad po fail env/infra v sekundách: výchozí, když worker retry_after_s nepošle, a povolený rozsah */
    public const RETRY_DEFAULT_S = 900;
    public const RETRY_MIN_S = 60;
    public const RETRY_MAX_S = 21600;
    /** fáze úlohy na PC, které worker hlásí v progress */
    public const STAGES = ['model', 'kontrola', 'oprava', 'render', 'upload'];
    /** druhy chyb, které posílá worker (data a lease zapisuje jen server) */
    public const FAIL_KINDS = ['content', 'timeout', 'env', 'infra'];
    /** důvody pozastavení workeru v heartbeat */
    public const PAUSE_REASONS = ['limit', 'login', 'mcp', 'nastroje', 'env'];
    /** pořadí výdeje podle zdroje úlohy, uvnitř podle created_at */
    /** Nejvýš tolik už vydaných videí k tisku dostane generátor v claim (pole published). */
    private const PUBLISHED_LIMIT = 10;
    private const SOURCE_ORDER = "CASE source WHEN 'manual' THEN 0 WHEN 'auto_pick' THEN 1 ELSE 2 END";

    /** @var callable(): int */
    private $now;

    /** Počet INSERTů do reel_events, které databáze odmítla kvůli klíči dedupe (souběh; pro test). */
    public int $dedupeConflicts = 0;

    public function __construct(private Db $db, private array $config, ?callable $now = null)
    {
        $this->now = $now ?? fn(): int => time();
    }

    /**
     * Čas pro uložení a porovnání se sloupci časů. SQLite má časy jako text 'Y-m-d H:i:s' (řadí se správně
     * jako řetězec); PostgreSQL má timestamptz, proto dostane i posun časového pásma, jinak by text vyložil
     * v pásmu spojení (v produkci UTC) a 60 minut by se posunulo o hodinu či dvě.
     */
    public function stamp(int $ts): string
    {
        return date($this->db->driver === 'pgsql' ? 'Y-m-d H:i:sP' : 'Y-m-d H:i:s', $ts);
    }

    /**
     * Vydá další úlohu workeru, nebo null, když ve frontě nic není. Nejdřív uklidí propadlá vydání a prošlé
     * fresh úlohy, pak bere frontu bez odkladu (not_before) v pořadí manual, auto_pick, fresh. Úloha bez rozboru
     * AI počká (wait_reason ai_title), dokud rozbor nedorazí, nejvýš AI_MAX_HOURS od založení.
     */
    public function claim(): ?array
    {
        $now = ($this->now)();
        $stampNow = $this->stamp($now);

        // 1. propadlá vydání: lease_until v minulosti, starý worker bez lease_until podle claimed_at + 60 min.
        //    Po řádcích (podmíněný UPDATE s tokenem), aby šlo po každé změně zapsat událost. Podmínka propadnutí
        //    platí i v UPDATE: průběh, který mezi SELECT a UPDATE vydání prodloužil, se nepřepíše.
        $staleCond = "status = 'claimed' AND (lease_until < ? OR (lease_until IS NULL AND claimed_at < ?))";
        $staleParams = [$stampNow, $this->stamp($now - self::CLAIM_TIMEOUT_MIN * 60)];
        $stale = $this->db->all("SELECT id, claim_token, attempts, stage FROM reel_jobs WHERE $staleCond ORDER BY id", $staleParams);
        foreach ($stale as $row) {
            $where = "id = ? AND claim_token = ? AND $staleCond";
            $params = [(int)$row['id'], (string)$row['claim_token'], ...$staleParams];
            $stage = trim((string)($row['stage'] ?? ''));
            if ((int)$row['attempts'] >= self::MAX_ATTEMPTS) {
                // po 3 vydáních failed; poslední fáze z průběhu napoví, kde PC uvázlo
                $error = 'PC nedokončilo úlohu ' . self::MAX_ATTEMPTS . '×' . ($stage !== '' ? " (poslední fáze: $stage)" : '');
                $dead = $this->db->run(
                    "UPDATE reel_jobs SET status = 'failed', error = ?, finished_at = ?, claim_token = NULL, fail_kind = 'lease' WHERE $where",
                    [$error, $stampNow, ...$params]
                );
                if ($dead->rowCount() > 0) {
                    $this->failedEvent((int)$row['id']);
                }
            } else {
                // zpět do fronty s odkladem: PC, které právě spadlo, nedostane úlohu hned znovu
                $next = $now + self::LEASE_RETRY_MIN * 60;
                $back = $this->db->run(
                    "UPDATE reel_jobs SET status = 'queued', claim_token = NULL, claimed_at = NULL, lease_until = NULL, stage = NULL,
                            stage_at = NULL, fail_kind = 'lease', not_before = ?, wait_reason = 'retry' WHERE $where",
                    [$this->stamp($next), ...$params]
                );
                if ($back->rowCount() > 0) {
                    $this->event('lease', (int)$row['id'], [
                        'next_hm' => date('G:i', $next), 'attempts' => (int)$row['attempts'], 'stage' => $stage !== '' ? $stage : null,
                    ]);
                }
            }
        }
        // 2. fresh starší 7 dní se už nevydá; až po kroku 1, aby se nevydala ani prošlá úloha vrácená do fronty
        $expired = $this->db->all(
            "SELECT id FROM reel_jobs WHERE source = 'fresh' AND status = 'queued' AND created_at < ? ORDER BY id",
            [$this->stamp($now - self::FRESH_MAX_DAYS * 86400)]
        );
        foreach ($expired as $row) {
            $gone = $this->db->run(
                "UPDATE reel_jobs SET status = 'expired', finished_at = ? WHERE id = ? AND status = 'queued'",
                [$stampNow, (int)$row['id']]
            );
            if ($gone->rowCount() > 0) {
                $this->event('expired', (int)$row['id'], [], 2, 'expired:' . (int)$row['id']);
            }
        }

        // 3.–4. první úloha ve frontě bez odkladu; při souběhu nebo nepoužitelné úloze se zkusí další
        $queued = $this->db->all(
            "SELECT id, source, ct, format_hint, focus_pn, autopilot FROM reel_jobs
              WHERE status = 'queued' AND (not_before IS NULL OR not_before <= ?) ORDER BY " . self::SOURCE_ORDER . ', created_at, id',
            [$stampNow]
        );
        foreach ($queued as $job) {
            $id = (int)$job['id'];
            $ct = $job['ct'] !== null ? (int)$job['ct'] : null;
            if ($ct === null) {
                if ($job['source'] !== 'auto_pick') {
                    $this->markFailed($id, 'úloha nemá číslo tisku', 'data');
                    continue;
                }
                // souběžný požadavek může stejný nález přiřadit jiné úloze; pak se vybere znovu (ten už je obsazený)
                $assigned = 'conflict';
                for ($try = 0; $try < 3 && $assigned === 'conflict'; $try++) {
                    $ct = $this->pickFinding();
                    $assigned = $ct === null ? 'none' : $this->assignCt($id, $ct);
                }
                if ($assigned === 'none') {
                    $this->markFailed($id, 'není nález bez videa', 'data');
                    continue;
                }
                if ($assigned !== 'ok') {
                    continue; // úlohu mezitím vzal jiný požadavek, nebo se nález nepodařilo přiřadit
                }
            }
            $focus = self::parsePnArray($job['focus_pn'] ?? null);
            $card = $this->cardV2($ct, $focus);
            if ($card === null) {
                $this->deferOrFail($id, $ct, $focus, $now);
                continue;
            }
            $token = bin2hex(random_bytes(16));
            $claimed = $this->db->run(
                "UPDATE reel_jobs SET status = 'claimed', claim_token = ?, claimed_at = ?, lease_until = ?, attempts = attempts + 1,
                        not_before = NULL, wait_reason = NULL, stage = NULL, stage_at = NULL
                  WHERE id = ? AND status = 'queued'",
                [$token, $stampNow, $this->stamp($now + self::CLAIM_TIMEOUT_MIN * 60), $id]
            );
            if ($claimed->rowCount() === 0) {
                continue;
            }
            // pořadí vydání a chyba předchozího pokusu (UPDATE výše error nemění)
            $issued = $this->db->one('SELECT attempts, error FROM reel_jobs WHERE id = ?', [$id]);
            $lastError = $issued !== null && $issued['error'] !== null && $issued['error'] !== '' ? (string)$issued['error'] : null;
            $this->event('claimed', $id, ['attempt' => (int)($issued['attempts'] ?? 0)]);
            return [
                'job_id' => $id,
                'claim_token' => $token,
                'ct' => $ct,
                'source' => (string)$job['source'],
                'format_hint' => $job['format_hint'] !== null && $job['format_hint'] !== '' ? (string)$job['format_hint'] : null,
                'reason_keys' => array_column($card['reasons'], 'key'),
                'has_amendments' => $this->hasAmendments($ct),
                'card_v2' => $card,
                'published' => $this->published($ct),
                // video jen o těchto PN (/pn); null = celý tisk. Starší worker pole ignoruje.
                'focus_pn' => $focus,
                'focus_pn_info' => $focus !== null ? $this->pnInfo($ct, $focus) : [],
                // úloha autopilota (/pn): worker dělá jen 2 varianty (v2 a v3 pn). Starší worker pole ignoruje.
                'autopilot' => self::isTrue($job['autopilot'] ?? false),
                // protokol F3: kolikáté vydání, chyba minulého pokusu, lhůta vydání a interval hlášení průběhu.
                // Starší worker pole ignoruje.
                'attempt' => (int)($issued['attempts'] ?? 0),
                'last_error' => $lastError,
                'lease_s' => self::CLAIM_TIMEOUT_MIN * 60,
                'progress_s' => self::PROGRESS_INTERVAL_S,
            ];
        }
        return null;
    }

    /** Logická hodnota z DB: PostgreSQL vrací bool, SQLite a MySQL 0/1 (i jako text), PostgreSQL jako text 't'. */
    private static function isTrue(mixed $value): bool
    {
        return in_array($value, [true, 1, '1', 't', 'true'], true);
    }

    /**
     * Sloupec focus_pn: PostgreSQL integer[] přijde z PDO jako text '{2376,2377}' (v testech nad SQLite stejný
     * text). Vrací seřazená čísla, nebo null (NULL, prázdné nebo neplatné pole = celý tisk).
     */
    public static function parsePnArray(mixed $value): ?array
    {
        if (!is_string($value) || !preg_match('~^\{\s*(\d+(?:\s*,\s*\d+)*)\s*\}$~', trim($value), $m)) {
            return null;
        }
        $nums = array_values(array_unique(array_map('intval', explode(',', $m[1]))));
        sort($nums);
        return $nums;
    }

    /**
     * Údaje vybraných PN tisku pro zadání generátoru: číslo, autor, čas podání, titulek rozboru AI, odkaz na psp.cz,
     * id osoby autora, klub autora ke dni podání (pn.klub z Importeru v podobě Clubs::label; bez údaje null, nic
     * se neodhaduje), den, ke kterému klub platí, a zda je stažený text PN. Tvar drží tests/contract/focus-pn-info.json
     * (čte ho i test generátoru v Pythonu). Starší worker nová pole ignoruje.
     */
    private function pnInfo(int $ct, array $pns): array
    {
        $obdobi = (new Repo($this->db))->obdobi();
        $marks = implode(', ', array_fill(0, count($pns), '?'));
        $rows = $this->db->all(
            "SELECT p.cislo, p.osoba, p.cas, p.id_osoba, p.klub, x.ai_json,
                    CASE WHEN x.text IS NOT NULL AND x.text <> '' THEN 1 ELSE 0 END AS has_text
               FROM pn p LEFT JOIN pn_text x ON x.id_dokument = p.id_dokument
              WHERE p.ct = ? AND p.cislo IN ($marks) ORDER BY p.cislo",
            [$ct, ...$pns]
        );
        return array_map(function (array $r) use ($obdobi): array {
            $ai = json_decode((string)($r['ai_json'] ?? ''), true);
            $title = is_array($ai) ? trim((string)($ai['titulek'] ?? '')) : '';
            $cas = $r['cas'] !== null ? (string)$r['cas'] : null;
            $club = Clubs::label($r['klub'] !== null ? (string)$r['klub'] : null);
            return [
                'cislo' => (int)$r['cislo'],
                'osoba' => $r['osoba'] !== null ? (string)$r['osoba'] : null,
                'cas' => $cas,
                'titulek' => $title !== '' ? $title : null,
                'url_psp' => View::pspSdUrl((int)$r['cislo'], $obdobi),
                'id_osoba' => $r['id_osoba'] !== null ? (int)$r['id_osoba'] : null,
                'klub' => $club !== '' ? $club : null,
                'klub_ke_dni' => $club !== '' && $cas !== null ? substr($cas, 0, 10) : null,
                'has_text' => (int)$r['has_text'] === 1,
            ];
        }, $rows);
    }

    /** Platí token pro vydanou úlohu? (po vrácení do fronty nebo novém výdeji starý token neplatí) */
    public function checkClaim(int $jobId, string $token): bool
    {
        $row = $this->db->one('SELECT status, claim_token FROM reel_jobs WHERE id = ?', [$jobId]);
        return $row !== null && $row['status'] === 'claimed' && $token !== ''
            && hash_equals((string)$row['claim_token'], $token);
    }

    /**
     * Úloha hotová: sada variant má set_id = id úlohy. False, když token neplatí. $note = poznámka do sloupce
     * error (např. které varianty nevznikly); úloha je přesto done. $detail = výsledek po variantách
     * ({"variants": [n…], "skipped": [{n, format, errors}]}) do sloupce detail jako JSON.
     */
    public function finish(int $jobId, string $token, ?string $note = null, ?array $detail = null): bool
    {
        if (!$this->checkClaim($jobId, $token)) {
            return false;
        }
        $note = $note !== null && $note !== '' ? mb_substr($note, 0, 2000) : null;
        $json = $detail !== null ? json_encode($detail, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) : false;
        $done = $this->db->run(
            "UPDATE reel_jobs SET status = 'done', finished_at = ?, set_id = id, error = ?, detail = ?
              WHERE id = ? AND status = 'claimed' AND claim_token = ?",
            [$this->stamp(($this->now)()), $note, $json === false ? null : $json, $jobId, $token]
        );
        if ($done->rowCount() === 0) {
            return false;
        }
        $this->event('done', $jobId, [
            'variants' => count((array)($detail['variants'] ?? [])),
            'skipped' => array_values((array)($detail['skipped'] ?? [])),
        ]);
        return true;
    }

    /**
     * Worker hlásí chybu. Vrací nový stav ('queued' nebo 'failed'), nebo 'conflict', když token neplatí (úloha
     * mezitím propadla, vrátila se, skončila nebo ji uživatel zrušil) – nic se nemění. Podle druhu ($kind):
     * - content, timeout: failed hned (rozpočet oprav už vyčerpalo PC);
     * - env, infra: pokus se vrací (attempts − 1, env_retries + 1), úloha čeká retry_after_s (výchozí 900 s,
     *   rozsah 60–21600 s); wait_reason limit (env s důvodem limit), env, nebo retry (infra). Po ENV_MAX_RETRIES
     *   návratech failed;
     * - bez druhu nebo neznámý (starší worker): do 3 pokusů zpět do fronty bez odkladu, potom failed; fail_kind content.
     */
    public function fail(int $jobId, string $token, string $error, ?string $kind = null, ?int $retryAfterS = null, ?string $reason = null): string
    {
        if (!$this->checkClaim($jobId, $token)) {
            return 'conflict';
        }
        $now = ($this->now)();
        $error = mb_substr($error, 0, 2000);
        $kind = in_array($kind, self::FAIL_KINDS, true) ? $kind : null;
        $where = "id = ? AND status = 'claimed' AND claim_token = ?";
        $row = $this->db->one('SELECT attempts, env_retries FROM reel_jobs WHERE id = ?', [$jobId]);
        if ($row === null) {
            return 'conflict';
        }

        if ($kind === 'env' || $kind === 'infra') {
            if ((int)$row['env_retries'] >= self::ENV_MAX_RETRIES) {
                $stmt = $this->db->run(
                    "UPDATE reel_jobs SET status = 'failed', error = ?, finished_at = ?, claim_token = NULL, fail_kind = ? WHERE $where",
                    ['Zadání se ' . self::ENV_MAX_RETRIES . "× vrátilo kvůli prostředí (poslední: $error)", $this->stamp($now), $kind, $jobId, $token]
                );
                return $this->failedResult($stmt, $jobId);
            }
            // chyba prostředí pokus nespotřebuje; úloha počká, ať se PC (limit, přihlášení, síť) vzpamatuje
            $delay = max(self::RETRY_MIN_S, min(self::RETRY_MAX_S, $retryAfterS ?? self::RETRY_DEFAULT_S));
            $wait = $kind === 'infra' ? 'retry' : ($reason === 'limit' ? 'limit' : 'env');
            $stmt = $this->db->run(
                "UPDATE reel_jobs SET status = 'queued', error = ?, fail_kind = ?, claim_token = NULL, claimed_at = NULL,
                        lease_until = NULL, stage = NULL, stage_at = NULL, not_before = ?, wait_reason = ?,
                        attempts = CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END, env_retries = env_retries + 1
                  WHERE $where",
                [$error, $kind, $this->stamp($now + $delay), $wait, $jobId, $token]
            );
            return $this->requeuedResult($stmt, $jobId, $kind, $error, $now + $delay, (int)$row['env_retries'] + 1);
        }

        // content a timeout jsou konečné; bez druhu (starší worker) do MAX_ATTEMPTS pokusů zpět do fronty bez odkladu
        if ($kind !== null || (int)$row['attempts'] >= self::MAX_ATTEMPTS) {
            $stmt = $this->db->run(
                "UPDATE reel_jobs SET status = 'failed', error = ?, finished_at = ?, claim_token = NULL, fail_kind = ? WHERE $where",
                [$error, $this->stamp($now), $kind ?? 'content', $jobId, $token]
            );
            return $this->failedResult($stmt, $jobId);
        }
        $stmt = $this->db->run(
            "UPDATE reel_jobs SET status = 'queued', error = ?, fail_kind = 'content', claim_token = NULL, claimed_at = NULL,
                    lease_until = NULL, stage = NULL, stage_at = NULL
              WHERE $where",
            [$error, $jobId, $token]
        );
        // bez odkladu: událost čas dalšího pokusu nenese (úloha se vydá, jakmile si ji PC vezme)
        return $this->requeuedResult($stmt, $jobId, 'content', $error, null, (int)$row['env_retries']);
    }

    /**
     * Worker se ozval (bot podle toho hlásí, že PC je dlouho offline): reel_state.worker_seen_at. Se stavem
     * ($state = {state: running|paused, reason?, detail?, until?}) navíc reel_state.worker_state jako JSON
     * {"state", "reason" (z PAUSE_REASONS, jinak null), "detail" (nejvýš 200 znaků), "until" (ISO 8601), "at"}.
     * Neplatný stav se nezapíše; bez $state (starší worker) zůstane worker_state, jak byl. Při změně stavu proti
     * uloženému událost (urgency 1): worker_paused (z running i bez uloženého stavu), worker_resumed (paused → running).
     * Opakovaný paused s novým until (každá sonda workeru) událost nepřidá.
     */
    public function heartbeat(?array $state = null): void
    {
        $now = ($this->now)();
        $this->putState('worker_seen_at', date('c', $now));
        if ($state === null || !in_array($state['state'] ?? null, ['running', 'paused'], true)) {
            return;
        }
        $reason = $state['reason'] ?? null;
        $detail = is_string($state['detail'] ?? null) ? trim($state['detail']) : '';
        $until = is_string($state['until'] ?? null) && trim($state['until']) !== '' ? strtotime(trim($state['until'])) : false;
        $stored = [
            'state' => $state['state'],
            'reason' => in_array($reason, self::PAUSE_REASONS, true) ? $reason : null,
            'detail' => $detail !== '' ? mb_substr($detail, 0, 200) : null,
            'until' => $until !== false ? date('c', $until) : null,
            'at' => date('c', $now),
        ];
        $value = json_encode($stored, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);
        if ($value === false) {
            return;
        }
        $before = $this->workerState();
        $this->putState('worker_state', $value);
        if ($stored['state'] === 'paused' && $before !== 'paused') {
            $this->event('worker_paused', null, [
                'reason' => $stored['reason'], 'detail' => $stored['detail'], 'until_hm' => $until !== false ? date('G:i', $until) : null,
            ], 1);
        } elseif ($stored['state'] === 'running' && $before === 'paused') {
            $this->event('worker_resumed', null, [], 1);
        }
    }

    /**
     * Karta pro šablonu v2 (stejný tvar jako data/reels-ukazky/cards-export.php) nebo null, když tisk nemá
     * titulek z rozboru AI. U úlohy k vybraným PN ($focusPn) se titulek tisku bez nálezu (Repo::headline bere jen
     * závažnost ≥ 4 s citací) nahradí rozborem prvního zadaného PN s titulkem, bez ohledu na závažnost. Volitelně typ_navrhu, predlozeno, deadline (zkrácená lhůta pro výbory)
     * a pn (počet PN, autorů a podaných v den konce podrobné rozpravy). timeline se nevyplňuje: popisy kroků
     * v ruční sérii jsou redakční, z tabulky kroky by šly do videa surové názvy stavů.
     */
    public function cardV2(int $ct, ?array $focusPn = null): ?array
    {
        $t = $this->db->one('SELECT * FROM tisky WHERE ct = ?', [$ct]);
        if ($t === null) {
            return null;
        }
        $h = (new Repo($this->db))->headline($ct);
        if ($h === null && $focusPn !== null) {
            $h = $this->pnHeadline($ct, $focusPn);
        }
        if ($h === null) {
            return null;
        }
        $reasons = array_map(
            fn(array $r): array => ['key' => (string)($r['key'] ?? ''), 'label' => (string)($r['label'] ?? ''), 'points' => (int)($r['points'] ?? 0)],
            array_values(array_filter((array)(json_decode((string)$t['reasons'], true) ?: []), 'is_array'))
        );
        // podle bodů sestupně (usort je stabilní, stejné body drží pořadí ze skóre)
        usort($reasons, fn(array $a, array $b): int => $b['points'] <=> $a['points']);

        $card = [
            'ct' => $ct,
            'nazev' => $t['nazev'],
            'faze' => $t['faze'],
            'score' => (int)$t['score'],
            'title' => $h['title'],
            'perex' => $h['perex'],
            'quote' => $h['quote'],
            'quote_source' => $h['source'],
            'verified' => $h['verified'],
            'reasons' => $reasons,
            'url' => rtrim((string)($this->config['site_url'] ?? ''), '/') . '/' . Url::path('tisk', ['ct' => $ct]),
            'data_date' => date('Y-m-d', ($this->now)()),
        ];
        if (($t['typ_navrhu'] ?? '') !== '') {
            $card['typ_navrhu'] = (string)$t['typ_navrhu'];
        }
        if (($t['predlozeno'] ?? '') !== '' && $t['predlozeno'] !== null) {
            $card['predlozeno'] = substr((string)$t['predlozeno'], 0, 10);
        }
        $lhuta = isset($t['lhuta']) ? (int)$t['lhuta'] : null;
        if (Analyzer::committeeCut($lhuta)) {
            $card['deadline'] = ['label' => 'Lhůta pro výbory', 'normal' => Analyzer::LHUTA_VYBORY, 'actual' => $lhuta, 'unit' => 'dní'];
        }
        $pn = $this->db->one('SELECT COUNT(*) AS n, COUNT(DISTINCT osoba) AS authors FROM pn WHERE ct = ?', [$ct]);
        if ($pn !== null && (int)$pn['n'] > 0) {
            $card['pn'] = ['count' => (int)$pn['n'], 'authors' => (int)$pn['authors']];
            // počet pozdních PN z důvodu skóre late_pn („N z M pozměňovacích návrhů podáno v den konce…“)
            foreach ($reasons as $r) {
                if ($r['key'] === 'late_pn' && preg_match('~^(\d+) z \d+ ~u', $r['label'], $m)) {
                    $card['pn']['late_count'] = (int)$m[1];
                    $card['pn']['late_label'] = 'podáno v den konce podrobné rozpravy';
                }
            }
        }
        return $card;
    }

    /**
     * Titulek karty z rozboru AI prvního zadaného PN (podle čísla), které titulek má; citace je první citace
     * rozboru, nebo prázdná. Stejný tvar jako Repo::headline. Null, když žádné zadané PN titulek nemá.
     */
    private function pnHeadline(int $ct, array $pns): ?array
    {
        if ($pns === []) {
            return null;
        }
        $marks = implode(', ', array_fill(0, count($pns), '?'));
        $rows = $this->db->all(
            "SELECT p.cislo, x.ai_json, x.ai_model FROM pn p JOIN pn_text x ON x.id_dokument = p.id_dokument
              WHERE p.ct = ? AND p.cislo IN ($marks) AND x.ai_json IS NOT NULL ORDER BY p.cislo",
            [$ct, ...$pns]
        );
        foreach ($rows as $r) {
            $ai = json_decode((string)$r['ai_json'], true);
            $title = is_array($ai) ? trim((string)($ai['titulek'] ?? '')) : '';
            if ($title === '') {
                continue;
            }
            return [
                'title' => $title,
                'perex' => trim((string)($ai['perex'] ?? '')),
                'quote' => trim((string)(((array)($ai['citace'] ?? []))[0] ?? '')),
                'source' => 'pozměňovací návrh č. ' . (int)$r['cislo'],
                'verified' => (string)$r['ai_model'] === AiReviewer::verifyModel($this->config),
            ];
        }
        return null;
    }

    /**
     * Videa k tisku, která už vyšla nebo se právě publikují (nejnovější první, nejvýš PUBLISHED_LIMIT):
     * generátor z nich pozná, jaký úhel už zazněl, a zvolí jiný.
     */
    private function published(int $ct): array
    {
        $rows = $this->db->all(
            "SELECT format, caption, published_at FROM reel_queue
              WHERE ct = ? AND status IN ('approved', 'publishing', 'published') ORDER BY id DESC LIMIT " . self::PUBLISHED_LIMIT,
            [$ct]
        );
        return array_map(fn(array $r): array => [
            'format' => $r['format'] !== null ? (string)$r['format'] : null,
            'caption' => (string)$r['caption'],
            'published_at' => $r['published_at'] !== null ? (string)$r['published_at'] : null,
        ], $rows);
    }

    /** Text návrhu mění jiný předpis („nahrazuje se“, bez ohledu na velikost písmen) – volba formátu zmena. */
    private function hasAmendments(int $ct): bool
    {
        $text = $this->db->value('SELECT text FROM tisk_text WHERE ct = ?', [$ct]);
        return $text !== null && mb_stripos((string)$text, 'nahrazuje se') !== false;
    }

    /**
     * Pro auto_pick: první hlavní nález (Závažné, nejvýš 60) s titulkem, ke kterému ještě není video
     * ve frontě reel_queue ani úloha mimo failed/expired/cancelled.
     */
    private function pickFinding(): ?int
    {
        $repo = new Repo($this->db);
        $level = (int)Scorer::weights($this->config)['level_major'];
        foreach (ReelsSnapshot::findings($repo, $level) as $t) {
            $ct = (int)$t['ct'];
            if ($this->db->value('SELECT 1 FROM reel_queue WHERE ct = ?', [$ct]) !== null) {
                continue;
            }
            $active = $this->db->value(
                "SELECT 1 FROM reel_jobs WHERE ct = ? AND status NOT IN ('failed', 'expired', 'cancelled')",
                [$ct]
            );
            if ($active === null) {
                return $ct;
            }
        }
        return null;
    }

    /**
     * Přiřadí auto_pick úloze tisk: 'ok', 'taken' (úloha už není queued bez ct) nebo 'conflict', když tisk
     * mezitím dostala jiná aktivní úloha k celému tisku (v PostgreSQL unikátní index reel_jobs_active_ct_pn vyhodí výjimku).
     */
    private function assignCt(int $id, int $ct): string
    {
        try {
            $set = $this->db->run(
                "UPDATE reel_jobs SET ct = ? WHERE id = ? AND status = 'queued' AND ct IS NULL",
                [$ct, $id]
            );
        } catch (\PDOException $e) {
            // 23505 = unique_violation (PostgreSQL), 23000 = porušení omezení (SQLite, MySQL)
            if (in_array((string)$e->getCode(), ['23505', '23000'], true)) {
                return 'conflict';
            }
            throw $e;
        }
        return $set->rowCount() > 0 ? 'ok' : 'taken';
    }

    /** Úloha ve frontě, kterou nejde vydat, skončí jako failed (jen pokud je pořád queued); $kind = fail_kind. */
    private function markFailed(int $id, string $error, ?string $kind = null): void
    {
        $stmt = $this->db->run(
            "UPDATE reel_jobs SET status = 'failed', error = ?, finished_at = ?, fail_kind = ? WHERE id = ? AND status = 'queued'",
            [$error, $this->stamp(($this->now)()), $kind, $id]
        );
        if ($stmt->rowCount() > 0) {
            $this->failedEvent($id);
        }
    }

    /**
     * Úloha bez karty (tisk ani zadané PN nemají titulek z rozboru AI). Když rozbor ještě neproběhl, počká
     * AI_RETRY_MIN minut (wait_reason ai_title), po AI_MAX_HOURS od založení failed. Když rozbor proběhl a titulek
     * nedal (např. závažnost pod 4), čekat nemá smysl: failed hned.
     */
    private function deferOrFail(int $id, int $ct, ?array $focus, int $now): void
    {
        if (!$this->aiPending($ct, $focus)) {
            $this->markFailed($id, $focus === null ? 'tisk nemá titulek z rozboru AI' : 'tisk ani zadané PN nemají titulek z rozboru AI', 'data');
            return;
        }
        $young = $this->db->value(
            'SELECT 1 FROM reel_jobs WHERE id = ? AND created_at >= ?',
            [$id, $this->stamp($now - self::AI_MAX_HOURS * 3600)]
        );
        if ($young !== null) {
            $next = $now + self::AI_RETRY_MIN * 60;
            $wait = $this->db->run(
                "UPDATE reel_jobs SET not_before = ?, wait_reason = 'ai_title' WHERE id = ? AND status = 'queued'",
                [$this->stamp($next), $id]
            );
            if ($wait->rowCount() > 0) {
                // jedna událost na celé čekání (dedupe), další odklady po 30 min nic nepřidají
                $this->event('waiting', $id, ['reason' => 'ai_title', 'next_hm' => date('G:i', $next)], 0, "waiting:$id:ai_title");
            }
            return;
        }
        $this->markFailed($id, ($focus === null ? 'rozbor AI tisku' : 'rozbor AI zadaných PN') . ' nedorazil do ' . self::AI_MAX_HOURS . ' h', 'data');
    }

    /**
     * Rozbor AI ještě neproběhl: u úlohy k PN některé zadané PN nemá řádek pn_text nebo rozbor (ai_json IS NULL;
     * PN, které ještě není v tabulce pn, také), u celého tisku chybí řádek tisk_text nebo rozbor.
     */
    private function aiPending(int $ct, ?array $focus): bool
    {
        if ($focus === null) {
            $bill = $this->db->one('SELECT ai_json FROM tisk_text WHERE ct = ?', [$ct]);
            return $bill === null || $bill['ai_json'] === null;
        }
        if ($focus === []) {
            return false;
        }
        $marks = implode(', ', array_fill(0, count($focus), '?'));
        $done = (int)$this->db->value(
            "SELECT COUNT(DISTINCT p.cislo) FROM pn p JOIN pn_text x ON x.id_dokument = p.id_dokument
              WHERE p.ct = ? AND p.cislo IN ($marks) AND x.ai_json IS NOT NULL",
            [$ct, ...$focus]
        );
        return $done < count(array_unique($focus));
    }

    /**
     * Worker hlásí průběh vydané úlohy: vydání platí PROGRESS_LEASE_MIN minut od teď. Fáze z STAGES se zapíše,
     * čas fáze (stage_at) jen při změně; jiná nebo chybějící fáze vydání jen prodlouží. Vrací 'ok', 'cancelled'
     * (uživatel úlohu zrušil, worker běh ukončí) nebo 'conflict' (token neplatí, úloha patří jinému vydání).
     * $note = poznámka k fázi (nejvýš 120 znaků). Událost stage {stage, note} jen s platnou fází, když se fáze nebo
     * poznámka liší od poslední události stage tohoto vydání (stageEvent).
     */
    public function progress(int $jobId, string $token, ?string $stage, ?string $note = null): string
    {
        $now = ($this->now)();
        if (!$this->checkClaim($jobId, $token)) {
            return $this->status($jobId) === 'cancelled' ? 'cancelled' : 'conflict';
        }
        $this->heartbeat(); // hlášení průběhu = worker se ozval (worker_seen_at), stav workeru se nemění
        $stage = in_array($stage, self::STAGES, true) ? $stage : null;
        $note = $note !== null && trim($note) !== '' ? mb_substr(trim($note), 0, 120) : null;
        $lease = $this->stamp($now + self::PROGRESS_LEASE_MIN * 60);
        $where = "id = ? AND status = 'claimed' AND claim_token = ?";
        if ($stage !== null) {
            $changed = $this->db->run(
                "UPDATE reel_jobs SET lease_until = ?, stage = ?, stage_at = ? WHERE $where AND (stage IS NULL OR stage <> ?)",
                [$lease, $stage, $this->stamp($now), $jobId, $token, $stage]
            );
            if ($changed->rowCount() > 0) {
                $this->stageEvent($jobId, $stage, $note);
                return 'ok';
            }
        }
        $extended = $this->db->run("UPDATE reel_jobs SET lease_until = ? WHERE $where", [$lease, $jobId, $token]);
        // MySQL hlásí 0 řádků i u UPDATE beze změny (dvě hlášení v téže sekundě), proto při 0 ještě kontrola tokenu
        if ($extended->rowCount() > 0 || $this->checkClaim($jobId, $token)) {
            if ($stage !== null) {
                $this->stageEvent($jobId, $stage, $note); // stejná fáze, nová poznámka (např. další varianta)
            }
            return 'ok';
        }
        return $this->status($jobId) === 'cancelled' ? 'cancelled' : 'conflict';
    }

    /** Stav úlohy (queued, claimed, done, failed, expired, cancelled), null = úloha neexistuje. */
    public function status(int $jobId): ?string
    {
        $status = $this->db->value('SELECT status FROM reel_jobs WHERE id = ?', [$jobId]);
        return $status !== null ? (string)$status : null;
    }

    /**
     * Zápis do reel_state bez ON CONFLICT (přenositelnost): když řádek existuje UPDATE, jinak INSERT. Existence
     * dotazem, ne podle rowCount: MySQL po UPDATE se stejnou hodnotou hlásí 0 řádků (viz ReelsSnapshot::write).
     */
    private function putState(string $name, string $value): void
    {
        $key = $this->db->driver === 'mysql' ? '`key`' : 'key';
        if ($this->db->value("SELECT 1 FROM reel_state WHERE $key = ?", [$name]) !== null) {
            $this->db->run("UPDATE reel_state SET value = ? WHERE $key = ?", [$value, $name]);
        } else {
            $this->db->run("INSERT INTO reel_state ($key, value) VALUES (?, ?)", [$name, $value]);
        }
    }

    /** Uložený stav workeru (running, paused) z reel_state.worker_state, null = zatím žádný nebo nečitelný. */
    private function workerState(): ?string
    {
        $key = $this->db->driver === 'mysql' ? '`key`' : 'key';
        $value = $this->db->value("SELECT value FROM reel_state WHERE $key = ?", ['worker_state']);
        $data = $value !== null ? json_decode((string)$value, true) : null;
        return is_array($data) && is_string($data['state'] ?? null) ? $data['state'] : null;
    }

    /**
     * Výsledek fail() po UPDATE do failed: 'failed' a událost failed, nebo 'conflict', když UPDATE nic nezměnil
     * (token mezitím přestal platit).
     */
    private function failedResult(\PDOStatement $stmt, int $jobId): string
    {
        if ($stmt->rowCount() === 0) {
            return 'conflict';
        }
        $this->failedEvent($jobId);
        return 'failed';
    }

    /**
     * Výsledek fail() po UPDATE zpět do fronty: 'queued' a událost requeued, jinak 'conflict'. Další pokus v $nextTs
     * (next_hm); null = bez odkladu, událost next_hm nemá.
     */
    private function requeuedResult(\PDOStatement $stmt, int $jobId, string $kind, string $error, ?int $nextTs, int $envRetries): string
    {
        if ($stmt->rowCount() === 0) {
            return 'conflict';
        }
        $detail = ['kind' => $kind, 'error' => $error];
        if ($nextTs !== null) {
            $detail['next_hm'] = date('G:i', $nextTs);
        }
        $this->event('requeued', $jobId, $detail + ['env_retries' => $envRetries]);
        return 'queued';
    }

    /**
     * Událost failed podle řádku úlohy (volá se hned po přechodu do failed). Vyžádaná úloha (manual, auto_pick)
     * urgency 1 = zpráva hned, automatická (fresh) 2 = denní souhrn. Klíč failed:<id> je stejný, jakým bot převezme
     * selhání bez události (events.adopt_failed), takže zpráva o selhání vznikne jen jednou.
     */
    private function failedEvent(int $id): void
    {
        $row = $this->db->one('SELECT source, error, fail_kind, attempts, env_retries, stage FROM reel_jobs WHERE id = ?', [$id]);
        if ($row === null) {
            return;
        }
        $stage = trim((string)($row['stage'] ?? ''));
        $this->event('failed', $id, [
            'kind' => $row['fail_kind'] !== null && $row['fail_kind'] !== '' ? (string)$row['fail_kind'] : null,
            'error' => (string)($row['error'] ?? ''),
            'attempts' => (int)$row['attempts'],
            'env_retries' => (int)$row['env_retries'],
            'stage' => $stage !== '' ? $stage : null,
        ], (string)$row['source'] === 'fresh' ? 2 : 1, "failed:$id");
    }

    /**
     * Událost stage, když se fáze nebo poznámka liší od poslední události stage tohoto vydání. Porovnává se s poslední
     * událostí claimed nebo stage úlohy: po novém vydání (claimed) se první fáze zapíše, i když byla stejná jako minule.
     */
    private function stageEvent(int $jobId, string $stage, ?string $note): void
    {
        try {
            $last = $this->db->one(
                "SELECT kind, detail FROM reel_events WHERE job_id = ? AND kind IN ('claimed', 'stage') ORDER BY id DESC LIMIT 1",
                [$jobId]
            );
        } catch (\PDOException $e) {
            // tabulka ještě není (okno nasazení, viz event()): průběh platí, událost se nezapíše
            error_log('reels event: ' . $e->getMessage());
            return;
        }
        if ($last !== null && $last['kind'] === 'stage') {
            $prev = json_decode((string)$last['detail'], true);
            if (is_array($prev) && ($prev['stage'] ?? null) === $stage && ($prev['note'] ?? null) === $note) {
                return;
            }
        }
        $this->event('stage', $jobId, ['stage' => $stage, 'note' => $note]);
    }

    /**
     * Událost do reel_events (historie úlohy a fronta zpráv bota; text zprávy skládá bot). Volá se jen po podmíněném
     * UPDATE, který řádek opravdu změnil. Klíč dedupe, který už v tabulce je (opakované čekání, selhání převzaté
     * botem), se přeskočí bez INSERTu: odmítnutý INSERT by PostgreSQL zapsal do logu jako ERROR (čekání na rozbor AI
     * každých 30 min). Když klíč mezi dotazem a INSERTem zapíše souběžný požadavek nebo bot, odmítnutí se tiše
     * přeskočí a započítá do dedupeConflicts. Jiná chyba (např. tabulka nebo sloupec ještě není: web dostal novou
     * verzi dřív, než bot založil reel_events) jde jen do error_log: přechod úlohy už platí a worker musí dostat
     * odpověď, jinak by ztratil token a úloha by visela do propadnutí vydání. Web zapisuje bez transakce (autocommit),
     * takže odmítnutý INSERT v PostgreSQL neruší další dotazy.
     */
    private function event(string $kind, ?int $jobId, array $detail = [], int $urgency = 0, ?string $dedupe = null): void
    {
        // (object): prázdné pole jako {} (detail je vždy objekt JSON)
        $json = json_encode((object)$detail, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);
        try {
            if ($dedupe !== null && $dedupe !== '' && $this->db->value('SELECT 1 FROM reel_events WHERE dedupe = ?', [$dedupe]) !== null) {
                return;
            }
            $this->db->run(
                'INSERT INTO reel_events (at, kind, job_id, detail, urgency, dedupe) VALUES (?, ?, ?, ?, ?, ?)',
                [$this->stamp(($this->now)()), $kind, $jobId, $json === false ? '{}' : $json, $urgency, $dedupe]
            );
        } catch (\PDOException $e) {
            // 23505 = unique_violation (PostgreSQL), 23000 = porušení omezení (SQLite, MySQL): klíč dedupe mezitím
            // zapsal souběžný požadavek
            if (in_array((string)$e->getCode(), ['23505', '23000'], true)) {
                $this->dedupeConflicts++;
            } else {
                error_log('reels event: ' . $e->getMessage());
            }
        }
    }
}
