@extends('layouts.app')
@section('content')
<?php
use Hlidac\View;
?>
<section class="hero">
  <div>
    <h1>Návrhy zákonů</h1>
    <p class="lead">{{ count($bills) }} tisků{{ $q !== '' ? ' pro „' . $q . '“' : '' }} · {{ $filters[$f] }}{!! $min > 0 ? ' · skóre ' . $min . '+' : '' !!} · řazeno {{ $sort === 'recent' ? 'podle poslední změny' : 'podle závažnosti' }}</p>
  </div>
  <form class="search" action="tisky.php" method="get" role="search">
    <label for="q" class="sr-only">Hledat tisky</label>
    <input id="q" name="q" type="search" value="{{ $q }}" placeholder="Tisk, číslo zákona nebo poslanec">
    <?php if ($f !== ''): ?><input type="hidden" name="f" value="{{ $f }}"><?php endif; ?>
    <button type="submit">Hledat</button>
  </form>
</section>

<section class="card">
  <div class="card-head">
    <div class="filters">
      <?php foreach ($filters as $key => $label): ?>
        <a class="pill{{ $key === $f ? ' active' : '' }}" href="tisky.php?{{ http_build_query(array_filter(['f' => $key, 'q' => $q, 'sort' => $sort === 'recent' ? 'recent' : null, 'min' => $min ?: null])) }}">{{ $label }}</a>
      <?php endforeach; ?>
    </div>
    <div class="filters">
      <a class="pill{{ $sort === 'score' ? ' active' : '' }}" href="tisky.php?{{ http_build_query(array_filter(['f' => $f, 'q' => $q, 'min' => $min ?: null])) }}">Podle závažnosti</a>
      <a class="pill{{ $sort === 'recent' ? ' active' : '' }}" href="tisky.php?{{ http_build_query(array_filter(['f' => $f, 'q' => $q, 'sort' => 'recent', 'min' => $min ?: null])) }}">Podle data</a>
    </div>
  </div>
  @include('partials.bills_table')
</section>

@endsection
