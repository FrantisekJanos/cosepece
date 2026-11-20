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
    public const AKCE_VRACENO = [24, 38];             // vrácen, vráceno k přepracování (např. rozpočet, tisk 17)

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
                } elseif (in_array($akce, self::AKCE_VRACENO, true)) {
                    $faze = 'Vráceno předkladateli';
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

    // Senát a kroky po něm (#6): výsledek je akce kroku, který následuje po stavu „Senát“
    public const STAV_SENAT = 12;
    public const STAV_PS_PO_SENATU = [14, 63];
    public const STAV_PREZIDENT = 8;
    public const STAV_PS_PO_VETU = 13;
    public const AKCE_SENAT = [21 => 'schvalen', 33 => 'schvalen', 24 => 'vracen', 27 => 'zamitnut',
        11 => 'nezabyval', 37 => 'nezabyval', 53 => 'nezabyval', 6 => 'necinny', 36 => 'bez_usneseni'];
    public const AKCE_SETRVANO = 20;      // Sněmovna setrvala na původním znění (přehlasovala Senát nebo veto)
    public const AKCE_PRIJATA_VERZE_SENATU = 15;
    public const AKCE_NEPODEPSAL = 7;     // veto prezidenta
    public const SENAT_LHUTA_DNI = 30;    // čl. 46 odst. 1 Ústavy

    /**
     * Průchod Senátem a kroky po něm z historie tisku. Jen popis postupu, do skóre nevstupuje.
     * Data jsou podle záznamů Sněmovny (den, kdy výsledek dorazil), ne den hlasování v Senátu.
     *
     * @param array<int, array{typ_stavu:int, akce_id:int, datum:?string}> $kroky seřazené chronologicky
     * @return array{doruceno: ?string, vysledek: ?string, rozhodnuto: ?string, lhuta_do: ?string, snemovna: ?string,
     *     snemovna_datum: ?string, veto: ?string, veto_prehlasovano: ?string}
     */
    public static function senate(array $kroky): array
    {
        $out = ['doruceno' => null, 'vysledek' => null, 'rozhodnuto' => null, 'lhuta_do' => null, 'snemovna' => null,
            'snemovna_datum' => null, 'veto' => null, 'veto_prehlasovano' => null];
        $prev = 0;
        foreach ($kroky as $krok) {
            $typ = (int)$krok['typ_stavu'];
            $akce = (int)$krok['akce_id'];
            $date = !empty($krok['datum']) ? substr((string)$krok['datum'], 0, 10) : null;
            if ($typ === self::STAV_SENAT && $prev !== self::STAV_SENAT) {
                // (nové) doručení Senátu: dřívější výsledek už neplatí
                $out = ['doruceno' => $date] + array_fill_keys(array_keys($out), null);
            } elseif ($prev === self::STAV_SENAT && isset(self::AKCE_SENAT[$akce])) {
                $out['vysledek'] = self::AKCE_SENAT[$akce];
                $out['rozhodnuto'] = $date;
            } elseif (in_array($prev, self::STAV_PS_PO_SENATU, true) && in_array($akce, [self::AKCE_SETRVANO, self::AKCE_PRIJATA_VERZE_SENATU], true)) {
                $out['snemovna'] = $akce === self::AKCE_SETRVANO ? 'setrvala' : 'prijala';
                $out['snemovna_datum'] = $date;
            } elseif ($prev === self::STAV_PREZIDENT && $akce === self::AKCE_NEPODEPSAL) {
                $out['veto'] = $date;
            } elseif ($prev === self::STAV_PS_PO_VETU && $akce === self::AKCE_SETRVANO) {
                $out['veto_prehlasovano'] = $date;
            }
            $prev = $typ;
        }
        if ($out['doruceno'] !== null && $out['vysledek'] === null) {
            $out['lhuta_do'] = date('Y-m-d', strtotime($out['doruceno']) + 86400 * self::SENAT_LHUTA_DNI);
        }
        return $out;
    }

    /** Čitelný popis průchodu Senátem (věty pro detail tisku a upozornění). @return string[] */
    public static function senateLines(array $s): array
    {
        if (($s['doruceno'] ?? null) === null) {
            return [];
        }
        $d = fn(?string $date): string => $date !== null ? formatDate($date) : 'bez data';
        $lines = [];
        $lines[] = match ($s['vysledek']) {
            null => 'Senát návrh dostal ' . $d($s['doruceno']) . '. Na projednání má ' . self::SENAT_LHUTA_DNI . ' dní, tedy do ' . $d($s['lhuta_do']) . '.',
            'schvalen' => 'Senát návrh schválil (' . $d($s['rozhodnuto']) . ').',
            'vracen' => 'Senát návrh vrátil Sněmovně s pozměňovacími návrhy (' . $d($s['rozhodnuto']) . ').',
            'zamitnut' => 'Senát návrh zamítl (' . $d($s['rozhodnuto']) . ').',
            'nezabyval' => 'Senát se usnesl, že se návrhem nebude zabývat (' . $d($s['rozhodnuto']) . '); tím je návrh přijat (čl. 48 Ústavy).',
            'necinny' => 'Senát se ve lhůtě ' . self::SENAT_LHUTA_DNI . ' dnů nevyjádřil (' . $d($s['rozhodnuto']) . '); návrh tak platí za přijatý (čl. 46 odst. 3 Ústavy).',
            default => 'Senát návrh projednal bez usnesení (' . $d($s['rozhodnuto']) . ').',
        };
        if ($s['snemovna'] !== null) {
            $gap = $s['rozhodnuto'] !== null && $s['snemovna_datum'] !== null
                ? (int)round((strtotime($s['snemovna_datum']) - strtotime($s['rozhodnuto'])) / 86400) : null;
            $after = $gap !== null ? ', ' . ($gap === 0 ? 'týž den' : ($gap === 1 ? 'den' : $gap . ' ' . ($gap < 5 ? 'dny' : 'dní')) . ' poté') : '';
            $lines[] = $s['snemovna'] === 'setrvala'
                ? 'Sněmovna setrvala na původním znění a Senát přehlasovala (' . $d($s['snemovna_datum']) . $after . ').'
                : 'Sněmovna přijala znění s pozměňovacími návrhy Senátu (' . $d($s['snemovna_datum']) . $after . ').';
        }
        if ($s['veto'] !== null) {
            $lines[] = 'Prezident zákon nepodepsal a vrátil ho Sněmovně (' . $d($s['veto']) . ').';
        }
        if ($s['veto_prehlasovano'] !== null) {
            $lines[] = 'Sněmovna na zákonu setrvala a veto prezidenta přehlasovala (' . $d($s['veto_prehlasovano']) . ').';
        }
        return $lines;
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
