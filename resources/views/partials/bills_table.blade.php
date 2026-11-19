<?php
// Tabulka tisků; očekává proměnnou $bills.

use Hlidac\View;

if ($bills === []): ?>
  <p class="empty">Žádné tisky neodpovídají filtru.</p>
<?php else: ?>
<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>Skóre</th><th>Tisk</th><th>Návrh</th><th>Předkladatel</th><th class="num">PN</th><th>Příznaky</th><th>Fáze</th><th>Hlasování</th><th>Poslední změna</th>
      </tr>
    </thead>
    <tbody>
      <?php foreach ($bills as $b): ?>
        <tr>
          <td>{!! View::scoreBadge((int)$b['score']) !!}</td>
          <td class="strong"><a href="{{ route('bill', ['ct' => (int)$b['ct']], false) }}">{{ (int)$b['ct'] }}</a></td>
          <td class="wide"><a class="plain" href="{{ route('bill', ['ct' => (int)$b['ct']], false) }}">{{ $b['nazev'] }}</a></td>
          <td class="muted">{{ $b['navrhovatel'] }}</td>
          <td class="num strong">{{ (int)$b['pn_count'] }}</td>
          <td><span class="chips">{!! View::chips($b['flags']) !!}</span></td>
          <td class="muted">{{ $b['faze'] }}</td>
          <td><a class="nowrap" href="{{ route('bill', ['ct' => (int)$b['ct']], false) }}#hlasovani">Hlasování ({{ (int)($b['votes_count'] ?? 0) }})</a></td>
          <td class="muted nowrap">{{ formatDate($b['posledni']) }}</td>
        </tr>
      <?php endforeach; ?>
    </tbody>
  </table>
</div>
<?php endif;

?>
