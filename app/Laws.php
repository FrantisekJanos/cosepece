<?php




declare(strict_types=1);

namespace Hlidac;

use App\Models\Bill;
use App\Models\AmendmentText;
use App\Models\UpcomingBill;

final class Laws
{


    private const NAME_RE = '~zákon\w*\s+č\.\s*(\d+/\d{4})\s*Sb\.,\s*([^,;()]+?)(?=\s*[,;(]|\s+ve\s+znění|\s+a\s+(?:zákon|další|některé|o\s+změně)|\s*$)~u';

    public function __construct(private \App\Services\Pages\UpcomingService $upcomingService)
    {
    }

    public static function valid(string $law): bool
    {
        return (bool)preg_match('~^\d{1,4}/\d{4}$~', $law);
    }

    /** číslo => název (bez „ve znění…“), z názvů tisků a připravovaných zákonů */
    public function names(): array
    {

        $seen = [];
        $texts = array_merge(
            Bill::query()->pluck('uplny_nazev')->all(),
            UpcomingBill::query()->pluck('nazev')->all()
        );
        foreach ($texts as $text) {
            preg_match_all(self::NAME_RE, (string)$text, $m, PREG_SET_ORDER);
            foreach ($m as $law) {
                $name = trim((string)preg_replace('~\s+ve\s+znění.*$~u', '', $law[2]));

                if ($name === '' || preg_match('~^(ve\s+znění|kterým|kterou|o\s+změně\s+(?:některých|dalších|souvisejících))~u', $name) || mb_strlen($name) > 140) {
                    continue;
                }
                $seen[$law[1]][$name] = ($seen[$law[1]][$name] ?? 0) + 1;
            }
        }
        $names = [];
        foreach ($seen as $number => $variants) {
            uksort($variants, fn(string $a, string $b) => [$variants[$b], mb_strlen($a)] <=> [$variants[$a], mb_strlen($b)]);
            $names[$number] = (string)array_key_first($variants);
        }
        return $names;
    }

    public function name(string $law): ?string
    {
        return $this->names()[$law] ?? null;
    }

    /** Přehled: číslo => [název, tisky, připravované], seřazeno podle počtu tisků. */
    public function overview(): array
    {
        $names = $this->names();
        $out = [];
        foreach (Bill::query()->get(['zakony'])->map->getAttributes()->all() as $t) {
            foreach (array_filter(explode(',', (string)$t['zakony'])) as $law) {
                $law = trim($law);
                $out[$law] ??= ['name' => $names[$law] ?? null, 'bills' => 0, 'upcoming' => 0];
                $out[$law]['bills']++;
            }
        }
        foreach (UpcomingBill::query()->get(['nazev'])->map->getAttributes()->all() as $u) {
            foreach (self::numbersIn((string)$u['nazev']) as $law) {
                $out[$law] ??= ['name' => $names[$law] ?? null, 'bills' => 0, 'upcoming' => 0];
                $out[$law]['upcoming']++;
            }
        }
        uksort($out, fn(string $a, string $b) => [$out[$b]['bills'] + $out[$b]['upcoming'], self::sortKey($b)] <=> [$out[$a]['bills'] + $out[$a]['upcoming'], self::sortKey($a)]);
        return $out;
    }

    /** Tisky aktuálního období, které zákon mění (od poslední změny). */
    public function bills(string $law): array
    {
        $rows = [];
        foreach (Bill::query()->orderByDesc('posledni')->orderByDesc('ct')->get()->map->getAttributes()->all() as $t) {
            if (in_array($law, array_map('trim', explode(',', (string)$t['zakony'])), true)) {
                $rows[] = $t;
            }
        }
        return $rows;
    }

    /**
     * Pozměňovací návrhy k jiným tiskům, jejichž text mění tento zákon (možné přílepky):
     * tisky, které zákon mění samy, se vynechají.
     */
    public function amendments(string $law): array
    {
        $own = array_flip(array_map(fn(array $t) => (int)$t['ct'], $this->bills($law)));
        $rows = [];
        foreach (AmendmentText::query()->from('pn_text as x')->join('pn as p', 'p.id_dokument', '=', 'x.id_dokument')->join('tisky as t', 't.ct', '=', 'x.ct')->whereNotNull('x.laws')->orderByDesc('p.cas')->get(['x.id_dokument', 'x.cislo', 'x.ct', 'x.laws', 'x.ai_score', 'x.ai_json', 'p.osoba', 'p.cas', 't.nazev'])->map->getAttributes()->all() as $r) {
            if (!isset($own[(int)$r['ct']]) && in_array($law, array_map('trim', explode(',', (string)$r['laws'])), true)) {
                $r['ai'] = json_decode((string)$r['ai_json'], true) ?: null;
                $rows[] = $r;
            }
        }
        return $rows;
    }

    /** Připravované zákony u vlády, které zákon mění (podle čísla v názvu materiálu). */
    public function upcoming(string $law): array
    {
        return array_values(array_filter($this->upcomingService->items(),
            fn(array $u) => in_array($law, self::numbersIn((string)$u['nazev']), true)));
    }

    /** Čísla zákonů v textu názvu („zákon č. 159/2006 Sb.“, „zákona č. 90/2024 Sb.“). */
    public static function numbersIn(string $text): array
    {
        preg_match_all('~č\.\s*(\d{1,4}/\d{4})\s*Sb\.~u', $text, $m);
        return array_values(array_unique($m[1]));
    }

    public static function eSbirkaUrl(string $law): string
    {
        [$number, $year] = explode('/', $law);
        return 'https://e-sbirka.gov.cz/sb/' . (int)$year . '/' . (int)$number;
    }

    public static function url(string $law): string
    {
        [$number, $year] = explode('/', $law);
        return route('law', ['year' => $year, 'number' => $number], false);
    }

    private static function sortKey(string $law): int
    {

        [$number, $year] = array_map('intval', explode('/', $law) + [1 => 0]);
        return $year * 10000 + $number;
    }
}
