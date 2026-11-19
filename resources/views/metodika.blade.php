@extends('layouts.app')
@section('content')
<?php
use Hlidac\View;
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
    <li>{!! View::scoreBadge((int)$w['level_major']) !!} <span>od {{ (int)$w['level_major'] }} bodů: hlavní nálezy na úvodní stránce a příspěvky na sociálních sítích.</span></li>
    <li>{!! View::scoreBadge((int)$w['level_watch']) !!} <span>{{ (int)$w['level_watch'] }}–{{ (int)$w['level_major'] - 1 }} bodů: v upozorněních a v RSS.</span></li>
    <li>{!! View::scoreBadge(0) !!} <span>pod {{ (int)$w['level_watch'] }} bodů: běžná agenda, na webu skrytá za odkazem „Včetně běžné agendy“.</span></li>
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
    <li><b>Pauza před hlasováním.</b> Od rozeslání pozměňovacích návrhů má uplynout aspoň 14 dní, aby je všichni stihli prostudovat. Sněmovna může pauzu zkrátit nejvýš na 7 dní. <span class="muted small">§ 95 odst. 1 · Hlídač: méně než {{ (int)$w['short_gap_days'] }} dní, zkrácená lhůta</span></li>
    <li><b>Třetí čtení.</b> Už jen opravy chyb a hlasování: o zamítnutí, o pozměňovacích návrzích a o celém zákoně. Obvykle ve středu nebo v pátek, nikdy po 21. hodině. <span class="muted small">§ 95 a 95a</span></li>
    <li><b>Senát.</b> Má 30 dní. Když zákon zamítne nebo vrátí s úpravami, Sněmovna o něm hlasuje znovu. <span class="muted small">Ústava čl. 46 a 47, § 97</span></li>
    <li><b>Prezident a Sbírka.</b> Prezident může zákon do 15 dní vrátit, Sněmovna ho pak přehlasuje 101 hlasy. Zákon platí, až když vyjde ve Sbírce zákonů. <span class="muted small">Ústava čl. 50 a 52, § 98</span></li>
  </ol>
  <p class="muted small">Výjimka: ve stavu legislativní nouze projedná Sněmovna vládní návrh bez prvního čtení a třetí čtení může následovat hned po druhém (§ 99). Podle znění jednacího řádu po novele č. 157/2026 Sb.</p>
</section>

<?php foreach ($groups as [$groupTitle, $capKey, $rows]): ?>
<section class="card" aria-label="{{ $groupTitle }}">
  <div class="card-head"><h2>{{ $groupTitle }}</h2><span class="muted small">nejvýš {{ (int)$w[$capKey] }} bodů za skupinu</span></div>
  <div class="table-wrap">
    <table class="table-compact">
      <thead><tr><th class="num">Body</th><th>Signál</th></tr></thead>
      <tbody>
        <?php foreach ($rows as [$points, $text]): ?>
          <tr><td class="num"><b>{{ (string)$points }}</b></td><td>{{ $text }}</td></tr>
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
  <p>Texty navíc posuzuje jazykový model Claude. Hodnotí jen formu a postup: přílepek, zpětnou účinnost, zkrácení lhůt, prospěch úzké skupiny a zásadní novou úpravu bez připomínkového řízení. Každé zjištění musí doložit doslovnou citací; citace, která v textu není, se zahodí. Zpětnost ve prospěch veřejnosti (např. nové osvobození od poplatku) má nízkou závažnost. Nálezy se závažností {{ $verifyMin }} a víc znovu posoudí silnější model a u nálezu je pak poznámka „ověřeno druhým modelem“. U pozměňovacích návrhů se takový nález do skóre počítá naplno až po tomto ověření, do té doby nejvýš jako {{ $verifyMin - 1 }}/10. Přílepek se posuzuje proti zákonům, které mění název i text samotného návrhu. Tisk s nálezem od {{ (int)$w['finding_ai_min'] }}/10, který ověřil silnější model a doložil citací, je v hlavních nálezech na úvodní stránce i tehdy, když celkové skóre nedosahuje {{ (int)$w['finding_min'] }} bodů (jinak jsou tam tisky od {{ (int)$w['finding_min'] }} bodů).</p>
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
  <p class="muted small">Tyto údaje zatím do skóre nevstupují; body za poslanecký návrh zůstávají, jen jejich popis uvádí, zda si vláda připomínky vyžádala. Po několika týdnech dat zvážíme, zda a jak je započítat. Stanovisko Legislativní rady vlády a jednotlivé připomínky jsou u materiálu ve VeKLEP.</p>
</section>

<section class="card pad" aria-label="Omezení">
  <h2>Co skóre nevidí</h2>
  <ul>
    <li>Politický obsah a dopady zákona. Velká, ale řádně připravená vládní změna může mít nízké skóre.</li>
    <li>Jak se hlasovalo a v kolik hodin.</li>
    <li>Kroky mimo Sněmovnu, např. změny jednacího řádu vlády nebo výběrová řízení.</li>
    <li>Které pozměňovací návrhy byly platně podány ve 2. čtení; Sněmovna to v otevřených datech neeviduje.</li>
  </ul>
  <p class="muted small">Váhy jsou nastavitelné a mohou se po kalibraci měnit; tato stránka vždy ukazuje aktuální hodnoty.<?php if ((string)hlidacConfig('issues_url', '') !== ''): ?> <a href="{{ (string)hlidacConfig('issues_url', '') }}">Nesouhlasíte s hodnocením? Napište nám.</a><?php endif; ?></p>
</section>

@endsection
