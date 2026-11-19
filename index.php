<?php
// Přehled: klíčová čísla, poslední upozornění, sledované zákony, tisky ke sledování.

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\Repo;
use Hlidac\Scorer;
use Hlidac\View;

$w = Scorer::weights(config());
$repo = new Repo(db());
$s = $repo->stats();
// hlavní nálezy: od skóre finding_min, hned vidět 9 karet (3 řady), další po trojicích (bez JS odkazem ?n=);
// řazení ?razeni= a druh problému ?druh= jsou v adrese, takže odkaz jde poslat dál
$findingsShown = max(9, min(60, (int)($_GET['n'] ?? 9)));
$sort = (string)($_GET['razeni'] ?? 'skore');
$sort = isset(Repo::FINDING_SORTS[$sort]) ? $sort : 'skore';
$group = (string)($_GET['druh'] ?? '');
$group = isset(Repo::PROBLEM_GROUPS[$group]) ? $group : '';
$found = $repo->findings((int)$w['finding_min'], $sort, $group);
$findings = array_slice($found['rows'], 0, 60);
$findingsUrl = fn(array $set): string => '?' . http_build_query(array_filter(array_merge(['razeni' => $sort === 'skore' ? null : $sort, 'druh' => $group ?: null], $set))) . '#nalezy';
$alerts = $repo->alerts(8, 0, null, (int)$w['level_watch']);
$suppressed = $repo->suppressedCount((int)$w['level_watch']);
$watch = $repo->watchlist(config('watch_laws', []), config('watch_tisky', []));
$authors = $repo->topAuthors(7, 6);
$bills = $repo->bills('flagged', '', 12);
$maxAuthor = $authors[0]['n'] ?? 1;

View::header('Přehled', 'prehled');
?>
<section class="hero">
  <div>
    <h1>Co se změnilo ve Sněmovně</h1>
    <p class="lead">Pozměňovací návrhy, zrychlená projednávání a zkrácené lhůty — části legislativního procesu, které obcházejí veřejné připomínkové řízení.</p>
  </div>
  <form class="search" action="tisky.php" method="get" role="search">
    <label for="q" class="sr-only">Hledat tisky</label>
    <input id="q" name="q" type="search" placeholder="Tisk, číslo zákona nebo poslanec">
    <button type="submit">Hledat</button>
  </form>
</section>

<section class="kpis" aria-label="Klíčová čísla">
  <div class="card kpi">
    <div class="kpi-label">Pozměňovací návrhy v tomto období</div>
    <div class="kpi-value"><?= number_format($s['pn_total'], 0, ',', ' ') ?></div>
    <div class="kpi-note">u <?= $s['bills_with_pn'] ?> ze <?= $s['bills_total'] ?> návrhů zákonů</div>
  </div>
  <div class="card kpi">
    <div class="kpi-label">Podáno za posledních 7 dní</div>
    <div class="kpi-value"><?= $s['pn_7d'] ?></div>
    <div class="kpi-note"><?php if ($s['pn_7d_top']): ?>z toho <?= (int)$s['pn_7d_top']['n'] ?> k tisku <a href="tisk.php?ct=<?= (int)$s['pn_7d_top']['ct'] ?>"><?= (int)$s['pn_7d_top']['ct'] ?></a><?php else: ?>žádné nové návrhy<?php endif; ?></div>
  </div>
  <div class="card kpi">
    <div class="kpi-label">Navrženo schválit v 1. čtení (§ 90/2)</div>
    <div class="kpi-value"><?= $s['p90'] ?></div>
    <div class="kpi-note"><?= $s['p90ok'] ?> jich v 1. čtení prošlo</div>
  </div>
  <div class="card kpi">
    <div class="kpi-label">Zkrácená lhůta</div>
    <div class="kpi-value kpi-warn"><?= $s['zl'] ?></div>
    <div class="kpi-note"><?php if ($s['zl_last']): ?>naposledy <?= e(formatDate($s['zl_last']['datum'])) ?> (tisk <a href="tisk.php?ct=<?= (int)$s['zl_last']['ct'] ?>"><?= (int)$s['zl_last']['ct'] ?></a>)<?php else: ?>zatím žádná<?php endif; ?></div>
  </div>
</section>

<section class="card" id="nalezy" aria-label="Hlavní nálezy">
  <div class="card-head">
    <div>
      <h2>Hlavní nálezy</h2>
      <p class="muted small head-note">Rozpracované tisky se skóre od <?= (int)$w['finding_min'] ?> bodů (a uzavřené za poslední 4 měsíce), k tomu tisky s nálezem AI od <?= (int)$w['finding_ai_min'] ?>/10 ověřeným silnějším modelem. Skóre hodnotí postup (obcházení připomínek, zrychlení, přílepky), ne politický obsah.</p>
    </div>
    <a href="tisky.php?min=<?= (int)$w['finding_min'] ?>">Všechny od <?= (int)$w['finding_min'] ?> bodů</a>
  </div>
  <?php if ($found['total'] > 0): ?>
  <div class="finding-controls">
    <div class="control-row" role="group" aria-label="Řazení nálezů">
      <span class="control-label">Řadit</span>
      <?php foreach (Repo::FINDING_SORTS as $key => $label): ?>
        <a class="pill<?= $key === $sort ? ' active' : '' ?>" href="<?= e($findingsUrl(['razeni' => $key === 'skore' ? null : $key])) ?>"<?= $key === $sort ? ' aria-current="true"' : '' ?>><?= e($label) ?></a>
      <?php endforeach; ?>
    </div>
    <div class="control-row" role="group" aria-label="Druh problému">
      <span class="control-label">Co hledám</span>
      <a class="pill<?= $group === '' ? ' active' : '' ?>" href="<?= e($findingsUrl(['druh' => null])) ?>">Vše <span class="pill-count"><?= (int)$found['total'] ?></span></a>
      <?php foreach (Repo::PROBLEM_GROUPS as $key => [$label]): ?>
        <?php if ($found['counts'][$key] === 0 && $group !== $key) continue; ?>
        <a class="pill<?= $key === $group ? ' active' : '' ?>" href="<?= e($findingsUrl(['druh' => $key])) ?>"<?= $key === $group ? ' aria-current="true"' : '' ?>><?= e($label) ?> <span class="pill-count"><?= (int)$found['counts'][$key] ?></span></a>
      <?php endforeach; ?>
    </div>
    <?php if ($sort === 'ai'): ?><p class="muted small control-note">Podle nejzávažnějšího nálezu v rozboru textu návrhu nebo pozměňovacího návrhu; počítají se jen nálezy ověřené silnějším modelem a doložené citací. Tisky bez ověřeného nálezu jsou na konci.</p>
    <?php elseif ($sort === 'hlasovani'): ?><p class="muted small control-note">Napřed tisky ve 3. čtení a před hlasováním po Senátu nebo po vetu, pak 2. a 1. čtení; tisky, o kterých už Sněmovna nehlasuje, jsou na konci.</p>
    <?php elseif ($sort === 'nove'): ?><p class="muted small control-note">Podle posledního kroku v projednávání nebo nového pozměňovacího návrhu.</p><?php endif; ?>
  </div>
  <?php endif; ?>
  <?php if ($findings === []): ?>
    <p class="empty"><?= $group !== '' ? 'Mezi hlavními nálezy teď nic takového není.' : 'Žádný tisk nyní nemá skóre aspoň ' . (int)$w['finding_min'] . ' bodů.' ?></p>
  <?php endif; ?>
  <div class="findings">
    <?php foreach ($findings as $i => $t): ?>
      <a class="finding" href="tisk.php?ct=<?= (int)$t['ct'] ?>"<?= $i >= $findingsShown ? ' hidden' : '' ?>>
        <span class="finding-top"><?= View::scoreBadge((int)$t['score']) ?><?php if ($t['strong_ai'] !== null || ($sort === 'ai' && $t['ai_verified'] !== null)): ?><span class="chip <?= (int)$t['ai_verified'] >= 6 ? 'chip-cut' : 'chip-surge' ?>" title="Nález AI ověřený silnějším modelem a doložený citací z textu">Ověřený nález AI <?= (int)$t['ai_verified'] ?>/10</span><?php endif; ?><span class="muted small">Tisk <?= (int)$t['ct'] ?> · <?= e($t['nazev']) ?> · <?= e($t['faze']) ?></span></span>
        <?php if ($t['headline'] !== null): ?>
          <?php $h = $t['headline']; ?>
          <strong class="finding-title"><?= e($h['title']) ?></strong>
          <?php if ($h['perex'] !== ''): ?><span class="finding-perex"><?= e($h['perex']) ?></span><?php endif; ?>
          <?php if ($h['quote'] !== ''): ?>
            <span class="finding-quote small"><?= e(mb_strlen($h['quote']) > 200 ? mb_substr($h['quote'], 0, 199) . '…' : $h['quote']) ?></span>
          <?php endif; ?>
          <span class="muted small">Shrnutí AI z textu (<?= e($h['source']) ?>), doloženo citací<?= $h['verified'] ? ', ověřeno druhým modelem' : '' ?> · body skóre: <?= View::reasonKeysSummary($t['reasons_list'], 3) ?></span>
        <?php else: ?>
          <strong class="finding-title"><?= e($t['uplny_nazev'] ?: $t['nazev']) ?></strong>
          <?= View::reasons($t['reasons_list'], 3) ?>
        <?php endif; ?>
      </a>
    <?php endforeach; ?>
  </div>
  <?php if (count($findings) > $findingsShown): ?>
    <div class="findings-more">
      <a class="btn btn-ghost" id="findings-more" href="<?= e($findingsUrl(['n' => $findingsShown + 3])) ?>">Zobrazit další nálezy <span class="muted">(zbývá <?= count($findings) - $findingsShown ?>)</span></a>
    </div>
    <script>
    // odkryje další 3 nálezy bez načtení stránky; bez JS funguje odkaz ?n=
    (function () {
      var btn = document.getElementById('findings-more');
      btn.addEventListener('click', function (ev) {
        ev.preventDefault();
        var hidden = document.querySelectorAll('#nalezy .finding[hidden]');
        for (var i = 0; i < 3 && i < hidden.length; i++) { hidden[i].hidden = false; }
        var left = hidden.length - Math.min(3, hidden.length);
        if (left === 0) { btn.parentNode.remove(); } else { btn.querySelector('span').textContent = '(zbývá ' + left + ')'; }
      });
    })();
    </script>
  <?php endif; ?>
</section>

<div class="split">
  <section class="card main-col" aria-label="Poslední upozornění">
    <div class="card-head">
      <h2>Poslední upozornění</h2>
      <a href="upozorneni.php">Všechna upozornění</a>
    </div>
    <?php View::alertList($alerts); ?>
    <?php if ($suppressed > 0): ?>
      <p class="suppressed"><a href="upozorneni.php?vse=1"><?= $suppressed ?> běžných událostí je skryto</a> – mají nízké skóre závažnosti.</p>
    <?php endif; ?>
  </section>

  <aside class="side-col">
    <section class="card pad" aria-label="Sledované zákony">
      <h2>Sledované zákony</h2>
      <?php if ($watch === []): ?>
        <p class="muted">Nastavte sledované zákony v konfiguraci (<code>watch_laws</code>).</p>
      <?php endif; ?>
      <?php foreach (array_slice($watch, 0, 4) as $t): ?>
        <a class="watch" href="tisk.php?ct=<?= (int)$t['ct'] ?>">
          <span class="muted small">Tisk <?= (int)$t['ct'] ?> · <?= e($t['typ_navrhu']) ?> návrh</span>
          <strong><?= e($t['nazev']) ?></strong>
          <span class="small"><?= e($t['faze']) ?> · <?= (int)$t['pn_count'] ?> PN · <?= e(formatDate($t['posledni'])) ?></span>
          <span class="chips"><?= View::chips($t['flags']) ?></span>
        </a>
      <?php endforeach; ?>
    </section>

    <section class="card pad" aria-label="Nejaktivnější předkladatelé PN">
      <h2>Nejaktivnější předkladatelé PN</h2>
      <p class="muted small">posledních 7 dní</p>
      <?php if ($authors === []): ?><p class="muted">Žádné nové návrhy.</p><?php endif; ?>
      <?php foreach ($authors as $a): ?>
        <div class="bar-row">
          <span class="bar-name"><?= e($a['osoba']) ?></span>
          <span class="bar"><span style="width: <?= round($a['n'] / $maxAuthor * 100) ?>%"></span></span>
          <span class="bar-n"><?= (int)$a['n'] ?></span>
        </div>
      <?php endforeach; ?>
    </section>
  </aside>
</div>

<section class="card" aria-label="Tisky ke sledování">
  <div class="card-head">
    <h2>Tisky ke sledování</h2>
    <div class="filters">
      <a class="pill active" href="tisky.php?f=flagged">Všechny příznaky</a>
      <a class="pill" href="tisky.php?f=mp">Poslanecké návrhy</a>
      <a class="pill" href="tisky.php?f=p90">Zrychlené čtení</a>
      <a class="pill" href="tisky.php?f=cut">Zkrácená lhůta</a>
    </div>
  </div>
  <?php require __DIR__ . '/app/partials/bills_table.php'; ?>
</section>
<?php
View::footer();
