<?php
// Všechna upozornění se stránkováním.

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\Repo;
use Hlidac\Scorer;
use Hlidac\View;

$perPage = 30;
$page = max(1, (int)($_GET['p'] ?? 1));
$all = !empty($_GET['vse']);
$min = $all ? 0 : (int)Scorer::weights(config())['level_watch'];
$repo = new Repo(db());
$total = $repo->alertsCount($min);
$pages = max(1, (int)ceil($total / $perPage));
$page = min($page, $pages);
$alerts = $repo->alerts($perPage, ($page - 1) * $perPage, null, $min);
$qs = $all ? '&vse=1' : '';

View::header('Upozornění', 'upozorneni');
?>
<section class="hero">
  <div>
    <h1>Upozornění</h1>
    <p class="lead"><?= $total ?> událostí podle data, kdy se staly · odebírat můžete přes <a href="<?= e(url('rss')) ?>">RSS</a> nebo na sociálních sítích · kdy jsme je zaznamenali, ukazuje <a href="<?= e(url('aktualizace')) ?>">časová osa aktualizací</a></p>
  </div>
</section>
<section class="card">
  <div class="card-head">
    <div class="filters">
      <a class="pill<?= $all ? '' : ' active' ?>" href="<?= e(url('upozorneni')) ?>">Jen důležité (skóre <?= (int)Scorer::weights(config())['level_watch'] ?>+)</a>
      <a class="pill<?= $all ? ' active' : '' ?>" href="<?= e(url('upozorneni', ['vse' => 1])) ?>">Včetně běžné agendy</a>
    </div>
  </div>
  <?php View::alertList($alerts); ?>
  <?php if ($pages > 1): ?>
    <nav class="pager" aria-label="Stránkování">
      <?php if ($page > 1): ?><a class="pill" href="?p=<?= $page - 1 ?><?= $qs ?>">← Novější</a><?php endif; ?>
      <span class="muted">Strana <?= $page ?> z <?= $pages ?></span>
      <?php if ($page < $pages): ?><a class="pill" href="?p=<?= $page + 1 ?><?= $qs ?>">Starší →</a><?php endif; ?>
    </nav>
  <?php endif; ?>
</section>
<?php
View::footer();
