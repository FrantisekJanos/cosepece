<?php
// Metodika skóre závažnosti pro čtenáře. Čísla se berou z aktuálních vah (Scorer::weights),
// takže stránka vždy odpovídá tomu, jak se skóre skutečně počítá.

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\Scorer;
use Hlidac\View;

$w = Scorer::weights(config());
$ai = (array)config('ai', []);
$aiOn = !empty($ai['enabled']);
$verifyMin = (int)($ai['verify_min'] ?? 6);

// skupiny signálů: název skupiny, klíč stropu, řádky [body, popis]
$groups = [
    ['Postup projednávání', 'cap_procedure', [
        [$w['mp_bill'], 'Poslanecký návrh: neprošel meziresortním připomínkovým řízením (ministerstva, legislativní rada vlády, odborná veřejnost).'],
        [$w['senate_bill'], 'Senátní návrh: také bez meziresortního připomínkového řízení.'],
        [$w['p90_requested'], 'Navrženo schválit už v 1. čtení podle § 90 odst. 2 jednacího řádu.'],
        [$w['p90_passed'], 'Schváleno už v 1. čtení podle § 90 odst. 2, bez 2. a 3. čtení (místo bodů za pouhý návrh).'],
        [$w['deadline_cut'], 'Zkrácená lhůta při projednávání (např. lhůta pro výbory nebo pro pozměňovací návrhy).'],
        [$w['short_gap'], 'Mezi pozměňovacími návrhy a zahájením 3. čtení méně než ' . (int)$w['short_gap_days'] . ' dní.'],
        [$w['late_pn'], 'Aspoň ' . (int)$w['late_pn_min'] . ' pozměňovací návrhy podané v den konce podrobné rozpravy.'],
        [$w['surge'], 'Nával pozměňovacích návrhů: najednou jich přibylo hodně.'],
    ]],
    ['Obsah textu návrhu a pozměňovacích návrhů', 'cap_content', [
        [$w['pn_other_law'], 'Přílepek: pozměňovací návrh mění jiný zákon, než který se projednává.'],
        [$w['pn_large'], 'Rozsáhlá nová úprava zavedená pozměňovacím návrhem.'],
        [$w['retro_deadline'], 'Text mění lhůtu pro vymáhání nebo kontrolu a výslovně ji použije i na případy z doby před účinností.'],
        [$w['pn_many_flagged'], 'Aspoň 3 problematické pozměňovací návrhy.'],
        ['0–' . (10 * (int)$w['ai_multiplier']), 'Rozbor textu návrhu zákona umělou inteligencí: závažnost 0–10 × ' . (int)$w['ai_multiplier'] . ' (počítá se od závažnosti 4).'],
        ['0–' . (10 * (int)$w['ai_multiplier']), 'Rozbor nejzávažnějšího pozměňovacího návrhu umělou inteligencí: závažnost 0–10 × ' . (int)$w['ai_multiplier'] . ' (počítá se od závažnosti 4).'],
    ]],
    ['Kontext', 'cap_context', [
        [$w['topic'], 'Citlivá oblast: ' . implode(', ', array_keys((array)$w['topics'])) . '.'],
        [$w['omnibus'], 'Souhrnná novela měnící více zákonů najednou.'],
        [$w['watched'], 'Zákon, který web výslovně sleduje.'],
    ]],
];

View::header('Jak hodnotíme', 'metodika', 'Jak se počítá skóre závažnosti legislativního postupu: signály, body, stropy a úrovně.');
?>
<section class="hero">
  <div>
    <h1>Jak hodnotíme</h1>
    <p class="lead">Skóre 0–100 měří, jak zákon prochází Sněmovnou: zda obchází připomínkové řízení, zda se zrychluje projednávání a zda se do něj vkládají nesouvisející nebo zpětné změny. Nehodnotí politický obsah zákona ani to, kdo ho předložil.</p>
  </div>
</section>

<section class="card pad" aria-label="Úrovně závažnosti">
  <h2>Úrovně</h2>
  <ul class="reasons">
    <li><?= View::scoreBadge((int)$w['level_major']) ?> <span>od <?= (int)$w['level_major'] ?> bodů: hlavní nálezy na úvodní stránce a příspěvky na sociálních sítích.</span></li>
    <li><?= View::scoreBadge((int)$w['level_watch']) ?> <span><?= (int)$w['level_watch'] ?>–<?= (int)$w['level_major'] - 1 ?> bodů: v upozorněních a v RSS.</span></li>
    <li><?= View::scoreBadge(0) ?> <span>pod <?= (int)$w['level_watch'] ?> bodů: běžná agenda, na webu skrytá za odkazem „Včetně běžné agendy“.</span></li>
  </ul>
</section>

<?php foreach ($groups as [$title, $capKey, $rows]): ?>
<section class="card" aria-label="<?= e($title) ?>">
  <div class="card-head"><h2><?= e($title) ?></h2><span class="muted small">nejvýš <?= (int)$w[$capKey] ?> bodů za skupinu</span></div>
  <div class="table-wrap">
    <table class="table-compact">
      <thead><tr><th class="num">Body</th><th>Signál</th></tr></thead>
      <tbody>
        <?php foreach ($rows as [$points, $text]): ?>
          <tr><td class="num"><b><?= e((string)$points) ?></b></td><td><?= e($text) ?></td></tr>
        <?php endforeach; ?>
      </tbody>
    </table>
  </div>
</section>
<?php endforeach; ?>

<section class="card pad" aria-label="Jak se body sčítají">
  <h2>Jak se body sčítají</h2>
  <p>Body ze všech signálů se sečtou. Každá skupina má strop, aby se příbuzné signály nesčítaly donekonečna; signál nad stropem zůstane v detailu tisku vidět s poznámkou „strop skupiny“. Celkové skóre je nejvýš 100.</p>
  <p>Upozornění na konkrétní událost (návrh na schválení v 1. čtení, zkrácená lhůta, schválení Sněmovnou, nával pozměňovacích návrhů) dostane skóre tisku a malý bonus podle typu události.</p>
  <p>U každého tisku je v detailu oddíl „Proč skóre“ se všemi body a důvody.</p>
</section>

<section class="card pad" aria-label="Rozbor textu">
  <h2>Rozbor textu</h2>
  <p>Web stahuje texty návrhů zákonů a pozměňovacích návrhů ve Wordu. Pravidla v nich hledají odkazy na jiné zákony (přílepky), zpětné použití a změny lhůt pro vymáhání.</p>
  <?php if ($aiOn): ?>
  <p>Texty navíc posuzuje jazykový model Claude. Hodnotí jen formu a postup: přílepek, zpětnou účinnost, zkrácení lhůt, prospěch úzké skupiny a zásadní novou úpravu bez připomínkového řízení. Každé zjištění musí doložit doslovnou citací; citace, která v textu není, se zahodí. Zpětnost ve prospěch veřejnosti (např. nové osvobození od poplatku) má nízkou závažnost. Nálezy se závažností <?= $verifyMin ?> a víc znovu posoudí silnější model a u nálezu je pak poznámka „ověřeno druhým modelem“.</p>
  <?php endif; ?>
</section>

<section class="card pad" aria-label="Omezení">
  <h2>Co skóre nevidí</h2>
  <ul>
    <li>Politický obsah a dopady zákona. Velká, ale řádně připravená vládní změna může mít nízké skóre.</li>
    <li>Jak se hlasovalo a v kolik hodin.</li>
    <li>Kroky mimo Sněmovnu, např. změny jednacího řádu vlády nebo výběrová řízení.</li>
    <li>Které pozměňovací návrhy byly platně podány ve 2. čtení; Sněmovna to v otevřených datech neeviduje.</li>
  </ul>
  <p class="muted small">Váhy jsou nastavitelné a mohou se po kalibraci měnit; tato stránka vždy ukazuje aktuální hodnoty.<?php if ((string)config('issues_url', '') !== ''): ?> <a href="<?= e((string)config('issues_url', '')) ?>">Nesouhlasíte s hodnocením? Napište nám.</a><?php endif; ?></p>
</section>
<?php
View::footer();
