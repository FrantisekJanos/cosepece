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
        'nehlasovatelny' => 'Nehlasovalo se o něm (nehlasovatelný)',
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
     * Přiřadí hlasování ve 3. čtení k pozměňovacím návrhům podle stenozáznamu.
     * Platí jen ohlášení těsně před hlasováním: poslední projev mimo předsedajícího. Čte se po větách:
     * věty o nehlasovatelných návrzích se vynechají a ve zbytku se hledá číslo dokumentu („(dokument) 1219“,
     * má přednost) nebo písmeno („Pozměňovací návrh E1 pana poslance …“, „návrh s označením D“, „D-a, varianta 2“).
     * Víc návrhů v jednom ohlášení platí jen při společném hlasování („jedním hlasováním“, „E1 až E6“);
     * jinak rozhodne jméno předkladatele a bez jednoznačného návrhu se hlasování nepřiřadí.
     * Hlasování, které předsedající prohlásil za zmatečné, se vynechá.
     *
     * @param array<int, array{predsedajici: mixed, text: string, hlasovani: int[]}> $speeches v pořadí, jak zazněly
     * @param array<int, array{cislo: int, vysledek: string}> $votes id_hlasovani => hlasování tohoto tisku
     * @param string[] $letters písmena ze souhrnu
     * @param array<string, string[]> $authorsByLetter písmeno => předkladatelé návrhů pod tímto písmenem
     * @param array<int, string> $sdLetters číslo písemného PN (sněmovního dokumentu) => písmeno ze souhrnu nebo ''
     * @return array<int, array{pismeno: string, zaklad: string, sd: ?int, varianta: ?int, stanovisko: ?string, ohlaseni: string, id_hlasovani: int, cislo: int, vysledek: string}>
     */
    public static function mapVotes(array $speeches, array $votes, array $letters, array $authorsByLetter = [], array $sdLetters = []): array
    {
        if (($letters === [] && $sdLetters === []) || $votes === []) {
            return [];
        }
        $pending = [];
        $mapped = [];
        $void = [];
        foreach ($speeches as $s) {
            $text = trim((string)preg_replace('~\s+~u', ' ', (string)$s['text']));
            $ids = array_values(array_filter(array_map('intval', (array)$s['hlasovani']), fn(int $id) => isset($votes[$id])));
            if (empty($s['predsedajici'])) {
                $pending = self::announced($text, $letters, $authorsByLetter, $sdLetters);
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
                $pending = [];
                continue;
            }
            if ($pending !== [] && count($ids) === 1) {
                $id = $ids[0];
                foreach ($pending as $target) {
                    $mapped[] = $target + ['id_hlasovani' => $id, 'cislo' => (int)$votes[$id]['cislo'], 'vysledek' => (string)$votes[$id]['vysledek']];
                }
            }
            // jedno ohlášení = jedno hlasování
            $pending = [];
        }
        $mapped = array_values(array_filter($mapped, fn(array $m) => !isset($void[$m['id_hlasovani']])));
        usort($mapped, fn(array $a, array $b) => [$a['cislo'], $a['pismeno']] <=> [$b['cislo'], $b['pismeno']]);
        return $mapped;
    }

    /**
     * Věty projevu; „č. 15“ ani „A2.62“ větu nedělí (dělí jen tečka před velkým písmenem nebo závorkou).
     * Dělí se i souvětí „… se stal nehlasovatelným návrh A a jdeme na pozměňovací návrh B2“
     * a „… se staly nehlasovatelné A14 a D3, takže bychom měli hlasovat návrh G“.
     */
    private static function sentences(string $text): array
    {
        $out = [];
        foreach (preg_split('~(?<=[\.\!\?;])\s+(?=[\p{Lu}\(])~u', $text) ?: [$text] as $sentence) {
            $out = array_merge($out, preg_split('~,?\s+a\s+(?=(?:jdeme|přejdeme|přecházíme|nyní|teď|hlasujeme|budeme\s+hlasovat|přistoupíme|následuje|pokračujeme)\b)'
                . '|,?\s+(?:a\s+)?(?:takže|tudíž)\s+~ui', $sentence) ?: [$sentence]);
        }
        return $out;
    }

    /**
     * Popisuje nebo navrhuje projev hlasovací proceduru („výbor navrhuje následující proceduru“, „návrh hlasovací
     * procedury“, „hlasovat o proceduře“)? Takový projev vyjmenuje písmena všech návrhů a následuje po něm hlasování
     * o proceduře. Pouhý odkaz („podle procedury“, „v hlasovací proceduře“, „provedu vás procedurou“) se nepočítá.
     */
    private static function describesProcedure(string $head): bool
    {
        $head = (string)preg_replace('~(?:podle|dle)\s+(?:\S+\s+){0,2}procedur\w+~ui', '', $head);
        return (bool)preg_match('~procedur(?:u|y)(?![\p{L}])|(?<![\p{L}])o\s+(?:\S+\s+){0,2}proceduře~ui', $head);
    }

    /** Je návrh na tomto místě věty zmíněný jen jako důvod („díky přijetí B1“, „totožné znění s P1.13“)? */
    private static function isReason(string $sentence, int $offset): bool
    {
        return (bool)preg_match('~přijetí|přijat|totožn|shodn~ui', mb_scrub(substr($sentence, max(0, $offset - 45), min(45, $offset))));
    }

    /** Regulární výraz pro písmeno ze souhrnu, případně s částí („D-a“); písmeno musí být velké. */
    private static function letterToken(array $letters): ?string
    {
        // i samotné písmeno bez čísla, aby se „návrh B“ nečetl jako něco jiného
        $letters = array_values(array_unique(array_merge($letters, array_map(fn(string $l) => (string)preg_replace('~\d+$~', '', $l), $letters))));
        $letters = array_values(array_filter($letters, fn(string $l) => $l !== ''));
        if ($letters === []) {
            return null;
        }
        usort($letters, fn(string $a, string $b) => strlen($b) <=> strlen($a));
        return '(' . implode('|', array_map(fn(string $l) => preg_quote($l, '~'), $letters)) . ')(?:[\-–\.]([a-d]))?(?![\p{L}\d]|\.\d)';
    }

    /** Čísla sněmovních dokumentů zmíněná ve větě, jen ta, která patří písemným PN tohoto tisku. */
    private static function documents(string $sentence, array $sdLetters): array
    {
        if (!preg_match_all('~(?i:(?:sněmovní\w*\s+)?\(?dokument\w*\)?|\bSD)\s*(?i:č\.|číslo)?\s*(\d{2,6})\b~u', $sentence, $m)) {
            return [];
        }
        return array_values(array_unique(array_filter(array_map('intval', $m[1]), fn(int $n) => isset($sdLetters[$n]))));
    }

    /** Mluví věta o tom, že se o návrhu nehlasuje (nehlasovatelný, totožný s přijatým…)? */
    private static function saysNotVoted(string $sentence): bool
    {
        return (bool)preg_match('~nehlasovateln|se\s+nehlasuj|nehlasuje\s+se|nebudeme\s+hlasovat|nebude\s+se\s+hlasovat~ui', $sentence);
    }

    /**
     * O čem se podle ohlášení bude hlasovat: seznam návrhů (obvykle jeden, při společném hlasování víc).
     *
     * @return array<int, array{pismeno: string, zaklad: string, sd: ?int, varianta: ?int, stanovisko: ?string, ohlaseni: string}>
     */
    public static function announced(string $text, array $letters, array $authorsByLetter = [], array $sdLetters = []): array
    {
        $head = mb_substr($text, 0, 600);
        // popis hlasovací procedury vyjmenuje písmena všech návrhů a následuje po něm hlasování o proceduře;
        // dlouhý projev těsně před hlasováním je rozprava, ne ohlášení
        if (mb_strlen($text) > 800 || self::describesProcedure($head)) {
            return [];
        }
        // věty o návrzích, o kterých se nehlasuje, k ohlášení nepatří
        $body = implode(' ', array_filter(self::sentences($head), fn(string $sentence) => !self::saysNotVoted($sentence)));
        if (trim($body) === '') {
            return [];
        }
        $block = (bool)preg_match('~jedním\s+hlasováním|společn|najednou|dohromady~ui', $body);
        $variant = preg_match('~variant\w*\s+(\d{1,2})\b~u', $body, $v) ? (int)$v[1] : null;
        $opinion = null;
        if (preg_match('~[Ss]tanovisko(?:\s+\S+){0,3}?\s*:?\s*(nedoporučující|doporučující|nesouhlasné|souhlasné|neutrální|bez\s+stanoviska|žádné)~u', $text, $o)) {
            $opinion = (string)preg_replace('~\s+~u', ' ', $o[1]);
        } elseif (preg_match('~(?i:výbor)\w*\s+(nedoporučuje|doporučuje)|(?<![\p{L}])([Nn]edoporučuje|[Dd]oporučuje)\s+(?:\S+\s+)?(?i:výbor)~u', $text, $o)) {
            $opinion = mb_strtolower($o[1] !== '' ? $o[1] : $o[2]) === 'doporučuje' ? 'doporučující' : 'nedoporučující';
        }
        $row = fn(string $label, string $letter, ?int $sd): array => ['pismeno' => $label, 'zaklad' => $letter, 'sd' => $sd, 'varianta' => $variant,
            'stanovisko' => $opinion, 'ohlaseni' => mb_substr($text, 0, 300)];

        // číslo dokumentu je nejspolehlivější („návrh B, sněmovní dokument 1411“ je B1, ne celé B)
        $docs = self::documents($body, $sdLetters);
        if (count($docs) > 1 && !$block) {
            return [];
        }
        if ($docs !== []) {
            $out = [];
            foreach ($docs as $sd) {
                // označení, jak ho říká zpravodaj („(návrh) H1, (dokument) 1219“), jinak písmeno ze souhrnu
                $spoken = preg_match('~\(?(?i:návrh\w*|písmen\w*)\)?\s+(?:(?i:pod\s+písmenem|s\s+označením)\s+)?([A-Z]{1,2}[a-z]?\d{0,2}(?:\.\d+)*)\s*[,;\)]?\s*\(?(?i:(?:sněmovní\w*\s+)?dokument\w*)\)?\s*(?i:č\.|číslo)?\s*' . $sd . '\b~u', $body, $l)
                    ? $l[1] : '';
                $letter = (string)($sdLetters[$sd] ?? '');
                $out[] = $row($spoken !== '' ? $spoken : ($letter !== '' ? $letter : 'SD ' . $sd), $letter, $sd);
            }
            return $out;
        }

        $token = self::letterToken($letters);
        if ($token === null) {
            return [];
        }
        $announce = '~(?i:návrh\w*|písmen\w*|označení\w*)\s+(?:(?i:pod\s+písmenem|s\s+označením|označen\w+(?:\s+jako)?|písmeno|písmenem)\s+)?' . $token . '~u';
        $bare = '~^\W*(?:(?i:ano|přesně|dále|nyní|teď|takže|další)\W+)?' . $token . '\s*[,\.:]~u';
        $found = [];
        if (preg_match($bare, $body, $m)) {
            $found[$m[1] . '|' . ($m[2] ?? '')] = [$m[1], $m[2] ?? '', 0];
        }
        if (preg_match_all($announce, $body, $all, PREG_SET_ORDER | PREG_OFFSET_CAPTURE)) {
            foreach ($all as $m) {
                $part = isset($m[2]) && $m[2][1] >= 0 ? $m[2][0] : '';
                $found[$m[1][0] . '|' . $part] ??= [$m[1][0], $part, (int)$m[0][1]];
                // výčet za množným číslem: „pozměňovací návrhy pod písmeny E1, E2 a E4“ je společné hlasování
                $rest = substr($body, (int)$m[0][1] + strlen($m[0][0]));
                $listed = 0;
                while (preg_match('~^(?:\s*,\s*|\s+a\s+|\s+i\s+)' . $token . '~u', $rest, $next)) {
                    $found[$next[1] . '|' . ($next[2] ?? '')] ??= [$next[1], $next[2] ?? '', (int)$m[0][1]];
                    $rest = substr($rest, strlen($next[0]));
                    $listed++;
                }
                if ($listed > 0 && preg_match('~(?i:návrhy|návrhů|návrzích|písmeny)~u', $m[0][0])) {
                    $block = true;
                }
            }
        }
        // společné hlasování o řadě návrhů: „E1 až E6“
        if (preg_match_all('~(?<![\p{L}\d])([A-Z]{1,2})(\d{1,2})\s*(?:až|–|-)\s*(?:\1)?(\d{1,2})(?![\p{L}\d\.])~u', $body, $ranges, PREG_SET_ORDER)) {
            foreach ($ranges as $r) {
                for ($i = (int)$r[2]; $i <= (int)$r[3] && $i - (int)$r[2] < 40; $i++) {
                    if (in_array($r[1] . $i, $letters, true)) {
                        $found[$r[1] . $i . '|'] ??= [$r[1] . $i, '', 0];
                        $block = true;
                    }
                }
            }
        }
        if (count($found) > 1 && !$block) {
            // zpravodaj často zmíní i jiné návrhy („díky přijetí E8…“, „širší než H1“): ty se nepočítají
            $current = array_filter($found, function (array $f) use ($body): bool {
                $around = substr($body, max(0, $f[2] - 45), 45 + 90);
                return !preg_match('~přijetí|přijat\w*\s+návrh|totožn|širší\s+než~u', mb_scrub($around));
            });
            $found = $current !== [] ? $current : $found;
        }
        if (count($found) > 1 && !$block) {
            // pořád víc písmen: platí to, jehož předkladatele zpravodaj jmenuje
            $named = array_filter($found, function (array $f) use ($authorsByLetter, $body): bool {
                foreach (self::authorsOf($f[0], $authorsByLetter) as $author) {
                    if (self::namesAuthor($body, $author)) {
                        return true;
                    }
                }
                return false;
            });
            $found = count($named) === 1 ? $named : [];
        }
        return array_values(array_map(fn(array $f) => $row($f[0] . ($f[1] !== '' ? '-' . $f[1] : ''), $f[0], null), $found));
    }

    /**
     * Návrhy, o kterých zpravodaj ve 3. čtení řekl, že se o nich nehlasuje („tímto se stal nehlasovatelným G2,
     * (dokument) 1175“). Jen jednoznačné výroky: podmíněné („pokud bude přijat A, stane se nehlasovatelným B“)
     * a popis procedury se nepočítají.
     *
     * @return array{sd: int[], pismena: string[]}
     */
    public static function notVoted(array $speeches, array $letters, array $sdLetters = []): array
    {
        $out = ['sd' => [], 'pismena' => []];
        $token = self::letterToken($letters);
        foreach ($speeches as $s) {
            if (!empty($s['predsedajici'])) {
                continue;
            }
            $text = trim((string)preg_replace('~\s+~u', ' ', (string)$s['text']));
            if (mb_strlen($text) > 800 || self::describesProcedure(mb_substr($text, 0, 600))) {
                continue;
            }
            foreach (self::sentences($text) as $sentence) {
                if (!self::saysNotVoted($sentence) || preg_match('~\b(pokud|jestliže|v\s+případě|bude-li|budou-li|stane\s+se|stanou\s+se|stal[ay]?\s+by|by\s+se\s+stal)~ui', $sentence)) {
                    continue;
                }
                // návrhy zmíněné jen jako důvod („jelikož byly přijaty návrhy A1 a P1“) se nepočítají
                $docs = [];
                if (preg_match_all('~(?i:(?:sněmovní\w*\s+)?\(?dokument\w*\)?|\bSD)\s*(?i:č\.|číslo)?\s*(\d{2,6})\b~u', $sentence, $d, PREG_SET_ORDER | PREG_OFFSET_CAPTURE)) {
                    foreach ($d as $hit) {
                        if (isset($sdLetters[(int)$hit[1][0]]) && !self::isReason($sentence, (int)$hit[0][1])) {
                            $docs[] = (int)$hit[1][0];
                        }
                    }
                }
                $out['sd'] = array_merge($out['sd'], $docs);
                // písmena jen tam, kde věta dokument neuvádí (jinak by se písmeno a číslo počítaly dvakrát)
                if ($docs === [] && $token !== null && preg_match_all('~(?<![\p{L}\d])' . $token . '~u', $sentence, $m, PREG_SET_ORDER | PREG_OFFSET_CAPTURE)) {
                    foreach ($m as $hit) {
                        if (!self::isReason($sentence, (int)$hit[0][1])) {
                            $out['pismena'][] = $hit[1][0];
                        }
                    }
                }
            }
        }
        return ['sd' => array_values(array_unique($out['sd'])), 'pismena' => array_values(array_unique($out['pismena']))];
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
     * @param array{sd: int[], pismena: string[]} $notVoted výstup notVoted(): návrhy, o kterých se podle zpravodaje nehlasuje
     * @return array<int, array{pismena: string[], stav: string, hlasovani: array, stanovisko: ?string}> číslo PN => výsledek
     */
    public static function outcomes(array $summary, array $mapped, array $authors, bool $voted = true, array $notVoted = ['sd' => [], 'pismena' => []]): array
    {
        $letters = $summary['pismena'];
        $out = [];
        foreach ($authors as $number => $author) {
            $number = (int)$number;
            $own = array_map('strval', array_keys(array_filter($letters, fn(array $sds) => in_array($number, $sds, true))));
            $guessed = $own !== [] && array_diff($own, $summary['odhad']) === [];
            // jen hlasování přesně k písmenu návrhu: „návrh B“ bez čísla se k B1, B2… nepřiřazuje,
            // protože nejde ověřit, zda se hlasovalo o všech najednou
            $votes = array_values(array_filter($mapped, function (array $v) use ($own, $guessed, $author, $number): bool {
                // zpravodaj jmenoval číslo dokumentu: hlasování patří právě tomuto návrhu, bez ohledu na písmeno
                if (($v['sd'] ?? null) !== null) {
                    return (int)$v['sd'] === $number;
                }
                if (!in_array($v['zaklad'], $own, true)) {
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
            // zpravodaj výslovně řekl, že se o návrhu nehlasuje: podle čísla dokumentu, nebo podle písmene
            // (jen písmeno uvedené v souhrnu výslovně, ne odvozené z pořadí)
            $dropped = $voted && (in_array($number, $notVoted['sd'] ?? [], true)
                || (!$guessed && array_intersect($own, $notVoted['pismena'] ?? []) !== []));
            if ($votes === [] && $dropped) {
                $state = 'nehlasovatelny';
            } elseif ($own === [] && $votes === []) {
                // v souhrnu není vůbec, nebo ho jen neumíme přiřadit k písmenu
                $state = in_array($number, $summary['zminena'], true) ? ($voted ? 'neurceno' : 'ceka') : 'nezaznel';
            } elseif ($votes === []) {
                // před 3. čtením návrh na hlasování teprve čeká. Po něm: hlasování k návrhu se nepodařilo přiřadit
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
            // označení, jak ho u hlasování řekl zpravodaj, má přednost před písmenem odvozeným ze souhrnu
            $spoken = array_values(array_unique(array_filter(array_map(fn(array $v) => ($v['sd'] ?? null) !== null && !str_starts_with((string)$v['pismeno'], 'SD ')
                ? (string)$v['pismeno'] : '', $votes))));
            $out[$number] = [
                'pismena' => $spoken !== [] ? $spoken : $own,
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
            $notVoted = ['sd' => [], 'pismena' => []];
            if ($steno && $votes !== []) {
                $speeches = [];
                foreach ($days as [$sitting, $day]) {
                    $speeches = array_merge($speeches, $this->speeches($ct, $sitting, $day));
                }
                $byLetter = [];
                foreach ($letters as $letter => $sds) {
                    $byLetter[(string)$letter] = array_values(array_filter(array_map(fn($sd) => $authors[(int)$sd] ?? '', $sds)));
                }
                // každý písemný PN tisku s písmenem ze souhrnu (prázdné, když písmeno neznáme nebo jich má víc)
                $sdLetters = array_fill_keys(array_keys($authors), '');
                $seen = [];
                foreach ($letters as $letter => $sds) {
                    foreach ($sds as $sd) {
                        if (isset($sdLetters[(int)$sd])) {
                            $sdLetters[(int)$sd] = isset($seen[(int)$sd]) ? '' : (string)$letter;
                            $seen[(int)$sd] = true;
                        }
                    }
                }
                $mapped = self::mapVotes($speeches, $votes, array_map('strval', array_keys($letters)), $byLetter, $sdLetters);
                $notVoted = self::notVoted($speeches, array_map('strval', array_keys($letters)), $sdLetters);
            }
            $results = self::outcomes($summary, $mapped, $authors, $votes !== [], $notVoted);
            $this->db->run('DELETE FROM pn_vysledek WHERE ct = ?', [$ct]);
            $now = date('Y-m-d H:i:s');
            $this->db->insertMany('pn_vysledek', ['ct', 'cislo', 'pismena', 'stav', 'hlasovani', 'stanovisko', 'updated_at'], array_map(
                fn(int $number, array $r) => ['ct' => $ct, 'cislo' => $number, 'pismena' => mb_substr(implode(', ', $r['pismena']), 0, 120), 'stav' => $r['stav'],
                    'hlasovani' => json_encode($r['hlasovani']), 'stanovisko' => $r['stanovisko'], 'updated_at' => $now],
                array_keys($results), $results));
            $stats['tisku']++;
            $stats['pn'] += count($results);
            $stats['prirazeno_hlasovani'] += count(array_unique(array_column($mapped, 'id_hlasovani')));
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
