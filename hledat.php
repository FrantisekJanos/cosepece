<?php
// Vyhledávání: stránka výsledků rozdělená do skupin (funguje i bez JavaScriptu) a index pro našeptávač
// (hledat.php?index=1, JSON; prohlížeč ho stáhne jednou a hledá v něm sám, viz assets/search.js).
// Dotazy se nikam neukládají.

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\SearchIndex;
use Hlidac\View;

$search = new SearchIndex(db());

if (!empty($_GET['index'])) {
    $index = $search->load();
    $etag = '"si' . SearchIndex::VERSION . '-' . md5((string)$index['built'] . '|' . ($index['sync'] ?? '')) . '"';
    if (!headers_sent()) {
        header('Content-Type: application/json; charset=utf-8');
        // adresa nese otisk synchronizace (View::searchForm), takže smí zůstat v mezipaměti déle
        header('Cache-Control: public, max-age=' . (strlen((string)$_GET['index']) > 1 ? 86400 : 300));
        header('ETag: ' . $etag);
        header('X-Content-Type-Options: nosniff');
        if (trim((string)($_SERVER['HTTP_IF_NONE_MATCH'] ?? '')) === $etag) {
            http_response_code(304);
            exit;
        }
    }
    $body = json_encode($index, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    // komprese (proxy před webem ji nemusí dělat); index je jinak několik set kB
    if (!headers_sent() && str_contains((string)($_SERVER['HTTP_ACCEPT_ENCODING'] ?? ''), 'gzip') && function_exists('gzencode')) {
        header('Content-Encoding: gzip');
        header('Vary: Accept-Encoding');
        $body = gzencode((string)$body, 6);
    }
    echo $body;
    exit;
}

$q = mb_substr(trim((string)($_GET['q'] ?? '')), 0, 100);
// samotné číslo tisku vede rovnou na tisk
if (preg_match('~^(?:(?:sněmovní\s+)?tisk\s*)?(\d{1,4})$~ui', $q, $m) && db()->value('SELECT 1 FROM tisky WHERE ct = ?', [(int)$m[1]]) !== null) {
    if (!headers_sent()) {
        header('Location: tisk.php?ct=' . (int)$m[1], true, 302);
    }
    exit;
}
$groups = $q !== '' ? SearchIndex::search($search->load(), $q, 50) : [];
$total = array_sum(array_map('count', $groups));

View::header($q !== '' ? 'Hledání: ' . $q : 'Hledání', 'hledat', 'Hledání v tiscích, pozměňovacích návrzích, zákonech a připravovaných zákonech.');
?>
<section class="hero">
  <div>
    <h1>Hledání</h1>
    <p class="lead"><?= $q === '' ? 'Tisky, pozměňovací návrhy a jejich autoři, zákony, připravované zákony a témata.' : ($total > 0 ? $total . ' výsledků pro „' . e($q) . '“' : 'Pro „' . e($q) . '“ jsme nic nenašli.') ?></p>
  </div>
  <?php View::searchForm($q); ?>
</section>

<?php if ($q !== '' && $total === 0): ?>
<section class="card pad">
  <p>Zkuste kratší nebo obecnější slovo, číslo tisku (např. <a href="hledat.php?q=221">221</a>), číslo zákona (<a href="hledat.php?q=159%2F2006">159/2006</a>) nebo jméno poslance. Hledá se bez ohledu na diakritiku a stačí začátek slova.</p>
</section>
<?php endif; ?>

<?php foreach ($groups as $kind => $items): ?>
<section class="card search-group" aria-label="<?= e(SearchIndex::KINDS[$kind] ?? $kind) ?>">
  <div class="card-head"><h2><?= e(SearchIndex::KINDS[$kind] ?? $kind) ?> <span class="muted">· <?= count($items) ?><?= count($items) >= 50 ? '+' : '' ?></span></h2></div>
  <ul class="search-results">
    <?php foreach ($items as $it): ?>
      <li>
        <a href="<?= e($it['u']) ?>"><?php if ($it['k'] === 'tisk'): ?><?= View::scoreBadge((int)$it['w']) ?> <?php endif; ?><strong><?= e($it['t']) ?></strong></a>
        <?php if ($it['s'] !== ''): ?><span class="muted small"><?= e($it['s']) ?></span><?php endif; ?>
        <?php if ($it['d'] !== ''): ?><span class="small"><?= e($it['d']) ?></span><?php endif; ?>
      </li>
    <?php endforeach; ?>
  </ul>
</section>
<?php endforeach; ?>
<?php if ($total > 0): ?><p class="muted small">Položky začínající „AI:“ jsou shrnutí připravená umělou inteligencí, která se může mýlit.</p><?php endif; ?>
<?php
View::footer();
