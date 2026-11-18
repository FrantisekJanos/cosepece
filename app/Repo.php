<?php
// Dotazy pro webové stránky.

declare(strict_types=1);

namespace Hlidac;

final class Repo
{
    public function __construct(private Db $db)
    {
    }

    public function obdobi(): int
    {
        return (int)$this->db->getMeta('obdobi_cislo', '10');
    }

    public function lastSync(): ?string
    {
        return $this->db->getMeta('last_sync');
    }

    public function stats(): array
    {
        $since = date('Y-m-d H:i:s', strtotime('-7 days'));
        $zkraceni = implode(',', Analyzer::AKCE_ZKRACENI);
        $top = $this->db->one(
            'SELECT p.ct, COUNT(*) n, t.nazev FROM pn p LEFT JOIN tisky t ON t.ct = p.ct
             WHERE p.cas >= ? GROUP BY p.ct, t.nazev ORDER BY n DESC LIMIT 1', [$since]);
        $lastCut = $this->db->one(
            "SELECT k.ct, k.datum FROM kroky k WHERE k.akce_id IN ($zkraceni) ORDER BY k.datum DESC, k.id_hist DESC LIMIT 1");
        return [
            'pn_total' => (int)$this->db->value('SELECT COUNT(*) FROM pn'),
            'bills_total' => (int)$this->db->value('SELECT COUNT(*) FROM tisky WHERE druh IN (1, 2)'),
            'bills_with_pn' => (int)$this->db->value('SELECT COUNT(*) FROM tisky WHERE pn_count > 0'),
            'pn_7d' => (int)$this->db->value('SELECT COUNT(*) FROM pn WHERE cas >= ?', [$since]),
            'pn_7d_top' => $top,
            'p90' => (int)$this->db->value('SELECT COUNT(*) FROM tisky WHERE p90 = 1'),
            'p90ok' => (int)$this->db->value('SELECT COUNT(*) FROM tisky WHERE p90ok = 1'),
            'zl' => (int)$this->db->value('SELECT COUNT(*) FROM tisky WHERE zl = 1'),
            'zl_last' => $lastCut,
        ];
    }

    public function alerts(int $limit, int $offset = 0, ?int $ct = null, int $minScore = 0): array
    {
        if ($ct !== null) {
            return $this->db->all('SELECT * FROM alerts WHERE ct = ? ORDER BY event_at DESC LIMIT ' . $limit . ' OFFSET ' . $offset, [$ct]);
        }
        return $this->db->all('SELECT * FROM alerts WHERE score >= ? ORDER BY event_at DESC LIMIT ' . $limit . ' OFFSET ' . $offset, [$minScore]);
    }

    public function alertsCount(int $minScore = 0): int
    {
        return (int)$this->db->value('SELECT COUNT(*) FROM alerts WHERE score >= ?', [$minScore]);
    }

    public function topFindings(int $minScore, int $limit): array
    {
        // hlavní nálezy: nejzávažnější rozpracované tisky (mohou se kdykoli rozhýbat)
        // a uzavřené tisky s aktivitou za posledních 120 dní
        $since = date('Y-m-d', strtotime('-120 days'));
        $rows = $this->db->all(
            "SELECT * FROM tisky WHERE score >= ?
               AND (faze NOT IN ('Vyhlášeno', 'Vzat zpět', 'Zamítnuto') OR posledni >= ?)
             ORDER BY score DESC, posledni DESC LIMIT " . $limit,
            [$minScore, $since]
        );
        foreach ($rows as &$row) {
            $row['flags'] = $this->flags($row);
            $row['reasons_list'] = json_decode((string)$row['reasons'], true) ?: [];
            $row['headline'] = $this->headline((int)$row['ct']);
        }
        return $rows;
    }

    public function headline(int $ct): ?array
    {
        // srozumitelný titulek z rozboru AI: z textu návrhu nebo z PN, podle toho, kde je závažnější nález
        $candidates = [];
        $bill = $this->db->one('SELECT ai_score, ai_json, ai_model FROM tisk_text WHERE ct = ? AND ai_at IS NOT NULL', [$ct]);
        if ($bill !== null) {
            $candidates[] = ['score' => (int)$bill['ai_score'], 'json' => $bill['ai_json'], 'model' => $bill['ai_model'], 'source' => 'text návrhu'];
        }
        $pn = $this->db->one('SELECT x.ai_score, x.ai_json, x.ai_model, x.cislo FROM pn_text x WHERE x.ct = ? AND x.ai_at IS NOT NULL ORDER BY x.ai_score DESC LIMIT 1', [$ct]);
        if ($pn !== null && (int)$pn['ai_score'] >= 4) {
            $candidates[] = ['score' => (int)$pn['ai_score'], 'json' => $pn['ai_json'], 'model' => $pn['ai_model'], 'source' => 'pozměňovací návrh č. ' . (int)$pn['cislo']];
        }
        usort($candidates, fn(array $a, array $b): int => $b['score'] <=> $a['score']);
        foreach ($candidates as $c) {
            $ai = json_decode((string)$c['json'], true) ?: [];
            $title = trim((string)($ai['titulek'] ?? ''));
            if ($title !== '') {
                return [
                    'title' => $title,
                    'perex' => trim((string)($ai['perex'] ?? '')),
                    'quote' => (string)(($ai['citace'] ?? [])[0] ?? ''),
                    'source' => $c['source'],
                    // ověřeno druhým kolem silnějším modelem?
                    'verified' => (string)$c['model'] === AiReviewer::verifyModel(config()),
                ];
            }
        }
        return null;
    }

    public function suppressedCount(int $minScore): int
    {
        return (int)$this->db->value('SELECT COUNT(*) FROM alerts WHERE score < ?', [$minScore]);
    }

    public function pnTexts(int $ct): array
    {
        $out = [];
        foreach ($this->db->all('SELECT id_dokument, rule_score, ai_score, signals, ai_json, error FROM pn_text WHERE ct = ?', [$ct]) as $x) {
            $x['signals'] = json_decode((string)$x['signals'], true) ?: [];
            $x['ai'] = json_decode((string)$x['ai_json'], true) ?: null;
            $out[(int)$x['id_dokument']] = $x;
        }
        return $out;
    }

    public function billText(int $ct): ?array
    {
        // stažený text návrhu zákona s pravidlovými signály a rozborem AI
        $x = $this->db->one('SELECT ct, docx_url, chars, signals, ai_score, ai_json, ai_at, fetched_at, error FROM tisk_text WHERE ct = ?', [$ct]);
        if ($x === null) {
            return null;
        }
        $x['signals'] = json_decode((string)$x['signals'], true) ?: [];
        $x['ai'] = json_decode((string)$x['ai_json'], true) ?: null;
        return $x;
    }

    public function watchlist(array $laws, array $tisky): array
    {
        // filtrováno v PHP, aby dotaz fungoval stejně v SQLite i MySQL
        $out = [];
        foreach ($this->db->all('SELECT * FROM tisky ORDER BY posledni DESC') as $t) {
            $tLaws = array_filter(explode(',', (string)$t['zakony']));
            if (array_intersect($tLaws, $laws) !== [] || in_array((int)$t['ct'], array_map('intval', $tisky), true)) {
                $t['flags'] = $this->flags($t);
                $out[] = $t;
            }
        }
        return $out;
    }

    public function topAuthors(int $days, int $limit): array
    {
        $since = date('Y-m-d H:i:s', strtotime("-$days days"));
        return $this->db->all('SELECT osoba, COUNT(*) n FROM pn WHERE cas >= ? GROUP BY osoba ORDER BY n DESC, osoba LIMIT ' . $limit, [$since]);
    }

    public function bills(string $filter = '', string $q = '', int $limit = 500, string $sort = 'score', int $minScore = 0): array
    {
        $where = ['1 = 1'];
        $params = [];
        switch ($filter) {
            case 'mp':
                $where[] = "typ_navrhu <> 'vládní'";
                break;
            case 'p90':
                $where[] = 'p90 = 1';
                break;
            case 'cut':
                $where[] = 'zl = 1';
                break;
            case 'surge':
                $where[] = "ct IN (SELECT ct FROM alerts WHERE kind = 'surge')";
                break;
            case 'flagged':
                $where[] = "(typ_navrhu <> 'vládní' OR p90 = 1 OR zl = 1 OR pn_count > 0)";
                break;
        }
        if ($minScore > 0) {
            $where[] = 'score >= ?';
            $params[] = $minScore;
        }
        if ($q !== '') {
            $like = '%' . $q . '%';
            $where[] = '(nazev LIKE ? OR uplny_nazev LIKE ? OR navrhovatel LIKE ? OR zakony LIKE ? OR CAST(ct AS CHAR) = ?)';
            array_push($params, $like, $like, $like, $like, $q);
        }
        $order = $sort === 'recent' ? 'posledni DESC, ct DESC' : 'score DESC, posledni DESC';
        $rows = $this->db->all('SELECT * FROM tisky WHERE ' . implode(' AND ', $where)
            . ' ORDER BY ' . $order . ' LIMIT ' . $limit, $params);
        $surge = $this->surgeSet();
        foreach ($rows as &$row) {
            $row['flags'] = $this->flags($row, $surge);
        }
        return $rows;
    }

    public function bill(int $ct): ?array
    {
        $t = $this->db->one('SELECT * FROM tisky WHERE ct = ?', [$ct]);
        if ($t !== null) {
            $t['flags'] = $this->flags($t);
            $t['reasons_list'] = json_decode((string)$t['reasons'], true) ?: [];
        }
        return $t;
    }

    public function steps(int $ct): array
    {
        return $this->db->all('SELECT * FROM kroky WHERE ct = ? ORDER BY id_hist', [$ct]);
    }

    public function amendments(int $ct): array
    {
        return $this->db->all('SELECT * FROM pn WHERE ct = ? ORDER BY cas DESC', [$ct]);
    }

    public function amendmentAuthors(int $ct): array
    {
        return $this->db->all('SELECT osoba, COUNT(*) n FROM pn WHERE ct = ? GROUP BY osoba ORDER BY n DESC, osoba', [$ct]);
    }

    private function surgeSet(): array
    {
        $out = [];
        foreach ($this->db->all("SELECT DISTINCT ct FROM alerts WHERE kind = 'surge'") as $r) {
            $out[(int)$r['ct']] = true;
        }
        return $out;
    }

    public function flags(array $t, ?array $surge = null): array
    {
        // štítky v pořadí, v jakém se zobrazují
        $surge ??= $this->surgeSet();
        $flags = [];
        if ($t['typ_navrhu'] === 'poslanecký') {
            $flags[] = ['mp', 'Poslanecký návrh'];
        } elseif ($t['typ_navrhu'] === 'senátní') {
            $flags[] = ['mp', 'Senátní návrh'];
        }
        if ((int)$t['p90ok']) {
            $flags[] = ['fast', 'Schváleno v 1. čtení'];
        } elseif ((int)$t['p90']) {
            $flags[] = ['fast', 'Navrženo § 90/2'];
        }
        if ((int)$t['zl']) {
            $flags[] = ['cut', 'Zkrácená lhůta'];
        }
        if (isset($surge[(int)$t['ct']])) {
            $flags[] = ['surge', 'Nával PN'];
        }
        return $flags;
    }
}
