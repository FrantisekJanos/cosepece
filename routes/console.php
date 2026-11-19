<?php

declare(strict_types=1);

use Illuminate\Support\Facades\Schedule;

Schedule::command('hlidac:sync')->hourly()->withoutOverlapping(120);
