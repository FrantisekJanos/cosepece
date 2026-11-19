<?php

declare(strict_types=1);

use Illuminate\Support\Facades\Schedule;

Schedule::exec(escapeshellarg(PHP_BINARY) . ' -d memory_limit=1024M ' . escapeshellarg(base_path('artisan')) . ' hlidac:sync')->name('hlidac:sync')->hourly()->withoutOverlapping(120);
