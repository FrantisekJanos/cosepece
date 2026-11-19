<?php

declare(strict_types=1);
require dirname(__DIR__) . '/vendor/autoload.php';
$app = require dirname(__DIR__) . '/bootstrap/app.php';
$kernel = $app->make(Illuminate\Contracts\Console\Kernel::class);
$input = new Symfony\Component\Console\Input\ArgvInput(array_merge([$argv[0], 'hlidac:texts'], array_slice($argv, 1)));
$output = new Symfony\Component\Console\Output\ConsoleOutput();
$status = $kernel->handle($input, $output);
$kernel->terminate($input, $status);
exit($status);
