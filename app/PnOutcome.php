<?php
// Které pozměňovací návrhy prošly (#5). První krok: jen fakta o hlasování, do skóre nevstupuje.
//
// Řetěz bez AI:
//  1. Souhrn pozměňovacích návrhů po 2. čtení (sněmovní tisk X/n, tisky.souhrn_pn) přiřazuje písmena
//     k číslům sněmovních dokumentů: „A. Poslankyně … / SD 839“, „B1. SD 825“.
//  2. Ve 3. čtení zpravodaj před každým hlasováním řekne písmeno („Pozměňovací návrh E1 pana poslance …
//     Stanovisko výboru: nedoporučující.“) a předsedající pak nechá hlasovat. Stenozáznamy s vazbou projevu
//     na hlasování má služba rag/ (steno.projev.hlasovani), výsledek hlasování je v tabulce hlasovani.
//  3. Kde se písmeno nebo hlasování nepodaří spárovat jednoznačně, zůstává „nelze určit“ – nehádá se.
//
// Bez schématu steno (SQLite, MySQL, hosting bez služby rag/) se určí jen to, zda návrh ve 2. čtení zazněl.

declare(strict_types=1);

namespace Hlidac;

final class PnOutcome
{
    // stav => popis na webu
    public const STATES = [
        'prijat' => 'Přijat',
        'castecne' => 'Přijat zčásti',
        'zamitnut' => 'Zamítnut',
        'nezaznel' => 'Nezazněl ve 2. čtení',
        'ceka' => 'Čeká na hlasování ve 3. čtení',
        'neurceno' => 'Výsledek nelze určit',
    ];

    public function __construct(private Db $db, private array $config)
    {
    }

    public function run(): array
    {
        $cfg = $this->config['pn_outcome'] ?? [];
        if (isset($cfg['enabled']) && !$cfg['enabled']) {
            return ['skipped' => true];
        }
        $fetched = $this->fetchSummaries((int)($cfg['per_run'] ?? 10), (int)($cfg['retry_hours'] ?? 24));
        return $fetched + $this->compute();
    }

    /** Stáhne souhrny pozměňovacích návrhů, které ještě nemáme (nebo se změnilo číslo podtisku); nejdřív tisky po 3. čtení. */
    public function fetchSummaries(int $limit, int $retryHours = 24): array
    {
        $rows = $this->db->all('SELECT t.ct, t.souhrn_pn FROM tisky t LEFT JOIN pn_souhrn s ON s.ct = t.ct
            WHERE t.souhrn_pn IS NOT NULL AND t.souhrn_pn > 0
              AND (s.ct IS NULL OR s.cislo_za <> t.souhrn_pn OR (s.error IS NOT NULL AND s.fetched_at < ?))
            ORDER BY CASE WHEN EXISTS (SELECT 1 FROM kroky k WHERE k.ct = t.ct AND k.typ_stavu = ' . Analyzer::STAV_ODESLANO_SENATU . ') THEN 0 ELSE 1 END,
                t.posledni DESC LIMIT ' . max(0, $limit), [date('Y-m-d H:i:s', time() - 3600 * $retryHours)]);
        $obdobi = (int)$this->db->getMeta('obdobi_cislo', '0');
        $stats = ['souhrny_stazeno' => 0, 'souhrny_chyby' => 0];
        foreach ($rows as $r) {
            $ct = (int)$r['ct'];
            $za = (int)$r['souhrn_pn'];
            $url = null;
            $letters = null;
            $chars = 0;
            $error = null;
            try {
                $url = self::docxUrl($obdobi, $ct, $za);
                if ($url === null) {
                    throw new \RuntimeException('Souhrn nemá verzi ve Wordu');
                }
                $text = PnText::docxToText(Http::get($url));
                $chars = mb_strlen($text);
                $letters = self::parseSummary($text);
                $stats['souhrny_stazeno']++;
            } catch (\Throwable $ex) {
                $error = mb_substr(Http::redact($ex->getMessage()), 0, 250);
                $stats['souhrny_chyby']++;
            }
            $this->db->run('DELETE FROM pn_souhrn WHERE ct = ?', [$ct]);
            $this->db->run('INSERT INTO pn_souhrn (ct, cislo_za, docx_url, pismena, chars, fetched_at, error) VALUES (?, ?, ?, ?, ?, ?, ?)',
                [$ct, $za, $url, $letters !== null ? json_encode($letters) : null, $chars, date('Y-m-d H:i:s'), $error]);
        }
        return $stats;
    }

    public static function docxUrl(int $obdobi, int $ct, int $za): ?string
    {
        // stránka „Soubory sněmovního tisku“ podtisku: stejný odkaz na DOCX jako u textu tisku (TiskText)
        $base = 'https://www.psp.cz';
        $html = Http::get($base . '/sqw/text/tiskt.sqw?o=' . $obdobi . '&ct=' . $ct . '&ct1=' . $za);
        $html = iconv('CP1250', 'UTF-8//IGNORE', $html) ?: $html;
        if (preg_match('~<a href="(/sqw/text/orig2\.sqw\?idd=\d+)"[^>]*title="Dokument DOCX"~', $html, $m)) {
            return $base . html_entity_decode($m[1]);
        }
        return null;
    }

    /**
     * Písmena ze souhrnu pozměňovacích návrhů a čísla sněmovních dokumentů (SD), která k nim patří.
     * Podoby v praxi: „A. Poslankyně …“ a na dalším řádku „SD 839“; „B1. SD 825“; „D.1 (SD 1306)“;
     * „E.1.“ a o pár řádků níž „(SD 633)“. Písmeno u předkladatele často čísluje Word automaticky a v textu
     * chybí: pak se odvodí z pořadí (po A následuje B…) a označí jako odhad; výslovně uvedené písmeno
     * (např. „B1.“) odhad potvrdí, nebo při neshodě odhady od posledního jistého místa zahodí.
     * Návrhy výborů (písmena bez SD) se nevracejí.
     *
     * @return array{pismena: array<string, int[]>, odhad: string[], zminena: int[]}
     */
    public static function parseSummary(string $text): array
    {
        $letters = [];
        $guessed = [];      // písmena odvozená z pořadí
        $sinceAnchor = [];  // odhady od posledního výslovně uvedeného písmene
        $top = null;
        $topGuessed = false;
        $pending = null;    // samostatně stojící písmeno („E.1.“), ke kterému teprve přijde SD
        $next = fn(?string $l): string => $l === null ? 'A' : (strlen($l) === 1 && $l !== 'Z' ? chr(ord($l) + 1) : $l . 'A');
        $confirm = function (string $prefix) use (&$top, &$topGuessed, &$letters, &$guessed, &$sinceAnchor): void {
            // výslovné písmeno: sedí-li s odhadem, odhady platí; jinak se od posledního jistého místa zahodí
            if ($top !== $prefix && $sinceAnchor !== []) {
                foreach ($sinceAnchor as $l) {
                    unset($letters[$l]);
                }
                $guessed = array_values(array_diff($guessed, $sinceAnchor));
            }
            $sinceAnchor = [];
            $top = $prefix;
            $topGuessed = false;
        };
        foreach (preg_split('~\R~u', $text) ?: [] as $line) {
            $line = trim((string)preg_replace('~[ \t\x{00a0}]+~u', ' ', $line));
            if ($line === '') {
                continue;
            }
            // oddíl návrhů výboru zabírá jedno písmeno (bývá také číslované automaticky)
            if (preg_match('~^Pozměňovací návrhy obsažen\w+ v usnesení~u', $line)) {
                $top = $next($top);
                $topGuessed = true;
                $pending = null;
                continue;
            }
            // písmeno s číslem („B1.“, „D.1“, „E.1.“), případně rovnou s SD na řádku
            if (preg_match('~^([A-Z]{1,2})\.?(\d{1,2})[\.\)]?\s*(?:\(?SD\s*(?:č\.\s*)?(\d{2,6})\)?.*)?$~u', $line, $m)) {
                $confirm($m[1]);
                $pending = $m[1] . $m[2];
                if (($m[3] ?? '') !== '') {
                    $letters[$pending][] = (int)$m[3];
                    $pending = null;
                }
                continue;
            }
            // předkladatel, s písmenem nebo bez něj
            if (mb_strlen($line) < 120 && preg_match('~^(?:([A-Z]{1,2})[\.\)]\s*)?(?:Poslan|Ministr|Místopředsed|Předsed)\w*\s+\p{Lu}~u', $line, $m)) {
                if (($m[1] ?? '') !== '') {
                    $confirm($m[1]);
                } else {
                    $top = $next($top);
                    $topGuessed = true;
                }
                $pending = null;
                continue;
            }
            // samostatný řádek „SD 839“ nebo „(SD 1156)“: k čekajícímu písmenu, jinak k předkladateli
            if (preg_match('~^\(?SD\s*(?:č\.\s*)?(\d{2,6})\)?\.?$~u', $line, $m)) {
                $key = $pending ?? $top;
                if ($key !== null) {
                    $letters[$key][] = (int)$m[1];
                    if ($pending === null && $topGuessed && !in_array($key, $guessed, true)) {
                        $guessed[] = $key;
                        $sinceAnchor[] = $key;
                    }
                }
                $pending = null;
            }
        }
        // předkladatel s více dokumenty bez vlastních písmen: Word je čísluje B1, B2… (odhad)
        foreach ($letters as $letter => $sds) {
            $sds = array_values(array_unique($sds));
            if (count($sds) > 1 && preg_match('~^[A-Z]{1,2}$~', (string)$letter)) {
                unset($letters[$letter]);
                $guessed = array_values(array_diff($guessed, [$letter]));
                foreach ($sds as $i => $sd) {
                    $letters[$letter . ($i + 1)] = [$sd];
                    $guessed[] = $letter . ($i + 1);
                }
            } else {
                $letters[$letter] = $sds;
            }
        }
        preg_match_all('~\bSD\s*(?:č\.\s*)?(\d{2,6})\b~u', $text, $all);
        return ['pismena' => $letters, 'odhad' => array_values(array_unique($guessed)), 'zminena' => array_values(array_unique(array_map('intval', $all[1])))];
    }

    /**
     * Přiřadí hlasování ve 3. čtení k písmenům pozměňovacích návrhů podle stenozáznamu.
     * Platí jen ohlášení těsně před hlasováním: poslední projev mimo předsedajícího. Zpravodajové ohlašují
     * různě („Pozměňovací návrh E1 pana poslance …“, „návrh s označením D, navrhovatel …“, „pod písmenem E7“,
     * „D-a, varianta 2“). Zmíní-li projev víc písmen (např. které návrhy se staly nehlasovatelnými),
     * rozhodne jméno předkladatele; bez jednoznačného písmene se hlasování nepřiřadí.
     * Hlasování, které předsedající prohlásil za zmatečné, se vynechá.
     *
     * @param array<int, array{predsedajici: mixed, text: string, hlasovani: int[]}> $speeches v pořadí, jak zazněly
     * @param array<int, array{cislo: int, vysledek: string}> $votes id_hlasovani => hlasování tohoto tisku
     * @param string[] $letters písmena ze souhrnu
     * @param array<string, string[]> $authorsByLetter písmeno => předkladatelé návrhů pod tímto písmenem
     * @return array<int, array{pismeno: string, zaklad: string, varianta: ?int, stanovisko: ?string, ohlaseni: string, id_hlasovani: int, cislo: int, vysledek: string}>
     */
    public static function mapVotes(array $speeches, array $votes, array $letters, array $authorsByLetter = []): array
    {
        if ($letters === [] || $votes === []) {
            return [];
        }
        // i samotné písmeno bez čísla: „pozměňovací návrh B“ může znamenat B1 i B2 dohromady
        $letters = array_values(array_unique(array_merge($letters, array_map(fn(string $l) => (string)preg_replace('~\d+$~', '', $l), $letters))));
        usort($letters, fn(string $a, string $b) => strlen($b) <=> strlen($a));
        // písmeno ze souhrnu, případně s částí („D-a“) – část jen se spojovníkem nebo tečkou, ne „D a …“;
        // písmeno musí být velké, okolní slova na velikosti nezáleží
        $token = '(' . implode('|', array_map(fn(string $l) => preg_quote($l, '~'), $letters)) . ')(?:[\-–\.]([a-d]))?(?![\p{L}\d])';
        $announce = '~(?i:návrh\w*|písmen\w*|označení\w*)\s+(?:(?i:pod\s+písmenem|s\s+označením|označen\w+(?:\s+jako)?|písmeno|písmenem)\s+)?' . $token . '~u';
        $bare = '~^\W*(?:(?i:ano|přesně|dále|nyní|teď|takže|další)\W+)?' . $token . '\s*[,\.:]~u';
        $pending = null;
        $mapped = [];
        $void = [];
        foreach ($speeches as $s) {
            $text = trim((string)preg_replace('~\s+~u', ' ', (string)$s['text']));
            $ids = array_values(array_filter(array_map('intval', (array)$s['hlasovani']), fn(int $id) => isset($votes[$id])));
            if (empty($s['predsedajici'])) {
                $pending = null;
                $head = mb_substr($text, 0, 500);
                $found = [];
                if (preg_match($bare, $head, $m)) {
                    $found[$m[1] . '|' . ($m[2] ?? '')] = [$m[1], $m[2] ?? '', 0];
                }
                if (preg_match_all($announce, $head, $all, PREG_SET_ORDER | PREG_OFFSET_CAPTURE)) {
                    foreach ($all as $m) {
                        $part = isset($m[2]) && $m[2][1] >= 0 ? $m[2][0] : '';
                        $found[$m[1][0] . '|' . $part] ??= [$m[1][0], $part, (int)$m[0][1]];
                    }
                }
                if (count($found) > 1) {
                    // zpravodaj často zmíní i jiné návrhy („díky přijetí E8 se stal nehlasovatelným A3…“): ty se nepočítají
                    $current = array_filter($found, function (array $f) use ($head): bool {
                        $around = substr($head, max(0, $f[2] - 45), 45 + 90);
                        return !preg_match('~nehlasovateln|přijetí|přijat\w*\s+návrh|totožn|širší\s+než~u', mb_scrub($around));
                    });
                    $found = $current !== [] ? $current : $found;
                }
                if (count($found) > 1) {
                    // pořád víc písmen: platí to, jehož předkladatele zpravodaj jmenuje
                    $named = array_filter($found, function (array $f) use ($authorsByLetter, $head): bool {
                        foreach (self::authorsOf($f[0], $authorsByLetter) as $author) {
                            if (self::namesAuthor($head, $author)) {
                                return true;
                            }
                        }
                        return false;
                    });
                    $found = count($named) === 1 ? $named : [];
                }
                if (count($found) === 1) {
                    [$letter, $part] = array_slice(array_values($found)[0], 0, 2);
                    $variant = preg_match('~variant\w*\s+(\d{1,2})\b~u', $head, $v) ? (int)$v[1] : null;
                    $opinion = preg_match('~[Ss]tanovisko(?:\s+\S+){0,3}?\s*:?\s*(nedoporučující|doporučující|nesouhlasné|souhlasné|neutrální|bez\s+stanoviska|žádné)~u', $text, $o)
                        ? (string)preg_replace('~\s+~u', ' ', $o[1]) : null;
                    $pending = ['pismeno' => $letter . ($part !== '' ? '-' . $part : ''), 'zaklad' => $letter, 'varianta' => $variant, 'stanovisko' => $opinion,
                        'ohlaseni' => mb_substr($text, 0, 300)];
                }
                continue;
            }
            if ($ids === []) {
                continue;
            }
            if (preg_match('~zmatečn~u', $text)) {
                // předsedající prohlásil hlasování za zmatečné: neplatí a ohlášení se musí zopakovat
                foreach ($ids as $id) {
                    $void[$id] = true;
                }
                $pending = null;
                continue;
            }
            if ($pending !== null && count($ids) === 1) {
                $id = $ids[0];
                $mapped[$id] = $pending + ['id_hlasovani' => $id, 'cislo' => (int)$votes[$id]['cislo'], 'vysledek' => (string)$votes[$id]['vysledek']];
            }
            // jedno ohlášení = jedno hlasování
            $pending = null;
        }
        $mapped = array_values(array_diff_key($mapped, $void));
        usort($mapped, fn(array $a, array $b) => $a['cislo'] <=> $b['cislo']);
        return $mapped;
    }

    /** Předkladatelé návrhů pod písmenem; samotné písmeno bez čísla („B“) zahrnuje B1, B2… */
    private static function authorsOf(string $letter, array $authorsByLetter): array
    {
        $out = $authorsByLetter[$letter] ?? [];
        if (preg_match('~^[A-Z]{1,2}$~', $letter)) {
            foreach ($authorsByLetter as $key => $authors) {
                if (preg_match('~^' . $letter . '\d+$~', (string)$key)) {
                    $out = array_merge($out, $authors);
                }
            }
        }
        return array_values(array_unique($out));
    }

    /**
     * Výsledek pro každý písemný pozměňovací návrh tisku.
     *
     * @param array{pismena: array<string, int[]>, odhad: string[], zminena: int[]} $summary výstup parseSummary()
     * @param array $mapped výstup mapVotes()
     * @param array<int, string> $authors číslo PN => předkladatel (pn.osoba)
     * @param bool $voted o tisku se po rozeslání souhrnu už hlasovalo (3. čtení proběhlo nebo běží)
     * @return array<int, array{pismena: string[], stav: string, hlasovani: array, stanovisko: ?string}> číslo PN => výsledek
     */
    public static function outcomes(array $summary, array $mapped, array $authors, bool $voted = true): array
    {
        $letters = $summary['pismena'];
        $out = [];
        foreach ($authors as $number => $author) {
            $number = (int)$number;
            $own = array_map('strval', array_keys(array_filter($letters, fn(array $sds) => in_array($number, $sds, true))));
            $guessed = $own !== [] && array_diff($own, $summary['odhad']) === [];
            // hlasování k písmenu návrhu, nebo k samotnému písmenu bez čísla („B“ pro B1 i B2)
            $votes = array_values(array_filter($mapped, function (array $v) use ($own, $guessed, $author): bool {
                $hit = in_array($v['zaklad'], $own, true)
                    || (preg_match('~^[A-Z]{1,2}$~', $v['zaklad']) && array_filter($own, fn(string $l) => preg_match('~^' . $v['zaklad'] . '\d+$~', $l)) !== []);
                if (!$hit) {
                    return false;
                }
                // křížová kontrola jménem: zpravodaj obvykle řekne i předkladatele („E1 pana poslance Jurečky“).
                // Jmenuje-li někoho jiného, hlasování k tomuto návrhu nepatří; písmeno odvozené z pořadí
                // platí jen se jménem.
                $text = (string)($v['ohlaseni'] ?? '');
                $named = (bool)preg_match('~\b(pan[aí]?|paní|poslan\w+|ministr\w*|koleg\w+)\b~ui', $text);
                return self::namesAuthor($text, $author) || (!$named && !$guessed);
            }));
            $opinion = null;
            foreach ($votes as $v) {
                $opinion ??= $v['stanovisko'];
            }
            if ($own === []) {
                // v souhrnu není vůbec, nebo ho jen neumíme přiřadit k písmenu
                $state = in_array($number, $summary['zminena'], true) ? 'neurceno' : 'nezaznel';
            } elseif ($votes === []) {
                // před 3. čtením návrh na hlasování teprve čeká. Po něm: písmeno v souhrnu je, ale hlasování k němu ne –
                // návrh se stal nehlasovatelným, nebo se hlasování nepodařilo přiřadit; to zatím neumíme rozlišit
                $state = $voted ? 'neurceno' : 'ceka';
            } else {
                // část návrhu = písmeno (i s částí „D-a“); u variant platí poslední hlasování každé varianty
                $parts = [];
                foreach ($votes as $v) {
                    $parts[$v['pismeno']][(string)($v['varianta'] ?? 0)] = $v['vysledek'] === 'A';
                }
                $accepted = array_map(fn(array $variants) => in_array(true, $variants, true), $parts);
                $yes = count(array_filter($accepted));
                $state = $yes === 0 ? 'zamitnut' : ($yes === count($accepted) ? 'prijat' : 'castecne');
            }
            $out[$number] = [
                'pismena' => $own,
                'stav' => $state,
                'hlasovani' => array_map(fn(array $v) => ['cislo' => $v['cislo'], 'id' => $v['id_hlasovani'], 'vysledek' => $v['vysledek'],
                    'pismeno' => $v['pismeno'], 'varianta' => $v['varianta']], $votes),
                'stanovisko' => $opinion,
            ];
        }
        return $out;
    }

    /** Jmenuje ohlášení zpravodaje předkladatele? Porovnává začátek příjmení bez diakritiky („Jurečky“ × „Jurečka“). */
    public static function namesAuthor(string $announcement, string $author): bool
    {
        $parts = preg_split('~\s+~u', trim($author)) ?: [];
        $surname = Repo::fold((string)end($parts));
        if (mb_strlen($surname) < 3) {
            return false;
        }
        $stem = mb_substr($surname, 0, max(3, min(5, mb_strlen($surname) - 1)));
        return str_contains(Repo::fold($announcement), $stem);
    }

    /** Je k dispozici schéma steno služby rag/ (stenozáznamy s vazbou na hlasování)? */
    public function stenoAvailable(): bool
    {
        try {
            if ($this->db->driver === 'pgsql') {
                return $this->db->value("SELECT to_regclass('steno.projev')::text") !== null;
            }
            if ($this->db->driver === 'sqlite') {
                // testy: připojená databáze pojmenovaná steno
                return in_array('steno', array_column($this->db->all('PRAGMA database_list'), 'name'), true);
            }
        } catch (\Throwable) {
        }
        return false;
    }

    /** Přepočítá výsledky pro všechny tisky se staženým souhrnem. */
    public function compute(): array
    {
        $steno = $this->stenoAvailable();
        $stats = ['tisku' => 0, 'pn' => 0, 'prirazeno_hlasovani' => 0, 'steno' => $steno];
        foreach ($this->db->all('SELECT s.ct, s.pismena, t.souhrn_pn_at FROM pn_souhrn s JOIN tisky t ON t.ct = s.ct WHERE s.pismena IS NOT NULL') as $row) {
            $ct = (int)$row['ct'];
            $summary = json_decode((string)$row['pismena'], true);
            if (!is_array($summary) || empty($summary['pismena'])) {
                continue;
            }
            $letters = $summary['pismena'];
            $authors = [];
            foreach ($this->db->all('SELECT cislo, osoba FROM pn WHERE ct = ? ORDER BY cislo', [$ct]) as $p) {
                $authors[(int)$p['cislo']] = (string)$p['osoba'];
            }
            // hlasování po rozeslání souhrnu = 3. čtení (ve 2. čtení se o pozměňovacích návrzích nehlasuje)
            $votes = [];
            $days = [];
            foreach ($this->db->all('SELECT id_hlasovani, schuze, cislo, datum, vysledek FROM hlasovani WHERE ct = ? AND datum >= ? ORDER BY datum, cislo',
                [$ct, (string)($row['souhrn_pn_at'] ?: '0000')]) as $v) {
                $votes[(int)$v['id_hlasovani']] = ['cislo' => (int)$v['cislo'], 'vysledek' => (string)$v['vysledek']];
                $days[(int)$v['schuze'] . '|' . substr((string)$v['datum'], 0, 10)] = [(int)$v['schuze'], substr((string)$v['datum'], 0, 10)];
            }
            $mapped = [];
            if ($steno && $votes !== []) {
                $speeches = [];
                foreach ($days as [$sitting, $day]) {
                    $speeches = array_merge($speeches, $this->speeches($ct, $sitting, $day));
                }
                $byLetter = [];
                foreach ($letters as $letter => $sds) {
                    $byLetter[(string)$letter] = array_values(array_filter(array_map(fn($sd) => $authors[(int)$sd] ?? '', $sds)));
                }
                $mapped = self::mapVotes($speeches, $votes, array_map('strval', array_keys($letters)), $byLetter);
            }
            $results = self::outcomes($summary, $mapped, $authors, $votes !== []);
            $this->db->run('DELETE FROM pn_vysledek WHERE ct = ?', [$ct]);
            $now = date('Y-m-d H:i:s');
            $this->db->insertMany('pn_vysledek', ['ct', 'cislo', 'pismena', 'stav', 'hlasovani', 'stanovisko', 'updated_at'], array_map(
                fn(int $number, array $r) => ['ct' => $ct, 'cislo' => $number, 'pismena' => mb_substr(implode(', ', $r['pismena']), 0, 120), 'stav' => $r['stav'],
                    'hlasovani' => json_encode($r['hlasovani']), 'stanovisko' => $r['stanovisko'], 'updated_at' => $now],
                array_keys($results), $results));
            $stats['tisku']++;
            $stats['pn'] += count($results);
            $stats['prirazeno_hlasovani'] += count($mapped);
        }
        return $stats;
    }

    /** Projevy k bodu tisku v daný den schůze, v pořadí (jen úsek, kdy se tisk projednával). */
    public function speeches(int $ct, int $sitting, string $day): array
    {
        $range = $this->db->one('SELECT MIN(poradi) AS od, MAX(poradi) AS do FROM steno.projev WHERE schuze = ? AND datum = ? AND ct = ?', [$sitting, $day, $ct]);
        if ($range === null || $range['od'] === null) {
            return [];
        }
        $out = [];
        foreach ($this->db->all('SELECT predsedajici, text, hlasovani FROM steno.projev WHERE schuze = ? AND datum = ? AND poradi >= ? AND poradi <= ? ORDER BY poradi',
            [$sitting, $day, (int)$range['od'], (int)$range['do']]) as $r) {
            // pole čísel hlasování: PostgreSQL ho vrací jako text „{87400,87401}“
            preg_match_all('~\d+~', (string)$r['hlasovani'], $m);
            $out[] = ['predsedajici' => $r['predsedajici'] === true || $r['predsedajici'] === 1 || $r['predsedajici'] === '1' || $r['predsedajici'] === 't',
                'text' => (string)$r['text'], 'hlasovani' => array_map('intval', $m[0])];
        }
        return $out;
    }
}
