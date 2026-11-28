<?php
// Časová osa aktualizací (/aktualizace): kdy web zaznamenal nové dění. Stejné události jako na stránce
// Upozornění, ale seskupené podle synchronizace, která je našla (Hlidac\Updates), a řazené podle času
// zaznamenání, ne podle data události. Výchozí pohled skrývá běžnou agendu (?vse=1 ji ukáže).

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\Scorer;
use Hlidac\Updates;
use Hlidac\View;

$perPage = 30;
$all = !empty($_GET['vse']);
$watch = (int)Scorer::weights(config())['level_watch'];
$groups = Updates::groups(db(), $watch);
// pořadí aktualizace v pohledu s běžnou agendou: odkaz „+ N událostí z běžné agendy“ musí trefit správnou stranu
$position = array_flip(array_column($groups, 'id'));
if (!$all) {
    $groups = array_values(array_filter($groups, static fn(array $g): bool => $g['important'] > 0));
}
$total = count($groups);
$pages = max(1, (int)ceil($total / $perPage));
$page = min(max(1, (int)($_GET['p'] ?? 1)), $pages);
$updates = Updates::withEvents(db(), array_slice($groups, ($page - 1) * $perPage, $perPage), $all ? 0 : $watch);
$qs = $all ? '&vse=1' : '';

View::header('Aktualizace', 'upozorneni', 'Časová osa: kdy web zaznamenal nové pozměňovací návrhy, zkrácené lhůty, schválení a další kroky ve Sněmovně a u vlády.');
?>
<section class="hero">
  <div>
    <h1>Aktualizace</h1>
    <p class="lead">Kdy jsme zaznamenali nové dění ve Sněmovně a u vlády. Záznam vznikne při kontrole dat, která našla něco nového; datum vlevo u události říká, kdy se stala. Tytéž události podle data, kdy se staly, jsou na stránce <a href="<?= e(url('upozorneni')) ?>">Upozornění</a>.</p>
  </div>
</section>
<section class="card">
  <div class="card-head">
    <div class="filters">
      <a class="pill<?= $all ? '' : ' active' ?>" href="<?= e(url('aktualizace')) ?>">Jen důležité (skóre <?= $watch ?>+)</a>
      <a class="pill<?= $all ? ' active' : '' ?>" href="<?= e(url('aktualizace', ['vse' => 1])) ?>">Včetně běžné agendy</a>
    </div>
    <span class="muted small"><?= $total ?> <?= $total >= 1 && $total <= 4 ? 'aktualizace' : 'aktualizací' ?></span>
  </div>
  <?php if ($updates === []): ?>
    <p class="empty">Zatím žádné aktualizace.</p>
  <?php else: ?>
  <div class="upd">
    <?php $day = ''; ?>
    <?php foreach ($updates as $update): ?>
      <?php
      $ts = (int)strtotime((string)$update['from']);
      $events = $update['events'];
      $visible = array_slice($events, 0, Updates::VISIBLE);
      $folded = array_slice($events, Updates::VISIBLE, Updates::FOLDED);
      $beyond = count($events) - count($visible) - count($folded);
      $routine = $all ? 0 : (int)$update['n'] - (int)$update['important'];
      $routinePage = intdiv((int)($position[$update['id']] ?? 0), $perPage) + 1;
      ?>
      <?php if (date('Y-m-d', $ts) !== $day): $day = date('Y-m-d', $ts); ?>
      <h2 class="upd-day"><?= e(Updates::dayLabel((string)$update['from'])) ?></h2>
      <?php endif; ?>
      <section class="upd-item" id="<?= e((string)$update['id']) ?>" aria-label="Aktualizace <?= e(formatDate((string)$update['from'], true)) ?>">
        <p class="upd-when"><time datetime="<?= e(date('Y-m-d\TH:i', $ts)) ?>"><?= e(date('G:i', $ts)) ?></time> · <?= e(Updates::eventsWord(count($events))) ?></p>
        <?php View::alertList($visible); ?>
        <?php if ($folded !== []): ?>
        <details class="upd-more">
          <summary><?= count($folded) + $beyond >= 5 ? 'Dalších' : 'Další' ?> <?= e(Updates::eventsWord(count($folded) + $beyond)) ?> této aktualizace</summary>
          <?php View::alertList($folded); ?>
          <?php if ($beyond > 0): ?>
          <p class="muted small upd-note">Zbytek (<?= $beyond ?>) najdete na stránce <a href="<?= e(url('upozorneni', $all ? ['vse' => 1] : [])) ?>">Upozornění</a>.</p>
          <?php endif; ?>
        </details>
        <?php endif; ?>
        <?php if ($routine > 0): ?>
        <p class="muted small upd-note">+ <a href="<?= e(url('aktualizace', array_filter(['vse' => 1, 'p' => $routinePage > 1 ? $routinePage : null]), (string)$update['id'])) ?>"><?= e(Updates::eventsWord($routine)) ?> z běžné agendy</a></p>
        <?php endif; ?>
      </section>
    <?php endforeach; ?>
  </div>
  <?php endif; ?>
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
