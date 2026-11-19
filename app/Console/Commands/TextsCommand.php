<?php

declare(strict_types=1);

namespace App\Console\Commands;

use Illuminate\Console\Command;
use Hlidac\PnText;
use Hlidac\Scorer;
use Hlidac\TiskText;

final class TextsCommand extends Command
{
    protected $signature = 'hlidac:texts {--reanalyze}';
    protected $description = 'Stažení textů nebo přepočet pravidlových signálů';

    public function handle(): int
    {

        if ($this->option('reanalyze')) {
            $n = 0;
            foreach (db()->all('SELECT ct, text FROM tisk_text WHERE chars > 0') as $x) {
                $a = TiskText::analyze((string)$x['text']);
                db()->run('UPDATE tisk_text SET signals = ? WHERE ct = ?', [json_encode($a['signals'], JSON_UNESCAPED_UNICODE), (int)$x['ct']]);
                $n++;
            }
            foreach (db()->all('SELECT x.id_dokument, x.text, t.zakony FROM pn_text x LEFT JOIN tisky t ON t.ct = x.ct WHERE x.chars > 0') as $x) {
                $a = PnText::analyze((string)$x['text'], array_filter(explode(',', (string)$x['zakony'])), hlidacConfig());
                db()->run('UPDATE pn_text SET signals = ?, laws = ?, rule_score = ? WHERE id_dokument = ?',
                    [json_encode($a['signals'], JSON_UNESCAPED_UNICODE), implode(',', $a['laws']), (int)$a['score'], (int)$x['id_dokument']]);
                $n++;
            }
            $scorer = new Scorer(db(), hlidacConfig());
            $scorer->scoreBills();
            $scorer->scoreAlerts();
            $this->output->write("Přepočteno $n textů\n");
            return 0;
        }

        $config = hlidacConfig();
        $config['tisk_text'] = ['enabled' => true, 'per_run' => 25];
        $config['pn_text'] = ['enabled' => true, 'per_run' => 25];

        $total = ['tisky' => 0, 'pn' => 0, 'errors' => 0];
        foreach (['tisky' => new TiskText(db(), $config), 'pn' => new PnText(db(), $config)] as $label => $fetcher) {
            do {
                $stats = $fetcher->run();
                $total[$label] += $stats['fetched'];
                $total['errors'] += $stats['errors'];
                $this->output->write(date('H:i:s') . " $label: +{$stats['fetched']} (chyb {$stats['errors']}), celkem {$total[$label]}\n");
            } while ($stats['fetched'] + $stats['errors'] > 0);
        }

        $scorer = new Scorer(db(), hlidacConfig());
        $scorer->scoreBills();
        $scorer->scoreAlerts();
        $this->output->write(json_encode($total, JSON_UNESCAPED_UNICODE) . "\n");

        return $total['errors'] > 0 ? 1 : 0;
    }
}
