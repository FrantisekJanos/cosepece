@extends('layouts.app')
@section('content')
<section class="hero">
  <div>
    <h1>Hledání</h1>
    <p class="lead"><?= $q === '' ? 'Tisky, pozměňovací návrhy a jejich autoři, zákony, připravované zákony a témata.' : ($total > 0 ? $total . ' výsledků pro „' . e($q) . '“' : 'Pro „' . e($q) . '“ jsme nic nenašli.') ?></p>
  </div>
  @include('partials.search', ['searchQuery' => $q, 'searchVariant' => 'hero', 'searchId' => 'search-results'])
</section>

<?php if ($q !== '' && $total === 0): ?>
<section class="card pad">
  <p>Zkuste kratší nebo obecnější slovo, číslo tisku (např. <a href="/hledat?q=221">221</a>), číslo zákona (<a href="/hledat?q=159%2F2006">159/2006</a>) nebo jméno poslance. Hledá se bez ohledu na diakritiku a stačí začátek slova.</p>
</section>
<?php endif; ?>

<?php foreach ($groups as $kind => $items): ?>
<section class="card search-group" aria-label="<?= e(\Hlidac\SearchIndex::KINDS[$kind] ?? $kind) ?>">
  <div class="card-head"><h2><?= e(\Hlidac\SearchIndex::KINDS[$kind] ?? $kind) ?> <span class="muted">· <?= count($items) ?><?= count($items) >= 50 ? '+' : '' ?></span></h2></div>
  <ul class="search-results">
    <?php foreach ($items as $it): ?>
      <li>
        <a href="<?= e($it['u']) ?>"><?php if ($it['k'] === 'tisk'): ?><?= \Hlidac\View::scoreBadge((int)$it['w']) ?> <?php endif; ?><strong><?= e($it['t']) ?></strong></a>
        <?php if ($it['s'] !== ''): ?><span class="muted small"><?= e($it['s']) ?></span><?php endif; ?>
        <?php if ($it['d'] !== ''): ?><span class="small"><?= e($it['d']) ?></span><?php endif; ?>
      </li>
    <?php endforeach; ?>
  </ul>
</section>
<?php endforeach; ?>
<?php if ($total > 0): ?><p class="muted small">Položky začínající „AI:“ jsou shrnutí připravená umělou inteligencí, která se může mýlit.</p><?php endif; ?>
@endsection
