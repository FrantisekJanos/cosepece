<?php
// Změny jednoho tisku den po dni: řádek „Poslední změna“ na stránce tisku a časová osa na /tisk/N/aktualizace.
// Skládá se z toho, co už k tisku máme: kroky projednávání (kroky), podané pozměňovací návrhy (pn), rozeslaný
// souhrn pozměňovacích návrhů a vyhlášení ve Sbírce (tisky). Řadí se podle dne, kdy se změna stala; u kroků
// Sněmovna čas neuvádí a nevíme ani, kdy se krok v otevřených datech objevil. Upozornění (alerts) k tomu
// nestačí: běžné kroky (rozeslání, zahájení čtení, garanční výbor) v nich nejsou. Bez AI.
// Jen zobrazení: skóre ani rozesílání se tím nemění.

declare(strict_types=1);

namespace Hlidac;

final class BillUpdates
{
    /** Kolik změn nejvýš v jednořádkovém souhrnu dne. */
    public const ROW_ITEMS = 3;
    /** Kolik předchozích dnů ukáže řádek po rozbalení. */
    public const ROW_DAYS = 5;
    /** Kolik čísel pozměňovacích návrhů nejvýš v jednořádkovém souhrnu (o jedno víc, když je to poslední). */
    public const PN_LINKS = 4;
    /** Kolik pozměňovacích návrhů jednoho dne je na časové ose vidět hned. */
    public const PN_VISIBLE = 8;

    /** Pořadí změn v rámci dne: výrazný krok, podané pozměňovací návrhy, souhrn, ostatní kroky. */
    private const RANK = ['flag' => 0, 'pn' => 1, 'summary' => 2, 'step' => 3];

    /**
     * Dny se změnami od nejnovějšího. Každý den: day (Y-m-d) a items, v pořadí výrazné kroky, podané
     * pozměňovací návrhy, souhrn a ostatní kroky (od posledního). Položka:
     * type (step, pn, summary, law), flag ('', cut, fast, main), title, detail, time (H:i nebo ''),
     * id (id_hist kroku pro kotvu, jinak 0), vote (id hlasování nebo 0), url (odkaz mimo web nebo '')
     * a pn (řádky tabulky pn od posledního podaného).
     *
     * @param array $bill řádek tabulky tisky
     * @param list<array> $steps řádky tabulky kroky
     * @param list<array> $amendments řádky tabulky pn
     * @return list<array{day: string, items: list<array>}>
     */
    public static function days(array $bill, array $steps, array $amendments, int $obdobi = 0): array
    {
        $ct = (int)$bill['ct'];
        $days = [];
        $add = static function (string $at, array $item) use (&$days): void {
            $ts = strtotime($at);
            if ($at === '' || $ts === false) {
                return;
            }
            $item += ['flag' => '', 'detail' => '', 'id' => 0, 'vote' => 0, 'url' => '', 'pn' => []];
            $item['time'] = date('H:i', $ts) !== '00:00' ? date('G:i', $ts) : '';
            $item['at'] = date('Y-m-d H:i:s', $ts);
            $days[date('Y-m-d', $ts)][] = $item;
        };

        foreach ($steps as $step) {
            $flag = self::stepFlag($step);
            $raw = self::stepLabel($step);
            $add((string)$step['datum'], [
                'type' => 'step',
                'flag' => $flag,
                // zkrácená lhůta se v datech jmenuje „změna lhůty“; čtenář ji zná pod názvem z upozornění
                'title' => $flag === 'cut' ? 'Zkrácená lhůta' : $raw,
                'detail' => $flag === 'cut' ? $raw : '',
                'id' => (int)$step['id_hist'],
                'vote' => (int)($step['id_hlasovani'] ?? 0),
            ]);
        }

        // pozměňovací návrhy podané týž den jsou jedna změna
        $byDay = [];
        foreach ($amendments as $pn) {
            $ts = strtotime((string)$pn['cas']);
            if ($ts !== false) {
                $byDay[date('Y-m-d', $ts)][] = $pn;
            }
        }
        foreach ($byDay as $list) {
            usort($list, static fn(array $a, array $b): int => [(string)$b['cas'], (int)$b['cislo']] <=> [(string)$a['cas'], (int)$a['cislo']]);
            $add((string)$list[0]['cas'], ['type' => 'pn', 'title' => self::pnLabel(count($list)), 'pn' => $list]);
        }

        if ((int)($bill['souhrn_pn'] ?? 0) > 0 && (string)($bill['souhrn_pn_at'] ?? '') !== '') {
            $add((string)$bill['souhrn_pn_at'], [
                'type' => 'summary',
                'title' => 'Rozeslán souhrn pozměňovacích návrhů',
                'detail' => 'tisk ' . $ct . '/' . (int)$bill['souhrn_pn'],
                'url' => $obdobi > 0 ? 'https://www.psp.cz/sqw/text/tiskt.sqw?o=' . $obdobi . '&ct=' . $ct . '&ct1=' . (int)$bill['souhrn_pn'] : '',
            ]);
        }

        if ((string)($bill['vyhlaseno'] ?? '') !== '') {
            $add((string)$bill['vyhlaseno'], [
                'type' => 'law',
                'flag' => 'main',
                'title' => 'Vyhlášeno ve Sbírce zákonů' . ((string)($bill['sbirka'] ?? '') !== '' ? ' jako č. ' . $bill['sbirka'] . ' Sb.' : ''),
            ]);
        }

        krsort($days);
        $out = [];
        foreach ($days as $day => $items) {
            usort($items, static fn(array $a, array $b): int => [self::rank($a), $b['at'], $b['id']] <=> [self::rank($b), $a['at'], $a['id']]);
            $out[] = ['day' => (string)$day, 'items' => $items];
        }
        return $out;
    }

    /**
     * Souhrn dne do jednoho řádku: nejvýš $max změn a počet těch, které se nevešly.
     *
     * @return array{items: list<array>, rest: int}
     */
    public static function summary(array $day, int $max = self::ROW_ITEMS): array
    {
        return ['items' => array_slice($day['items'], 0, $max), 'rest' => max(0, count($day['items']) - $max)];
    }

    /**
     * Výrazný krok na časové ose: fast (1. čtení podle § 90 odst. 2), cut (změna lhůty), main (3. čtení,
     * odeslání Senátu, podpis, Sbírka); jinak prázdný řetězec. Stejné rozlišení mají tečky v průběhu projednávání.
     */
    public static function stepFlag(array $step): string
    {
        $type = (int)$step['typ_stavu'];
        if (in_array($type, Analyzer::STAV_P90, true)) {
            return 'fast';
        }
        if (in_array((int)$step['akce_id'], Analyzer::AKCE_ZKRACENI, true)) {
            return 'cut';
        }
        return in_array($type, [64, 11, 8, 12], true) ? 'main' : '';
    }

    /** Krok slovy Sněmovny: „2. čtení - podrobná rozprava – ukončena“. */
    public static function stepLabel(array $step): string
    {
        return (string)$step['stav'] . ((string)$step['akce'] !== '' ? ' – ' . $step['akce'] : '');
    }

    /** „Podán 1 pozměňovací návrh“, „Podány 3 pozměňovací návrhy“, „Podáno 5 pozměňovacích návrhů“. */
    public static function pnLabel(int $n): string
    {
        return match (true) {
            $n === 1 => 'Podán 1 pozměňovací návrh',
            $n >= 2 && $n <= 4 => 'Podány ' . $n . ' pozměňovací návrhy',
            default => 'Podáno ' . $n . ' pozměňovacích návrhů',
        };
    }

    /** Den změny pro čtenáře: „dnes“, „včera“, „26. 11.“, v jiném roce „31. 12. 2025“. */
    public static function dayWhen(string $day, ?int $now = null): string
    {
        $ts = strtotime($day);
        if ($ts === false) {
            return $day;
        }
        $now ??= time();
        if (date('Y-m-d', $ts) === date('Y-m-d', $now)) {
            return 'dnes';
        }
        if (date('Y-m-d', $ts) === date('Y-m-d', strtotime('-1 day', $now))) {
            return 'včera';
        }
        return date(date('Y', $ts) === date('Y', $now) ? 'j. n.' : 'j. n. Y', $ts);
    }

    /** Skloňování počtu dnů se změnami: „změny v 1 dni“, „změny ve 4 dnech“, „změny v 6 dnech“. */
    public static function daysLabel(int $n): string
    {
        return 'změny ' . (in_array($n, [2, 3, 4, 12, 13, 14], true) || ($n >= 20 && $n < 30) || ($n >= 200 && $n < 300) ? 've' : 'v') . ' ' . $n . ' ' . ($n === 1 ? 'dni' : 'dnech');
    }

    /** Kotva dne na časové ose tisku. */
    public static function anchor(string $day): string
    {
        return 'd-' . $day;
    }

    private static function rank(array $item): int
    {
        return $item['flag'] !== '' ? self::RANK['flag'] : (self::RANK[$item['type']] ?? self::RANK['step']);
    }
}
