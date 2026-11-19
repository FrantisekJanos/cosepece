<?php

declare(strict_types=1);

use Hlidac\Db;

function hlidacConfig(?string $key = null, mixed $default = null): mixed
{
    return config($key === null ? 'hlidac' : 'hlidac.' . $key, $default);
}

function db(): Db
{
    return app(Db::class);
}

function hlidacDataPath(string $path = ''): string
{
    return rtrim((string)hlidacConfig('data_path'), '/') . ($path === '' ? '' : '/' . ltrim($path, '/'));
}

function formatDate(?string $value, bool $withTime = false): string
{
    if ($value === null || $value === '') {
        return '—';
    }
    $ts = strtotime($value);
    if ($ts === false) {
        return $value;
    }
    $out = date('j. n. Y', $ts);
    if ($withTime && date('H:i', $ts) !== '00:00') {
        $out .= ', ' . date('H:i', $ts);
    }
    return $out;
}

function siteUrl(string $path = ''): string
{
    return rtrim((string)hlidacConfig('site_url'), '/') . '/' . ltrim($path, '/');
}
