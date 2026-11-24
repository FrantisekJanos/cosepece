<?php
// Místní náhled bez Apache (spouštět z kořene projektu):
//   HLIDAC_CONFIG=/cesta/config.php php -S 127.0.0.1:8099 bin/router.php
// Vestavěný server PHP nečte .htaccess, proto hezké adresy (app/Url.php) a zákazy z .htaccess řeší tento skript.

declare(strict_types=1);

if (PHP_SAPI !== 'cli-server') {
    http_response_code(403);
    exit;
}

$root = dirname(__DIR__);
require_once $root . '/app/Url.php';

$rel = ltrim((string)parse_url((string)($_SERVER['REQUEST_URI'] ?? '/'), PHP_URL_PATH), '/');
// stejné zákazy jako v .htaccess: interní adresáře, soubory začínající tečkou, konfigurace a dokumentace
if (preg_match('~^(app|bin|data|deploy|docker|rag|reels|tests|podnety)(/|$)~', $rel)
    || preg_match('~(^|/)\.(?!well-known/)~', $rel)
    || preg_match('~(^|/)(config\.php|config\.example\.php|[^/]*\.md|[^/]*\.sh|[^/]*\.env[^/]*|[^/]*\.plist|composer\.[^/]*)$~', $rel)) {
    http_response_code(403);
    echo 'Zakázáno.';
    return true;
}
// existující soubor (skript, styl, obrázek) vydá server sám
if ($rel !== '' && is_file($root . '/' . $rel)) {
    return false;
}
$route = Hlidac\Url::route($rel);
if ($route === null) {
    http_response_code(404);
    echo 'Stránka nenalezena.';
    return true;
}
[$script, $params] = $route;
// jako [QSA] v .htaccess: parametr z dotazu má přednost před parametrem z cesty
$_GET = array_merge($params, $_GET);
$_SERVER['SCRIPT_NAME'] = $_SERVER['PHP_SELF'] = '/' . $script;
$_SERVER['SCRIPT_FILENAME'] = $root . '/' . $script;
chdir($root);
require $root . '/' . $script;
