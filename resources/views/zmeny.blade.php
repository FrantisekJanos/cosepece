@extends('layouts.app')
@section('content')
<section class="hero">
  <div>
    <h1>Co jsme změnili</h1>
    <p class="lead">Opravy a změny v tom, jak web čte data Sněmovny a jak počítá skóre. Změny vycházejí hlavně z připomínek čtenářů. Každá má odkaz na konkrétní úpravu kódu.</p>
  </div>
</section>

<section class="card pad" aria-label="Seznam změn">
  <ul class="changes">
    <?php foreach ($changes as [$date, $title, $text, $prs, $score]): ?>
      <li class="change">
        <div class="change-meta">
          <time datetime="<?= e($date) ?>"><?= e(formatDate($date)) ?></time>
          <?php if ($score): ?><span class="chip chip-cut">Mění skóre</span><?php endif; ?>
        </div>
        <h3><?= e($title) ?></h3>
        <p><?= e($text) ?></p>
        <p class="muted small">Úprava kódu: <?= implode(', ', array_map(fn(int $n) => '<a href="' . e($repo . '/pull/' . $n) . '">#' . $n . '</a>', $prs)) ?></p>
      </li>
    <?php endforeach; ?>
  </ul>
  <p class="muted small">Jak se skóre počítá teď, popisuje stránka <a href="{{ route('methodology', [], false) }}">Jak hodnotíme</a>. <?php if ((string)hlidacConfig('issues_url', '') !== ''): ?><a href="<?= e((string)hlidacConfig('issues_url', '')) ?>">Našli jste chybu nebo máte podnět? Napište nám.</a><?php endif; ?></p>
</section>
@endsection
