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
    /** vydaná úloha bez výsledku se po tolika minutách vrátí do fronty */
    public const CLAIM_TIMEOUT_MIN = 60;
    /** po tolika pokusech úloha skončí jako failed */
    public const MAX_ATTEMPTS = 3;
    /** pořadí výdeje podle zdroje úlohy, uvnitř podle created_at */
    private const SOURCE_ORDER = "CASE source WHEN 'manual' THEN 0 WHEN 'auto_pick' THEN 1 ELSE 2 END";

    /** @var callable(): int */
    private $now;

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
     * Vydá další úlohu workeru, nebo null, když ve frontě nic není. Nejdřív uklidí prošlé fresh úlohy
     * a vydané úlohy bez výsledku, pak bere frontu v pořadí manual, auto_pick, fresh.
     */
    public function claim(): ?array
    {
        $now = ($this->now)();
        $stampNow = $this->stamp($now);

        // 1. vydaná úloha bez výsledku po 60 min: po 3 pokusech failed, jinak zpět do fronty
        $staleBefore = $this->stamp($now - self::CLAIM_TIMEOUT_MIN * 60);
        $this->db->run(
            "UPDATE reel_jobs SET status = 'failed', error = ?, finished_at = ?, claim_token = NULL
              WHERE status = 'claimed' AND claimed_at < ? AND attempts >= ?",
            ['PC nedokončilo úlohu ' . self::MAX_ATTEMPTS . '×', $stampNow, $staleBefore, self::MAX_ATTEMPTS]
        );
        $this->db->run(
            "UPDATE reel_jobs SET status = 'queued', claim_token = NULL, claimed_at = NULL
              WHERE status = 'claimed' AND claimed_at < ? AND attempts < ?",
            [$staleBefore, self::MAX_ATTEMPTS]
        );
        // 2. fresh starší 7 dní se už nevydá; až po kroku 1, aby se nevydala ani prošlá úloha vrácená do fronty
        $this->db->run(
            "UPDATE reel_jobs SET status = 'expired', finished_at = ? WHERE source = 'fresh' AND status = 'queued' AND created_at < ?",
            [$stampNow, $this->stamp($now - self::FRESH_MAX_DAYS * 86400)]
        );

        // 3.–4. první úloha ve frontě; při souběhu nebo nepoužitelné úloze se zkusí další
        $queued = $this->db->all(
            "SELECT id, source, ct, format_hint FROM reel_jobs WHERE status = 'queued' ORDER BY " . self::SOURCE_ORDER . ', created_at, id'
        );
        foreach ($queued as $job) {
            $id = (int)$job['id'];
            $ct = $job['ct'] !== null ? (int)$job['ct'] : null;
            if ($ct === null) {
                if ($job['source'] !== 'auto_pick') {
                    $this->markFailed($id, 'úloha nemá číslo tisku');
                    continue;
                }
                // souběžný požadavek může stejný nález přiřadit jiné úloze; pak se vybere znovu (ten už je obsazený)
                $assigned = 'conflict';
                for ($try = 0; $try < 3 && $assigned === 'conflict'; $try++) {
                    $ct = $this->pickFinding();
                    $assigned = $ct === null ? 'none' : $this->assignCt($id, $ct);
                }
                if ($assigned === 'none') {
                    $this->markFailed($id, 'není nález bez videa');
                    continue;
                }
                if ($assigned !== 'ok') {
                    continue; // úlohu mezitím vzal jiný požadavek, nebo se nález nepodařilo přiřadit
                }
            }
            $card = $this->cardV2($ct);
            if ($card === null) {
                $this->markFailed($id, 'tisk nemá titulek z rozboru AI');
                continue;
            }
            $token = bin2hex(random_bytes(16));
            $claimed = $this->db->run(
                "UPDATE reel_jobs SET status = 'claimed', claim_token = ?, claimed_at = ?, attempts = attempts + 1
                  WHERE id = ? AND status = 'queued'",
                [$token, $stampNow, $id]
            );
            if ($claimed->rowCount() === 0) {
                continue;
            }
            return [
                'job_id' => $id,
                'claim_token' => $token,
                'ct' => $ct,
                'source' => (string)$job['source'],
                'format_hint' => $job['format_hint'] !== null && $job['format_hint'] !== '' ? (string)$job['format_hint'] : null,
                'reason_keys' => array_column($card['reasons'], 'key'),
                'has_amendments' => $this->hasAmendments($ct),
                'card_v2' => $card,
            ];
        }
        return null;
    }

    /** Platí token pro vydanou úlohu? (po vrácení do fronty nebo novém výdeji starý token neplatí) */
    public function checkClaim(int $jobId, string $token): bool
    {
        $row = $this->db->one('SELECT status, claim_token FROM reel_jobs WHERE id = ?', [$jobId]);
        return $row !== null && $row['status'] === 'claimed' && $token !== ''
            && hash_equals((string)$row['claim_token'], $token);
    }

    /** Úloha hotová: sada variant má set_id = id úlohy. False, když token neplatí. */
    public function finish(int $jobId, string $token): bool
    {
        if (!$this->checkClaim($jobId, $token)) {
            return false;
        }
        $done = $this->db->run(
            "UPDATE reel_jobs SET status = 'done', finished_at = ?, set_id = id, error = NULL
              WHERE id = ? AND status = 'claimed' AND claim_token = ?",
            [$this->stamp(($this->now)()), $jobId, $token]
        );
        return $done->rowCount() > 0;
    }

    /**
     * Worker hlásí chybu: do 3 pokusů úloha jde zpět do fronty, potom failed. Vrací nový stav,
     * nebo 'conflict', když token neplatí (úloha mezitím vypršela, vrátila se nebo skončila) – nic se nemění.
     */
    public function fail(int $jobId, string $token, string $error): string
    {
        if (!$this->checkClaim($jobId, $token)) {
            return 'conflict';
        }
        $error = mb_substr($error, 0, 2000);
        $attempts = (int)$this->db->value('SELECT attempts FROM reel_jobs WHERE id = ?', [$jobId]);
        if ($attempts >= self::MAX_ATTEMPTS) {
            $stmt = $this->db->run(
                "UPDATE reel_jobs SET status = 'failed', error = ?, finished_at = ?, claim_token = NULL
                  WHERE id = ? AND status = 'claimed' AND claim_token = ?",
                [$error, $this->stamp(($this->now)()), $jobId, $token]
            );
            $status = 'failed';
        } else {
            $stmt = $this->db->run(
                "UPDATE reel_jobs SET status = 'queued', error = ?, claim_token = NULL, claimed_at = NULL
                  WHERE id = ? AND status = 'claimed' AND claim_token = ?",
                [$error, $jobId, $token]
            );
            $status = 'queued';
        }
        return $stmt->rowCount() > 0 ? $status : 'conflict';
    }

    /** Worker se ozval (bot podle toho hlásí, že PC je dlouho offline). Bez ON CONFLICT: UPDATE, jinak INSERT. */
    public function heartbeat(): void
    {
        $key = $this->db->driver === 'mysql' ? '`key`' : 'key';
        $value = date('c', ($this->now)());
        $updated = $this->db->run("UPDATE reel_state SET value = ? WHERE $key = ?", [$value, 'worker_seen_at']);
        if ($updated->rowCount() === 0) {
            $this->db->run("INSERT INTO reel_state ($key, value) VALUES (?, ?)", ['worker_seen_at', $value]);
        }
    }

    /**
     * Karta pro šablonu v2 (stejný tvar jako data/reels-ukazky/cards-export.php) nebo null, když tisk nemá
     * titulek z rozboru AI. Volitelně typ_navrhu, predlozeno, deadline (zkrácená lhůta pro výbory)
     * a pn (počet PN, autorů a podaných v den konce podrobné rozpravy). timeline se nevyplňuje: popisy kroků
     * v ruční sérii jsou redakční, z tabulky kroky by šly do videa surové názvy stavů.
     */
    public function cardV2(int $ct): ?array
    {
        $t = $this->db->one('SELECT * FROM tisky WHERE ct = ?', [$ct]);
        if ($t === null) {
            return null;
        }
        $h = (new Repo($this->db))->headline($ct);
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
            'url' => rtrim((string)($this->config['site_url'] ?? ''), '/') . '/tisk.php?ct=' . $ct,
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
     * mezitím dostala jiná aktivní úloha (v PostgreSQL unikátní index reel_jobs_active_ct vyhodí výjimku).
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

    /** Úloha ve frontě, kterou nejde vydat, skončí jako failed (jen pokud je pořád queued). */
    private function markFailed(int $id, string $error): void
    {
        $this->db->run(
            "UPDATE reel_jobs SET status = 'failed', error = ?, finished_at = ? WHERE id = ? AND status = 'queued'",
            [$error, $this->stamp(($this->now)()), $id]
        );
    }
}
