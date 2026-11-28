<?php
// Kdo návrh zákona předkládá, pro scénu „Kdo to přináší“ ve videích Reels (ReelJobs::cardV2): celý název
// ministerstva ze zkratky v tisky.navrhovatel („min. financí“ → „Ministerstvo financí“), počet a jména
// poslanců z úplného názvu tisku („Návrh poslanců Radka Vondráčka, Davida Pražáka a Filipa Turka na vydání…“).
// Všechno deterministicky z dat Sněmovny, nic se neodhaduje: co nejde určit jednoznačně, zůstane prázdné.
// Jen se zobrazuje, do skóre nevstupuje.

declare(strict_types=1);

namespace Hlidac;

final class Proposers
{
    /** Resorty podle kmene slova v navrhovateli; pořadí rozhoduje („min.pro sport,prev.,zdr.“ dřív než zdravotnictví). */
    private const MINISTRIES = [
        '~sport~u' => 'Ministerstvo pro sport, prevenci a zdraví',
        '~financ|\bfin\.~u' => 'Ministerstvo financí',
        '~vnitr~u' => 'Ministerstvo vnitra',
        '~práce|soc\.~u' => 'Ministerstvo práce a sociálních věcí',
        '~zeměd~u' => 'Ministerstvo zemědělství',
        '~prům|obchod~u' => 'Ministerstvo průmyslu a obchodu',
        '~zdrav~u' => 'Ministerstvo zdravotnictví',
        '~živ~u' => 'Ministerstvo životního prostředí',
        '~místní\s*rozvoj~u' => 'Ministerstvo pro místní rozvoj',
        '~spravedl~u' => 'Ministerstvo spravedlnosti',
        '~doprav~u' => 'Ministerstvo dopravy',
        '~kultur~u' => 'Ministerstvo kultury',
        '~škol~u' => 'Ministerstvo školství, mládeže a tělovýchovy',
        '~obran~u' => 'Ministerstvo obrany',
        '~zahr~u' => 'Ministerstvo zahraničních věcí',
    ];

    private const VOWELS = 'aeiouyáéíóúůýě';

    /**
     * Předkladatel vládního návrhu jako celý název: „min. financí“ → „Ministerstvo financí“, „ministr vnitra“ →
     * „Ministerstvo vnitra“, „min. zdrav., min. fin.“ → „Ministerstvo zdravotnictví a Ministerstvo financí“,
     * „předseda vlády“ → „Předseda vlády“. Neznámý tvar se vrátí beze změny (jen s velkým písmenem na začátku),
     * prázdný = null.
     */
    public static function institution(?string $navrhovatel): ?string
    {
        $text = trim((string)$navrhovatel);
        if ($text === '') {
            return null;
        }
        $names = [];
        // víc resortů odděluje čárka před další zkratkou („min. zdrav., min. fin.“); čárky uvnitř jedné zkratky
        // („min.pro sport,prev.,zdr.“) se nedělí
        foreach (preg_split('~,\s*(?=min)~u', $text) ?: [] as $part) {
            $part = mb_strtolower(trim($part));
            if (!preg_match('~^(?:min\.|ministr|ministryně|ministerstvo)~u', $part)) {
                return self::ucfirst($text);
            }
            $found = null;
            foreach (self::MINISTRIES as $re => $name) {
                if (preg_match($re, $part)) {
                    $found = $name;
                    break;
                }
            }
            if ($found === null) {
                return self::ucfirst($text);
            }
            $names[] = $found;
        }
        return implode(' a ', array_values(array_unique($names)));
    }

    /**
     * Jména poslanců z úplného názvu poslaneckého návrhu v 2. pádě, jak je Sněmovna uvádí („Návrh poslanců Radka
     * Vondráčka, Davida Pražáka a Filipa Turka na vydání zákona…“): [jména, zda název končí „a dalších“].
     * Jiný začátek názvu = null.
     *
     * @return array{0: list<string>, 1: bool}|null
     */
    public static function genitiveNames(?string $uplnyNazev): ?array
    {
        if (!preg_match('~^Návrh\s+poslan\S*\s+(.+?)\s+na\s+vydání\b~u', trim((string)$uplnyNazev), $m)) {
            return null;
        }
        $list = trim($m[1]);
        $more = false;
        if (preg_match('~^(.*\S)\s+a\s+dalších$~u', $list, $mm)) {
            [$list, $more] = [$mm[1], true];
        }
        $names = array_values(array_filter(array_map('trim', preg_split('~,\s*|\s+a\s+~u', $list) ?: []), fn($n) => $n !== ''));
        return [$names, $more];
    }

    /**
     * Počet navrhovatelů poslaneckého návrhu: z úplného názvu, jen když ho vyjmenovává celý a sedí s navrhovatelem
     * (bez „a další“ stejný počet jmen, s „a další“ víc jmen, než navrhovatel jmenuje). Jinak null.
     */
    public static function count(?string $uplnyNazev, ?string $navrhovatel): ?int
    {
        $parsed = self::genitiveNames($uplnyNazev);
        if ($parsed === null || $parsed[1] || $parsed[0] === []) {
            return null;
        }
        $n = count($parsed[0]);
        $text = trim((string)$navrhovatel);
        $rest = (bool)preg_match('~\s+a další$~u', $text);
        $named = $text === '' ? 0 : count(explode(',', (string)preg_replace('~\s+a další$~u', '', $text)));
        if ($named > 0 && ($rest ? $n <= $named : $n !== $named)) {
            return null;
        }
        return $n;
    }

    /**
     * Počet podepsaných PN z hlavičky jeho textu („Pozměňovací návrh poslanců Michaely Šebelové, Toma Philippa
     * a Pavly Pivoňka Vaňkové k vládnímu návrhu…“), pro „a N dalších“ ve videích Reels. Hledá se v prvních 800
     * znacích; jména odděluje čárka nebo „a“ (víceslovné příjmení zůstane jedním jménem). Seznam končící
     * „a dalších“, jiný začátek textu nebo prázdný seznam = null (počet nejde určit).
     *
     * @return array{0: int, 1: string}|null [počet jmen, první jméno ve 2. pádě]
     */
    public static function pnSigners(?string $text): ?array
    {
        $head = (string)preg_replace('~\s+~u', ' ', mb_substr((string)$text, 0, 800));
        if (!preg_match('~Pozměňovací\s+návrh\s+poslan\S*(?:\s+a\s+poslan\S*)?\s+(.+?)\s+(?:k|ke)\s+'
            . '(?:vládnímu\s+|senátnímu\s+|poslaneckému\s+)?(?:návrhu|sněmovnímu\s+tisku|tisku)\b~u', $head, $m)) {
            return null;
        }
        $list = trim($m[1]);
        if (preg_match('~\s+a\s+dalších$~u', $list) || str_contains($list, '(')) {
            return null;
        }
        $names = array_values(array_filter(array_map('trim', preg_split('~,\s*|\s+a\s+~u', $list) ?: []), fn($n) => $n !== ''));
        if ($names === []) {
            return null;
        }
        return [count($names), $names[0]];
    }

    /**
     * Počet spolupodepsaných PN (bez autora) z hlavičky textu: null, když počet nejde určit, nebo když první jméno
     * hlavičky neodpovídá autorovi z evidence (pn.osoba, „Jan Novák“), aby se „a N dalších“ nepřipsalo jinému.
     */
    public static function pnCosigners(?string $text, ?string $osoba): ?int
    {
        $signers = self::pnSigners($text);
        $osoba = trim((string)$osoba);
        if ($signers === null || $osoba === '' || !str_contains($osoba, ' ')) {
            return null;
        }
        $pos = (int)mb_strrpos($osoba, ' ');
        $person = [1 => [mb_substr($osoba, 0, $pos), mb_substr($osoba, $pos + 1)]];
        return self::matchGenitive($signers[1], $person) === 1 ? $signers[0] - 1 : null;
    }

    /** Dovětek k jménu prvního navrhovatele: „a 1 další poslanec“, „a 3 další poslanci“, „a 5 dalších poslanců“, bez počtu „a další poslanci“. */
    public static function othersText(?int $n): string
    {
        if ($n === null) {
            return 'a další poslanci';
        }
        $word = $n === 1 ? 'další poslanec' : ($n >= 2 && $n <= 4 ? 'další poslanci' : 'dalších poslanců');
        return 'a ' . $n . ' ' . $word;
    }

    /**
     * Osoba ke jménu v 2. pádě („Radka Vondráčka“, „Marie Pošarové“, „Jiřího Nováka“): id jediného poslance,
     * jehož křestní jména i příjmení po slovech odpovídají (stejný kmen po odtržení koncovky, vypadávající e:
     * Turek → Turka, -ý → -ého). Víc shod nebo žádná = null.
     *
     * @param array<int, array{0: string, 1: string}> $people id => [křestní jména, příjmení] v 1. pádě
     */
    public static function matchGenitive(string $genitive, array $people): ?int
    {
        $gen = preg_split('~\s+~u', trim($genitive), -1, PREG_SPLIT_NO_EMPTY) ?: [];
        if ($gen === []) {
            return null;
        }
        $found = null;
        foreach ($people as $id => [$first, $surname]) {
            $nom = preg_split('~\s+~u', trim($first . ' ' . $surname), -1, PREG_SPLIT_NO_EMPTY) ?: [];
            if (count($nom) !== count($gen)) {
                continue;
            }
            foreach ($nom as $i => $word) {
                if (!self::sameWord($word, $gen[$i])) {
                    continue 2;
                }
            }
            if ($found !== null && $found !== $id) {
                return null;
            }
            $found = (int)$id;
        }
        return $found;
    }

    /** Odpovídá slovo v 1. pádě slovu v 2. pádě? Porovnávají se kmeny bez koncových samohlásek. */
    private static function sameWord(string $nominative, string $genitive): bool
    {
        $gen = mb_strtolower($genitive);
        $gen = (string)preg_replace(['~ého$~u', '~ího$~u'], ['ý', 'í'], $gen);
        $genStem = self::stem($gen);
        $nom = mb_strtolower($nominative);
        $stems = [self::stem($nom)];
        $v = self::VOWELS;
        // vypadávající e v 2. pádě: Vondráček → Vondráčka, Pavel → Pavla, Němec → Němce, Zbyněk → Zbyňka
        if (preg_match("~^(.+)ně([kc])$~u", $nom, $m)) {
            $stems[] = $m[1] . 'ň' . $m[2];
        } elseif (preg_match("~^(.*[^$v])e([^$v])$~u", $nom, $m)) {
            $stems[] = $m[1] . $m[2];
        }
        return $genStem !== '' && in_array($genStem, $stems, true);
    }

    private static function stem(string $word): string
    {
        return (string)preg_replace('~[' . self::VOWELS . ']+$~u', '', $word);
    }

    private static function ucfirst(string $text): string
    {
        return mb_strtoupper(mb_substr($text, 0, 1)) . mb_substr($text, 1);
    }

    /**
     * Předkladatel krajského návrhu z úplného názvu: „Návrh zastupitelstva Jihomoravského kraje na vydání…“ →
     * „Zastupitelstvo Jihomoravského kraje“; jinak null.
     */
    public static function regionName(?string $uplnyNazev): ?string
    {
        if (!preg_match('~^Návrh\s+zastupitelstva\s+(.+?)\s+na\s+vydání\b~u', trim((string)$uplnyNazev), $m)) {
            return null;
        }
        return 'Zastupitelstvo ' . trim($m[1]);
    }
}
