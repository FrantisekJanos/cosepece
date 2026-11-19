<?php
// Historie verzí textu návrhu zákona nebo pozměňovacího návrhu a jeho rozborů AI, s rozdílem mezi dvěma verzemi textu.
// revize.php?druh=tisk&id={ct} | revize.php?druh=pn&id={id_dokument} [&a=1&b=2] [&o={období}]

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\Diff;
use Hlidac\Revisions;
use Hlidac\View;

$kind = in_array($_GET['druh'] ?? '', ['tisk', 'pn'], true) ? (string)$_GET['druh'] : 'tisk';
$id = (int)($_GET['id'] ?? 0);
$current = Revisions::period(db());
$obdobi = isset($_GET['o']) ? (int)$_GET['o'] : $current;
$texts = $id > 0 ? Revisions::textVersions(db(), $kind, $id, $obdobi) : [];
$analyses = $id > 0 ? Revisions::aiVersions(db(), $kind, $id, $obdobi) : [];

if ($texts === [] && $analyses === []) {
    http_response_code(404);
    View::header('Historie nenalezena', 'tisky');
    echo '<section class="card pad"><h1>Historie nenalezena</h1><p><a href="tisky.php">Zpět na seznam tisků</a></p></section>';
    View::footer();
    exit;
}

$ct = (int)(($texts !== [] ? end($texts) : end($analyses))['ct']);
$cislo = $texts !== [] ? (int)end($texts)['cislo'] : 0;
$past = $obdobi !== $current;
$billUrl = $past ? View::pspTiskUrl($ct, $obdobi) : 'tisk.php?ct=' . $ct;
$subject = $kind === 'tisk' ? 'textu návrhu zákona' : 'pozměňovacího návrhu' . ($cislo > 0 ? ' č. ' . $cislo : '');

// porovnávané verze: výchozí předposlední → poslední
$numbers = array_map(fn(array $r): int => (int)$r['verze'], $texts);
$b = (int)($_GET['b'] ?? (end($numbers) ?: 0));
$a = (int)($_GET['a'] ?? ($numbers[count($numbers) - 2] ?? 0));
$diff = null;
if (count($numbers) >= 2 && in_array($a, $numbers, true) && in_array($b, $numbers, true) && $a !== $b) {
    $diff = Diff::render(
        (string)Revisions::textVersion(db(), $kind, $id, $obdobi, $a),
        (string)Revisions::textVersion(db(), $kind, $id, $obdobi, $b)
    );
}
// ke které verzi textu rozbor patří (podle otisku textu)
$versionByHash = [];
foreach ($texts as $t) {
    $versionByHash[(string)$t['hash']] ??= (int)$t['verze'];
}
$docLabel = function (?string $url): string {
    // číslo dokumentu na psp.cz (idd) z odkazu na Word
    if ($url === null || $url === '') {
        return '—';
    }
    $label = preg_match('~idd=(\d+)~', $url, $m) ? 'idd ' . $m[1] : 'Word';
    return '<a href="' . e($url) . '">' . e($label) . '</a>';
};

View::header('Historie ' . $subject . ' – tisk ' . $ct, 'tisky', 'Předchozí znění a rozbory ' . $subject . ' k tisku ' . $ct . '.');
?>
<p class="crumbs"><a href="tisky.php">Tisky</a> › <a href="<?= e($billUrl) ?>">Tisk <?= $ct ?><?= $past ? ' (' . $obdobi . '. období)' : '' ?></a> › Historie</p>
<section class="detail-head">
  <h1>Historie <?= e($subject) ?></h1>
  <p class="muted">Tisk <?= $ct ?> · <?= e(Revisions::versionsLabel(count($texts))) ?> textu · <?= count($analyses) ?> <?= count($analyses) === 1 ? 'rozbor' : (count($analyses) >= 2 && count($analyses) <= 4 ? 'rozbory' : 'rozborů') ?> AI<?= $past ? ' · ' . $obdobi . '. volební období' : '' ?></p>
  <p class="muted small">Nová verze se uloží, jen když se text nebo rozbor skutečně změní (např. psp.cz vymění dokument, nebo rozbor znovu posoudí silnější model). Starší verze se nemažou.</p>
</section>

<?php if ($texts !== []): ?>
<section class="card" aria-label="Verze textu">
  <div class="card-head"><h2>Verze textu</h2></div>
  <div class="table-wrap">
    <table class="table-compact">
      <thead><tr><th class="num">Verze</th><th>Uloženo</th><th>Zdroj</th><th>Dokument</th><th class="num">Znaků</th></tr></thead>
      <tbody>
        <?php foreach ($texts as $t): ?>
          <tr>
            <td class="num"><b><?= (int)$t['verze'] ?></b></td>
            <td><?= e(formatDate((string)($t['fetched_at'] ?? $t['created_at']), true)) ?></td>
            <td><?= e(Revisions::SOURCES[(string)$t['zdroj']] ?? (string)$t['zdroj']) ?></td>
            <td><?= $docLabel($t['docx_url']) ?></td>
            <td class="num"><?= number_format((int)$t['chars'], 0, ',', ' ') ?></td>
          </tr>
        <?php endforeach; ?>
      </tbody>
    </table>
  </div>
</section>
<?php endif; ?>

<?php if (count($numbers) >= 2): ?>
<section class="card pad" aria-label="Rozdíl mezi verzemi">
  <h2>Rozdíl mezi verzemi</h2>
  <form class="diff-form" method="get" action="revize.php">
    <input type="hidden" name="druh" value="<?= e($kind) ?>">
    <input type="hidden" name="id" value="<?= $id ?>">
    <?php if ($past): ?><input type="hidden" name="o" value="<?= $obdobi ?>"><?php endif; ?>
    <label>Starší <select name="a"><?php foreach ($numbers as $n): ?><option value="<?= $n ?>"<?= $n === $a ? ' selected' : '' ?>>verze <?= $n ?></option><?php endforeach; ?></select></label>
    <label>Novější <select name="b"><?php foreach ($numbers as $n): ?><option value="<?= $n ?>"<?= $n === $b ? ' selected' : '' ?>>verze <?= $n ?></option><?php endforeach; ?></select></label>
    <button class="btn" type="submit">Porovnat</button>
  </form>
  <?php if ($diff === null): ?>
    <p class="muted">Vyberte dvě různé verze.</p>
  <?php elseif ($diff['added'] === 0 && $diff['removed'] === 0): ?>
    <p class="muted">Verze <?= $a ?> a <?= $b ?> se liší jen mezerami nebo prázdnými řádky.</p>
  <?php else: ?>
    <p class="small">Verze <?= $a ?> → <?= $b ?>: <span class="diff-plus">+<?= (int)$diff['added'] ?></span> <span class="diff-minus">−<?= (int)$diff['removed'] ?></span> odstavců. <span class="muted">Smazané části jsou přeškrtnuté, nové zvýrazněné.</span></p>
    <div class="diff">
      <?php foreach ($diff['blocks'] as $blk): ?>
        <?php if ($blk['type'] === 'skip'): ?>
          <div class="diff-skip">… <?= (int)$blk['n'] ?> <?= (int)$blk['n'] === 1 ? 'stejný odstavec' : 'stejných odstavců' ?> …</div>
        <?php else: ?>
          <div class="diff-line diff-<?= e($blk['type']) ?>"><span class="diff-mark" aria-hidden="true"><?= $blk['type'] === 'add' ? '+' : ($blk['type'] === 'del' ? '−' : '') ?></span><span><?= $blk['html'] /* escapováno v Diff::render */ ?></span></div>
        <?php endif; ?>
      <?php endforeach; ?>
      <?php if ($diff['truncated']): ?><div class="diff-skip">Rozdíl je příliš dlouhý, zbytek se nezobrazuje.</div><?php endif; ?>
    </div>
  <?php endif; ?>
</section>
<?php endif; ?>

<?php if ($analyses !== []): ?>
<section class="card" aria-label="Rozbory AI">
  <div class="card-head"><h2>Rozbory AI</h2><span class="muted small">jazykový model hodnotí formu a postup, ne politický obsah, a může se mýlit</span></div>
  <div class="table-wrap">
    <table class="table-compact">
      <thead><tr><th class="num">Verze</th><th>Datum</th><th>Model</th><th class="num">Závažnost</th><th>Titulek</th><th>K textu</th></tr></thead>
      <tbody>
        <?php $prev = null; ?>
        <?php foreach ($analyses as $r): ?>
          <?php
            $score = $r['ai_score'] !== null ? (int)$r['ai_score'] : null;
            $title = trim((string)($r['ai']['titulek'] ?? ''));
            $prevScore = $prev !== null && $prev['ai_score'] !== null ? (int)$prev['ai_score'] : null;
            $titleChanged = $prev !== null && $title !== trim((string)($prev['ai']['titulek'] ?? ''));
            $textVersion = $versionByHash[(string)$r['text_hash']] ?? null;
          ?>
          <tr>
            <td class="num"><b><?= (int)$r['verze'] ?></b></td>
            <td><?= e(formatDate((string)($r['ai_at'] ?? $r['created_at']), true)) ?><br><span class="muted small"><?= e(Revisions::SOURCES[(string)$r['zdroj']] ?? (string)$r['zdroj']) ?></span></td>
            <td><?= e((string)($r['ai_model'] ?? '—')) ?><?= $r['ai_prompt'] !== null ? '<br><span class="muted small">prompt v' . (int)$r['ai_prompt'] . '</span>' : '' ?></td>
            <td class="num"><b><?= $score !== null ? $score . '/10' : '—' ?></b><?= $prevScore !== null && $score !== null && $prevScore !== $score ? '<br><span class="muted small">předtím ' . $prevScore . '/10</span>' : '' ?></td>
            <td class="wide"><?= $title !== '' ? e($title) : '<span class="muted">bez titulku</span>' ?><?= $titleChanged ? ' <span class="chip chip-surge">nový titulek</span>' : '' ?></td>
            <td><?= $textVersion !== null ? 'verze ' . $textVersion : '<span class="muted">—</span>' ?></td>
          </tr>
          <?php $prev = $r; ?>
        <?php endforeach; ?>
      </tbody>
    </table>
  </div>
</section>
<?php endif; ?>
<?php
View::footer();
