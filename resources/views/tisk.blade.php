@extends('layouts.app')
@section('content')
<?php
use Hlidac\View;
?>
<div class="bill-detail-page">
<p class="crumbs"><a href="{{ route('bills', [], false) }}">Tisky</a> › Tisk {{ $ct }}</p>
<section class="detail-head">
  <div class="detail-head-main">
    <div class="detail-title-row">
      <h1>{{ $t['nazev'] ?: $t['uplny_nazev'] }}</h1>
      <div class="detail-score">
        {!! View::scoreBadge((int)$t['score']) !!}
        <details class="score-info">
          <summary aria-label="Proč skóre {{ (int)$t['score'] }} ze 100"><span aria-hidden="true">i</span></summary>
          <section class="card pad compact score-explanation" aria-label="Proč toto skóre">
  <h2>Proč skóre {{ (int)$t['score'] }} ze 100</h2>
  <?php if ($t['reasons_list'] === []): ?>
    <p class="muted">Žádné procedurální příznaky – běžné projednávání.</p>
  <?php else: ?>
    {!! View::reasons($t['reasons_list']) !!}
  <?php endif; ?>
  <?php if ($review['total'] > 0 && $review['done'] < $review['total']): ?>
    <p class="small"><strong>Rozbor textů není úplný:</strong> posouzeno {{ (int)$review['done'] }} z {{ (int)$review['total'] }} textů (návrh zákona a pozměňovací návrhy). Neposouzené texty do skóre zatím nepřispívají, nízké skóre proto neznamená „bez problému“.</p>
  <?php endif; ?>
  <p class="muted small">Skóre hodnotí postup projednávání a formu pozměňovacích návrhů, ne politický obsah zákona. <a href="{{ route('methodology', [], false) }}">Jak skóre počítáme.</a> <a href="{{ (string)hlidacConfig('issues_url', '') }}">Nesouhlasíte? Napište nám.</a></p>
</section>
        </details>
      </div>
    </div>
    <div class="detail-meta">
      <strong>{{ $t['faze'] }}</strong>
      @if ($t['typ_navrhu'] === 'vládní')
      <span>Vláda
        @if ($t['navrhovatel'])
        · zastupuje {{ $t['navrhovatel'] === 'min. financí' ? 'ministr financí' : $t['navrhovatel'] }}
        @endif
      </span>
      @elseif ($t['navrhovatel'])
      <span>{{ $t['navrhovatel'] }}</span>
      @endif
      <a href="#pozmenovaci-navrhy">{{ count($pn) }} {{ count($pn) === 1 ? 'pozměňovací návrh' : (count($pn) >= 2 && count($pn) <= 4 ? 'pozměňovací návrhy' : 'pozměňovacích návrhů') }}</a>
      <span class="chips">{!! View::chips($t['flags']) !!}</span>
    </div>
  </div>
  <div class="detail-documents">
    <a class="detail-votes-link" href="#hlasovani">Hlasování ({{ count($votes) }}) ↓</a>
    <a class="btn reader-launch" href="{{ route('bill-documents', ['ct' => $ct], false) }}">
      <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5v15M3 4h5a4 4 0 0 1 4 2 4 4 0 0 1 4-2h5v15h-5a4 4 0 0 0-4 2 4 4 0 0 0-4-2H3z"/></svg>
      <span>Otevřít zákon a návrhy</span>
      <span class="reader-launch-arrow" aria-hidden="true">→</span>
    </a>
    <a class="detail-source" href="{{ View::pspTiskUrl($ct, $obdobi) }}">psp.cz <span aria-hidden="true">↗</span><span class="sr-only"> – tisk {{ $ct }}</span></a>
  </div>
</section>

<details class="bill-metadata">
  <summary>Podrobnosti návrhu</summary>
  <div class="card pad compact">
    <p>{{ $t['uplny_nazev'] ?: $t['nazev'] }}</p>
    <dl class="bill-metadata-list">
      <div><dt>Typ návrhu</dt><dd>{{ $t['typ_navrhu'] }}</dd></div>
      <div><dt>Předloženo</dt><dd>{{ formatDate($t['predlozeno']) }}</dd></div>
      <div><dt>Poslední změna</dt><dd>{{ formatDate($t['posledni']) }}</dd></div>
      <div><dt>Od předložení po poslední krok</dt><dd>{{ $days !== null ? $days . ' dní' : '—' }}</dd></div>
      @if ($laws)<div><dt>Zákony v názvu</dt><dd>{{ implode(', ', array_map(fn ($law) => $law . ' Sb.', $laws)) }}</dd></div>@endif
      @if ($t['sbirka'])<div><dt>Vyhlášeno ve Sbírce</dt><dd>{{ $t['sbirka'] }} Sb.</dd></div>@endif
    </dl>
  </div>
</details>

<div class="detail-dashboard">
@if ($prep !== null)
<details class="card detail-preparation"><summary>Příprava návrhu u vlády</summary>
@include('partials.government-preparation')
</details>
@endif

<?php $showBillText = $billText !== null && ($billText['ai'] !== null || array_diff_key($billText['signals'], ['laws' => true]) !== [] || \Hlidac\Revisions::hasHistory($billRev)); ?>

<?php if ($showBillText): ?>
<section class="card pad compact" aria-label="Rozbor textu návrhu">
  <h2>Rozbor textu návrhu</h2>
  <?php $bai = $billText['ai']; ?>
  <span class="chips">
    <?php if ($bai !== null): ?><span class="chip {{ (int)$billText['ai_score'] >= 6 ? 'chip-cut' : 'chip-surge' }}">AI {{ (int)$billText['ai_score'] }}/10</span><?php endif; ?>
    <?php if (!empty($bai['zpetna_ucinnost']) || !empty($billText['signals']['retro'])): ?><span class="chip chip-cut">Zpětné použití</span><?php endif; ?>
    <?php if (!empty($bai['zkraceni_lhut']) || !empty($billText['signals']['deadline'])): ?><span class="chip chip-cut">Změna lhůt</span><?php endif; ?>
    <?php if (!empty($bai['prospech_uzke_skupiny'])): ?><span class="chip chip-cut">Prospěch úzké skupiny</span><?php endif; ?>
    <?php if (isset($bai['souvisi_s_predmetem']) && $bai['souvisi_s_predmetem'] === false): ?><span class="chip chip-cut">Nesouvisí s předmětem</span><?php endif; ?>
    {!! View::aiRounds($billText) !!}
  </span>
  <?php if (!empty($bai['shrnuti'])): ?><p>{{ $bai['shrnuti'] }}</p><?php endif; ?>
  <details class="analysis-evidence"><summary>Odůvodnění a citace</summary>
  <?php if (!empty($bai['duvod'])): ?><p class="muted">{{ $bai['duvod'] }}</p><?php endif; ?>
  <?php foreach ((array)($bai['upravy'] ?? []) as $note): ?><p class="muted small">Automatická korekce: {{ (string)$note }}</p><?php endforeach; ?>
  <?php
    $quotes = !empty($bai['citace']) ? $bai['citace'] : array_merge($billText['signals']['deadline'] ?? [], $billText['signals']['retro'] ?? []);
  ?>
  <?php foreach (array_slice($quotes, 0, 3) as $q): ?>
    <blockquote class="quote"><a class="quote-document-link" href="{{ route('bill-documents', ['ct' => $ct], false) }}#citace={{ rawurlencode(trim((string)$q)) }}" title="Najít citaci v původním dokumentu">{{ trim((string)$q) }} <span aria-hidden="true">↗</span><span class="sr-only"> – najít v dokumentu</span></a></blockquote>
  <?php endforeach; ?>
  <p class="muted small">Citace jsou doslovně z textu návrhu{{ $billText['docx_url'] ? ' (<a href="' . $billText['docx_url'] . '">Word na psp.cz</a>)' : '' }}.
    {{ $bai !== null ? 'Rozbor připravil jazykový model (AI) a může se mýlit; hodnotí formu a postup, ne politický obsah. Rozhodující je původní text.' : 'Pasáže vybrala automatická pravidla.' }}
    @if (\Hlidac\Revisions::hasHistory($billRev))
    <a href="{{ route('revisions', ['druh' => 'tisk', 'id' => $ct], false) }}">Historie ({{ \Hlidac\Revisions::versionsLabel($billRev['text'] + $billRev['ai']) }})</a> – předchozí znění textu a rozbory.
    @endif
  </p>
  </details>
</section>
<?php endif; ?>

<div class="split-detail">
  <section class="card pad compact" aria-label="Průběh projednávání">
    <h2>Průběh projednávání</h2>
    <?php if ($steps === []): ?><p class="muted">Historie zatím není k dispozici.</p><?php endif; ?>
    <?php $prevDate = null; $olderSteps = max(0, count($steps) - 3); ?>
    <?php if ($olderSteps > 0): ?>
    <details class="earlier-steps"><summary>Předchozí kroky ({{ $olderSteps }})</summary>
    <?php endif; ?>
    <ol class="timeline timeline-compact">
      <?php foreach ($steps as $stepIndex => $k): ?>
        <?php if ($olderSteps > 0 && $stepIndex === $olderSteps): ?></ol></details><ol class="timeline timeline-compact"><?php $prevDate = null; endif; ?>
        <?php $sameDate = $prevDate === $k['datum']; $prevDate = $k['datum']; ?>
        <li>
          <span class="tl-date">{!! $sameDate ? '<span class="sr-only">' . e(formatDate($k['datum'])) . '</span>' : e(formatDate($k['datum'])) !!}</span>
          <span class="tl-dot {{ View::timelineDotClass($k) }}" aria-hidden="true"></span>
          <span class="tl-text"><strong>{{ $k['stav'] }}</strong>{{ $k['akce'] !== '' ? ' – ' . $k['akce'] : '' }}</span>
        </li>
      <?php endforeach; ?>
    </ol>
  </section>

  <section class="card pad compact" aria-label="Pozměňovací návrhy" id="pozmenovaci-navrhy">
    <h2>Pozměňovací návrhy <span class="muted small">{{ count($pn) }}</span></h2>
    <p class="muted small">Hodnocení „AI N/10“ a shrnutí připravuje jazykový model a může se mýlit; rozhodující je text návrhu na psp.cz.</p>
      @if (!empty($t['souhrn_pn']))
      <p class="small"><a href="https://www.psp.cz/sqw/text/tiskt.sqw?o={{ (int)$obdobi }}&amp;ct={{ $ct }}&amp;ct1={{ (int)$t['souhrn_pn'] }}">Souhrn platně podaných pozměňovacích návrhů (tisk {{ $ct }}/{{ (int)$t['souhrn_pn'] }})</a> <span class="muted">rozeslán {{ formatDate((string)$t['souhrn_pn_at'] . ':00', true) }}</span></p>
      @endif
    <?php if ($authors === []): ?>
      <p class="muted">K tisku nebyl podán žádný písemný pozměňovací návrh.</p>
    <?php else: ?>
      <details class="pn-authors"><summary>Předkladatelé ({{ count($authors) }})</summary>
      <ul class="authors" aria-label="Předkladatelé PN">
        <?php foreach ($authors as $a): ?>
          <li><span>{{ $a['osoba'] }}</span> <b>{{ (int)$a['n'] }}</b></li>
        <?php endforeach; ?>
      </ul>
      </details>
    <?php endif; ?>
    <ul class="plain-list">
      <?php foreach (array_slice($pn, 0, 30) as $p): ?>
        <?php $x = $texts[(int)$p['id_dokument']] ?? null; ?>
        <li class="pn-item">
          <span><a href="{{ View::pspSdUrl((int)$p['cislo'], $obdobi) }}">Návrh č. {{ (int)$p['cislo'] }}</a> <span class="muted small">{{ $p['osoba'] }} · {{ formatDate($p['cas'], true) }}</span></span>
          <?php if ($x !== null): ?>
            <span class="chips">
              <?php if (!empty($x['signals']['other_laws'])): ?><span class="chip chip-cut">Přílepek: {{ implode(', ', $x['signals']['other_laws']) }}</span><?php endif; ?>
              <?php if (!empty($x['signals']['retro_deadline']) && !empty($x['signals']['deadline'])): ?><span class="chip chip-cut">Lhůta a zpětné použití</span><?php endif; ?>
              <?php if (!empty($x['signals']['large'])): ?><span class="chip chip-surge">Rozsáhlý</span><?php endif; ?>
              <?php if ($x['ai_score'] !== null): ?><span class="chip {{ (int)$x['ai_score'] >= 6 ? 'chip-cut' : 'chip-surge' }}">AI {{ (int)$x['ai_score'] }}/10</span><?php endif; ?>
            </span>
            <a class="small" href="{{ route('bill-documents', ['ct' => $ct, 'pn' => (int)$p['id_dokument']], false) }}">Otevřít →</a>
            <?php if ($x['ai'] !== null || \Hlidac\Revisions::hasHistory($pnRev[(int)$p['id_dokument']] ?? [])): ?>
            <details class="pn-analysis"><summary>Rozbor a historie</summary>
            {!! View::aiRounds($x) !!}
            <?php $rev = $pnRev[(int)$p['id_dokument']] ?? []; ?>
            @if (\Hlidac\Revisions::hasHistory($rev))
            <a class="small" href="{{ route('revisions', ['druh' => 'pn', 'id' => (int)$p['id_dokument']], false) }}">Historie ({{ \Hlidac\Revisions::versionsLabel($rev['text'] + $rev['ai']) }})</a>
            @endif
            <?php if (!empty($x['ai']['shrnuti'])): ?><span class="small pn-summary">{{ $x['ai']['shrnuti'] }}</span><?php endif; ?>
            <?php foreach (array_slice($x['ai']['citace'] ?? [], 0, 1) as $q): ?><blockquote class="quote small"><a class="quote-document-link" href="{{ route('bill-documents', ['ct' => $ct, 'pn' => (int)$p['id_dokument']], false) }}#citace={{ rawurlencode(trim((string)$q)) }}" title="Najít citaci v pozměňovacím návrhu">{{ trim((string)$q) }} <span aria-hidden="true">↗</span><span class="sr-only"> – najít v dokumentu</span></a></blockquote><?php endforeach; ?>
            </details>
            <?php endif; ?>
          <?php endif; ?>
        </li>
      <?php endforeach; ?>
    </ul>
    <?php if (count($pn) > 30): ?><p class="muted small">a dalších {{ count($pn) - 30 }} na psp.cz</p><?php endif; ?>
  </section>
</div>

<section class="card pad compact" aria-label="Hlasování" id="hlasovani">
<?php if ($votes !== []): ?>
  <div class="voting-heading">
    <h2>Hlasování ve Sněmovně <span class="muted small">{{ count($votes) }}</span></h2>
    <span class="muted small">Od nejnovějšího · rozklikněte pro kluby</span>
  </div>
  <p class="muted small voting-note">Přijetí hlasování neznamená schválení celého zákona. Výsledky jednotlivých PN zatím neumíme spolehlivě přiřadit.</p>
  <div class="voting-list">
    <?php foreach ($votes as $i => $v): ?>
      <?php if ($i === 5): ?>
      <details class="voting-more"><summary>Zobrazit dalších {{ count($votes) - 5 }} hlasování</summary><div class="voting-list">
      <?php endif; ?>
      <details class="voting-item" name="bill-vote">
        <summary class="voting-row">
          <span class="voting-date">{{ formatDate($v['datum'], true) }}</span>
          <span class="voting-subject">
            <strong>{{ $stepByVote[(int)$v['id_hlasovani']] ?? 'Hlasování č. ' . (int)$v['cislo'] }}</strong>
          </span>
          <span class="voting-tally"><b class="voting-for">{{ (int)$v['pro'] }}</b> <span class="muted">pro</span> <span class="voting-divider">/</span> <b>{{ (int)$v['proti'] }}</b> <span class="muted">proti</span></span>
          <span class="chip {{ $v['vysledek'] === 'A' ? 'chip-mp' : 'chip-cut' }}">{{ $v['vysledek'] === 'A' ? 'Přijato' : 'Zamítnuto' }}</span>
          <span class="voting-chevron" aria-hidden="true">⌄</span>
        </summary>
        <div class="voting-body">
          <div class="voting-details-meta">
            <span>{{ (int)$v['schuze'] }}. schůze · hlasování č. {{ (int)$v['cislo'] }}</span>
            <span>Zdrželo se {{ (int)$v['zdrzel'] }} · nehlasovalo {{ (int)$v['nehlasoval'] }} · potřeba {{ (int)$v['kvorum'] }} hlasů pro</span>
            <a href="https://www.psp.cz/sqw/hlasy.sqw?g={{ (int)$v['id_hlasovani'] }}">jmenovitě na psp.cz ↗</a>
          </div>
          @if (!isset($stepByVote[(int)$v['id_hlasovani']]))
          <p class="muted small">Přesný předmět hlasování ověřte v přepisu jednání na PSP.</p>
          @endif
          <?php if ($v['kluby'] !== []): ?>
          <div class="vote-scroll"><table class="table-compact vote-clubs">
            <caption class="sr-only">Hlasy klubů v hlasování č. {{ (int)$v['cislo'] }}</caption>
            <thead><tr><th scope="col">Klub</th><th scope="col">Pro</th><th scope="col">Proti</th><th scope="col">Zdržel se / nehlasoval</th><th scope="col">Nepřítomen</th></tr></thead>
            <tbody>
              <?php foreach ($v['kluby'] as $c): ?>
                <tr><td>{{ $c['klub'] }}</td><td>{{ (int)$c['pro'] }}</td><td>{{ (int)$c['proti'] }}</td><td>{{ (int)$c['zdrzel'] }}</td><td>{{ (int)$c['nepritomen'] }}</td></tr>
              <?php endforeach; ?>
            </tbody>
          </table></div>
          <?php else: ?>
          <p class="muted small">Rozpis podle klubů není dostupný.</p>
          <?php endif; ?>
        </div>
      </details>
    <?php endforeach; ?>
    <?php if (count($votes) > 5): ?></div></details><?php endif; ?>
  </div>
<?php else: ?>
  <h2>Hlasování ve Sněmovně</h2>
  <p class="muted">K tomuto tisku zatím nemáme načtená hlasování. Neznamená to, že se o něm nehlasovalo.</p>
  <a href="{{ View::pspTiskUrl($ct, $obdobi) }}">Ověřit hlasování na psp.cz ↗</a>
<?php endif; ?>
</section>

</div>
<details class="card alerts-details">
  <summary><h2>Upozornění k tisku <span class="muted small">({{ count($alerts) }})</span></h2></summary>
  @include('partials.alerts')
</details>

</div>
@endsection
