<?php
// Obrázek pro sdílení odkazu na sociálních sítích (og:image): og.php?ct=221 pro tisk, og.php?p=tisky pro stránku.
// Hotové PNG je v data/cache/og; bez GD/FreeType se pošle statický assets/og-default.png.

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\OgCard;
use Hlidac\Repo;

$send = function (string $path): never {
    header('Content-Type: image/png');
    header('Content-Length: ' . filesize($path));
    header('Cache-Control: public, max-age=86400');
    header('X-Content-Type-Options: nosniff');
    readfile($path);
    exit;
};

if (!OgCard::available()) {
    $send(__DIR__ . '/assets/og-default.png');
}

try {
    $repo = new Repo(db());
    $ct = (int)($_GET['ct'] ?? 0);
    $bill = $ct > 0 ? $repo->bill($ct) : null;
    $card = $bill !== null
        ? OgCard::forBill($bill, config())
        : OgCard::forPage((string)($_GET['p'] ?? 'prehled'), config(), $repo->obdobi());
    $send(OgCard::cached($card, APP_ROOT . '/data/cache/og'));
} catch (Throwable $e) {
    error_log('og.php: ' . $e->getMessage());
    $send(__DIR__ . '/assets/og-default.png');
}
