@extends('layouts.app')
@section('content')
<?php
use Hlidac\View;
?>
<section class="hero">
  <div>
    <h1>Co se změnilo ve Sněmovně</h1>
    <p class="lead">Pozměňovací návrhy, zrychlená projednávání a zkrácené lhůty — části legislativního procesu, které obcházejí veřejné připomínkové řízení.</p>
  </div>
  <form class="search" action="{{ route('bills', [], false) }}" method="get" role="search">
    <label for="q" class="sr-only">Hledat tisky</label>
    <input id="q" name="q" type="search" placeholder="Tisk, číslo zákona nebo poslanec">
    <button type="submit">Hledat</button>
  </form>
</section>

<section class="kpis" aria-label="Klíčová čísla">
  <div class="card kpi">
    <div class="kpi-label">Pozměňovací návrhy v tomto období</div>
    <div class="kpi-value">{{ number_format($s['pn_total'], 0, ',', ' ') }}</div>
    <div class="kpi-note">u {{ $s['bills_with_pn'] }} ze {{ $s['bills_total'] }} návrhů zákonů</div>
  </div>
  <div class="card kpi">
    <div class="kpi-label">Podáno za posledních 7 dní</div>
    <div class="kpi-value">{{ $s['pn_7d'] }}</div>
    <div class="kpi-note"><?php if ($s['pn_7d_top']): ?>z toho {{ (int)$s['pn_7d_top']['n'] }} k tisku <a href="{{ route('bill', ['ct' => (int)$s['pn_7d_top']['ct']], false) }}">{{ (int)$s['pn_7d_top']['ct'] }}</a><?php else: ?>žádné nové návrhy<?php endif; ?></div>
  </div>
  <div class="card kpi">
    <div class="kpi-label">Navrženo schválit v 1. čtení (§ 90/2)</div>
    <div class="kpi-value">{{ $s['p90'] }}</div>
    <div class="kpi-note">{{ $s['p90ok'] }} jich v 1. čtení prošlo</div>
  </div>
  <div class="card kpi">
    <div class="kpi-label">Zkrácená lhůta</div>
    <div class="kpi-value kpi-warn">{{ $s['zl'] }}</div>
    <div class="kpi-note"><?php if ($s['zl_last']): ?>naposledy {{ formatDate($s['zl_last']['datum']) }} (tisk <a href="{{ route('bill', ['ct' => (int)$s['zl_last']['ct']], false) }}">{{ (int)$s['zl_last']['ct'] }}</a>)<?php else: ?>zatím žádná<?php endif; ?></div>
  </div>
</section>

<section class="card" id="nalezy" aria-label="Hlavní nálezy">
  <div class="card-head">
    <div>
      <h2>Hlavní nálezy</h2>
      <p class="muted small head-note">Rozpracované tisky s nejvyšším skóre závažnosti (a uzavřené za poslední 4 měsíce). Skóre hodnotí postup (obcházení připomínek, zrychlení, přílepky), ne politický obsah.</p>
    </div>
    <a href="{{ route('bills', ['min' => (int)$w['level_major']], false) }}">Všechny závažné</a>
  </div>
  <?php if ($findings === []): ?>
    <p class="empty">Žádný tisk nyní nepřekračuje práh závažnosti {{ (int)$w['level_major'] }} bodů.</p>
  <?php endif; ?>
  <div class="findings">
    <?php foreach ($findings as $i => $t): ?>
      <a class="finding" href="{{ route('bill', ['ct' => (int)$t['ct']], false) }}"{{ $i >= $findingsShown ? ' hidden' : '' }}>
        <span class="finding-top">{!! View::scoreBadge((int)$t['score']) !!}<span class="muted small">Tisk {{ (int)$t['ct'] }} · {{ $t['nazev'] }} · {{ $t['faze'] }}</span></span>
        <?php if ($t['headline'] !== null): ?>
          <?php $h = $t['headline']; ?>
          <strong class="finding-title">{{ $h['title'] }}</strong>
          <?php if ($h['perex'] !== ''): ?><span class="finding-perex">{{ $h['perex'] }}</span><?php endif; ?>
          <?php if ($h['quote'] !== ''): ?>
            <span class="finding-quote small">{{ mb_strlen($h['quote']) > 200 ? mb_substr($h['quote'], 0, 199) . '…' : $h['quote'] }}</span>
          <?php endif; ?>
          <span class="muted small">Shrnutí AI z textu ({{ $h['source'] }}), doloženo citací{{ $h['verified'] ? ', ověřeno druhým modelem' : '' }} · body skóre: {!! View::reasonKeysSummary($t['reasons_list'], 3) !!}</span>
        <?php else: ?>
          <strong class="finding-title">{{ $t['uplny_nazev'] ?: $t['nazev'] }}</strong>
          {!! View::reasons($t['reasons_list'], 3) !!}
        <?php endif; ?>
      </a>
    <?php endforeach; ?>
  </div>
  <?php if (count($findings) > $findingsShown): ?>
    <div class="findings-more">
      <a class="btn btn-ghost" id="findings-more" href="{{ route('home', ['n' => $findingsShown + 3], false) }}#nalezy">Zobrazit další nálezy <span class="muted">(zbývá {{ count($findings) - $findingsShown }})</span></a>
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
      <a href="{{ route('alerts', [], false) }}">Všechna upozornění</a>
    </div>
    @include('partials.alerts')
    <?php if ($suppressed > 0): ?>
      <p class="suppressed"><a href="{{ route('alerts', ['vse' => '1'], false) }}">{{ $suppressed }} běžných událostí je skryto</a> – mají nízké skóre závažnosti.</p>
    <?php endif; ?>
  </section>

  <aside class="side-col">
    <section class="card pad" aria-label="Sledované zákony">
      <h2>Sledované zákony</h2>
      <?php if ($watch === []): ?>
        <p class="muted">Nastavte sledované zákony v konfiguraci (<code>watch_laws</code>).</p>
      <?php endif; ?>
      <?php foreach (array_slice($watch, 0, 4) as $t): ?>
        <a class="watch" href="{{ route('bill', ['ct' => (int)$t['ct']], false) }}">
          <span class="muted small">Tisk {{ (int)$t['ct'] }} · {{ $t['typ_navrhu'] }} návrh</span>
          <strong>{{ $t['nazev'] }}</strong>
          <span class="small">{{ $t['faze'] }} · {{ (int)$t['pn_count'] }} PN · {{ formatDate($t['posledni']) }}</span>
          <span class="chips">{!! View::chips($t['flags']) !!}</span>
        </a>
      <?php endforeach; ?>
    </section>

    <section class="card pad" aria-label="Nejaktivnější předkladatelé PN">
      <h2>Nejaktivnější předkladatelé PN</h2>
      <p class="muted small">posledních 7 dní</p>
      <?php if ($authors === []): ?><p class="muted">Žádné nové návrhy.</p><?php endif; ?>
      <?php foreach ($authors as $a): ?>
        <div class="bar-row">
          <span class="bar-name">{{ $a['osoba'] }}</span>
          <span class="bar"><span style="width: {{ round($a['n'] / $maxAuthor * 100) }}%"></span></span>
          <span class="bar-n">{{ (int)$a['n'] }}</span>
        </div>
      <?php endforeach; ?>
    </section>
  </aside>
</div>

<section class="card" aria-label="Tisky ke sledování">
  <div class="card-head">
    <h2>Tisky ke sledování</h2>
    <div class="filters">
      <a class="pill active" href="{{ route('bills', ['f' => 'flagged'], false) }}">Všechny příznaky</a>
      <a class="pill" href="{{ route('bills', ['f' => 'mp'], false) }}">Poslanecké návrhy</a>
      <a class="pill" href="{{ route('bills', ['f' => 'p90'], false) }}">Zrychlené čtení</a>
      <a class="pill" href="{{ route('bills', ['f' => 'cut'], false) }}">Zkrácená lhůta</a>
    </div>
  </div>
  @include('partials.bills_table')
</section>

@endsection
