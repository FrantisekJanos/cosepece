<?php
// Hlasování Sněmovny k tiskům z otevřených dat psp.cz (hl-{rok}ps.zip, schuze.zip, poslanci.zip):
// výsledek, počty a rozdělení podle poslaneckých klubů. Do skóre nevstupuje, jen se zobrazuje u tisku.
//
// Párování hlasování s tiskem:
//  1. krok v historii tisku (hist.unl, sloupec id_hlasovani) – závěrečná hlasování o tisku,
//  2. bod schůze (bod_schuze.unl: schůze + číslo bodu → id_tisk) a zároveň stejný krátký název tisku
//     v popisu hlasování; číslo bodu se mezi verzemi pořadu schůze opakuje, název to pojistí.

declare(strict_types=1);

namespace Hlidac;

final class Votes
{
    // kód hlasu poslance → sloupec souhrnu
    private const VOTE = [
        'A' => 'pro', 'B' => 'proti', 'N' => 'proti',
        'C' => 'zdrzel', 'F' => 'zdrzel', 'K' => 'zdrzel',
        '@' => 'nepritomen', 'M' => 'nepritomen', 'W' => 'nepritomen',
    ];

    public function __construct(private Db $db, private Source $source)
    {
    }

    public function run(): array
    {
        $obdobiId = (int)$this->db->getMeta('obdobi_id', '0');
        if ($obdobiId <= 0) {
            return ['votes' => 0];
        }
        $year = $this->periodYear($obdobiId);
        $package = 'hl-' . $year . 'ps';
        $prefix = 'hl' . $year;

        // tisky aktuálního období: id_tisk → ct a krátký název
        $tisky = [];
        foreach ($this->db->all('SELECT id_tisk, ct, nazev FROM tisky') as $t) {
            $tisky[(int)$t['id_tisk']] = ['ct' => (int)$t['ct'], 'nazev' => self::normName((string)$t['nazev'])];
        }

        // 1. hlasování z kroků historie tisku
        $byStep = [];
        foreach ($this->db->all('SELECT ct, id_hlasovani FROM kroky WHERE id_hlasovani IS NOT NULL AND id_hlasovani > 0') as $k) {
            $byStep[(int)$k['id_hlasovani']] = (int)$k['ct'];
        }

        // 2. body schůzí: „schůze|bod“ → tisky
        $schuze = [];
        foreach ($this->source->rows('schuze', 'schuze.unl') as $c) {
            if ((int)($c[1] ?? 0) === $obdobiId) {
                $schuze[(int)$c[0]] = (int)$c[2];
            }
        }
        $byBod = [];
        foreach ($this->source->rows('schuze', 'bod_schuze.unl') as $c) {
            $idTisk = (int)($c[2] ?? 0);
            $no = $schuze[(int)($c[1] ?? 0)] ?? null;
            if ($no !== null && isset($tisky[$idTisk])) {
                $byBod[$no . '|' . (int)($c[4] ?? 0)][$idTisk] = true;
            }
        }
        $byCt = [];
        foreach ($tisky as $t) {
            $byCt[$t['ct']] = $t;
        }

        // souhrny hlasování
        $votes = [];
        foreach ($this->source->rows($package, $prefix . 's.unl') as $c) {
            if ((int)($c[1] ?? 0) !== $obdobiId) {
                continue;
            }
            $id = (int)$c[0];
            $name = trim((string)($c[15] ?? '') !== '' ? $c[15] : ($c[16] ?? ''));
            $ct = $byStep[$id] ?? null;
            if ($ct === null) {
                foreach (array_keys($byBod[(int)$c[2] . '|' . (int)$c[4]] ?? []) as $idTisk) {
                    if ($name !== '' && self::normName($name) === $tisky[$idTisk]['nazev']) {
                        $ct = $tisky[$idTisk]['ct'];
                        break;
                    }
                }
            }
            if ($ct === null) {
                continue;
            }
            $votes[$id] = [
                'id_hlasovani' => $id, 'ct' => $ct, 'schuze' => (int)$c[2], 'cislo' => (int)$c[3],
                'datum' => self::isoDate((string)$c[5], (string)($c[6] ?? '')),
                'pro' => (int)$c[7], 'proti' => (int)$c[8], 'zdrzel' => (int)$c[9], 'nehlasoval' => (int)$c[10],
                'prihlaseno' => (int)$c[11], 'kvorum' => (int)$c[12], 'vysledek' => substr((string)($c[14] ?? ''), 0, 1),
                'nazev' => mb_substr($name, 0, 255), 'krok' => isset($byStep[$id]) ? 1 : 0,
            ];
        }

        // rozdělení podle klubů (klub poslance ke dni hlasování)
        $clubs = $this->clubs($obdobiId);
        $poslanci = $this->deputies($obdobiId);
        $perClub = [];
        foreach ($this->source->rows($package, $prefix . 'h1.unl') as $c) {
            $id = (int)($c[1] ?? 0);
            if (!isset($votes[$id])) {
                continue;
            }
            $osoba = $poslanci[(int)$c[0]] ?? null;
            $club = $osoba !== null ? $this->clubAt($clubs['member'][$osoba] ?? [], substr($votes[$id]['datum'], 0, 10)) : null;
            $club ??= 'Nezařazení';
            $col = self::VOTE[$c[2] ?? ''] ?? 'nepritomen';
            $perClub[$id][$club][$col] = ($perClub[$id][$club][$col] ?? 0) + 1;
        }

        $clubRows = [];
        foreach ($perClub as $id => $list) {
            ksort($list, SORT_LOCALE_STRING);
            foreach ($list as $club => $n) {
                $clubRows[] = [
                    'id_hlasovani' => $id, 'klub' => mb_substr($club, 0, 40),
                    'pro' => $n['pro'] ?? 0, 'proti' => $n['proti'] ?? 0,
                    'zdrzel' => $n['zdrzel'] ?? 0, 'nepritomen' => $n['nepritomen'] ?? 0,
                ];
            }
        }

        $this->db->pdo->beginTransaction();
        try {
            $this->db->run('DELETE FROM hlasovani');
            $this->db->run('DELETE FROM hlasovani_kluby');
            $this->db->insertMany('hlasovani', ['id_hlasovani', 'ct', 'schuze', 'cislo', 'datum', 'pro', 'proti', 'zdrzel',
                'nehlasoval', 'prihlaseno', 'kvorum', 'vysledek', 'nazev', 'krok'], array_values($votes));
            $this->db->insertMany('hlasovani_kluby', ['id_hlasovani', 'klub', 'pro', 'proti', 'zdrzel', 'nepritomen'], $clubRows);
            $this->db->pdo->commit();
        } catch (\Throwable $ex) {
            $this->db->pdo->rollBack();
            throw $ex;
        }
        return ['votes' => count($votes)];
    }

    public static function normName(string $name): string
    {
        // krátký název bez hvězdiček (psp.cz jimi značí hlasování k PN) a mezer navíc
        return mb_strtolower(trim(preg_replace('~\s+~u', ' ', rtrim($name, " *")) ?? $name));
    }

    private static function isoDate(string $date, string $time): string
    {
        // „03.03.2026“ + „21:01“ → „2026-03-03 21:01“
        if (!preg_match('~^(\d{2})\.(\d{2})\.(\d{4})$~', trim($date), $m)) {
            return '';
        }
        return $m[3] . '-' . $m[2] . '-' . $m[1] . (trim($time) !== '' ? ' ' . trim($time) : '');
    }

    private function periodYear(int $obdobiId): string
    {
        // rok začátku období (organy.unl, od_organ „04.10.2025“) je v názvu balíku hlasování
        foreach ($this->source->rows('poslanci', 'organy.unl') as $c) {
            if ((int)$c[0] === $obdobiId && preg_match('~(\d{4})$~', trim($c[6] ?? ''), $m)) {
                return $m[1];
            }
        }
        throw new \RuntimeException('Nelze určit rok začátku volebního období');
    }

    private function deputies(int $obdobiId): array
    {
        // id_poslanec → id_osoba v aktuálním období
        $out = [];
        foreach ($this->source->rows('poslanci', 'poslanec.unl') as $c) {
            if ((int)($c[4] ?? 0) === $obdobiId) {
                $out[(int)$c[0]] = (int)$c[1];
            }
        }
        return $out;
    }

    private function clubs(int $obdobiId): array
    {
        // poslanecké kluby období (organy typu 1 pod orgánem období) a členství osob s daty
        $names = [];
        foreach ($this->source->rows('poslanci', 'organy.unl') as $c) {
            if ((int)($c[1] ?? 0) === $obdobiId && (int)($c[2] ?? 0) === 1) {
                $names[(int)$c[0]] = trim($c[3] ?? '');
            }
        }
        $member = [];
        foreach ($this->source->rows('poslanci', 'zarazeni.unl') as $c) {
            $of = (int)($c[1] ?? 0);
            if (isset($names[$of]) && (int)($c[2] ?? 1) === 0) {
                $member[(int)$c[0]][] = [$names[$of], substr(trim($c[3] ?? ''), 0, 10), substr(trim($c[4] ?? ''), 0, 10)];
            }
        }
        return ['names' => $names, 'member' => $member];
    }

    private function clubAt(array $memberships, string $day): ?string
    {
        foreach ($memberships as [$club, $from, $to]) {
            if (($from === '' || $from <= $day) && ($to === '' || $to >= $day)) {
                return $club;
            }
        }
        return null;
    }
}
