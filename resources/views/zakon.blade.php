@extends('layouts.app')
@section('content')
<section class="hero">
  <div>
    <h1><?= e($title) ?></h1>
    <p class="lead">
      <?= count($bills) ?> <?= count($bills) === 1 ? 'tisk' : (count($bills) >= 2 && count($bills) <= 4 ? 'tisky' : 'tisků') ?> ve Sněmovně<?php if ($amendments !== []): ?>, <?= count($amendments) ?> pozměňovacích návrhů k jiným tiskům<?php endif; ?><?php if ($upcoming !== []): ?>, <?= count($upcoming) ?> připravovaných u vlády<?php endif; ?>.
      <?= in_array($law, $watched, true) ? '<span class="chip chip-mp">Sledovaný zákon</span>' : '' ?>
    </p>
    <p class="small"><a href="<?= e(\Hlidac\Laws::eSbirkaUrl($law)) ?>">Platné znění v e-Sbírce</a> · <a href="/rss?zakon=<?= e(rawurlencode($law)) ?>">RSS upozornění k tomuto zákonu</a> · <a href="/api?zakon=<?= e(rawurlencode($law)) ?>">JSON</a> · <a href="{{ route('laws', [], false) }}">Všechny zákony</a></p>
  </div>
</section>

<section class="card">
  <div class="card-head"><h2>Návrhy ve Sněmovně</h2><span class="muted small">tisky tohoto období, které zákon mění</span></div>
  @include('partials.bills_table')
</section>

<?php if ($amendments !== []): ?>
<section class="card upcoming" aria-label="Pozměňovací návrhy k jiným tiskům">
  <div class="card-head"><h2>Pozměňovací návrhy k jiným tiskům · <?= count($amendments) ?></h2>
    <span class="muted small">Text návrhu mění tento zákon, i když projednávaný tisk ho nemění – může jít o přílepek. Posuďte podle textu.</span></div>
  <ul class="upcoming-list">
    <?php foreach ($amendments as $a): ?>
      <li>
        <a class="upcoming-title" href="/tisky/<?= (int)$a['ct'] ?>#pn-<?= (int)$a['cislo'] ?>">Pozměňovací návrh č. <?= (int)$a['cislo'] ?> k tisku <?= (int)$a['ct'] ?> · <?= e($a['nazev']) ?></a>
        <div class="muted small"><?= e((string)$a['osoba']) ?> · <?= e(formatDate((string)$a['cas'])) ?><?php if ($a['ai_score'] !== null): ?> · AI <?= (int)$a['ai_score'] ?>/10<?php endif; ?></div>
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
        <a class="upcoming-title" href="<?= e(\Hlidac\GovPrep::url((string)$u['pid'])) ?>"><?= e((string)$u['nazev']) ?></a>
        <div class="muted small"><?= e((string)$u['resort']) ?> · <?= e($phase[$u['faze']] ?? $u['faze']) ?><?php if ($u['dni'] !== null): ?> · připomínky <?= (int)$u['dni'] ?> dní<?php endif; ?></div>
      </li>
    <?php endforeach; ?>
  </ul>
</section>
<?php endif; ?>
<p class="muted small">Přiřazení k zákonu vychází z čísel zákonů v názvech tisků a připravovaných materiálů a v textech pozměňovacích návrhů; souhrnné novely mohou měnit i zákony, které v názvu neuvádějí.</p>
@endsection
