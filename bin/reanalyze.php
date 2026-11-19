<?php
// Znovu spočítá pravidlové signály (přílepek, rozsah, zpětnost, lhůty) u už stažených textů tisků a PN,
// bez stahování z psp.cz a bez AI; pak přepočítá skóre. Použití po změně pravidel v PnText/TiskText:
//   php bin/reanalyze.php

declare(strict_types=1);

require_once __DIR__ . '/../app/bootstrap.php';

use Hlidac\PnText;
use Hlidac\Scorer;
use Hlidac\TiskText;

$db = db();
$changed = ['pn' => 0, 'tisk' => 0];
foreach ($db->all('SELECT x.id_dokument, x.text, x.signals, x.rule_score, t.zakony FROM pn_text x JOIN tisky t ON t.ct = x.ct') as $r) {
    $a = PnText::analyze((string)$r['text'], array_filter(explode(',', (string)$r['zakony'])), config());
    $signals = json_encode($a['signals'], JSON_UNESCAPED_UNICODE);
    if ($signals !== (string)$r['signals'] || (int)$a['score'] !== (int)$r['rule_score']) {
        $db->run('UPDATE pn_text SET signals = ?, rule_score = ?, laws = ?, chars = ? WHERE id_dokument = ?',
            [$signals, (int)$a['score'], implode(',', $a['laws']), (int)$a['chars'], (int)$r['id_dokument']]);
        $changed['pn']++;
    }
}
foreach ($db->all('SELECT ct, text, signals FROM tisk_text') as $r) {
    $a = TiskText::analyze((string)$r['text']);
    $signals = json_encode($a['signals'], JSON_UNESCAPED_UNICODE);
    if ($signals !== (string)$r['signals']) {
        $db->run('UPDATE tisk_text SET signals = ?, chars = ? WHERE ct = ?', [$signals, (int)$a['chars'], (int)$r['ct']]);
        $changed['tisk']++;
    }
}
$scorer = new Scorer($db, config());
$scorer->scoreBills();
$scorer->scoreAlerts();
echo 'Změněno: ' . json_encode($changed) . ", skóre přepočítáno\n";
