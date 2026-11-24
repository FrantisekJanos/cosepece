<?php
// Stránka zákona: co ho v tomto období mění – tisky ve Sněmovně, pozměňovací návrhy k jiným tiskům
// (možné přílepky) a připravované návrhy u vlády; bez parametru přehled všech zákonů.

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\GovPrep;
use Hlidac\Laws;
use Hlidac\Repo;
use Hlidac\View;

$laws = new Laws(db());
$repo = new Repo(db());
$law = trim((string)($_GET['z'] ?? ''));
$watched = array_map('strval', (array)config('watch_laws', []));

if ($law === '' || !Laws::valid($law)) {
    $all = $laws->overview();
    View::header('Zákony', 'zakony', 'Které zákony se v tomto volebním období mění: návrhy ve Sněmovně i připravované u vlády.');
    ?>
<section class="hero">
  <div>
    <h1>Zákony</h1>
    <p class="lead"><?= count($all) ?> zákonů, které mění návrhy ve Sněmovně nebo připravované u vlády. U každého je přehled všech změn a vlastní RSS.</p>
  </div>
</section>
<?php if ($law !== ''): ?><section class="card pad"><p>„<?= e($law) ?>“ není číslo zákona ve tvaru <code>159/2006</code>.</p></section><?php endif; ?>
<section class="card">
  <div class="table-wrap">
    <table>
      <thead><tr><th>Zákon</th><th>Název</th><th class="num">Tisky</th><th class="num">U vlády</th></tr></thead>
      <tbody>
        <?php foreach ($all as $number => $info): ?>
          <tr>
            <td class="strong nowrap"><a href="<?= e(Laws::url((string)$number)) ?>"><?= e((string)$number) ?> Sb.</a><?= in_array((string)$number, $watched, true) ? ' <span class="chip chip-mp">sledovaný</span>' : '' ?></td>
            <td class="wide"><a class="plain" href="<?= e(Laws::url((string)$number)) ?>"><?= e($info['name'] ?? '—') ?></a></td>
            <td class="num"><?= $info['bills'] ?: '—' ?></td>
            <td class="num"><?= $info['upcoming'] ?: '—' ?></td>
          </tr>
        <?php endforeach; ?>
      </tbody>
    </table>
  </div>
</section>
<?php
    View::footer();
    return;
}

$name = $laws->name($law);
$bills = $laws->bills($law);
$surge = $repo->surgeSet();
$review = $repo->reviewStatus();
foreach ($bills as &$b) {
    $b['flags'] = $repo->flags($b, $surge, $review);
}
unset($b);
$amendments = $laws->amendments($law);
$upcoming = $laws->upcoming($law);
$title = 'Zákon č. ' . $law . ' Sb.' . ($name !== null ? ', ' . $name : '');
$phase = ['pripominky' => 'v připomínkovém řízení', 'po_pripominkach' => 'po připomínkovém řízení', 'vlada' => 'předloženo vládě', 'schvaleno' => 'schváleno vládou'];

View::header($title, 'zakony', 'Co v tomto volebním období mění ' . $title . ': návrhy ve Sněmovně, pozměňovací návrhy a připravované změny u vlády.',
    ['url' => Laws::url($law)]);
?>
<section class="hero">
  <div>
    <h1><?= e($title) ?></h1>
    <p class="lead">
      <?= count($bills) ?> <?= count($bills) === 1 ? 'tisk' : (count($bills) >= 2 && count($bills) <= 4 ? 'tisky' : 'tisků') ?> ve Sněmovně<?php if ($amendments !== []): ?>, <?= count($amendments) ?> pozměňovacích návrhů k jiným tiskům<?php endif; ?><?php if ($upcoming !== []): ?>, <?= count($upcoming) ?> připravovaných u vlády<?php endif; ?>.
      <?= in_array($law, $watched, true) ? '<span class="chip chip-mp">Sledovaný zákon</span>' : '' ?>
    </p>
    <p class="small"><a href="<?= e(Laws::eSbirkaUrl($law)) ?>">Platné znění v e-Sbírce</a> · <a href="rss.php?zakon=<?= e(rawurlencode($law)) ?>">RSS upozornění k tomuto zákonu</a> · <a href="api.php?zakon=<?= e(rawurlencode($law)) ?>">JSON</a> · <a href="zakon.php">Všechny zákony</a></p>
  </div>
</section>

<section class="card">
  <div class="card-head"><h2>Návrhy ve Sněmovně</h2><span class="muted small">tisky tohoto období, které zákon mění</span></div>
  <?php require __DIR__ . '/app/partials/bills_table.php'; ?>
</section>

<?php if ($amendments !== []): ?>
<section class="card upcoming" aria-label="Pozměňovací návrhy k jiným tiskům">
  <div class="card-head"><h2>Pozměňovací návrhy k jiným tiskům · <?= count($amendments) ?></h2>
    <span class="muted small">Text návrhu mění tento zákon, i když projednávaný tisk ho nemění – může jít o přílepek. Posuďte podle textu.</span></div>
  <ul class="upcoming-list">
    <?php foreach ($amendments as $a): ?>
      <li>
        <a class="upcoming-title" href="tisk.php?ct=<?= (int)$a['ct'] ?>#pn-<?= (int)$a['cislo'] ?>">Pozměňovací návrh č. <?= (int)$a['cislo'] ?> k tisku <?= (int)$a['ct'] ?> · <?= e($a['nazev']) ?></a>
        <div class="muted small"><?= View::person((string)$a['osoba'], $a['klub'] ?? null) ?> · <?= e(formatDate((string)$a['cas'])) ?><?php if ($a['ai_score'] !== null): ?> · AI <?= (int)$a['ai_score'] ?>/10<?php endif; ?></div>
        <?php if (!empty($a['ai']['titulek'])): ?><div class="small">AI: <?= e((string)$a['ai']['titulek']) ?></div><?php endif; ?>
      </li>
    <?php endforeach; ?>
  </ul>
</section>
<?php endif; ?>

<?php if ($upcoming !== []): ?>
<section class="card upcoming" aria-label="Připravované u vlády">
  <div class="card-head"><h2>Připravované u vlády · <?= count($upcoming) ?></h2><span class="muted small">návrhy ministerstev, které ještě nejsou ve Sněmovně</span></div>
  <ul class="upcoming-list">
    <?php foreach ($upcoming as $u): ?>
      <li>
        <a class="upcoming-title" href="<?= e(GovPrep::url((string)$u['pid'])) ?>"><?= e((string)$u['nazev']) ?></a>
        <div class="muted small"><?= e((string)$u['resort']) ?> · <?= e($phase[$u['faze']] ?? $u['faze']) ?><?php if ($u['dni'] !== null): ?> · připomínky <?= (int)$u['dni'] ?> dní<?php endif; ?></div>
      </li>
    <?php endforeach; ?>
  </ul>
</section>
<?php endif; ?>
<p class="muted small">Přiřazení k zákonu vychází z čísel zákonů v názvech tisků a připravovaných materiálů a v textech pozměňovacích návrhů; souhrnné novely mohou měnit i zákony, které v názvu neuvádějí.</p>
<?php
View::footer();
