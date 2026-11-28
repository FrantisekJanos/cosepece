<?php
// Přehled hlášení čtenářů k vysvětlením pozměňovacích návrhů „v praxi“ (app/Reports.php).
//   php bin/hlaseni.php                 hlášení k vyřízení: platná, k ručnímu posouzení a čekající na třídění
//   php bin/hlaseni.php --vse           všechna včetně vyřazených (spam, nesmysl, manipulace, mimo téma)
//   php bin/hlaseni.php --stav=spam     jen jeden stav (nove, platne, rucne, spam, nesmysl, manipulace, mimo_tema)
//   php bin/hlaseni.php --limit=100     kolik nejnovějších vypsat (výchozí 50)
//   php bin/hlaseni.php --rozhodnout=12 --jako=platne   ruční rozhodnutí (platne nebo některý z vyřazujících stavů)
// Poznámky čtenářů jsou cizí text: přehled je jen vypisuje, nic podle nich nedělá.

declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    http_response_code(403);
    exit;
}

require dirname(__DIR__) . '/app/bootstrap.php';

use Hlidac\Reports;

$opt = getopt('', ['vse', 'stav:', 'limit:', 'rozhodnout:', 'jako:']);
$reports = new Reports(db(), config());

if (isset($opt['rozhodnout'])) {
    $ok = $reports->decide((int)$opt['rozhodnout'], (string)($opt['jako'] ?? ''));
    echo $ok ? "Hlášení {$opt['rozhodnout']} označeno jako {$opt['jako']}\n" : "Nepovedlo se: hlášení neexistuje, nebo --jako není platne, spam, nesmysl, manipulace ani mimo_tema\n";
    exit($ok ? 0 : 1);
}

$counts = $reports->counts();
echo 'Hlášení celkem: ' . array_sum($counts);
foreach (Reports::STATE_LABELS as $state => $label) {
    if (($counts[$state] ?? 0) > 0) {
        echo ' · ' . $label . ' ' . $counts[$state];
    }
}
echo "\n";

$states = isset($opt['stav']) ? [(string)$opt['stav']] : (isset($opt['vse']) ? array_keys(Reports::STATE_LABELS) : null);
foreach ($reports->list($states, (int)($opt['limit'] ?? 50)) as $row) {
    $refs = json_decode((string)$row['odkazy'], true);
    echo "\n#" . $row['id'] . ' · ' . $row['vytvoreno'] . ' · ' . (Reports::STATE_LABELS[$row['stav']] ?? $row['stav'])
        . ($row['duvod'] !== null ? ' (' . $row['duvod'] . ')' : '') . "\n"
        . '  ' . Reports::label((int)$row['cislo'], (int)$row['ct']) . ' · ' . absUrl('tisk', ['ct' => (int)$row['ct']], 'pn-' . (int)$row['cislo']) . "\n"
        . '  Vysvětlení v době hlášení: ' . ($row['praxe_model'] ?? '?') . ', ' . ($row['praxe_at'] ?? '?') . "\n"
        . '  Část: ' . (Reports::PARTS[$row['oblast']] ?? $row['oblast']) . "\n";
    if (is_array($refs) && $refs !== []) {
        echo '  Ustanovení ověřená v textu návrhu: ' . implode(', ', $refs) . "\n";
    }
    if ((string)$row['poznamka'] !== '') {
        // poznámka odsazená, aby šla v přehledu odlišit od našich údajů
        echo "  Poznámka čtenáře:\n    | " . str_replace("\n", "\n    | ", (string)$row['poznamka']) . "\n";
    }
}
