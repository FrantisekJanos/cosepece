<?php
// Aktualizace: nové události (tabulka alerts) seskupené podle synchronizace, která je zaznamenala.
// Řádek „Poslední aktualizace“ na přehledu a časová osa na /aktualizace. Skupina se pozná podle času vzniku
// upozornění (created_at): jeden běh je zapíše během vteřin, další běh přijde nejdřív za desítky minut.
// Historii běhů (sync_beh) k tomu nepoužíváme, uchovává se jen 90 dní. Popisy se skládají z titulků
// upozornění, bez AI. Jen zobrazení: skóre ani rozesílání se tím nemění.

declare(strict_types=1);

namespace Hlidac;

final class Updates
{
    /** Nejdelší mezera (s) mezi upozorněními téže aktualizace. */
    public const GAP = 600;
    /** Kolik odkazů nejvýš v jednořádkovém souhrnu aktualizace. */
    public const ROW_ITEMS = 4;
    /** Kolik událostí jedné aktualizace je na časové ose vidět hned a kolik dalších po rozbalení. */
    public const VISIBLE = 12;
    public const FOLDED = 50;
    /** Kolik posledních časů vzniku stačí projít pro řádek na přehledu. */
    private const RECENT_STAMPS = 500;

    /**
     * Aktualizace od nejnovější, bez událostí: id (kotva na stránce), from a to (první a poslední čas
     * vzniku upozornění), n (počet událostí) a important (kolik jich má skóre aspoň $minScore).
     * $stamps > 0 projde jen tolik posledních časů vzniku (přehled nepotřebuje celou historii).
     *
     * @return list<array{id: string, from: string, to: string, n: int, important: int}>
     */
    public static function groups(Db $db, int $minScore, int $stamps = 0): array
    {
        $rows = $db->all('SELECT created_at, COUNT(*) AS n, SUM(CASE WHEN score >= ? THEN 1 ELSE 0 END) AS important FROM alerts'
            . ' GROUP BY created_at ORDER BY created_at DESC' . ($stamps > 0 ? ' LIMIT ' . $stamps : ''), [$minScore]);
        $groups = [];
        $last = -1;
        foreach ($rows as $row) {
            $at = (string)$row['created_at'];
            // řádky jdou od nejnovějšího: 'from' skupiny je zatím nejstarší čas, který do ní patří
            if ($last >= 0 && strtotime($groups[$last]['from']) - strtotime($at) <= self::GAP) {
                $groups[$last]['from'] = $at;
                $groups[$last]['n'] += (int)$row['n'];
                $groups[$last]['important'] += (int)$row['important'];
                continue;
            }
            $groups[] = ['id' => '', 'from' => $at, 'to' => $at, 'n' => (int)$row['n'], 'important' => (int)$row['important']];
            $last++;
        }
        // omezený výběr může poslední skupinu useknout uprostřed: neúplnou nezobrazujeme
        if ($stamps > 0 && count($rows) === $stamps && count($groups) > 1) {
            array_pop($groups);
        }
        foreach ($groups as &$group) {
            $group['id'] = 'u-' . date('Ymd-Hi', (int)strtotime($group['from']));
        }
        unset($group);
        return $groups;
    }

    /**
     * Doplní skupinám události (řádky alerts), u každé od nejvyššího skóre. $minScore > 0 nechá jen
     * události se skóre aspoň $minScore; počty n a important zůstávají za celou aktualizaci.
     *
     * @param list<array{id: string, from: string, to: string, n: int, important: int}> $groups
     * @return list<array{id: string, from: string, to: string, n: int, important: int, events: list<array>}>
     */
    public static function withEvents(Db $db, array $groups, int $minScore = 0): array
    {
        if ($groups === []) {
            return [];
        }
        foreach ($groups as &$group) {
            $group['events'] = [];
        }
        unset($group);
        $rows = $db->all('SELECT * FROM alerts WHERE created_at >= ? AND created_at <= ? AND score >= ? ORDER BY score DESC, event_at DESC, alert_key',
            [min(array_column($groups, 'from')), max(array_column($groups, 'to')), $minScore]);
        foreach ($rows as $row) {
            $at = (string)$row['created_at'];
            foreach ($groups as &$group) {
                if ($at >= $group['from'] && $at <= $group['to']) {
                    $group['events'][] = $row;
                    break;
                }
            }
            unset($group);
        }
        return $groups;
    }

    /**
     * Posledních $count aktualizací i s událostmi (včetně běžné agendy), pro řádek na přehledu.
     * $minScore jen počítá důležité události (important); podle toho odkaz na časovou osu ví,
     * jestli je aktualizace vidět i ve výchozím pohledu bez běžné agendy.
     */
    public static function latest(Db $db, int $count, int $minScore = 0): array
    {
        return self::withEvents($db, array_slice(self::groups($db, $minScore, self::RECENT_STAMPS), 0, $count));
    }

    /**
     * Souhrn aktualizace do jednoho řádku: nejvýš $max událostí (od nejvyššího skóre) seskupených podle
     * toho, co se stalo („Zkrácená lhůta“, „Sněmovna schválila“) a kdy (day = den události, Y-m-d),
     * a počet těch, které se nevešly. Den události se liší ode dne zaznamenání, když se krok objeví
     * v otevřených datech se zpožděním.
     *
     * @return array{parts: list<array{what: string, day: string, events: list<array>}>, rest: int}
     */
    public static function summary(array $events, int $max = self::ROW_ITEMS): array
    {
        $parts = [];
        foreach (array_slice($events, 0, $max) as $event) {
            [$what, $name] = self::split((string)$event['title']);
            $day = substr((string)$event['event_at'], 0, 10);
            $key = $what . '|' . $day;
            $parts[$key] ??= ['what' => $what, 'day' => $day, 'events' => []];
            $parts[$key]['events'][] = $event + ['name' => $name];
        }
        return ['parts' => array_values($parts), 'rest' => max(0, count($events) - $max)];
    }

    /**
     * Datum události do závorky za popis („26. 11.“, v jiném roce „31. 12. 2025“), když se stala jiný den,
     * než ji web zaznamenal; jinak prázdný řetězec. Čtenář pak na stránce tisku hledá správné datum.
     */
    public static function dayNote(string $eventDay, string $recordedAt): string
    {
        $event = strtotime($eventDay);
        $recorded = strtotime($recordedAt);
        if ($event === false || $recorded === false || date('Y-m-d', $event) === date('Y-m-d', $recorded)) {
            return '';
        }
        return date(date('Y', $event) === date('Y', $recorded) ? 'j. n.' : 'j. n. Y', $event);
    }

    /**
     * Titulek upozornění „Zkrácená lhůta: Novela z. - zákoník práce“ rozdělí na [co se stalo, název].
     * Titulek bez dvojtečky je celý název.
     *
     * @return array{0: string, 1: string}
     */
    public static function split(string $title): array
    {
        $pos = mb_strpos($title, ': ');
        return $pos === false ? ['', trim($title)] : [trim(mb_substr($title, 0, $pos)), trim(mb_substr($title, $pos + 2))];
    }

    /** Zkrátí dlouhý název (připravované zákony mají v názvu celou větu). */
    public static function shorten(string $text, int $max = 70): string
    {
        $text = trim((string)preg_replace('~\s+~u', ' ', $text));
        return mb_strlen($text) > $max ? rtrim(mb_substr($text, 0, $max - 1), ' ,.;:-') . '…' : $text;
    }

    /**
     * Čas aktualizace pro čtenáře: „dnes 2:11“, „včera 16:46“, „26. 11. 20:51“ (jiný rok i s rokem).
     * S předložkou ($prep) do věty: „dnes ve 2:11“, „26. 11. v 16:46“.
     */
    public static function when(string $at, bool $prep = false, ?int $now = null): string
    {
        $ts = strtotime($at);
        if ($ts === false) {
            return $at;
        }
        $now ??= time();
        $day = date('Y-m-d', $ts);
        if ($day === date('Y-m-d', $now)) {
            $date = 'dnes';
        } elseif ($day === date('Y-m-d', strtotime('-1 day', $now))) {
            $date = 'včera';
        } else {
            $date = date(date('Y', $ts) === date('Y', $now) ? 'j. n.' : 'j. n. Y', $ts);
        }
        return $date . ' ' . ($prep ? self::prep((int)date('G', $ts)) . ' ' : '') . date('G:i', $ts);
    }

    /** Předložka před hodinou: ve dvě, ve tři, ve čtyři, ve dvanáct až čtrnáct, ve dvacet až třiadvacet; jinak v. */
    public static function prep(int $hour): string
    {
        return in_array($hour, [2, 3, 4, 12, 13, 14, 20, 21, 22, 23], true) ? 've' : 'v';
    }

    /** Nadpis dne na časové ose: „Sobota 28. 11. 2026“. */
    public static function dayLabel(string $at): string
    {
        $ts = (int)strtotime($at);
        return ['Neděle', 'Pondělí', 'Úterý', 'Středa', 'Čtvrtek', 'Pátek', 'Sobota'][(int)date('w', $ts)] . ' ' . date('j. n. Y', $ts);
    }

    /** Skloňování počtu: 1 událost, 2 události, 5 událostí. */
    public static function eventsWord(int $n): string
    {
        return $n . ' ' . ($n === 1 ? 'událost' : ($n >= 2 && $n <= 4 ? 'události' : 'událostí'));
    }
}
