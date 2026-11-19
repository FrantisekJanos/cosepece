<?php
// Seznam tisků s filtry a hledáním.

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\Repo;
use Hlidac\View;

$filters = [
    '' => 'Všechny tisky',
    'flagged' => 'Všechny příznaky',
    'mp' => 'Poslanecké a senátní návrhy',
    'p90' => 'Zrychlené čtení',
    'cut' => 'Zkrácená lhůta',
    'surge' => 'Nával PN',
];
$f = (string)($_GET['f'] ?? '');
if (!array_key_exists($f, $filters)) {
    $f = '';
}
$q = trim((string)($_GET['q'] ?? ''));
$q = mb_substr($q, 0, 100);
$sort = ($_GET['sort'] ?? '') === 'recent' ? 'recent' : 'score';
$min = max(0, min(100, (int)($_GET['min'] ?? 0)));

$repo = new Repo(db());
$bills = $repo->bills($f, $q, 500, $sort, $min);

View::header('Tisky', 'tisky');
?>
<section class="hero">
  <div>
    <h1>Návrhy zákonů</h1>
    <p class="lead"><?= count($bills) ?> tisků<?= $q !== '' ? ' pro „' . e($q) . '“' : '' ?> · <?= e($filters[$f]) ?><?= $min > 0 ? ' · skóre ' . $min . '+' : '' ?> · řazeno <?= $sort === 'recent' ? 'podle poslední změny' : 'podle závažnosti' ?></p>
  </div>
  <form class="search" action="tisky.php" method="get" role="search">
    <label for="q" class="sr-only">Hledat tisky</label>
    <input id="q" name="q" type="search" value="<?= e($q) ?>" placeholder="Tisk, číslo zákona nebo poslanec">
    <?php if ($f !== ''): ?><input type="hidden" name="f" value="<?= e($f) ?>"><?php endif; ?>
    <?php if ($sort === 'recent'): ?><input type="hidden" name="sort" value="recent"><?php endif; ?>
    <?php if ($min > 0): ?><input type="hidden" name="min" value="<?= $min ?>"><?php endif; ?>
    <button type="submit">Hledat</button>
  </form>
</section>

<section class="card">
  <div class="card-head">
    <div class="filters">
      <?php foreach ($filters as $key => $label): ?>
        <a class="pill<?= $key === $f ? ' active' : '' ?>" href="tisky.php?<?= http_build_query(array_filter(['f' => $key, 'q' => $q, 'sort' => $sort === 'recent' ? 'recent' : null, 'min' => $min ?: null])) ?>"><?= e($label) ?></a>
      <?php endforeach; ?>
    </div>
    <div class="filters">
      <a class="pill<?= $sort === 'score' ? ' active' : '' ?>" href="tisky.php?<?= http_build_query(array_filter(['f' => $f, 'q' => $q, 'min' => $min ?: null])) ?>">Podle závažnosti</a>
      <a class="pill<?= $sort === 'recent' ? ' active' : '' ?>" href="tisky.php?<?= http_build_query(array_filter(['f' => $f, 'q' => $q, 'sort' => 'recent', 'min' => $min ?: null])) ?>">Podle data</a>
    </div>
  </div>
  <?php require __DIR__ . '/app/partials/bills_table.php'; ?>
</section>
<?php
View::footer();
