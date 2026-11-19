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
        [$w['mp_bill'], 'Poslanecký návrh, který postupuje projednáváním (2. čtení a dál nebo schválení v 1. čtení): neprošel meziresortním připomínkovým řízením (ministerstva, legislativní rada vlády, odborná veřejnost).'],
        [$w['mp_bill_early'], 'Poslanecký návrh, který zatím nepostoupil do 2. čtení (většina takových návrhů nikdy neprojde).'],
        [$w['senate_bill'], 'Senátní návrh: také bez meziresortního připomínkového řízení.'],
        [$w['region_bill'], 'Návrh krajského zastupitelstva nebo hlavního města Prahy: také bez meziresortního připomínkového řízení.'],
        [$w['p90_requested'], 'Navrženo schválit už v 1. čtení podle § 90 odst. 2 jednacího řádu (jen u tisku, který se ještě projednává).'],
        [$w['p90_passed'], 'Schváleno už v 1. čtení podle § 90 odst. 2, bez 2. a 3. čtení (místo bodů za pouhý návrh).'],
        [$w['deadline_cut'], 'Zkrácená lhůta při projednávání: lhůta pro výbory kratší než 60 dní, nebo Sněmovna lhůtu změnila během projednávání.'],
        [$w['short_gap'], 'Mezi pozměňovacími návrhy a zahájením 3. čtení méně než ' . (int)$w['short_gap_days'] . ' dní.'],
        [$w['late_pn'], 'V den konce podrobné rozpravy podáno aspoň ' . (int)$w['late_pn_min'] . ' pozměňovacích návrhů a zároveň aspoň ' . (int)round(100 * (float)$w['late_pn_share']) . ' % všech pozměňovacích návrhů k tisku.'],
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
        [$w['topic'], 'Citlivá oblast podle názvu tisku: ' . implode(', ', array_keys((array)$w['topics'])) . '. Jde o redakční výběr oblastí, kde mají procedurální zkratky větší dopad; proto jen malý příplatek, ne hodnocení obsahu.'],
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

<section class="card pad" aria-label="Jak má zákon běžně projít">
  <h2>Jak má zákon běžně projít</h2>
  <p>Skóre měří odchylky od běžného postupu, který stanoví Ústava, zákon č. 90/1995 Sb., o jednacím řádu Poslanecké sněmovny, a u vládních návrhů Legislativní pravidla vlády.</p>
  <ol class="steps">
    <li><b>Příprava.</b> Vládní návrh nejdřív připomínkují ministerstva, úřady, kraje, odbory a další, obvykle 20 pracovních dnů, a ministerstvo zpracuje rozbor dopadů. Návrh poslanců nebo Senátu tímto kolem neprochází. <span class="muted small">Legislativní pravidla vlády čl. 2 a 8 · Hlídač: poslanecký nebo senátní návrh</span></li>
    <li><b>Podání a názor vlády.</b> Návrh může podat poslanec, skupina poslanců, Senát, vláda nebo kraj. Když návrh nepodala vláda, má 30 dní na své stanovisko. <span class="muted small">Ústava čl. 41 a 44, § 86 a 87</span></li>
    <li><b>Čas na přečtení.</b> Poslanci musí mít návrh aspoň 10 dní před schůzí, na které proběhne první čtení. <span class="muted small">§ 89</span></li>
    <li><b>První čtení.</b> Sněmovna návrh zamítne, vrátí k přepracování, nebo pošle do výborů. Schválit ho hned v prvním čtení jde jen výjimečně, bez úprav a jen když to nezastaví dva poslanecké kluby nebo 50 poslanců. <span class="muted small">§ 90 · Hlídač: návrh na schválení v 1. čtení, schválení v 1. čtení</span></li>
    <li><b>Výbory.</b> Výbory mají na návrh 60 dní. Sněmovna jim tuto dobu může zkrátit, nejvýš o 30 dní. <span class="muted small">§ 91 · Hlídač: zkrácená lhůta</span></li>
    <li><b>Druhé čtení.</b> V podrobné rozpravě poslanci podávají pozměňovací návrhy, a to až do jejího konce. <span class="muted small">§ 93 a 94 · Hlídač: návrhy na poslední chvíli, přílepky, rozsáhlé změny</span></li>
    <li><b>Pauza před hlasováním.</b> Od rozeslání pozměňovacích návrhů má uplynout aspoň 14 dní, aby je všichni stihli prostudovat. Sněmovna může pauzu zkrátit nejvýš na 7 dní. <span class="muted small">§ 95 odst. 1 · Hlídač: méně než <?= (int)$w['short_gap_days'] ?> dní, zkrácená lhůta</span></li>
    <li><b>Třetí čtení.</b> Už jen opravy chyb a hlasování: o zamítnutí, o pozměňovacích návrzích a o celém zákoně. Obvykle ve středu nebo v pátek, nikdy po 21. hodině. <span class="muted small">§ 95 a 95a</span></li>
    <li><b>Senát.</b> Má 30 dní. Když zákon zamítne nebo vrátí s úpravami, Sněmovna o něm hlasuje znovu. <span class="muted small">Ústava čl. 46 a 47, § 97</span></li>
    <li><b>Prezident a Sbírka.</b> Prezident může zákon do 15 dní vrátit, Sněmovna ho pak přehlasuje 101 hlasy. Zákon platí, až když vyjde ve Sbírce zákonů. <span class="muted small">Ústava čl. 50 a 52, § 98</span></li>
  </ol>
  <p class="muted small">Výjimka: ve stavu legislativní nouze projedná Sněmovna vládní návrh bez prvního čtení a třetí čtení může následovat hned po druhém (§ 99). Podle znění jednacího řádu po novele č. 157/2026 Sb.</p>
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
  <p>Texty navíc posuzuje jazykový model Claude. Hodnotí jen formu a postup: přílepek, zpětnou účinnost, zkrácení lhůt, prospěch úzké skupiny a zásadní novou úpravu bez připomínkového řízení. Každé zjištění musí doložit doslovnou citací; citace, která v textu není, se zahodí. Zpětnost ve prospěch veřejnosti (např. nové osvobození od poplatku) má nízkou závažnost. Nálezy se závažností <?= $verifyMin ?> a víc znovu posoudí silnější model a u nálezu je pak poznámka „ověřeno druhým modelem“. U pozměňovacích návrhů se takový nález do skóre počítá naplno až po tomto ověření, do té doby nejvýš jako <?= $verifyMin - 1 ?>/10. Přílepek se posuzuje proti zákonům, které mění název i text samotného návrhu.</p>
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
