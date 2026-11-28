<?php
// Časová osa změn jednoho tisku (/tisk/N/aktualizace): kroky projednávání, podané pozměňovací návrhy,
// souhrn pozměňovacích návrhů a vyhlášení, den po dni od nejnovějšího (Hlidac\BillUpdates). Na stránce tisku
// jsou tytéž údaje rozdělené do oddílů; tady je vidět, co přibylo naposledy. Jen zobrazení, skóre se nemění.

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\BillUpdates;
use Hlidac\Repo;
use Hlidac\Updates;
use Hlidac\Url;
use Hlidac\View;

$repo = new Repo(db());
$ct = (int)($_GET['ct'] ?? 0);
$t = $ct > 0 ? $repo->bill($ct) : null;

if ($t === null) {
    http_response_code(404);
    View::header('Tisk nenalezen', 'tisky');
    echo '<section class="card pad"><h1>Tisk nenalezen</h1><p><a href="' . e(url('tisky')) . '">Zpět na seznam tisků</a></p></section>';
    View::footer();
    exit;
}

$obdobi = $repo->obdobi();
$days = BillUpdates::days($t, $repo->steps($ct), $repo->amendments($ct), $obdobi);
$texts = $repo->pnTexts($ct);
$billUrl = url('tisk', ['ct' => $ct]);

View::header('Změny tisku ' . $ct . ' – ' . $t['nazev'], 'tisky',
    mb_substr('Kroky projednávání a pozměňovací návrhy tisku ' . $ct . ' den po dni. ' . ($t['uplny_nazev'] ?: $t['nazev']), 0, 300),
    ['url' => Url::path('tisk-aktualizace', ['ct' => $ct])]);
?>
<p class="crumbs"><a href="<?= e(url('tisky')) ?>">Tisky</a> › <a href="<?= e($billUrl) ?>">Tisk <?= $ct ?></a> › Změny</p>
<section class="detail-head">
  <span class="chips"><?= View::scoreBadge((int)$t['score']) ?><?= View::chips($t['flags']) ?></span>
  <h1>Změny tisku <?= $ct ?></h1>
  <p><a href="<?= e($billUrl) ?>"><?= e($t['uplny_nazev'] ?: $t['nazev']) ?></a></p>
  <p class="muted">Kroky projednávání a podané pozměňovací návrhy den po dni, od nejnovějšího. Datum je den, kdy se změna stala; v otevřených datech Sněmovny se krok může objevit se zpožděním. Odkazy vedou na příslušné místo na <a href="<?= e($billUrl) ?>">stránce tisku</a>.</p>
</section>
<section class="card">
  <div class="card-head">
    <h2>Časová osa</h2>
    <span class="muted small"><?= e(BillUpdates::daysLabel(count($days))) ?></span>
  </div>
  <?php if ($days === []): ?>
    <p class="empty">U tisku zatím není žádná změna.</p>
  <?php else: ?>
  <div class="upd">
    <?php foreach ($days as $day): ?>
      <h2 class="upd-day"><?= e(Updates::dayLabel($day['day'])) ?></h2>
      <section class="upd-item" id="<?= e(BillUpdates::anchor($day['day'])) ?>" aria-label="Změny <?= e(formatDate($day['day'])) ?>">
        <ul class="upd-events">
          <?php foreach ($day['items'] as $item): ?>
          <li class="upd-event">
            <p><?= View::billUpdateItem($item, $ct, false) ?><?php if ($item['time'] !== '' && $item['type'] !== 'pn'): ?> <span class="muted small"><?= e((string)$item['time']) ?></span><?php endif; ?></p>
            <?php if ($item['type'] === 'pn'): ?>
              <?php
              $pnRow = static function (array $p) use ($texts, $ct): string {
                  $x = $texts[(int)$p['id_dokument']] ?? null;
                  $chips = $x !== null ? View::pnChips($x) : '';
                  $summary = is_string($x['ai']['shrnuti'] ?? null) ? trim($x['ai']['shrnuti']) : '';
                  return '<li><span><a href="' . e(url('tisk', ['ct' => $ct], 'pn-' . (int)$p['cislo'])) . '">Návrh č. ' . (int)$p['cislo'] . '</a> '
                      . '<span class="muted small">' . View::person($p['osoba'], $p['klub'] ?? null) . ' · ' . e(date('G:i', (int)strtotime((string)$p['cas']))) . '</span>'
                      . ($chips !== '' ? ' <span class="chips">' . $chips . '</span>' : '') . '</span>'
                      . ($summary !== '' ? '<span class="small pn-summary">' . e($summary) . '</span>' : '') . '</li>';
              };
              $shown = array_slice($item['pn'], 0, BillUpdates::PN_VISIBLE);
              $folded = array_slice($item['pn'], BillUpdates::PN_VISIBLE);
              ?>
              <ul class="upd-pn"><?php foreach ($shown as $p): ?><?= $pnRow($p) ?><?php endforeach; ?></ul>
              <?php if ($folded !== []): ?>
              <details class="upd-more">
                <summary><?= count($folded) >= 5 ? 'Dalších' : 'Další' ?> <?= count($folded) ?> z tohoto dne</summary>
                <ul class="upd-pn"><?php foreach ($folded as $p): ?><?= $pnRow($p) ?><?php endforeach; ?></ul>
              </details>
              <?php endif; ?>
            <?php endif; ?>
          </li>
          <?php endforeach; ?>
        </ul>
      </section>
    <?php endforeach; ?>
  </div>
  <?php endif; ?>
</section>
<p class="muted small">Shrnutí u pozměňovacích návrhů připravuje jazykový model a může se mýlit; rozhodující je text návrhu na psp.cz. Hodnocení, hlasování a upozornění k tisku jsou na <a href="<?= e($billUrl) ?>">stránce tisku</a>.</p>
<?php
View::footer();
