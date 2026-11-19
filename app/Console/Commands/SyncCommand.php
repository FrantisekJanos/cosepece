<?php

declare(strict_types=1);

namespace App\Console\Commands;

use Illuminate\Console\Command;


final class SyncCommand extends Command
{
    protected $signature = 'hlidac:sync';
    protected $description = 'Synchronizace dat z PSP, skóre a publikování';

    public function handle(): int
    {
        try {
            $result = \Hlidac\Sync::run();
            $this->output->write(json_encode($result, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT) . "\n");
            return 0;
        } catch (\Throwable $ex) {
            $this->output->write('Chyba: ' . $ex->getMessage() . "\n");
            return 1;
        }
    }
}
