<?php
// Poslanecké kluby aktuálního volebního období a členství poslanců v nich (poslanci.zip):
// organy.unl (typ 1 = klub pod orgánem období, sloupec 3 = zkratka) a zarazeni.unl (id_osoba, id orgánu,
// 0 = členství, od, do). Klub se určuje vždy k určitému dni: poslanec může během období klub změnit.
// Používá se u hlasování (Votes) a u předkladatelů pozměňovacích návrhů (Importer). Jen se zobrazuje,
// do skóre nevstupuje.

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
