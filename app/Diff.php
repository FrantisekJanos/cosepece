<?php



declare(strict_types=1);

namespace Hlidac;

final class Diff
{
    /** Řádky textu bez prázdných řádků a koncových mezer. */
    public static function lines(string $text): array
    {
        $out = [];
        foreach (preg_split("~\r\n|\r|\n~", $text) ?: [] as $line) {
            $line = rtrim($line);
            if (trim($line) !== '') {
                $out[] = $line;
            }
        }
        return $out;
    }

    /**
     * Posloupnost operací [op, hodnota]: '=' stejné, '-' jen ve staré verzi, '+' jen v nové.
     * Společný začátek a konec se oddělí předem; když je změn víc než $maxD, zbytek se ukáže jako blok
     * „vše staré pryč, vše nové přidáno“.
     */
    public static function ops(array $a, array $b, int $maxD = 500): array
    {
        $a = array_values($a);
        $b = array_values($b);
        $start = 0;
        $n = count($a);
        $m = count($b);
        while ($start < $n && $start < $m && $a[$start] === $b[$start]) {
            $start++;
        }
        $end = 0;
        while ($end < $n - $start && $end < $m - $start && $a[$n - 1 - $end] === $b[$m - 1 - $end]) {
            $end++;
        }
        $midA = array_slice($a, $start, $n - $start - $end);
        $midB = array_slice($b, $start, $m - $start - $end);

        $out = [];
        for ($i = 0; $i < $start; $i++) {
            $out[] = ['=', $a[$i]];
        }
        $mid = self::myers($midA, $midB, $maxD);
        if ($mid === null) {
            foreach ($midA as $x) {
                $out[] = ['-', $x];
            }
            foreach ($midB as $x) {
                $out[] = ['+', $x];
            }
        } else {
            array_push($out, ...$mid);
        }
        for ($i = $n - $end; $i < $n; $i++) {
            $out[] = ['=', $a[$i]];
        }
        return $out;
    }

    private static function myers(array $a, array $b, int $maxD): ?array
    {
        $n = count($a);
        $m = count($b);
        if ($n === 0 && $m === 0) {
            return [];
        }
        $v = [1 => 0];
        $trace = [];
        $limit = min($n + $m, $maxD);
        for ($d = 0; $d <= $limit; $d++) {
            $trace[] = $v;
            for ($k = -$d; $k <= $d; $k += 2) {
                if ($k === -$d || ($k !== $d && ($v[$k - 1] ?? -1) < ($v[$k + 1] ?? -1))) {
                    $x = $v[$k + 1] ?? 0;
                } else {
                    $x = ($v[$k - 1] ?? 0) + 1;
                }
                $y = $x - $k;
                while ($x < $n && $y < $m && $a[$x] === $b[$y]) {
                    $x++;
                    $y++;
                }
                $v[$k] = $x;
                if ($x >= $n && $y >= $m) {
                    return self::backtrack($trace, $a, $b);
                }
            }
        }
        return null;
    }

    private static function backtrack(array $trace, array $a, array $b): array
    {
        $x = count($a);
        $y = count($b);
        $ops = [];
        for ($d = count($trace) - 1; $d >= 0; $d--) {
            $v = $trace[$d];
            $k = $x - $y;
            if ($k === -$d || ($k !== $d && ($v[$k - 1] ?? -1) < ($v[$k + 1] ?? -1))) {
                $prevK = $k + 1;
            } else {
                $prevK = $k - 1;
            }
            $prevX = $v[$prevK] ?? 0;
            $prevY = $prevX - $prevK;
            while ($x > $prevX && $y > $prevY) {
                $ops[] = ['=', $a[$x - 1]];
                $x--;
                $y--;
            }
            if ($d > 0) {
                if ($x === $prevX) {
                    $ops[] = ['+', $b[$y - 1]];
                } else {
                    $ops[] = ['-', $a[$x - 1]];
                }
            }
            $x = $prevX;
            $y = $prevY;
        }
        return array_reverse($ops);
    }

    /** Slova a mezery jednoho řádku (pro rozdíl uvnitř změněného řádku). */
    public static function words(string $line): array
    {
        return preg_split('~(\s+)~u', $line, -1, PREG_SPLIT_DELIM_CAPTURE | PREG_SPLIT_NO_EMPTY) ?: [];
    }

    /** HTML jednoho změněného řádku: smazaná slova v <del>, přidaná v <ins>; vše escapované. */
    public static function inlineHtml(string $old, string $new): array
    {
        $ops = self::ops(self::words($old), self::words($new), 300);
        $oldHtml = '';
        $newHtml = '';
        foreach ($ops as [$op, $w]) {
            $esc = htmlspecialchars($w, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
            if ($op === '=') {
                $oldHtml .= $esc;
                $newHtml .= $esc;
            } elseif ($op === '-') {
                $oldHtml .= '<del>' . $esc . '</del>';
            } else {
                $newHtml .= '<ins>' . $esc . '</ins>';
            }
        }
        return [$oldHtml, $newHtml];
    }

    /**
     * Bloky pro zobrazení: změněné řádky s $context řádky okolo, dlouhé stejné úseky zkrácené.
     * Vrací ['blocks' => [['type' => 'same'|'skip'|'del'|'add', 'html' => …, 'n' => …]], 'added' => n, 'removed' => n].
     */
    public static function render(string $old, string $new, int $context = 2, int $maxChanged = 600): array
    {
        $ops = self::ops(self::lines($old), self::lines($new));
        $added = count(array_filter($ops, fn(array $o): bool => $o[0] === '+'));
        $removed = count(array_filter($ops, fn(array $o): bool => $o[0] === '-'));


        $show = [];
        foreach ($ops as $i => [$op]) {
            if ($op !== '=') {
                for ($j = max(0, $i - $context); $j <= min(count($ops) - 1, $i + $context); $j++) {
                    $show[$j] = true;
                }
            }
        }
        $esc = fn(string $s): string => htmlspecialchars($s, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
        $blocks = [];
        $skipped = 0;
        $changed = 0;
        $truncated = false;
        $count = count($ops);
        for ($i = 0; $i < $count; $i++) {
            [$op, $line] = $ops[$i];
            if ($op === '=') {
                if (empty($show[$i])) {
                    $skipped++;
                    continue;
                }
                if ($skipped > 0) {
                    $blocks[] = ['type' => 'skip', 'n' => $skipped];
                    $skipped = 0;
                }
                $blocks[] = ['type' => 'same', 'html' => $esc($line)];
                continue;
            }
            if ($skipped > 0) {
                $blocks[] = ['type' => 'skip', 'n' => $skipped];
                $skipped = 0;
            }
            if ($changed >= $maxChanged) {
                $truncated = true;
                break;
            }

            $dels = [];
            $adds = [];
            while ($i < $count && $ops[$i][0] !== '=') {
                if ($ops[$i][0] === '-') {
                    $dels[] = $ops[$i][1];
                } else {
                    $adds[] = $ops[$i][1];
                }
                $i++;
            }
            $i--;
            $pairs = min(count($dels), count($adds));
            $delHtml = [];
            $addHtml = [];
            foreach ($dels as $j => $d) {
                if ($j < $pairs && mb_strlen($d) + mb_strlen($adds[$j]) < 20000) {
                    [$delHtml[$j], $addHtml[$j]] = self::inlineHtml($d, $adds[$j]);
                } else {
                    $delHtml[$j] = $esc($d);
                }
            }
            foreach ($adds as $j => $a) {
                $addHtml[$j] ??= $esc($a);
            }
            ksort($addHtml);
            foreach ($delHtml as $h) {
                $blocks[] = ['type' => 'del', 'html' => $h];
            }
            foreach ($addHtml as $h) {
                $blocks[] = ['type' => 'add', 'html' => $h];
            }
            $changed += count($dels) + count($adds);
        }
        if ($skipped > 0) {
            $blocks[] = ['type' => 'skip', 'n' => $skipped];
        }
        return ['blocks' => $blocks, 'added' => $added, 'removed' => $removed, 'truncated' => $truncated];
    }
}
