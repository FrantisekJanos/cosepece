@extends('layouts.app')
@section('content')
<section class="hero">
  <div>
    <h1>Zákony</h1>
    <p class="lead"><?= count($all) ?> zákonů, které mění návrhy ve Sněmovně nebo připravované u vlády. U každého je přehled všech změn a vlastní RSS.</p>
  </div>
  @include('partials.search', ['searchId' => 'search-laws'])
</section>
<?php if ($law !== ''): ?><section class="card pad"><p>„<?= e($law) ?>“ není číslo zákona ve tvaru <code>159/2006</code>.</p></section><?php endif; ?>
<section class="card">
  <div class="table-wrap">
    <table>
      <thead><tr><th>Zákon</th><th>Název</th><th class="num">Tisky</th><th class="num">U vlády</th></tr></thead>
      <tbody>
        <?php foreach ($all as $number => $info): ?>
          <tr>
            <td class="strong nowrap"><a href="<?= e(\Hlidac\Laws::url((string)$number)) ?>"><?= e((string)$number) ?> Sb.</a><?= in_array((string)$number, $watched, true) ? ' <span class="chip chip-mp">sledovaný</span>' : '' ?></td>
            <td class="wide"><a class="plain" href="<?= e(\Hlidac\Laws::url((string)$number)) ?>"><?= e($info['name'] ?? '—') ?></a></td>
            <td class="num"><?= $info['bills'] ?: '—' ?></td>
            <td class="num"><?= $info['upcoming'] ?: '—' ?></td>
          </tr>
        <?php endforeach; ?>
      </tbody>
    </table>
  </div>
</section>
@endsection
