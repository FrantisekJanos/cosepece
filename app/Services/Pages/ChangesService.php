<?php

declare(strict_types=1);

namespace App\Services\Pages;

final class ChangesService
{
    public function data(): array
    {
        $changes = [
            ['2026-11-19', 'Historie verzí textů a rozborů',
                'Web si pamatuje každé znění textu návrhu i pozměňovacího návrhu a každý rozbor AI. Když se text nebo hodnocení změní, je na detailu tisku odkaz „Historie“ se seznamem verzí a s vyznačeným rozdílem mezi nimi. Texty a rozbory z minulého volebního období se po jeho skončení archivují, nemažou.',
                [34], false],
            ['2026-11-19', 'Hlasování ve Sněmovně u každého tisku',
                'Na detailu tisku jsou všechna hlasování k němu: výsledek, počty pro a proti a rozdělení podle poslaneckých klubů, u závěrečných hlasování i to, o jaký krok šlo (např. přehlasované veto). Jmenovité výsledky vedou na psp.cz. Do skóre hlasování nevstupují. Které pozměňovací návrhy prošly, otevřená data zatím neříkají.',
                [32], false],
            ['2026-11-19', 'Stejný nález se nepočítá dvakrát',
                'Když text mění lhůtu pro vymáhání a použije ji zpětně, dával se za to bod pravidlem a k tomu ještě z rozboru AI, který tentýž nález potvrdil. Teď se počítají jen body z rozboru. Týká se to jediného tisku: novela zákona o střetu zájmů (tisk 221) má 47 bodů místo 62.',
                [30], true],
            ['2026-11-19', 'Kalibrace skóre podle připomínek',
                'Poslanecký návrh dostane plné body až tehdy, když skutečně postupuje do 2. čtení. Návrh na schválení v 1. čtení (§ 90/2) se boduje jen u tisku, který se ještě projednává. Pozměňovací návrhy na poslední chvíli se počítají, jen když jde o většinu návrhů k tisku. Citlivá oblast (střet zájmů, dotace, média…) dává 5 bodů místo 15, protože jde o redakční výběr. Vysoké hodnocení pozměňovacího návrhu od AI se naplno počítá až po ověření silnějším modelem. Přílepek se posuzuje i proti zákonům, které mění samotný návrh. Závažných tisků je teď 10 místo 16.',
                [26], true],
            ['2026-11-19', 'Rozbor AI: obě kola a doložení citací',
                'U každého rozboru je vidět, který model ho udělal, a po ověření silnějším modelem i výsledek prvního kola. Titulek nálezu na úvodní stránce se ukáže jen u rozboru doloženého citací, která se ověřila v textu.',
                [22, 23], false],
            ['2026-11-19', 'Přesnější čtení textů',
                'Text ve Wordu se čte bez sledovaných změn (škrtnutý text se nepočítá). Věta „nemá zpětnou účinnost“ už se nepovažuje za zpětnost. Když Sněmovna vymění dokument tisku, web stáhne nové znění a rozbor zopakuje.',
                [20, 22], false],
            ['2026-11-19', 'Údaje o tisku přímo z číselníků Sněmovny',
                'Návrhy krajů a Prahy už nejsou vedené jako poslanecké. Návrh na schválení v 1. čtení se pozná hned z údajů tisku. Zkrácená lhůta pro výbory (např. 30 dní místo 60) se počítá jako zkrácená lhůta. Tisk vrácený předkladateli má správnou fázi.',
                [19, 21], true],
            ['2026-11-19', 'Hledání a upozornění',
                'Hledání tisků nezáleží na diakritice („skolsky“ najde „Školský“). Nával pozměňovacích návrhů se počítá podle dne podání. Tisky se stejným číslem z různých volebních období se nepletou.',
                [19, 21], false],
            ['2026-11-19', 'Náhled odkazu při sdílení',
                'Každá stránka má při sdílení na sociálních sítích obrázek s názvem, a u tisku i se skóre a hlavními důvody.',
                [16, 17], false],
            ['2026-11-18', 'Nový rozbor všech textů',
                'Model AI nově nedostává jméno předkladatele ani typ návrhu, aby hodnocení nemohla ovlivnit politická příslušnost. Všechny texty se posoudily znovu. U tisku je vidět, kolik textů je posouzeno, a „neposouzeno“ se už neplete s „bez problému“.',
                [14, 15], true],
            ['2026-11-18', 'Datum vyhlášení a krátké lhůty',
                '„Vyhlášeno ve Sbírce zákonů“ má skutečné datum vyhlášení, ne datum doručení. Odstup mezi pozměňovacími návrhy a 3. čtením se počítá od skutečného projednávání. Neúplná data ze Sněmovny už nepřepíšou přehled.',
                [12, 13, 14], true],
        ];
        $title = 'Co jsme změnili';
        $active = 'zmeny';
        $description = 'Přehled oprav a změn v hodnocení na Hlídači zákonů.';
        $repo = 'https://github.com/FrantisekJanos/cosepece';
        return compact('changes', 'title', 'active', 'description', 'repo');
    }
}
