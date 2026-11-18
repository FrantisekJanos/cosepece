<?php
// Detail tisku: příznaky, průběh projednávání, pozměňovací návrhy a jejich předkladatelé.

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\Analyzer;
use Hlidac\Repo;
use Hlidac\View;

$repo = new Repo(db());
$ct = (int)($_GET['ct'] ?? 0);
$t = $ct > 0 ? $repo->bill($ct) : null;

if ($t === null) {
    http_response_code(404);
    View::header('Tisk nenalezen', 'tisky');
    echo '<section class="card pad"><h1>Tisk nenalezen</h1><p><a href="tisky.php">Zpět na seznam tisků</a></p></section>';
    View::footer();
    exit;
}

$obdobi = $repo->obdobi();
$steps = $repo->steps($ct);
$pn = $repo->amendments($ct);
$authors = $repo->amendmentAuthors($ct);
$alerts = $repo->alerts(10, 0, $ct);
$texts = $repo->pnTexts($ct);
$billText = $repo->billText($ct);
$laws = array_filter(explode(',', (string)$t['zakony']));

// délka projednávání od předložení po poslední krok
$days = null;
if ($t['predlozeno'] && $t['posledni']) {
    $days = (int)round((strtotime($t['posledni']) - strtotime($t['predlozeno'])) / 86400);
}

// barevné rozlišení důležitých kroků na časové ose
$dotClass = function (array $k): string {
    $typ = (int)$k['typ_stavu'];
    if (in_array($typ, Analyzer::STAV_P90, true)) {
        return 'dot-fast';
    }
    if (in_array((int)$k['akce_id'], Analyzer::AKCE_ZKRACENI, true)) {
        return 'dot-cut';
    }
    if (in_array($typ, [64, 11, 8, 12], true)) {
        return 'dot-main';
    }
    return '';
};

View::header('Tisk ' . $ct . ' – ' . $t['nazev'], 'tisky', $t['uplny_nazev']);
?>
<p class="crumbs"><a href="tisky.php">Tisky</a> › Tisk <?= $ct ?></p>
<section class="detail-head">
  <span class="chips"><?= View::scoreBadge((int)$t['score']) ?><?= View::chips($t['flags']) ?></span>
  <h1><?= e($t['uplny_nazev'] ?: $t['nazev']) ?></h1>
  <p class="muted">Tisk <?= $ct ?> · <?= e($t['typ_navrhu']) ?> návrh · předkladatel <?= e($t['navrhovatel']) ?> · předloženo <?= e(formatDate($t['predlozeno'])) ?><?= $t['sbirka'] ? ' · vyhlášeno jako ' . e($t['sbirka']) . ' Sb.' : '' ?></p>
  <div class="actions">
    <a class="btn" href="<?= e(View::pspTiskUrl($ct, $obdobi)) ?>">Otevřít na psp.cz</a>
  </div>
</section>

<section class="kpis" aria-label="Klíčová čísla">
  <div class="card kpi">
    <div class="kpi-label">Pozměňovací návrhy</div>
    <div class="kpi-value"><?= count($pn) ?></div>
    <div class="kpi-note">od <?= count($authors) ?> <?= count($authors) === 1 ? 'poslance' : 'poslanců' ?></div>
  </div>
  <div class="card kpi">
    <div class="kpi-label">Fáze</div>
    <div class="kpi-value kpi-small"><?= e($t['faze']) ?></div>
    <div class="kpi-note">poslední změna <?= e(formatDate($t['posledni'])) ?></div>
  </div>
  <div class="card kpi">
    <div class="kpi-label">Projednávání trvá</div>
    <div class="kpi-value"><?= $days !== null ? $days . ' dní' : '—' ?></div>
    <div class="kpi-note">od předložení po poslední krok</div>
  </div>
  <div class="card kpi">
    <div class="kpi-label">Zákony v názvu</div>
    <div class="kpi-value"><?= count($laws) ?: '—' ?></div>
    <div class="kpi-note"><?= $laws ? e(implode(', ', array_map(fn($l) => $l . ' Sb.', array_slice($laws, 0, 3)))) : 'nový zákon nebo souhrnná novela' ?></div>
  </div>
</section>

<section class="card pad" aria-label="Proč toto skóre">
  <h2>Proč skóre <?= (int)$t['score'] ?> ze 100</h2>
  <?php if ($t['reasons_list'] === []): ?>
    <p class="muted">Žádné procedurální příznaky – běžné projednávání.</p>
  <?php else: ?>
    <?= View::reasons($t['reasons_list']) ?>
  <?php endif; ?>
  <p class="muted small">Skóre hodnotí postup projednávání a formu pozměňovacích návrhů, ne politický obsah zákona. <a href="<?= e((string)config('issues_url', '')) ?>">Nesouhlasíte? Napište nám.</a></p>
</section>

<?php if ($billText !== null && ($billText['ai'] !== null || $billText['signals'] !== [])): ?>
<section class="card pad" aria-label="Rozbor textu návrhu">
  <h2>Rozbor textu návrhu</h2>
  <?php $bai = $billText['ai']; ?>
  <span class="chips">
    <?php if ($bai !== null): ?><span class="chip <?= (int)$billText['ai_score'] >= 6 ? 'chip-cut' : 'chip-surge' ?>">AI <?= (int)$billText['ai_score'] ?>/10</span><?php endif; ?>
    <?php if (!empty($bai['zpetna_ucinnost']) || !empty($billText['signals']['retro'])): ?><span class="chip chip-cut">Zpětné použití</span><?php endif; ?>
    <?php if (!empty($bai['zkraceni_lhut']) || !empty($billText['signals']['deadline'])): ?><span class="chip chip-cut">Změna lhůt</span><?php endif; ?>
    <?php if (!empty($bai['prospech_uzke_skupiny'])): ?><span class="chip chip-cut">Prospěch úzké skupiny</span><?php endif; ?>
    <?php if (isset($bai['souvisi_s_predmetem']) && $bai['souvisi_s_predmetem'] === false): ?><span class="chip chip-cut">Nesouvisí s předmětem</span><?php endif; ?>
  </span>
  <?php if (!empty($bai['shrnuti'])): ?><p><?= e($bai['shrnuti']) ?></p><?php endif; ?>
  <?php if (!empty($bai['duvod'])): ?><p class="muted"><?= e($bai['duvod']) ?></p><?php endif; ?>
  <?php foreach ((array)($bai['upravy'] ?? []) as $note): ?><p class="muted small">Automatická korekce: <?= e((string)$note) ?></p><?php endforeach; ?>
  <?php
    // doslovné citace: ověřené citace AI, jinak pasáže nalezené pravidly
    $quotes = !empty($bai['citace']) ? $bai['citace'] : array_merge($billText['signals']['deadline'] ?? [], $billText['signals']['retro'] ?? []);
  ?>
  <?php foreach (array_slice($quotes, 0, 3) as $q): ?>
    <blockquote class="quote"><?= e(trim((string)$q)) ?></blockquote>
  <?php endforeach; ?>
  <p class="muted small">Citace jsou doslovně z textu návrhu<?= $billText['docx_url'] ? ' (<a href="' . e($billText['docx_url']) . '">Word na psp.cz</a>)' : '' ?>.
    <?= $bai !== null ? 'Rozbor připravil jazykový model; hodnotí formu a postup, ne politický obsah.' : 'Pasáže vybrala automatická pravidla.' ?></p>
</section>
<?php endif; ?>

<div class="split">
  <section class="card pad main-col" aria-label="Průběh projednávání">
    <h2>Průběh projednávání</h2>
    <?php if ($steps === []): ?><p class="muted">Historie zatím není k dispozici.</p><?php endif; ?>
    <ol class="timeline">
      <?php foreach ($steps as $k): ?>
        <li>
          <span class="tl-date"><?= e(formatDate($k['datum'])) ?></span>
          <span class="tl-dot <?= $dotClass($k) ?>" aria-hidden="true"></span>
          <span class="tl-text"><strong><?= e($k['stav']) ?></strong><?= $k['akce'] !== '' ? ' – ' . e($k['akce']) : '' ?></span>
        </li>
      <?php endforeach; ?>
    </ol>
  </section>

  <aside class="side-col">
    <section class="card pad">
      <h2>Předkladatelé PN</h2>
      <?php if ($authors === []): ?><p class="muted">K tisku nebyl podán žádný písemný pozměňovací návrh.</p><?php endif; ?>
      <ul class="plain-list">
        <?php foreach ($authors as $a): ?>
          <li><span><?= e($a['osoba']) ?></span><span class="strong"><?= (int)$a['n'] ?></span></li>
        <?php endforeach; ?>
      </ul>
    </section>
    <section class="card pad">
      <h2>Pozměňovací návrhy</h2>
      <ul class="plain-list">
        <?php foreach (array_slice($pn, 0, 30) as $p): ?>
          <?php $x = $texts[(int)$p['id_dokument']] ?? null; ?>
          <li class="pn-item">
            <span><a href="<?= e(View::pspSdUrl((int)$p['cislo'], $obdobi)) ?>">Návrh č. <?= (int)$p['cislo'] ?></a> <span class="muted small"><?= e($p['osoba']) ?> · <?= e(formatDate($p['cas'], true)) ?></span></span>
            <?php if ($x !== null): ?>
              <span class="chips">
                <?php if (!empty($x['signals']['other_laws'])): ?><span class="chip chip-cut">Přílepek: <?= e(implode(', ', $x['signals']['other_laws'])) ?></span><?php endif; ?>
                <?php if (!empty($x['signals']['retro']) && !empty($x['signals']['deadline'])): ?><span class="chip chip-cut">Lhůta i zpětně</span><?php endif; ?>
                <?php if (!empty($x['signals']['large'])): ?><span class="chip chip-surge">Rozsáhlý</span><?php endif; ?>
                <?php if ($x['ai_score'] !== null): ?><span class="chip <?= (int)$x['ai_score'] >= 6 ? 'chip-cut' : 'chip-surge' ?>">AI <?= (int)$x['ai_score'] ?>/10</span><?php endif; ?>
              </span>
              <?php if (!empty($x['ai']['shrnuti'])): ?><span class="small pn-summary"><?= e($x['ai']['shrnuti']) ?></span><?php endif; ?>
              <?php foreach (array_slice($x['ai']['citace'] ?? [], 0, 1) as $q): ?><blockquote class="quote small"><?= e(trim((string)$q)) ?></blockquote><?php endforeach; ?>
            <?php endif; ?>
          </li>
        <?php endforeach; ?>
      </ul>
      <?php if (count($pn) > 30): ?><p class="muted small">a dalších <?= count($pn) - 30 ?> na psp.cz</p><?php endif; ?>
    </section>
  </aside>
</div>

<section class="card" aria-label="Upozornění k tisku">
  <div class="card-head"><h2>Upozornění k tisku</h2></div>
  <?php View::alertList($alerts); ?>
</section>
<?php
View::footer();
