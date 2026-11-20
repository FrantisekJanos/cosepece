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
        [$w['mp_bill'], 'Poslanecký návrh, který postupuje projednáváním (2. čtení a dál nebo schválení v 1. čtení): text nepřipravovalo ministerstvo v meziresortním připomínkovém řízení (ministerstva, Legislativní rada vlády, odborná veřejnost).'],
        [$w['mp_bill_early'], 'Poslanecký návrh, který zatím nepostoupil do 2. čtení (většina takových návrhů nikdy neprojde).'],
        [$w['senate_bill'], 'Senátní návrh: text také nepřipravovalo ministerstvo v připomínkovém řízení.'],
        [$w['region_bill'], 'Návrh krajského zastupitelstva nebo hlavního města Prahy: text také nepřipravovalo ministerstvo v připomínkovém řízení.'],
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
        [$w['pn_many_flagged'], 'Aspoň ' . (int)$w['pn_many_min'] . ' problematické pozměňovací návrhy a zároveň aspoň ' . (int)round(100 * (float)$w['pn_many_share']) . ' % všech pozměňovacích návrhů k tisku (od ' . (int)$w['pn_many_abs'] . ' problematických stačí samotný počet). Problematický je návrh, který splní pravidlo (přílepek, rozsáhlá úprava), nebo ho umělá inteligence ohodnotí aspoň ' . (int)$w['pn_flag_ai_min'] . '/10.'],
        ['0–' . (10 * (int)$w['ai_multiplier']), 'Rozbor textu návrhu zákona umělou inteligencí: závažnost 0–10 × ' . (int)$w['ai_multiplier'] . ' (počítá se od závažnosti 4).'],
        ['0–' . (10 * (int)$w['ai_multiplier']), 'Rozbor nejzávažnějšího pozměňovacího návrhu umělou inteligencí: závažnost 0–10 × ' . (int)$w['ai_multiplier'] . ' (počítá se od závažnosti 4). Čím víc pozměňovacích návrhů, tím větší šance, že jeden vyjde závažně náhodou: plné body jen tehdy, když podobně závažné (aspoň ' . (int)$w['ai_pn_full_min'] . '/10, nebo jako ten nejhorší) jsou aspoň ' . (int)$w['ai_pn_full_count'] . ' návrhy, nebo aspoň ' . (int)round(100 * (float)$w['ai_pn_full_share']) . ' % všech; ojedinělý nález dostane ' . (int)round(100 * (float)$w['ai_pn_single_factor']) . ' % bodů.'],
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
  <p>Texty navíc posuzuje jazykový model Claude. Hodnotí jen formu a postup: přílepek, zpětnou účinnost, zkrácení lhůt, prospěch úzké skupiny a zásadní novou úpravu bez připomínkového řízení. Každé zjištění musí doložit doslovnou citací; citace, která v textu není, se zahodí. Zpětnost ve prospěch veřejnosti (např. nové osvobození od poplatku) má nízkou závažnost. Nálezy se závažností <?= $verifyMin ?> a víc znovu posoudí silnější model a u nálezu je pak poznámka „ověřeno druhým modelem“. U pozměňovacích návrhů se takový nález do skóre počítá naplno až po tomto ověření, do té doby nejvýš jako <?= $verifyMin - 1 ?>/10. Přílepek se posuzuje proti zákonům, které mění název i text samotného návrhu. Tisk s nálezem od <?= (int)$w['finding_ai_min'] ?>/10, který ověřil silnější model a doložil citací, je v hlavních nálezech na úvodní stránce i tehdy, když celkové skóre nedosahuje <?= (int)$w['finding_min'] ?> bodů (jinak jsou tam tisky od <?= (int)$w['finding_min'] ?> bodů).</p>
  <?php endif; ?>
  <p>Když psp.cz text návrhu nebo pozměňovacího návrhu vymění, uloží se nové znění jako další verze a rozbor se udělá znovu. Předchozí znění i dřívější rozbory zůstávají uložené: v detailu tisku vede odkaz „Historie“ na seznam verzí, rozdíl mezi nimi a změny závažnosti rozboru. Do skóre se počítá jen poslední verze. Texty a rozbory z minulého volebního období se uchovávají v archivu, ale ke stejně očíslovaným tiskům nového období se nepoužijí.</p>
</section>

<section class="card pad" aria-label="Příprava u vlády">
  <h2>Příprava u vlády (připomínkové řízení)</h2>
  <p>U tisku ukazujeme, jak návrh prošel přípravou u vlády. Údaje bereme z otevřených dat Úřadu vlády (portál ODok/VeKLEP), aktualizují se denně.</p>
  <ul>
    <li><b>Vládní návrh:</b> meziresortní připomínkové řízení (od–do, počet připomínkových míst), kdy návrh schválila vláda, důvod předložení (např. „mimo plán legislativních prací“) a udělené výjimky. Obvyklá délka připomínek jsou 4 týdny; kratší řízení označujeme. Když připomínkové řízení v datech nenajdeme, napíšeme „nedohledáno“ – mohlo proběhnout pod jiným názvem nebo u dřívější verze návrhu.</li>
    <li><b>Poslanecký, senátní a krajský návrh:</b> text nepřipravovalo ministerstvo. Vláda k němu ale zaujímá stanovisko a k tomu si někdy vyžádá připomínky (v 10. období u většiny takových návrhů, obvykle na 1–2 týdny). Ty se týkají stanoviska vlády, ne úprav návrhu.</li>
  </ul>
  <p><b>Upozornění na připravované zákony</b> (bez čísla tisku, skóre se neodvozuje od tisku): připomínkové řízení kratší než 10 dní má <?= Hlidac\AlertBuilder::UPCOMING_KINDS['prep_velmi'] ?> bodů (Závažné, jde i na sociální sítě), kratší než 3 týdny <?= Hlidac\AlertBuilder::UPCOMING_KINDS['prep_kratke'] ?> bodů (Pozor), předložení mimo plán legislativních prací vlády <?= Hlidac\AlertBuilder::UPCOMING_KINDS['prep_plan'] ?> bodů (Pozor); schválení návrhu vládou je běžná agenda. U <a href="pripravovane.php?druh=narizeni">připravovaných nařízení vlády</a> (do Sněmovny nejdou) upozorňujeme jen na připomínkové řízení kratší než 10 dní, <?= Hlidac\AlertBuilder::UPCOMING_KINDS['prep_narizeni'] ?> bodů (Pozor); obvyklé jsou u nich 3 týdny.</p>
  <p>Ze stejných dat je i přehled <a href="pripravovane.php">připravovaných zákonů</a>: vládní návrhy z tohoto volebního období, které ještě nemají číslo sněmovního tisku, rozdělené podle fáze (připomínkové řízení, vypořádání, vláda, schváleno). Kratší připomínky a nedohledané připomínkové řízení označujeme stejně jako u tisků.</p>
  <p class="muted small">Tyto údaje zatím do skóre nevstupují; body za poslanecký návrh zůstávají, jen jejich popis uvádí, zda si vláda připomínky vyžádala. Po několika týdnech dat zvážíme, zda a jak je započítat. Stanovisko Legislativní rady vlády a jednotlivé připomínky jsou u materiálu ve VeKLEP.</p>
</section>

<?php
  // spolehlivost rozboru AI (#7 bod 7) z uložených rozborů; počty se mění, jak přibývají ověřené texty
  $rel = (new Hlidac\Repo(db()))->aiReliability();
  $rc = Hlidac\AiReviewer::repeatConfig(config());
  $pct = fn(int $a, int $n): string => $n > 0 ? (string)round(100 * $a / $n) . ' %' : '–';
?>
<section class="card pad" id="spolehlivost-ai" aria-label="Spolehlivost rozboru AI">
  <h2>Jak ověřujeme rozbor AI</h2>
  <ul>
    <li><b>Opakované běhy u hraničních hodnocení.</b> Text, kterému rychlý model dá závažnost <?= $rc['min'] ?>–<?= $rc['max'] ?> (tam se rozhoduje, zda dostane body), posoudí celkem <?= $rc['runs'] ?>× a platí prostřední hodnota (medián). Jedno nahodilé hodnocení tak skóre neurčí.<?php if ($rel['runs']['n'] > 0): ?>
      Zatím u <?= $rel['runs']['n'] ?> textů: běhy se lišily nejvýš o 1 bod v <?= $pct($rel['runs']['stable'], $rel['runs']['n']) ?> případů (největší rozdíl <?= $rel['runs']['max_spread'] ?>), medián změnil, zda text dostane body, u <?= $rel['runs']['changed_points'] ?>.<?php endif; ?></li>
    <li><b>Silnější model ověřuje závažné nálezy</b> (od <?= $verifyMin ?>/10) a jen ty jdou do hlavních nálezů a na sítě.<?php if ($rel['high']['n'] > 0): ?>
      U <?= $rel['high']['n'] ?> ověřených nálezů se shodl s rychlým modelem (rozdíl nejvýš 1 bod) v <?= $pct($rel['high']['agree'], $rel['high']['n']) ?> případů, závažnost výrazně snížil u <?= $rel['high']['lower'] ?> a zvýšil u <?= $rel['high']['higher'] ?>.<?php endif; ?></li>
    <li><b>Namátková kontrola nízkých hodnocení.</b> Každý <?= $rc['sample_every'] ?>. text, kterému rychlý model dal méně než <?= $rc['sample_below'] ?>/10, posoudí i silnější model, aby se ukázalo, kolik problémů rychlý model přehlíží. Když silnější model najde závažný problém, jeho rozbor platí.<?php if ($rel['sample']['n'] > 0): ?>
      Ze <?= $rel['sample']['n'] ?> zkontrolovaných textů zvýšil závažnost tak, že text dostal body, u <?= $rel['sample']['to_points'] ?> (z toho na závažný nález <?= $rel['sample']['to_finding'] ?>).<?php else: ?> Výsledky přibudou po prvním ověřovacím běhu.<?php endif; ?></li>
  </ul>
  <p class="muted small">Model nedostává jméno předkladatele ani typ návrhu. Citace z textu ověřujeme strojově, nález bez ověřené citace má nejvýš 2/10. Srovnání s hodnocením lidí (ruční kalibrace) zatím neproběhlo, čísla výše porovnávají dva modely mezi sebou.</p>
</section>

<section class="card pad" id="odpovednost" aria-label="Upozornění a omezení odpovědnosti">
  <h2>Upozornění a omezení odpovědnosti</h2>
  <ul>
    <li><b>Automatické zpracování.</b> Web vzniká automaticky z otevřených dat Poslanecké sněmovny a Úřadu vlády. Data mohou být neúplná, opožděná nebo chybná už u zdroje, nebo se mohou zkreslit při zpracování.</li>
    <li><b>Umělá inteligence se může mýlit.</b> Rozbory textů návrhů a pozměňovacích návrhů, titulky, shrnutí a hodnocení závažnosti připravuje jazykový model. Citace ověřujeme proti textu a nejzávažnější nálezy posuzuje ještě druhý, silnější model, přesto AI může text špatně pochopit, přehlédnout souvislost nebo závažnost nadsadit či podcenit. Výstupy AI jsou na webu vždy označené.</li>
    <li><b>Skóre není obvinění.</b> Skóre je výsledek veřejné metodiky na této stránce. Hodnotí postup a formu projednávání, ne politický obsah zákona; netvrdí, že někdo jednal protiprávně nebo v něčím zájmu.</li>
    <li><b>Není to právní rada ani oficiální informace.</b> Závazné je znění na <a href="https://www.psp.cz">psp.cz</a> a ve Sbírce zákonů (<a href="https://www.e-sbirka.cz">e-Sbírka</a>). Před citací nebo dalším použitím údaje ověřte v původním zdroji; odkazy na něj jsou u každého tisku.</li>
    <li><b>Odpovědnost.</b> Údaje poskytujeme tak, jak jsou, bez záruky úplnosti a správnosti. V rozsahu, který připouští zákon, neodpovídáme za škodu vzniklou jejich použitím.</li>
  </ul>
  <p class="muted small">Najdete-li chybu, <?php if ((string)config('issues_url', '') !== ''): ?><a href="<?= e((string)config('issues_url', '')) ?>">napište nám</a><?php else: ?>napište nám<?php endif; ?>. Opravy uvádíme v <a href="zmeny.php">přehledu změn</a>.</p>
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
