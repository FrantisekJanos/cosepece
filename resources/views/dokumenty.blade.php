@extends('layouts.app')
@section('content')
<?php
use Hlidac\View;
$count = $bill->amendments->count();
$countLabel = $count === 1 ? 'pozměňovací návrh' : ($count >= 2 && $count <= 4 ? 'pozměňovací návrhy' : 'pozměňovacích návrhů');
$isBill = $selected === null;
$originalUrl = $document?->docx_url ?: ($isBill ? View::pspTiskUrl($bill->ct, $period) : View::pspSdUrl($selected->cislo, $period));
?>
<section class="card reader-context" aria-label="Souhrn tisku {{ $bill->ct }}">
  <div class="reader-context-title">
    <a href="{{ route('bill', ['ct' => $bill->ct], false) }}">← Tisk {{ $bill->ct }}</a>
    <strong>{{ $t['nazev'] ?: $t['uplny_nazev'] }}</strong>
    {!! View::scoreBadge((int)$t['score']) !!}
    <span class="chips">{!! View::chips($t['flags']) !!}</span>
  </div>
  <dl class="reader-facts">
    <div><dt>Fáze</dt><dd>{{ $t['faze'] }}</dd></div>
    <div><dt>Předkladatel</dt><dd>
      @if ($t['typ_navrhu'] === 'vládní')
      Vláda{{ $t['navrhovatel'] ? ' · ' . ($t['navrhovatel'] === 'min. financí' ? 'ministr financí' : $t['navrhovatel']) : '' }}
      @else
      {{ $t['navrhovatel'] ?: $t['typ_navrhu'] }}
      @endif
    </dd></div>
    <div><dt>Předloženo</dt><dd>{{ formatDate($t['predlozeno']) }}</dd></div>
    <div><dt>Poslední krok</dt><dd>{{ formatDate($t['posledni']) }}</dd></div>
    <div><dt>Pozměňovací návrhy</dt><dd>{{ $count }}</dd></div>
    <div><dt>Hlasování k tisku</dt><dd><a href="{{ route('bill', ['ct' => $bill->ct], false) }}#hlasovani">{{ $bill->votes_count }} načtených · otevřít ↗</a></dd></div>
  </dl>
</section>

<div class="document-reader">
  <article class="reader-main" aria-labelledby="document-title">
    <header class="reader-head">
      <div class="reader-head-text">
        <span class="reader-kind">{{ $isBill ? 'Základní dokument · ' . $bill->ct . '/0' : 'Pozměňovací návrh' }}</span>
        <h1 id="document-title">{{ $isBill ? 'Návrh zákona' : 'Návrh č. ' . $selected->cislo }}</h1>
        <p class="reader-sub">
          @if ($isBill)
          {{ $t['uplny_nazev'] ?: $t['nazev'] }}
          @else
          <strong>{{ $selected->osoba }}</strong> · podáno {{ formatDate($selected->cas->format('Y-m-d H:i:s'), true) }}
          @endif
        </p>
        @if ($analysis && !$analysis['ai'] && $analysis['quotes'] === [] && $analysis['badges'])
        <span class="chips reader-head-chips">
          @foreach ($analysis['badges'] as [$class, $label])
          <span class="chip chip-{{ $class }}">{{ $label }}</span>
          @endforeach
        </span>
        @endif
      </div>
      <nav class="reader-step" aria-label="Sousední dokument a originál">
        @if ($document?->docx_url)
        <a class="reader-word" href="{{ $originalUrl }}">Stáhnout Word <span aria-hidden="true">↗</span></a>
        @endif
        @if ($previous)
        <a href="{{ route('bill-documents', ['ct' => $bill->ct, 'pn' => $previous['pn']], false) }}" rel="prev"><span aria-hidden="true">‹</span> {{ $previous['pn'] === null ? 'Návrh zákona' : 'Č. ' . $previous['number'] }}</a>
        @endif
        @if ($next)
        <a href="{{ route('bill-documents', ['ct' => $bill->ct, 'pn' => $next['pn']], false) }}" rel="next">Č. {{ $next['number'] }} <span aria-hidden="true">›</span></a>
        @endif
      </nav>
    </header>

    @if ($analysis && ($analysis['ai'] || $analysis['quotes']))
    <section class="card reader-analysis" aria-label="Rozbor dokumentu">
      <div class="reader-analysis-head">
        <h2>{{ $analysis['ai'] ? 'Rozbor dokumentu' : 'Upozornění pravidel' }}</h2>
        <span class="chips">
          @foreach ($analysis['badges'] as [$class, $label])
          <span class="chip chip-{{ $class }}">{{ $label }}</span>
          @endforeach
        </span>
      </div>
      @if (!empty($analysis['ai']['shrnuti']))<p>{{ $analysis['ai']['shrnuti'] }}</p>@endif
      @if (!empty($analysis['ai']['duvod']))<p class="muted">{{ $analysis['ai']['duvod'] }}</p>@endif
      @if ($analysis['quotes'] !== [])
      <details class="reader-quote-list">
      <summary>{{ $analysis['quote_source'] === 'ai' ? 'Klíčové citace' : 'Nalezené pasáže' }} ({{ count($analysis['quotes']) }})</summary>
      <ul class="reader-quotes" aria-label="Doslovné citace z dokumentu">
        @foreach ($analysis['quotes'] as $quote)
        <li>
          <blockquote class="quote">{{ $quote }}</blockquote>
          <button type="button" class="reader-find" data-quote="{{ $quote }}" hidden>Najít v dokumentu</button>
        </li>
        @endforeach
      </ul>
      </details>
      @endif
      <p class="muted small reader-analysis-foot">
        {{ $analysis['ai'] ? 'Rozbor připravil jazykový model, hodnotí postup a formu, ne politický obsah.' : 'Pasáže vybrala automatická pravidla.' }}
        @if ($analysis['ai_round']){!! $analysis['ai_round'] !!}@endif
        @if ($analysis['fetched_at'])· Text stažen {{ formatDate($analysis['fetched_at'], true) }}@endif
      </p>
    </section>
    @endif

    @if (!$document?->docx_url)
    <div class="card reader-empty"><h2>Word zatím není dostupný</h2><p class="muted">Dokument si můžete otevřít na webu Sněmovny.</p><a class="btn" href="{{ $originalUrl }}">Originál na PSP ↗</a></div>
    @else
    <div class="word-viewer" data-source="{{ route('bill-word', ['ct' => $bill->ct, 'pn' => $selected?->id_dokument], false) }}">
      <div class="word-toolbar" role="toolbar" aria-label="Ovládání zobrazení dokumentu">
        <div class="word-controls" hidden>
          <div class="word-group" role="group" aria-label="Zvětšení">
            <button type="button" data-zoom="out" aria-label="Zmenšit">−</button>
            <output class="word-zoom" aria-live="polite">100 %</output>
            <button type="button" data-zoom="in" aria-label="Zvětšit">+</button>
            <button type="button" data-zoom="fit" class="word-fit">Na šířku</button>
          </div>
          <div class="word-group" role="group" aria-label="Stránky">
            <button type="button" data-page="prev" aria-label="Předchozí strana">‹</button>
            <span class="word-page"><span class="word-page-label">Strana </span><b>1</b> / <b>1</b></span>
            <button type="button" data-page="next" aria-label="Další strana">›</button>
          </div>
        </div>
      </div>
      <p class="word-status" role="status">Načítám Word…</p>
      <div class="word-viewport" tabindex="0" aria-label="Náhled dokumentu"><div class="word-scaler"><div class="word-pages"></div></div></div>
    </div>
    <noscript><p class="card pad">Pro zobrazení Wordu zapněte JavaScript nebo <a href="{{ $originalUrl }}">otevřete originální soubor</a>.</p></noscript>
    @endif
  </article>

  <aside class="reader-sidebar" id="reader-navigation" aria-label="Dokumenty tisku">
    <a class="reader-votes-link" href="{{ route('bill', ['ct' => $bill->ct], false) }}#hlasovani">
      <strong>Hlasování k tisku {{ $bill->ct }} ↗</strong>
      <span>{{ $bill->votes_count }} načtených hlasování</span>
    </a>
    @if (!$isBill)
    <p class="reader-votes-note">Výsledky jsou za celý tisk. Výsledek tohoto pozměňovacího návrhu zatím neumíme spolehlivě určit.</p>
    @endif
    <h2>Dokumenty tisku</h2>
    <nav aria-label="Výběr dokumentu">
      @foreach ($entries as $entry)
      @php($current = $entry['pn'] === $selected?->id_dokument)
      <a class="reader-document{{ $current ? ' is-selected' : '' }}" href="{{ route('bill-documents', ['ct' => $bill->ct, 'pn' => $entry['pn']], false) }}" @if ($current) aria-current="page" @endif>
        @if ($entry['pn'] === null)
        <strong>Návrh zákona {{ $bill->ct }}/0</strong>
        <span class="small muted">Základní dokument</span>
        @else
        <strong>Návrh č. {{ $entry['number'] }}</strong>
        <span>{{ $entry['author'] }}</span>
        <span class="small muted">{{ formatDate($entry['time'], true) }}</span>
        @endif
        @if ($entry['badges'] !== [] || !$entry['word'])
        <span class="chips">
          @foreach (array_slice($entry['badges'], 0, 3) as [$class, $label])
          <span class="chip chip-{{ $class }}">{{ $label }}</span>
          @endforeach
          @if (!$entry['word'])<span class="chip chip-pending">bez Wordu</span>@endif
        </span>
        @endif
      </a>
      @if ($entry['pn'] === null)
      <h3 class="reader-group">{{ $count }} {{ $countLabel }}</h3>
      @if ($count === 0)<p class="muted small">K tisku nebyl podán žádný písemný pozměňovací návrh.</p>@endif
      @endif
      @endforeach
    </nav>
  </aside>
</div>
<script src="{{ asset('assets/vendor/jszip-3.10.2.min.js') }}" defer></script>
<script src="{{ asset('assets/vendor/docx-preview-0.4.1.min.js') }}" defer></script>
<script src="{{ asset('assets/word-reader.js') }}" defer></script>
@endsection
