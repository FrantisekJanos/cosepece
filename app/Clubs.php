<?php
// Poslanecké kluby aktuálního volebního období a členství poslanců v nich (poslanci.zip):
// organy.unl (typ 1 = klub pod orgánem období, sloupec 3 = zkratka) a zarazeni.unl (id_osoba, id orgánu,
// 0 = členství, od, do). Klub se určuje vždy k určitému dni: poslanec může během období klub změnit.
// Používá se u hlasování (Votes), u předkladatelů pozměňovacích návrhů a u prvního předkladatele poslaneckého
// návrhu (Importer). Jen se zobrazuje, do skóre nevstupuje.

declare(strict_types=1);

namespace Hlidac;

final class Clubs
{
    /** @var array<int, list<array{0: string, 1: string, 2: string}>> id_osoba => [[zkratka klubu, od, do], …] */
    private array $member = [];

    public function __construct(Source $source, int $obdobiId)
    {
        $names = [];
        foreach ($source->rows('poslanci', 'organy.unl') as $c) {
            if ((int)($c[1] ?? 0) === $obdobiId && (int)($c[2] ?? 0) === 1) {
                $names[(int)$c[0]] = trim($c[3] ?? '');
            }
        }
        foreach ($source->rows('poslanci', 'zarazeni.unl') as $c) {
            $of = (int)($c[1] ?? 0);
            if (isset($names[$of]) && (int)($c[2] ?? 1) === 0) {
                $this->member[(int)$c[0]][] = [$names[$of], substr(trim($c[3] ?? ''), 0, 10), substr(trim($c[4] ?? ''), 0, 10)];
            }
        }
    }

    /**
     * Zkratka klubu, ve kterém byl poslanec v daný den (YYYY-MM-DD); null = v žádném klubu nebo neznámá osoba.
     * Při přestupu staré členství končí a nové začíná týž den, takže v den změny platí obě. Pak rozhoduje
     * novější klub (členství s nejpozdějším od, návrh 5.2: ORDER BY od_dne DESC), aby web, hlasování i video
     * uváděly stejný klub nezávisle na pořadí řádků v zarazeni.unl. Prázdné od = nejstarší členství.
     */
    public function at(int $idOsoba, string $day): ?string
    {
        $found = null;
        $foundFrom = null;
        foreach ($this->member[$idOsoba] ?? [] as [$club, $from, $to]) {
            if (($from === '' || $from <= $day) && ($to === '' || $to >= $day) && ($foundFrom === null || $from > $foundFrom)) {
                $found = $club;
                $foundFrom = $from;
            }
        }
        return $found;
    }

    /**
     * Platí v daný den (YYYY-MM-DD) víc členství, typicky v den přestupu? Pak `at()` vybírá novější klub
     * a údaje uložené dřív (hlasy poslanců z doby, kdy rozhodovalo pořadí řádků) mohou mít starý klub.
     */
    public function ambiguous(int $idOsoba, string $day): bool
    {
        $count = 0;
        foreach ($this->member[$idOsoba] ?? [] as [, $from, $to]) {
            if (($from === '' || $from <= $day) && ($to === '' || $to >= $day) && ++$count > 1) {
                return true;
            }
        }
        return false;
    }

    /**
     * Osoby, které mají v období aspoň jedno členství v poslaneckém klubu (poslanci, včetně těch, kdo už
     * mandát nemají).
     *
     * @return list<int>
     */
    public function memberIds(): array
    {
        return array_keys($this->member);
    }

    /** Jméno s klubem v závorce jako prostý text, např. „Jan Novák (ODS)“; bez klubu jen jméno. */
    public static function person(?string $name, ?string $club): string
    {
        $label = self::label($club);
        return trim((string)$name) . ($label !== '' ? ' (' . $label . ')' : '');
    }

    /**
     * Navrhovatel tisku rozdělený na jména a jejich kluby (tisky.navrhovatel_klub: kluby v pořadí jmen, oddělené
     * čárkou). „Babiš A.,Plaga R. a další“ + „ANO2011,“ → [[['Babiš A.', 'ANO'], ['Plaga R.', '']], ' a další'].
     * Klub patří jen jmenovanému poslanci, proto se „a další“ vrací zvlášť.
     *
     * @return array{0: list<array{0: string, 1: string}>, 1: string} [[jméno, klub k zobrazení], …] a přípona
     */
    public static function proposerParts(?string $proposer, ?string $clubs): array
    {
        $proposer = trim((string)$proposer);
        $rest = '';
        if (preg_match('~^(.*\S)(\s+a další)$~u', $proposer, $m)) {
            [$proposer, $rest] = [$m[1], $m[2]];
        }
        $list = explode(',', (string)$clubs);
        $parts = [];
        foreach (explode(',', $proposer) as $i => $name) {
            $parts[] = [trim($name), self::label($list[$i] ?? '')];
        }
        return [$parts, $rest];
    }

    /**
     * Navrhovatel tisku s kluby jmenovaných předkladatelů jako prostý text: „Babiš A. a další“ + ANO2011 →
     * „Babiš A. (ANO) a další“. Bez klubů vrací text navrhovatele beze změny.
     */
    public static function proposer(?string $proposer, ?string $clubs): string
    {
        if (trim((string)$clubs) === '') {
            return trim((string)$proposer);
        }
        [$parts, $rest] = self::proposerParts($proposer, $clubs);
        return implode(', ', array_map(fn(array $p): string => $p[0] . ($p[1] !== '' ? ' (' . $p[1] . ')' : ''), $parts)) . $rest;
    }

    /** Zkratka klubu z dat Sněmovny v podobě, jak se běžně píše (ANO2011 → ANO, TOP09 → TOP 09). */
    public static function label(?string $club): string
    {
        $club = trim((string)$club);
        return match ($club) {
            'ANO2011' => 'ANO',
            'TOP09' => 'TOP 09',
            'MS' => 'Motoristé',
            'Nezařaz', 'Nezařazení' => 'nezařazení',
            default => $club,
        };
    }
}
