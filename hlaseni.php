<?php
// Hlášení chyby ve vysvětlení pozměňovacího návrhu „v praxi“ (app/Reports.php).
//   GET  hlaseni?d={id dokumentu PN}  stránka s formulářem (bez JavaScriptu; jinak se formulář otevírá
//        jako vrstva na stránce tisku, assets/report.js)
//   POST hlaseni                      přijetí hlášení; s hlavičkou Accept: application/json odpoví JSON
//        {"ok": true} nebo {"error": "…"}, jinak stránkou s poděkováním nebo s formulářem a chybou

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\Reports;
use Hlidac\View;

$reports = new Reports(db(), config());
$wantsJson = str_contains((string)($_SERVER['HTTP_ACCEPT'] ?? ''), 'application/json');
$isPost = ($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'POST';
$result = null;

if (!headers_sent()) {
    // formulář a odpověď na něj nepatří do mezipaměti ani do vyhledávačů
    header('Cache-Control: no-store');
    header('X-Robots-Tag: noindex');
}
if ($isPost) {
    $result = $reports->submit($_POST, (string)($_SERVER['REMOTE_ADDR'] ?? ''), $_SERVER);
    if (isset($result['error']) && !headers_sent()) {
        http_response_code((int)$result['status']);
    }
    if ($wantsJson) {
        if (!headers_sent()) {
            header('Content-Type: application/json; charset=utf-8');
        }
        echo json_encode(isset($result['error']) ? ['error' => $result['error']] : ['ok' => true], JSON_UNESCAPED_UNICODE), "\n";
        exit;
    }
}

$id = (string)($isPost ? ($_POST['d'] ?? '') : ($_GET['d'] ?? ''));
$target = $reports->enabled() && is_string($id) && preg_match('~^\d{1,9}$~', $id) ? $reports->target((int)$id) : null;
$done = $isPost && isset($result['ok']);
if (!$done && $target === null && !isset($result['error']) && !headers_sent()) {
    http_response_code(404);
}
$back = $target !== null ? url('tisk', ['ct' => $target['ct']], 'pn-' . $target['cislo']) : url('tisky');

View::$noAnalytics = true;
View::header('Nahlásit chybu ve vysvětlení', 'tisky', 'Formulář pro nahlášení chyby ve vysvětlení pozměňovacího návrhu.');
?>
<section class="card pad report-page" aria-label="Nahlásit chybu ve vysvětlení">
  <h1>Nahlásit chybu ve vysvětlení</h1>
  <?php if ($done): ?>
    <?= View::reportThanks() ?>
    <p><a class="btn" href="<?= e($back) ?>">Zpět na návrh</a></p>
  <?php elseif ($target === null): ?>
    <p><?= e((string)($result['error'] ?? 'K tomuto návrhu vysvětlení nemáme.')) ?></p>
    <p><a class="btn" href="<?= e($back) ?>">Zpět na tisky</a></p>
  <?php else: ?>
    <p class="small"><strong>Vysvětlení na webu:</strong> <?= e((string)$target['praxe']['co_se_meni']) ?></p>
    <?= View::reportForm($target['id_dokument'], Reports::label($target['cislo'], $target['ct']),
        '<a class="btn btn-ghost" href="' . e($back) . '">Zrušit</a>', (string)($result['error'] ?? ''),
        $isPost ? ['cast' => is_string($_POST['cast'] ?? null) ? $_POST['cast'] : '', 'poznamka' => is_string($_POST['poznamka'] ?? null) ? $_POST['poznamka'] : ''] : []) ?>
  <?php endif; ?>
</section>
<?php
View::footer();
