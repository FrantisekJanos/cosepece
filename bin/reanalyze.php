<?php

declare(strict_types=1);

require dirname(__DIR__) . '/vendor/autoload.php';
$app = require dirname(__DIR__) . '/bootstrap/app.php';
exit($app->handleCommand(new Symfony\Component\Console\Input\ArrayInput([
    'command' => 'hlidac:texts', '--reanalyze' => true,
])));
