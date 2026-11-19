@extends('layouts.app')
@section('content')
<?php
use Hlidac\View;
?>
<section class="hero">
  <div>
    <h1>Upozornění</h1>
    <p class="lead">{{ $total }} událostí · odebírat můžete přes <a href="rss.php">RSS</a> nebo na sociálních sítích</p>
  </div>
</section>
<section class="card">
  <div class="card-head">
    <div class="filters">
      <a class="pill{{ $all ? '' : ' active' }}" href="upozorneni.php">Jen důležité (skóre {{ $watchThreshold }}+)</a>
      <a class="pill{{ $all ? ' active' : '' }}" href="upozorneni.php?vse=1">Včetně běžné agendy</a>
    </div>
  </div>
  @include('partials.alerts')
  <?php if ($pages > 1): ?>
    <nav class="pager" aria-label="Stránkování">
      <?php if ($page > 1): ?><a class="pill" href="?p={{ $page - 1 }}{{ $qs }}">← Novější</a><?php endif; ?>
      <span class="muted">Strana {{ $page }} z {{ $pages }}</span>
      <?php if ($page < $pages): ?><a class="pill" href="?p={{ $page + 1 }}{{ $qs }}">Starší →</a><?php endif; ?>
    </nav>
  <?php endif; ?>
</section>

@endsection
