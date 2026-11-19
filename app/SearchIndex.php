<?php
// Index pro vyhledávání a našeptávač: tisky (i podle shrnutí AI), pozměňovací návrhy a jejich autoři,
// zákony (slovník z názvů tisků), připravované zákony, druhy problémů a stránky webu.
// Staví se při synchronizaci do data/cache/search-index.json; prohlížeč ho stáhne jednou
// (hledat.php?index=1) a našeptává sám, stránka výsledků (hledat.php) hledá stejným postupem v PHP.
// Hledání: bez ohledu na diakritiku a velikost písmen, každé slovo dotazu musí být začátkem
// některého slova položky; když nic, toleruje se jeden překlep u slov od 5 písmen.

declare(strict_types=1);

namespace Hlidac;

final class SearchIndex
{
    public const VERSION = 1;
    // druhy položek v pořadí skupin: klíč => název skupiny
    public const KINDS = [
        'tisk' => 'Tisky',
        'zakon' => 'Zákony',
        'pn' => 'Pozměňovací návrhy',
        'osoba' => 'Autoři pozměňovacích návrhů',
        'prip' => 'Připravované u vlády',
        'tema' => 'Témata a stránky',
    ];

    public function __construct(private Db $db)
    {
    }

    public static function path(): string
    {
        // jeden soubor na databázi (testy a náhledy s jinou DB si index nepřepíšou)
        return APP_ROOT . '/data/cache/search-index-' . substr(md5((string)config('db_dsn', '')), 0, 8) . '.json';
    }

    /** Sestaví a uloží index; vrací počet položek. */
    public function rebuild(?string $path = null): int
    {
        $index = $this->build();
        $path ??= self::path();
        @mkdir(dirname($path), 0775, true);
        $tmp = $path . '.tmp';
        file_put_contents($tmp, json_encode($index, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
        rename($tmp, $path);
        return count($index['items']);
    }

    /** Uložený index; když chybí nebo vznikl před poslední synchronizací, sestaví se znovu. */
    public function load(): array
    {
        $path = self::path();
        $index = is_file($path) ? json_decode((string)file_get_contents($path), true) : null;
        if (!is_array($index) || ($index['v'] ?? 0) !== self::VERSION || ($index['sync'] ?? '') !== (string)$this->db->getMeta('last_sync', '')) {
            try {
                $this->rebuild($path);
                $index = json_decode((string)file_get_contents($path), true);
            } catch (\Throwable $ex) {
                error_log('SearchIndex: ' . $ex->getMessage());
            }
        }
        return is_array($index) ? $index : $this->build();
    }

    public function build(): array
    {
        // položka: k druh, id, t titulek, s podtitulek, d ukázka textu, x další hledaný text, u adresa, w váha (skóre)
        $items = [];
        $repo = new Repo($this->db);
        $laws = [];
        $lawCount = [];
        foreach ($this->db->all('SELECT ct, nazev, uplny_nazev, navrhovatel, faze, zakony, score FROM tisky ORDER BY ct') as $t) {
            $ct = (int)$t['ct'];
            $h = $repo->headline($ct);
            $items[] = [
                'k' => 'tisk', 'id' => (string)$ct,
                't' => (string)$t['nazev'],
                's' => 'Tisk ' . $ct . ' · ' . $t['faze'],
                'd' => $h !== null ? 'AI: ' . $h['title'] : self::shorten((string)$t['uplny_nazev'], 140),
                'x' => trim($t['uplny_nazev'] . ' ' . $t['navrhovatel'] . ' ' . str_replace(',', ' ', (string)$t['zakony'])
                    . ($h !== null ? ' ' . $h['perex'] : '')),
                'u' => 'tisk.php?ct=' . $ct,
                'w' => (int)$t['score'],
            ];
            // slovník zákonů z úplných názvů: „zákon č. 586/1992 Sb., o daních z příjmů“
            preg_match_all('~zákon\w*\s+č\.\s*(\d+/\d{4})\s*Sb\.,\s*((?:o|kterým)\s[^,;()]+)~u', (string)$t['uplny_nazev'], $m, PREG_SET_ORDER);
            foreach ($m as $law) {
                $laws[$law[1]] ??= trim($law[2]);
            }
            foreach (array_filter(explode(',', (string)$t['zakony'])) as $law) {
                $lawCount[trim($law)] = ($lawCount[trim($law)] ?? 0) + 1;
            }
        }
        foreach ($lawCount as $law => $n) {
            $items[] = [
                'k' => 'zakon', 'id' => $law,
                't' => $law . ' Sb.' . (isset($laws[$law]) ? ' ' . $laws[$law] : ''),
                's' => 'mění ' . $n . ' ' . ($n === 1 ? 'tisk' : ($n < 5 ? 'tisky' : 'tisků')),
                'd' => '', 'x' => '',
                'u' => 'tisky.php?q=' . rawurlencode($law),
                'w' => $n,
            ];
        }

        // pozměňovací návrhy: číslo, autor, tisk a shrnutí AI (je-li)
        $ai = [];
        foreach ($this->db->all('SELECT id_dokument, ai_json FROM pn_text WHERE ai_json IS NOT NULL') as $r) {
            $ai[(int)$r['id_dokument']] = json_decode((string)$r['ai_json'], true) ?: [];
        }
        $names = [];
        foreach ($this->db->all('SELECT id_dokument, cislo, ct, osoba, cas FROM pn ORDER BY cas DESC') as $p) {
            $a = $ai[(int)$p['id_dokument']] ?? [];
            $osoba = trim((string)$p['osoba']);
            $items[] = [
                'k' => 'pn', 'id' => (string)(int)$p['cislo'],
                't' => trim((string)($a['titulek'] ?? '')) !== '' ? 'AI: ' . self::shorten((string)$a['titulek'], 140) : 'Pozměňovací návrh č. ' . (int)$p['cislo'],
                's' => 'PN č. ' . (int)$p['cislo'] . ' k tisku ' . (int)$p['ct'] . ($osoba !== '' ? ' · ' . $osoba : ''),
                'd' => self::shorten((string)($a['perex'] ?? ''), 160),
                'x' => '',
                'u' => 'tisk.php?ct=' . (int)$p['ct'] . '#pn-' . (int)$p['cislo'],
                'w' => (int)($a['zavaznost'] ?? 0),
            ];
            if ($osoba !== '') {
                $names[$osoba] = ($names[$osoba] ?? 0) + 1;
            }
        }
        foreach ($names as $name => $n) {
            $items[] = [
                'k' => 'osoba', 'id' => $name, 't' => $name,
                's' => $n . ' ' . ($n === 1 ? 'pozměňovací návrh' : ($n < 5 ? 'pozměňovací návrhy' : 'pozměňovacích návrhů')),
                'd' => '', 'x' => '',
                'u' => 'hledat.php?q=' . rawurlencode($name),
                'w' => $n,
            ];
        }

        // připravované zákony u vlády
        $phase = ['pripominky' => 'v připomínkovém řízení', 'po_pripominkach' => 'po připomínkovém řízení', 'vlada' => 'předloženo vládě', 'schvaleno' => 'schváleno vládou'];
        foreach ($this->db->all('SELECT pid, nazev, resort, faze, popis FROM pripravovane') as $u) {
            $items[] = [
                'k' => 'prip', 'id' => (string)$u['pid'],
                't' => self::shorten((string)$u['nazev'], 160),
                's' => $u['resort'] . ' · ' . ($phase[$u['faze']] ?? $u['faze']),
                'd' => '', 'x' => self::shorten((string)$u['popis'], 300),
                'u' => GovPrep::url((string)$u['pid']),
                'w' => 0,
            ];
        }

        // druhy problémů (filtr hlavních nálezů) a stránky webu
        $groupWords = [
            'prilepky' => 'přílepek nesouvisející změna jiný zákon',
            'zmeny_pn' => 'pozměňovací návrh rozsáhlá nová úprava',
            'lhuty' => 'zkrácená lhůta zrychlení málo času',
            'par90' => 'paragraf 90 odst. 2 první čtení zrychlené projednání',
            'pripominky' => 'připomínkové řízení poslanecký návrh senátní',
            'zpetne' => 'zpětná účinnost retroaktivita zpětně',
        ];
        foreach (Repo::PROBLEM_GROUPS as $key => [$label]) {
            $items[] = ['k' => 'tema', 'id' => $key, 't' => $label, 's' => 'hlavní nálezy podle druhu problému', 'd' => '',
                'x' => $groupWords[$key] ?? '', 'u' => 'index.php?druh=' . $key . '#nalezy', 'w' => 0];
        }
        $pages = [
            ['Jak hodnotíme', 'metodika skóre body váhy omezení odpovědnosti AI', 'metodika.php'],
            ['Připravované zákony', 'vláda připomínkové řízení VeKLEP ministerstvo', 'pripravovane.php'],
            ['Data a API', 'JSON RSS export stažení vývojáři', 'data.php'],
            ['Co jsme změnili', 'změny opravy novinky', 'zmeny.php'],
            ['Upozornění', 'upozornění RSS nové kroky', 'upozorneni.php'],
        ];
        foreach ($pages as [$title, $words, $url]) {
            $items[] = ['k' => 'tema', 'id' => $url, 't' => $title, 's' => 'stránka webu', 'd' => '', 'x' => $words, 'u' => $url, 'w' => 0];
        }
        return ['v' => self::VERSION, 'built' => date('Y-m-d H:i:s'), 'sync' => (string)$this->db->getMeta('last_sync', ''),
            'kinds' => self::KINDS, 'items' => $items];
    }

    /**
     * Hledání v indexu (stejný postup jako assets/search.js).
     * @return array<string, array> druh => seřazené položky (nejvýš $perKind)
     */
    public static function search(array $index, string $query, int $perKind = 5): array
    {
        $tokens = self::tokens($query);
        if ($tokens === []) {
            return [];
        }
        $hits = self::match($index['items'] ?? [], $tokens, false);
        if ($hits === []) {
            $hits = self::match($index['items'] ?? [], $tokens, true);
        }
        usort($hits, fn(array $a, array $b) => [$b[0], (int)$b[1]['w']] <=> [$a[0], (int)$a[1]['w']]);
        $out = [];
        foreach ($hits as [$score, $item]) {
            if (count($out[$item['k']] ?? []) < $perKind) {
                $out[$item['k']][] = $item + ['score' => $score];
            }
        }
        // skupiny v pořadí podle nejlepšího zásahu, při shodě podle KINDS
        $order = array_flip(array_keys(self::KINDS));
        uksort($out, fn(string $a, string $b) => [-$out[$a][0]['score'], $order[$a] ?? 9] <=> [-$out[$b][0]['score'], $order[$b] ?? 9]);
        return $out;
    }

    public static function fold(string $s): string
    {
        return Repo::fold($s);
    }

    /** @return string[] */
    public static function tokens(string $query): array
    {
        $q = self::fold(trim($query));
        // „tisk 221“, „sněmovní tisk 221“ → hledá se číslo
        $q = (string)preg_replace('~\b(snemovni\s+)?tisk(u)?\s+(?=\d)~u', '', $q);
        $parts = preg_split('~[^\p{L}\p{N}/]+~u', $q, -1, PREG_SPLIT_NO_EMPTY) ?: [];
        return array_values(array_unique(array_slice($parts, 0, 8)));
    }

    private static function match(array $items, array $tokens, bool $fuzzy): array
    {
        $hits = [];
        foreach ($items as $item) {
            // u tisku je titulek z AI (d) vlastně hlavní název, váží jako titulek
            $main = $item['k'] === 'tisk';
            $title = self::words($item['t'] . ' ' . $item['id'] . ($main ? ' ' . $item['d'] : ''));
            $rest = self::words($item['s'] . ' ' . ($main ? '' : $item['d']) . ' ' . $item['x']);
            $score = 0.0;
            foreach ($tokens as $tok) {
                // přesná shoda čísla tisku nebo zákona
                if ($tok === self::fold((string)$item['id']) && in_array($item['k'], ['tisk', 'zakon'], true)) {
                    $score += 100;
                    continue;
                }
                $s = self::tokenScore($tok, $title, $fuzzy) * 3;
                if ($s === 0.0) {
                    $s = self::tokenScore($tok, $rest, $fuzzy);
                }
                if ($s === 0.0) {
                    continue 2;
                }
                $score += $s;
            }
            // čísla PN se hledají jen celým číslem, ne začátkem („2“ nemá najít PN 2044)
            $hits[] = [$score, $item];
        }
        return $hits;
    }

    private static function tokenScore(string $tok, array $words, bool $fuzzy): float
    {
        // čísla (tisků, PN) jen celá: „2“ nemá najít PN 2044
        $best = 0.0;
        $numeric = ctype_digit($tok);
        $stem = $numeric ? '' : self::stem($tok);
        foreach ($words as $w) {
            if ($w === $tok) {
                return 1.5;
            }
            if (!$numeric && str_starts_with($w, $tok)) {
                $best = max($best, 1.0);
            } elseif ($stem !== '' && str_starts_with($w, $stem)) {
                $best = max($best, 0.9);
            } elseif ($fuzzy && !$numeric && strlen($tok) >= 5 && self::nearlyEqual(substr($w, 0, strlen($tok)), $tok)) {
                $best = max($best, 0.5);
            }
        }
        return $best;
    }

    public static function stem(string $tok): string
    {
        // hrubé odříznutí české koncovky, ať „zbraně“ najde „zbraní“ a „dotace“ „dotacích“ (jen u slov od 5 písmen)
        if (strlen($tok) < 5 || preg_match('~\d~', $tok)) {
            return '';
        }
        $stem = (string)preg_replace('~(ich|ech|ami|emi|ovi|ove|ou|um|[aeiouy])$~', '', $tok);
        return $stem !== $tok && strlen($stem) >= 4 ? $stem : '';
    }

    public static function nearlyEqual(string $a, string $b): bool
    {
        // nejvýš jedna úprava: záměna, vložení, vynechání nebo prohození dvou sousedních znaků
        if ($a === $b) {
            return true;
        }
        $la = strlen($a);
        $lb = strlen($b);
        if ($la === $lb) {
            $diff = [];
            for ($i = 0; $i < $la; $i++) {
                if ($a[$i] !== $b[$i]) {
                    $diff[] = $i;
                }
            }
            return count($diff) === 1
                || (count($diff) === 2 && $diff[1] === $diff[0] + 1 && $a[$diff[0]] === $b[$diff[1]] && $a[$diff[1]] === $b[$diff[0]]);
        }
        return abs($la - $lb) === 1 && levenshtein($a, $b) === 1;
    }

    private static function words(string $text): array
    {
        return preg_split('~[^\p{L}\p{N}/]+~u', self::fold($text), -1, PREG_SPLIT_NO_EMPTY) ?: [];
    }

    private static function shorten(string $text, int $max): string
    {
        $text = trim((string)preg_replace('~\s+~u', ' ', $text));
        return mb_strlen($text) > $max ? rtrim(mb_substr($text, 0, $max - 1)) . '…' : $text;
    }
}
