<?php
// Dožene stažení všech textů tisků a PN (kromě tisků, které už jsou zákonem): php bin/texts.php
// Po změně pravidel: php bin/texts.php --reanalyze
// Běžná synchronizace stahuje jen pár textů za běh; tohle projede vše najednou (šetrně, s pauzami).

declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    http_response_code(403);
    exit;
}

require dirname(__DIR__) . '/app/bootstrap.php';

use Hlidac\PnText;
use Hlidac\Scorer;
use Hlidac\TiskText;

// --reanalyze: jen přepočítá pravidlové signály z už stažených textů (po změně pravidel), nic nestahuje
if (in_array('--reanalyze', $argv, true)) {
    $n = 0;
    foreach (db()->all('SELECT ct, text FROM tisk_text WHERE chars > 0') as $x) {
        $a = TiskText::analyze((string)$x['text']);
        db()->run('UPDATE tisk_text SET signals = ? WHERE ct = ?', [json_encode($a['signals'], JSON_UNESCAPED_UNICODE), (int)$x['ct']]);
        $n++;
    }
    foreach (db()->all('SELECT x.id_dokument, x.text, t.zakony FROM pn_text x LEFT JOIN tisky t ON t.ct = x.ct WHERE x.chars > 0') as $x) {
        $a = PnText::analyze((string)$x['text'], array_filter(explode(',', (string)$x['zakony'])), config());
        db()->run('UPDATE pn_text SET signals = ?, laws = ?, rule_score = ? WHERE id_dokument = ?',
            [json_encode($a['signals'], JSON_UNESCAPED_UNICODE), implode(',', $a['laws']), (int)$a['score'], (int)$x['id_dokument']]);
        $n++;
    }
    $scorer = new Scorer(db(), config());
    $scorer->scoreBills();
    $scorer->scoreAlerts();
    echo "Přepočteno $n textů\n";
    exit(0);
}

$config = config();
$config['tisk_text'] = ['enabled' => true, 'per_run' => 25];
$config['pn_text'] = ['enabled' => true, 'per_run' => 25];

$total = ['tisky' => 0, 'pn' => 0, 'errors' => 0];
foreach (['tisky' => new TiskText(db(), $config), 'pn' => new PnText(db(), $config)] as $label => $fetcher) {
    do {
        $stats = $fetcher->run();
        $total[$label] += $stats['fetched'];
        $total['errors'] += $stats['errors'];
        echo date('H:i:s') . " $label: +{$stats['fetched']} (chyb {$stats['errors']}), celkem {$total[$label]}\n";
    } while ($stats['fetched'] + $stats['errors'] > 0);
}

// pravidlové signály z nových textů se promítnou do skóre hned
$scorer = new Scorer(db(), config());
$scorer->scoreBills();
$scorer->scoreAlerts();
echo json_encode($total, JSON_UNESCAPED_UNICODE), "\n";
