@extends('layouts.app')
@section('content')
<section class="hero">
  <div>
    <h1>Připravované zákony</h1>
    <p class="lead"><?= count($items) ?> návrhů zákonů, které ministerstva připravují a Sněmovna je ještě nedostala<?= $items !== [] ? ': ' . implode(', ', array_map(fn(string $k) => count($byPhase[$k]) . ' ' . mb_strtolower($phases[$k][0]), array_filter(array_keys($phases), fn(string $k) => $byPhase[$k] !== []))) : '' ?>.</p>
  </div>
</section>

<section class="card pad upcoming-intro">
  <p>Vládní návrh zákona projde před Sněmovnou meziresortním připomínkovým řízením, obvykle na 4 týdny. Kratší řízení a chybějící připomínky označujeme stejně jako u tisků ve Sněmovně<?= $short + $missing > 0 ? ' (teď ' . ($short > 0 ? $short . '× kratší než 3 týdny' : '') . ($short > 0 && $missing > 0 ? ', ' : '') . ($missing > 0 ? $missing . '× nedohledáno' : '') . ')' : '' ?>. Jakmile návrh dostane číslo sněmovního tisku, zmizí odsud a najdete ho mezi <a href="{{ route('bills') }}">tisky</a> i s celou přípravou.</p>
  <p class="muted small">Zdroj: otevřená data Úřadu vlády (portál ODok/VeKLEP), aktualizují se denně. Hodnotíme jen postup, ne obsah. Do skóre nevstupuje.</p>
</section>

<?php if ($items === []): ?>
<section class="card pad"><p class="muted">Data o připravovaných zákonech zatím nejsou načtená.</p></section>
<?php endif; ?>

<?php foreach ($phases as $key => [$label, $hint]): ?>
  <?php if ($byPhase[$key] === []) continue; ?>
<section class="card upcoming" aria-label="<?= e($label) ?>">
  <div class="card-head">
    <h2><?= e($label) ?> <span class="muted">· <?= count($byPhase[$key]) ?></span></h2>
    <span class="muted small"><?= e($hint) ?></span>
  </div>
  <ul class="upcoming-list">
    <?php foreach ($byPhase[$key] as $u): ?>
      <?php $days = $u['dni'] !== null ? (int)$u['dni'] : null; $budget = str_contains(mb_strtolower((string)$u['nazev']), 'státním rozpočtu'); ?>
      <li>
        <a class="upcoming-title" href="<?= e(\Hlidac\GovPrep::url((string)$u['pid'])) ?>"><?= e($shortTitle((string)$u['nazev'])) ?></a>
        <div class="muted small"><?= e((string)$u['resort']) ?>
          <?php if ($days !== null): ?> · připomínky <?= e(formatDate($u['pripominky_od'])) ?> – <?= e(formatDate($u['pripominky_do'])) ?> (<?= $days ?> dní, <?= (int)$u['mist'] ?> míst)<?php elseif ($budget): ?> · státní rozpočet má vlastní postup přípravy<?php endif; ?>
          <?php if (!empty($u['vlada'])): ?> · vláda projednala <?= e(formatDate($u['vlada'])) ?><?php endif; ?>
        </div>
        <?php if (($days !== null && $days < 21) || ($days === null && !$budget && $key !== 'pripominky') || $u['mimo_plan'] || $u['vyjimka']): ?>
        <span class="chips">
          <?php if ($days !== null && $days < 21): ?><span class="chip chip-cut">Připomínky <?= $days ?> dní (obvykle 4 týdny)</span>
          <?php elseif ($days === null && !$budget && $key !== 'pripominky'): ?><span class="chip chip-surge">Připomínkové řízení nedohledáno</span><?php endif; ?>
          <?php if ($u['mimo_plan']): ?><span class="chip chip-surge">Mimo plán legislativních prací</span><?php endif; ?>
          <?php if ($u['vyjimka']): ?><span class="chip chip-surge">Udělena výjimka</span><?php endif; ?>
        </span>
        <?php endif; ?>
        <?php if (trim((string)$u['popis']) !== '' || trim((string)$u['duvod']) !== ''): ?>
        <details>
          <summary>O čem je</summary>
          <?php if (trim((string)$u['popis']) !== ''): ?><p><?= e((string)$u['popis']) ?></p><?php endif; ?>
          <?php if (trim((string)$u['duvod']) !== ''): ?><p class="muted">Důvod předložení: <?= e((string)$u['duvod']) ?></p><?php endif; ?>
        </details>
        <?php endif; ?>
      </li>
    <?php endforeach; ?>
  </ul>
</section>
<?php endforeach; ?>
@endsection
