<?php
// Strojový výstup (JSON, jen čtení) pro napojení dalších nástrojů (#28):
//   api.php                       tisky od poslední změny (since=YYYY-MM-DD, min_score, zakon=159/2006, limit)
//   api.php?tisk=221              detail tisku: skóre, důvody, hlasování, upozornění
//   api.php?upozorneni=1          upozornění (since, min_score, zakon, ct=221, limit)

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\Api;
use Hlidac\Repo;

[$status, $body] = (new Api(db(), new Repo(db()), config()))->handle($_GET);
if (!headers_sent()) {
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Access-Control-Allow-Origin: *');
    header('Cache-Control: public, max-age=300');
    header('X-Content-Type-Options: nosniff');
}
echo json_encode($body, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT), "\n";
