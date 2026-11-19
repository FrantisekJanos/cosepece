@extends('layouts.app')
@section('content')
<?php
use Hlidac\View;
?>
<p class="crumbs"><a href="{{ route('bills', [], false) }}">Tisky</a> › Tisk {{ $ct }}</p>
<section class="detail-head">
  <span class="chips">{!! View::scoreBadge((int)$t['score']) !!}{!! View::chips($t['flags']) !!}</span>
  <h1>{{ $t['uplny_nazev'] ?: $t['nazev'] }}</h1>
  <p class="muted">Tisk {{ $ct }} · {{ $t['typ_navrhu'] }} návrh · předkladatel {{ $t['navrhovatel'] }} · předloženo {{ formatDate($t['predlozeno']) }}{!! $t['sbirka'] ? ' · vyhlášeno jako ' . e($t['sbirka']) . ' Sb.' : '' !!}</p>
  <div class="actions">
    <a class="btn" href="{{ View::pspTiskUrl($ct, $obdobi) }}">Otevřít na psp.cz</a>
  </div>
</section>

<section class="kpis" aria-label="Klíčová čísla">
  <div class="card kpi">
    <div class="kpi-label">Pozměňovací návrhy</div>
    <div class="kpi-value">{{ count($pn) }}</div>
    <div class="kpi-note">od {{ count($authors) }} {{ count($authors) === 1 ? 'poslance' : 'poslanců' }}</div>
  </div>
  <div class="card kpi">
    <div class="kpi-label">Fáze</div>
    <div class="kpi-value kpi-small">{{ $t['faze'] }}</div>
    <div class="kpi-note">poslední změna {{ formatDate($t['posledni']) }}</div>
  </div>
  <div class="card kpi">
    <div class="kpi-label">Projednávání trvá</div>
    <div class="kpi-value">{{ $days !== null ? $days . ' dní' : '—' }}</div>
    <div class="kpi-note">od předložení po poslední krok</div>
  </div>
  <div class="card kpi">
    <div class="kpi-label">Zákony v názvu</div>
    <div class="kpi-value">{{ count($laws) ?: '—' }}</div>
    <div class="kpi-note">{{ $laws ? e(implode(', ', array_map(fn($l) => $l . ' Sb.', array_slice($laws, 0, 3)))) : 'nový zákon nebo souhrnná novela' }}</div>
  </div>
</section>

<section class="card pad" aria-label="Proč toto skóre">
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

<?php if ($billText !== null && ($billText['ai'] !== null || array_diff_key($billText['signals'], ['laws' => true]) !== [] || \Hlidac\Revisions::hasHistory($billRev))): ?>
<section class="card pad" aria-label="Rozbor textu návrhu">
  <h2>Rozbor textu návrhu</h2>
  <?php $bai = $billText['ai']; ?>
  <span class="chips">
    <?php if ($bai !== null): ?><span class="chip {{ (int)$billText['ai_score'] >= 6 ? 'chip-cut' : 'chip-surge' }}">AI {{ (int)$billText['ai_score'] }}/10</span><?php endif; ?>
    <?php if (!empty($bai['zpetna_ucinnost']) || !empty($billText['signals']['retro'])): ?><span class="chip chip-cut">Zpětné použití</span><?php endif; ?>
    <?php if (!empty($bai['zkraceni_lhut']) || !empty($billText['signals']['deadline'])): ?><span class="chip chip-cut">Změna lhůt</span><?php endif; ?>
    <?php if (!empty($bai['prospech_uzke_skupiny'])): ?><span class="chip chip-cut">Prospěch úzké skupiny</span><?php endif; ?>
    <?php if (isset($bai['souvisi_s_predmetem']) && $bai['souvisi_s_predmetem'] === false): ?><span class="chip chip-cut">Nesouvisí s předmětem</span><?php endif; ?>
  </span>
  {!! View::aiRounds($billText) !!}
  <?php if (!empty($bai['shrnuti'])): ?><p>{{ $bai['shrnuti'] }}</p><?php endif; ?>
  <?php if (!empty($bai['duvod'])): ?><p class="muted">{{ $bai['duvod'] }}</p><?php endif; ?>
  <?php foreach ((array)($bai['upravy'] ?? []) as $note): ?><p class="muted small">Automatická korekce: {{ (string)$note }}</p><?php endforeach; ?>
  <?php
    // doslovné citace: ověřené citace AI, jinak pasáže nalezené pravidly
    $quotes = !empty($bai['citace']) ? $bai['citace'] : array_merge($billText['signals']['deadline'] ?? [], $billText['signals']['retro'] ?? []);
  ?>
  <?php foreach (array_slice($quotes, 0, 3) as $q): ?>
    <blockquote class="quote">{{ trim((string)$q) }}</blockquote>
  <?php endforeach; ?>
  <p class="muted small">Citace jsou doslovně z textu návrhu{{ $billText['docx_url'] ? ' (<a href="' . $billText['docx_url'] . '">Word na psp.cz</a>)' : '' }}.
    {{ $bai !== null ? 'Rozbor připravil jazykový model; hodnotí formu a postup, ne politický obsah.' : 'Pasáže vybrala automatická pravidla.' }}</p>
  @if (\Hlidac\Revisions::hasHistory($billRev))
  <p class="small"><a href="{{ route('revisions', ['druh' => 'tisk', 'id' => $ct], false) }}">Historie ({{ \Hlidac\Revisions::versionsLabel($billRev['text'] + $billRev['ai']) }})</a> <span class="muted">– předchozí znění textu a rozbory</span></p>
  @endif

</section>
<?php endif; ?>

<div class="split">
  <section class="card pad main-col" aria-label="Průběh projednávání">
    <h2>Průběh projednávání</h2>
    <?php if ($steps === []): ?><p class="muted">Historie zatím není k dispozici.</p><?php endif; ?>
    <ol class="timeline">
      <?php foreach ($steps as $k): ?>
        <li>
          <span class="tl-date">{{ formatDate($k['datum']) }}</span>
          <span class="tl-dot {{ View::timelineDotClass($k) }}" aria-hidden="true"></span>
          <span class="tl-text"><strong>{{ $k['stav'] }}</strong>{{ $k['akce'] !== '' ? ' – ' . $k['akce'] : '' }}</span>
        </li>
      <?php endforeach; ?>
    </ol>
  </section>

  <aside class="side-col">
    <section class="card pad">
      <h2>Předkladatelé PN</h2>
      <?php if ($authors === []): ?><p class="muted">K tisku nebyl podán žádný písemný pozměňovací návrh.</p><?php endif; ?>
      <ul class="plain-list">
        <?php foreach ($authors as $a): ?>
          <li><span>{{ $a['osoba'] }}</span><span class="strong">{{ (int)$a['n'] }}</span></li>
        <?php endforeach; ?>
      </ul>
    </section>
    <section class="card pad">
      <h2>Pozměňovací návrhy</h2>
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
              {!! View::aiRounds($x) !!}
              <?php $rev = $pnRev[(int)$p['id_dokument']] ?? []; ?>
              @if (\Hlidac\Revisions::hasHistory($rev))
              <a class="small" href="{{ route('revisions', ['druh' => 'pn', 'id' => (int)$p['id_dokument']], false) }}">Historie ({{ \Hlidac\Revisions::versionsLabel($rev['text'] + $rev['ai']) }})</a>
              @endif
              <?php if (!empty($x['ai']['shrnuti'])): ?><span class="small pn-summary">{{ $x['ai']['shrnuti'] }}</span><?php endif; ?>
              <?php foreach (array_slice($x['ai']['citace'] ?? [], 0, 1) as $q): ?><blockquote class="quote small">{{ trim((string)$q) }}</blockquote><?php endforeach; ?>
            <?php endif; ?>
          </li>
        <?php endforeach; ?>
      </ul>
      <?php if (count($pn) > 30): ?><p class="muted small">a dalších {{ count($pn) - 30 }} na psp.cz</p><?php endif; ?>
    </section>
  </aside>
</div>

<?php if ($votes !== []): ?>
<section class="card pad" aria-label="Hlasování">
  <h2>Hlasování ve Sněmovně</h2>
  <p class="muted small">Všechna hlasování k tomuto tisku, včetně hlasování o jednotlivých pozměňovacích návrzích. Otevřená data neříkají, o kterém pozměňovacím návrhu se hlasovalo; jmenovité výsledky a podrobnosti jsou na psp.cz. Hlasování do skóre nevstupují.</p>
  <ul class="votes">
    <?php foreach ($votes as $i => $v): ?>
      <?php if ($i === 10): ?></ul><details class="votes-more"><summary>Další hlasování ({{ count($votes) - 10 }})</summary><ul class="votes"><?php endif; ?>
      <li class="vote">
        <div class="vote-head">
          <span class="chip {{ $v['vysledek'] === 'A' ? 'chip-mp' : 'chip-cut' }}">{{ $v['vysledek'] === 'A' ? 'Přijato' : 'Zamítnuto' }}</span>
          <strong>{{ $stepByVote[(int)$v['id_hlasovani']] ?? 'Hlasování č. ' . (int)$v['cislo'] }}</strong>
          <span class="muted small">{{ formatDate($v['datum'], true) }} · {{ (int)$v['schuze'] }}. schůze · pro {{ (int)$v['pro'] }}, proti {{ (int)$v['proti'] }}, zdrželo se {{ (int)$v['zdrzel'] }} (potřeba {{ (int)$v['kvorum'] }})</span>
          <a class="small" href="https://www.psp.cz/sqw/hlasy.sqw?g={{ (int)$v['id_hlasovani'] }}">jmenovitě na psp.cz</a>
        </div>
        <?php if ($v['kluby'] !== []): ?>
          <div class="vote-scroll"><table class="table-compact vote-clubs">
            <thead><tr><th>Klub</th><th>Pro</th><th>Proti</th><th>Zdržel se / nehlasoval</th><th>Nepřítomen</th></tr></thead>
            <tbody>
              <?php foreach ($v['kluby'] as $c): ?>
                <tr><td>{{ $c['klub'] }}</td><td>{{ (int)$c['pro'] }}</td><td>{{ (int)$c['proti'] }}</td><td>{{ (int)$c['zdrzel'] }}</td><td>{{ (int)$c['nepritomen'] }}</td></tr>
              <?php endforeach; ?>
            </tbody>
          </table></div>
        <?php endif; ?>
      </li>
    <?php endforeach; ?>
  </ul>
  <?php if (count($votes) > 10): ?></details><?php endif; ?>
</section>
<?php endif; ?>

<section class="card" aria-label="Upozornění k tisku">
  <div class="card-head"><h2>Upozornění k tisku</h2></div>
  @include('partials.alerts')
</section>

@endsection
