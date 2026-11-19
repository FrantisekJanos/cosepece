<?php
// Výpočet příznaků a fáze projednávání z historie tisku.

declare(strict_types=1);

namespace Hlidac;

final class Analyzer
{
    // typy stavů (typ_stavu.unl)
    public const LHUTA_VYBORY = 60;                  // výchozí lhůta pro výbory ve dnech
    public const STAV_P90 = [17, 18, 67];            // 1. čtení podle § 90 odst. 2
    public const STAV_2_3_CTENI = [2, 3, 4, 5];      // 2. a 3. čtení
    public const STAV_PO_SNEMOVNE = [64, 12, 14, 63, 8, 13, 11];
    public const STAV_ODESLANO_SENATU = 64;
    public const STAV_SBIRKA = 11;

    // typy akcí (typ_akce.unl)
    public const AKCE_ZKRACENI = [47, 59, 72];       // změna lhůty, změna lhůty PN, vráceno + změna lhůty
    public const AKCE_STAZENO = [26, 39, 50];
    public const AKCE_ZAMITNUTO = [8, 27, 28, 34, 45, 46, 49];

    private const FAZE = [
        23 => 'Podáno', 7 => 'Organizační výbor', 15 => 'U vlády', 16 => 'Stanovisko vlády',
        1 => '1. čtení', 17 => '1. čtení', 18 => '1. čtení', 40 => '1. čtení', 41 => '1. čtení',
        44 => '1. čtení', 67 => '1. čtení',
        2 => '2. čtení', 3 => '2. čtení', 4 => '2. čtení', 21 => '2. čtení', 54 => '2. čtení', 55 => '2. čtení',
        5 => '3. čtení',
        64 => 'Schváleno Sněmovnou', 12 => 'V Senátu', 14 => 'Sněmovna po Senátu', 63 => 'Sněmovna po Senátu',
        8 => 'U prezidenta', 13 => 'Sněmovna po vetu', 11 => 'Vyhlášeno',
    ];

    /**
     * @param array<int, array{typ_stavu:int, akce_id:int, datum:?string}> $kroky seřazené chronologicky
     */
    public static function analyze(array $kroky): array
    {
        $p90 = false;
        $druheTreti = false;
        $poSnemovne = false;
        $zl = false;
        $faze = 'Podáno';
        $posledni = null;

        foreach ($kroky as $krok) {
            $typ = (int)$krok['typ_stavu'];
            $akce = (int)$krok['akce_id'];
            if (in_array($typ, self::STAV_P90, true)) {
                $p90 = true;
            }
            if (in_array($typ, self::STAV_2_3_CTENI, true)) {
                $druheTreti = true;
            }
            if (in_array($typ, self::STAV_PO_SNEMOVNE, true)) {
                $poSnemovne = true;
            }
            if (in_array($akce, self::AKCE_ZKRACENI, true)) {
                $zl = true;
            }
            if ($typ === 6) {
                // KONEC: rozhoduje akce, jinak zůstává předchozí fáze (např. Vyhlášeno)
                if (in_array($akce, self::AKCE_STAZENO, true)) {
                    $faze = 'Vzat zpět';
                } elseif (in_array($akce, self::AKCE_ZAMITNUTO, true)) {
                    $faze = 'Zamítnuto';
                }
            } elseif (isset(self::FAZE[$typ])) {
                $faze = self::FAZE[$typ];
            }
            if (!empty($krok['datum'])) {
                $posledni = $krok['datum'];
            }
        }

        return [
            'p90' => $p90,
            // schváleno už v 1. čtení = návrh prošel Sněmovnou bez 2. a 3. čtení
            'p90ok' => $p90 && $poSnemovne && !$druheTreti,
            'zl' => $zl,
            'faze' => $faze,
            'posledni' => $posledni,
        ];
    }

    public static function typNavrhu(int $druh, string $uplnyNazev, int $idNavrh = 0): string
    {
        // předkladatel podle číselníku typ_zakon (tisky.id_navrh): 1 vláda, 2–3 poslanci, 4 Senát,
        // 6–19 kraje a hlavní město Praha; bez kódu podle druhu tisku a začátku úplného názvu
        if ($idNavrh === 1) {
            return 'vládní';
        }
        if ($idNavrh === 2 || $idNavrh === 3) {
            return 'poslanecký';
        }
        if ($idNavrh === 4) {
            return 'senátní';
        }
        if ($idNavrh >= 6 && $idNavrh <= 19) {
            return 'krajský';
        }
        if ($druh === 1 || $druh === 3) {
            return 'vládní';
        }
        if (mb_stripos($uplnyNazev, 'Senátní') === 0) {
            return 'senátní';
        }
        if (mb_stripos($uplnyNazev, 'Návrh zastupitelstva') === 0) {
            return 'krajský';
        }
        return 'poslanecký';
    }

    public static function committeeCut(?int $lhuta): bool
    {
        // lhůta pro výbory kratší než výchozích 60 dní (§ 91 odst. 3 jednacího řádu)
        return $lhuta !== null && $lhuta > 0 && $lhuta < self::LHUTA_VYBORY;
    }

    public static function zakony(string $uplnyNazev): array
    {
        // čísla zákonů uvedená v názvu, např. „č. 586/1992 Sb.“ → 586/1992
        preg_match_all('~č\.\s*(\d+/\d{4})\s*Sb\.~u', $uplnyNazev, $m);
        return array_values(array_unique($m[1]));
    }
}
