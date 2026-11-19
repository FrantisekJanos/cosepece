<?php if ($prep !== null): ?>
<?php
  $gov = $prep['druh'] === 'vladni';
  $prepDays = $prep['dni'] !== null ? (int)$prep['dni'] : null;
?>
<section class="card pad prep" aria-label="Příprava návrhu u vlády">
  <h2><?= $gov ? 'Příprava návrhu u vlády' : 'Stanovisko vlády k návrhu' ?></h2>
  <span class="chips">
    <?php if ($prepDays === null && !$gov): ?><span class="chip chip-cut">Vláda si připomínky nevyžádala</span>
    <?php elseif ($prepDays === null && !$prep['znovu'] && (int)$t['druh'] !== 3): ?><span class="chip chip-surge">Připomínkové řízení nedohledáno</span>
    <?php elseif ($gov && $prepDays < 21): ?><span class="chip chip-cut">Připomínky <?= $prepDays ?> dní (obvykle 4 týdny)</span>
    <?php elseif (!$gov): ?><span class="chip chip-surge">Připomínky ke stanovisku <?= $prepDays ?> dní</span><?php endif; ?>
    <?php if ($prep['mimo_plan']): ?><span class="chip chip-surge">Mimo plán legislativních prací</span><?php endif; ?>
    <?php if ($prep['vyjimka']): ?><span class="chip chip-surge">Udělena výjimka</span><?php endif; ?>
  </span>
  <dl>
    <?php if ($prepDays !== null): ?>
      <dt><?= $gov ? 'Připomínkové řízení' : 'Připomínky' ?></dt>
      <dd><?= e(formatDate($prep['pripominky_od'])) ?> – <?= e(formatDate($prep['pripominky_do'])) ?> · <strong><?= $prepDays ?> dní</strong>, <?= (int)$prep['mist'] ?> připomínkových míst</dd>
      <?php if ($gov): ?><dt></dt><dd><div class="bar" title="<?= $prepDays ?> z obvyklých 28 dní"><span style="width:<?= min(100, (int)round($prepDays / 28 * 100)) ?>%"></span></div><span class="muted small">obvyklá délka u vládních návrhů: 4 týdny</span></dd><?php endif; ?>
    <?php else: ?>
      <dt><?= $gov ? 'Připomínkové řízení' : 'Připomínky' ?></dt>
      <dd><?php if (!$gov): ?>vláda si k návrhu připomínky nevyžádala<?php elseif ($prep['znovu']): ?>u tohoto materiálu není; jde o znovu předložený návrh, připomínky mohly proběhnout u předchozí verze<?php elseif ((int)$t['druh'] === 3): ?>státní rozpočet má vlastní postup přípravy<?php else: ?>v otevřených datech Úřadu vlády (od roku 2023) jsme ho k tomuto materiálu nenašli<?php endif; ?></dd>
    <?php endif; ?>
    <?php if (!empty($prep['vlada'])): ?><dt><?= $gov ? 'Vláda schválila' : 'Vláda projednala' ?></dt><dd><?= e(formatDate($prep['vlada'])) ?></dd><?php endif; ?>
    <?php if ($gov && trim((string)$prep['duvod']) !== ''): ?><dt>Důvod předložení</dt><dd><?= e((string)$prep['duvod']) ?></dd><?php endif; ?>
    <?php if (trim((string)$prep['poznamka']) !== ''): ?><dt>Poznámka</dt><dd class="small"><?= e((string)$prep['poznamka']) ?></dd><?php endif; ?>
  </dl>
  <p class="muted small"><?= $gov ? '' : 'Text poslaneckého návrhu nepřipravovalo ministerstvo; připomínky se týkají stanoviska vlády, ne úprav návrhu. ' ?>Zdroj: <a href="<?= e(Hlidac\GovPrep::url((string)$prep['pid'])) ?>">materiál ve VeKLEP</a> (otevřená data Úřadu vlády), kde je i stanovisko Legislativní rady vlády a připomínky. Do skóre nevstupuje.</p>
</section>
<?php endif; ?>
