<?php
// Test celé synchronizace nad reálnými řádky z psp.cz: php tests/run.php

declare(strict_types=1);

$tmp = sys_get_temp_dir() . '/hlidac-test-' . getmypid();
mkdir($tmp, 0775, true);

// testovací data a konfigurace
passthru(PHP_BINARY . ' ' . escapeshellarg(__DIR__ . '/make_fixtures.php') . ' ' . escapeshellarg($tmp . '/src'));
$base = require dirname(__DIR__) . '/config.example.php';
// HLIDAC_TEST_DSN (+ _USER, _PASS) spustí testy proti jiné databázi, např. prázdnému PostgreSQL
$base['db_dsn'] = getenv('HLIDAC_TEST_DSN') ?: 'sqlite:' . $tmp . '/test.sqlite';
$base['db_user'] = getenv('HLIDAC_TEST_USER') ?: null;
$base['db_pass'] = getenv('HLIDAC_TEST_PASS') ?: null;
$base['source_dir'] = $tmp . '/src';
$base['notify']['dry_run_log'] = $tmp . '/data/posts.log';
$base['site_url'] = 'https://hlidac.test';
$base['notify']['dry_run'] = true;
$base['pn_text']['enabled'] = false; // test běží bez sítě; analýzu textu testujeme přímo
$base['tisk_text']['enabled'] = false;
$base['ai']['enabled'] = false;
file_put_contents($tmp . '/config.php', '<?php return ' . var_export($base, true) . ';');
// Test nesmí převzít provozní DB_CONNECTION, APP_URL ani konfigurační cache z .env.
putenv('DB_CONNECTION=' . explode(':', $base['db_dsn'], 2)[0]);
putenv('APP_CONFIG_CACHE=' . $tmp . '/config-cache.php');
putenv('APP_ROUTES_CACHE=' . $tmp . '/routes-cache.php');
putenv('APP_ENV=testing');
putenv('HLIDAC_CONFIG=' . $tmp . '/config.php');
putenv('HLIDAC_DATA_PATH=' . $tmp . '/data');

require dirname(__DIR__) . '/app/bootstrap.php';
$driver = explode(':', $base['db_dsn'], 2)[0];
$connection = config('database.connections.' . $driver);
if ($driver === 'sqlite') {
    $connection['database'] = substr($base['db_dsn'], 7);
} else {
    foreach (explode(';', explode(':', $base['db_dsn'], 2)[1]) as $part) {
        if (str_contains($part, '=')) {
            [$key, $value] = explode('=', $part, 2);
            $connection[$key === 'dbname' ? 'database' : $key] = $value;
        }
    }
    $connection['username'] = $base['db_user'];
    $connection['password'] = $base['db_pass'];
}
config(['database.connections.' . $driver => $connection, 'hlidac' => $base + ['data_path' => $tmp . '/data']]);
Illuminate\Support\Facades\Artisan::call('migrate', ['--force' => true]);

$failures = 0;
function check(string $label, mixed $actual, mixed $expected): void
{
    global $failures;
    $ok = $actual === $expected;
    if (!$ok) {
        $failures++;
    }
    echo ($ok ? '  OK   ' : '  FAIL ') . $label . ($ok ? '' : ' → ' . var_export($actual, true) . ' (čekáno ' . var_export($expected, true) . ')') . "\n";
}

function testPage(string $path, array $query = []): string
{
    $kernel = app(Illuminate\Contracts\Http\Kernel::class);
    $request = Illuminate\Http\Request::create($path, 'GET', $query);
    $response = $kernel->handle($request);
    $kernel->terminate($request, $response);
    return (string)$response->getContent();
}

echo "1. běh (bootstrap)\n";
$r = Hlidac\Sync::run();
check('volební období', $r['import']['obdobi'], 10);
check('počet tisků (bez usnesení a 9. období)', $r['import']['tisky'], 4);
check('počet PN (bez typu 12 a 9. období)', $r['import']['pn'], 10);

$t = fn(int $ct) => db()->one('SELECT * FROM tisky WHERE ct = ?', [$ct]);
check('tisk 90: navrženo § 90/2', (int)$t(90)['p90'], 1);
check('tisk 90: neprošel v 1. čtení', (int)$t(90)['p90ok'], 0);
check('tisk 90: zkrácená lhůta', (int)$t(90)['zl'], 1);
check('tisk 90: fáze', $t(90)['faze'], 'Vyhlášeno');
check('tisk 90: Sbírka', $t(90)['sbirka'], '155/2026');
check('tisk 90: 3 PN', (int)$t(90)['pn_count'], 3);
check('tisk 78: schváleno v 1. čtení', (int)$t(78)['p90ok'], 1);
check('tisk 78: Sbírka', $t(78)['sbirka'], '59/2026');
check('tisk 311: zkrácená lhůta', (int)$t(311)['zl'], 1);
check('tisk 311: fáze', $t(311)['faze'], '2. čtení');
check('tisk 311: zákon v názvu', $t(311)['zakony'], '586/1992');
check('tisk 221: poslanecký', $t(221)['typ_navrhu'], 'poslanecký');
check('tisk 221: fáze', $t(221)['faze'], '1. čtení');
check('tisk 221: zákon v názvu', $t(221)['zakony'], '159/2006');
check('autor PN', db()->value('SELECT osoba FROM pn WHERE cislo = 2312'), 'Marian Jurečka');
check('bootstrap nic neposlal', (int)db()->value('SELECT COUNT(*) FROM alerts WHERE posted <> 2'), 0);
check('upozornění vytvořena', (int)db()->value('SELECT COUNT(*) FROM alerts') > 5, true);
check('tisk 221 sledovaný', (int)db()->value("SELECT watched FROM alerts WHERE alert_key = 'newbill:221'"), 1);
check('tisk 78: datum vyhlášení', $t(78)['vyhlaseno'], '2026-05-12');
check('tisk 78: „Vyhlášeno“ s datem vyhlášení, ne doručení', db()->value("SELECT event_at FROM alerts WHERE alert_key = 'law:78'"), '2026-05-12 00:00:00');
check('tisk 311 bez vyhlášení nemá „Vyhlášeno“', db()->value("SELECT 1 FROM alerts WHERE alert_key = 'law:311'"), null);
// dřív vytvořené upozornění s datem doručení se při dalším běhu opraví
db()->run("UPDATE alerts SET event_at = '2026-04-28 00:00:00', body = 'x' WHERE alert_key = 'law:78'");
(new Hlidac\AlertBuilder(db(), hlidacConfig()))->run();
check('staré datum „Vyhlášeno“ opraveno', db()->value("SELECT event_at FROM alerts WHERE alert_key = 'law:78'"), '2026-05-12 00:00:00');
// předčasné „Vyhlášeno“ (zákon ještě není ve Sbírce) se odstraní, odeslané na sítě zůstane
db()->run("INSERT INTO alerts (alert_key, kind, ct, event_at, created_at, title, body, watched, posted) VALUES ('law:311', 'law', 311, '2026-11-04 00:00:00', '2026-11-04 00:00:00', 'Vyhlášeno', 'x', 0, 2)");
db()->run("INSERT INTO alerts (alert_key, kind, ct, event_at, created_at, title, body, watched, posted) VALUES ('law:221', 'law', 221, '2026-11-04 00:00:00', '2026-11-04 00:00:00', 'Vyhlášeno', 'x', 0, 1)");
(new Hlidac\AlertBuilder(db(), hlidacConfig()))->run();
check('předčasné „Vyhlášeno“ bez data vyhlášení odstraněno', db()->value("SELECT 1 FROM alerts WHERE alert_key = 'law:311'"), null);
check('odeslané na sítě zůstane', (int)db()->value("SELECT COUNT(*) FROM alerts WHERE alert_key = 'law:221'"), 1);
check('správné „Vyhlášeno“ zůstane', (int)db()->value("SELECT COUNT(*) FROM alerts WHERE alert_key = 'law:78'"), 1);
db()->run("DELETE FROM alerts WHERE alert_key = 'law:221'");

echo "Skóre závažnosti\n";
$sc = fn(int $ct) => (int)db()->value('SELECT score FROM tisky WHERE ct = ?', [$ct]);
$keys = fn(int $ct) => array_column(json_decode((string)db()->value('SELECT reasons FROM tisky WHERE ct = ?', [$ct]), true), 'key');
check('tisk 78: § 90/2 prošlo + poslanecký = 40', $sc(78), 40);
check('tisk 90: § 90/2 navrženo + zkrácení + souhrnná novela = 28', $sc(90), 28);
check('tisk 221: poslanecký v 1. čtení 5 + citlivá oblast 5 + sledovaný 10 = 20', $sc(221), 20);
check('tisk 311: PN na poslední chvíli', in_array('late_pn', $keys(311), true), true);
check('tisk 311: zkrácená lhůta + PN na poslední chvíli + sledovaný = 35', $sc(311), 35);

echo "Analýza textu PN\n";
$pnTextSample = "Pozměňovací návrh\nk vládnímu návrhu zákona, kterým se mění zákon č. 586/1992 Sb.\n"
    . "V zákoně č. 252/1997 Sb., o zemědělství, ve znění pozdějších předpisů, se § 2 mění takto:\n"
    . "Za § 21i se vkládají nové § 21j až 21m.\n"
    . str_repeat('Text úpravy. ', 1200)
    . "\nOdůvodnění\nV zákoně č. 999/2020 Sb. se nic nemění, jen příklad v odůvodnění.";
$a = Hlidac\PnText::analyze($pnTextSample, ['586/1992'], hlidacConfig());
check('přílepek rozpoznán', $a['signals']['other_laws'] ?? [], ['252/1997']);
check('odůvodnění se nepočítá', in_array('999/2020', $a['laws'], true), false);
check('rozsáhlý PN', isset($a['signals']['large']), true);
check('skóre pravidel PN', $a['score'], 30);
db()->run("INSERT INTO pn_text (id_dokument, ct, cislo, text, chars, laws, signals, rule_score, fetched_at) VALUES (31312, 311, 2312, 'x', 15000, '252/1997', ?, 30, ?)",
    [json_encode($a['signals']), date('Y-m-d H:i:s')]);
$scorer = new Hlidac\Scorer(db(), hlidacConfig());
$scorer->scoreBills();
$scorer->scoreAlerts();
check('tisk 311 po přílepku je závažný (55)', $sc(311), 55);
check('úroveň závažnosti', Hlidac\Scorer::level($sc(311), hlidacConfig()), 'major');
// rozbor AI potvrdil, že změna jiného zákona s předmětem souvisí → přílepek se nepočítá
db()->run("UPDATE pn_text SET ai_json = ? WHERE id_dokument = 31312", [json_encode(['souvisi_s_predmetem' => true])]);
$scorer->scoreBills();
check('související změna jiného zákona není přílepek', in_array('pn_other_law', $keys(311), true), false);
db()->run("UPDATE pn_text SET ai_json = ? WHERE id_dokument = 31312", [json_encode(['souvisi_s_predmetem' => false])]);
$scorer->scoreBills();
check('přílepek potvrzený rozborem zůstává', $sc(311), 55);
// krátký PN se souvisejícím jiným zákonem: rule_score > 0, ale ani přílepek, ani „rozsáhlá úprava“ (issue #7, bod 1)
db()->run("UPDATE pn_text SET signals = ?, ai_json = ? WHERE id_dokument = 31312",
    [json_encode(['other_laws' => ['252/1997']]), json_encode(['souvisi_s_predmetem' => true])]);
$scorer->scoreBills();
check('související krátký PN nedá „rozsáhlou úpravu“', in_array('pn_large', $keys(311), true), false);
db()->run("UPDATE pn_text SET signals = ?, ai_json = NULL WHERE id_dokument = 31312", [json_encode($a['signals'])]);
$scorer->scoreBills();
check('po vrácení signálů zase 55', $sc(311), 55);
// tři krátké PN se souvisejícím přílepkem (rule_score 20, AI 3) nejsou problematické: bez pn_many i pn_large (issue #7, bod 1)
$relatedPn = [json_encode(['other_laws' => ['252/1997']]), json_encode(['souvisi_s_predmetem' => true])];
db()->run('UPDATE pn_text SET signals = ?, ai_json = ?, rule_score = 20, ai_score = 3 WHERE id_dokument = 31312', $relatedPn);
foreach ([31313 => 2313, 31330 => 2330] as $idPn => $cisloPn) {
    db()->run("INSERT INTO pn_text (id_dokument, ct, cislo, text, chars, laws, signals, rule_score, ai_json, ai_score, fetched_at) VALUES (?, 311, ?, 'x', 500, '252/1997', ?, 20, ?, 3, ?)",
        [$idPn, $cisloPn, $relatedPn[0], $relatedPn[1], date('Y-m-d H:i:s')]);
}
$scorer->scoreBills();
check('3 související přílepky: bez pn_many a pn_large', array_values(array_intersect(['pn_many', 'pn_large'], $keys(311))), []);
// jeden nesouvisející přílepek a dva rozsáhlé PN = 3 problematické → pn_many
db()->run('UPDATE pn_text SET ai_json = ? WHERE id_dokument = 31312', [json_encode(['souvisi_s_predmetem' => false])]);
db()->run('UPDATE pn_text SET signals = ?, ai_json = NULL, rule_score = 10 WHERE id_dokument IN (31313, 31330)', [json_encode(['large' => 15000])]);
$scorer->scoreBills();
check('přílepek + 2 rozsáhlé PN: pn_many', in_array('pn_many', $keys(311), true), true);
// úklid: testovací PN pryč, PN 31312 do původního stavu
db()->run('DELETE FROM pn_text WHERE id_dokument IN (31313, 31330)');
db()->run('UPDATE pn_text SET signals = ?, ai_json = NULL, rule_score = 30, ai_score = NULL WHERE id_dokument = 31312', [json_encode($a['signals'])]);
$scorer->scoreBills();
check('po úklidu testovacích PN zase 55', $sc(311), 55);

echo "2. běh (bez nových dat)\n";
$r2 = Hlidac\Sync::run();
check('žádná nová upozornění', $r2['alerts_new'], 0);

echo "3. běh (nový PN k tisku 221 a zkrácení lhůty u 221)\n";
$zip = new ZipArchive();
$zip->open($tmp . '/src/sd.zip');
$sd = iconv('CP1250', 'UTF-8', $zip->getFromName('sd_dokument.unl'));
$sd .= "40001|174|3001|13|||221|6205|" . date('Y-m-d H:i:s') . "|\n";
$zip->addFromString('sd_dokument.unl', iconv('UTF-8', 'CP1250', $sd));
$zip->close();
$zip->open($tmp . '/src/tisky.zip');
$hist = iconv('CP1250', 'UTF-8', $zip->getFromName('hist.unl'));
$hist .= "300001|43343|" . date('Y-m-d') . " 00:00||2031|||||||||||\n";
$zip->addFromString('hist.unl', iconv('UTF-8', 'CP1250', $hist));
$zip->close();

$r3 = Hlidac\Sync::run();
check('2 nová upozornění', $r3['alerts_new'], 2);
check('tisk 221 se zkrácenou lhůtou (45)', $sc(221), 45);
check('odesláno jen nad prahem (dry run): zkrácení 55, PN 45 čeká', [$r3['posts']['sent'], $r3['posts']['held']], [1, 1]);
$log = (string)@file_get_contents(hlidacDataPath('posts.log'));
check('příspěvek obsahuje odkaz', str_contains($log, 'https://hlidac.test/tisky/221'), true);
check('příspěvek o zkrácené lhůtě', str_contains($log, 'Zkrácená lhůta: Novela z. o střetu zájmů'), true);
@unlink(hlidacDataPath('posts.log'));

echo "4. běh (běžný PN pod prahem se nepošle)\n";
db()->run("UPDATE alerts SET posted = 1 WHERE posted = 0");
$zip->open($tmp . '/src/sd.zip');
$sd = iconv('CP1250', 'UTF-8', $zip->getFromName('sd_dokument.unl'));
$sd .= "40002|174|3002|13|||90|6687|" . date('Y-m-d H:i:s') . "|\n";
$zip->addFromString('sd_dokument.unl', iconv('UTF-8', 'CP1250', $sd));
$zip->close();
$r4 = Hlidac\Sync::run();
check('vzniklo upozornění', $r4['alerts_new'], 1);
check('nic neodesláno (skóre 28 < 50)', $r4['posts']['sent'], 0);
check('pod prahem čeká na rozbor textů (hold_hours)', [$r4['posts']['held'], $r4['posts']['filtered']], [1, 0]);
db()->run('UPDATE alerts SET created_at = ? WHERE posted = 0', [date('Y-m-d H:i:s', time() - 49 * 3600)]);
$r4b = (new Hlidac\Publisher(db(), hlidacConfig()))->run();
check('po uplynutí čekání potlačeno', [$r4b['filtered'], $r4b['sent']], [1, 0]);

echo "Text návrhu zákona (tisk 221: lhůta pro vymáhání dotací 4 roky i zpětně)\n";
$billSample = "Návrh\nZÁKON\nkterým se mění zákon č. 159/2006 Sb., o střetu zájmů\n"
    . "V § 44a odst. 11 se za větu předposlední vkládá věta „Řízení o uložení odvodu za porušení rozpočtové kázně lze zahájit nejpozději do 4 let od porušení rozpočtové kázně.“.\n"
    . "Řízení zahájená přede dnem nabytí účinnosti tohoto zákona se dokončí podle dosavadních právních předpisů.\n"
    . "Lhůta pro zahájení řízení o vrácení dotace se posuzuje podle zákona č. 256/2000 Sb., ve znění účinném ode dne nabytí účinnosti tohoto zákona, i tehdy, pokud byla neoprávněná platba dotace poskytnuta přede dnem nabytí účinnosti tohoto zákona.\n"
    . "DŮVODOVÁ ZPRÁVA\nI tehdy, pokud to bylo přede dnem nabytí účinnosti, lhůta 10 let pro vrácení dotace se nemění.";
$ba = Hlidac\TiskText::analyze($billSample);
check('důvodová zpráva se nepočítá', count($ba['signals']['retro'] ?? []), 1);
check('zpětné použití rozpoznáno', str_contains($ba['signals']['retro'][0] ?? '', 'i tehdy, pokud byla neoprávněná platba'), true);
check('změna lhůty rozpoznána', str_contains($ba['signals']['deadline'][0] ?? '', 'nejpozději do 4 let'), true);
check('běžné přechodné ustanovení není zpětné', Hlidac\PnText::procedureSignals("Řízení zahájená přede dnem nabytí účinnosti tohoto zákona se dokončí podle dosavadních právních předpisů."), []);
// jeden dlouhý odstavec z Wordu se dvěma nesouvisejícími větami nesmí dát „lhůtu i zpětně“ (PN 2056 k tisku 174)
$paragraph = "15. V § 28c se vkládá věta „Úřad nařídí péči i tehdy, pokud k týrání došlo přede dnem nabytí účinnosti tohoto zákona.“. "
    . "16. V § 28d se vkládá věta „Odvolání proti rozhodnutí o záloze nemá odkladný účinek a řízení lze zahájit nejpozději do 2 let.“.";
check('dlouhý odstavec: zpětnost bez lhůty nedá body', isset(Hlidac\PnText::procedureSignals($paragraph)['retro_deadline']), false);
check('zpětně použitá lhůta tisku 221 se pozná', isset($ba['signals']['retro_deadline']), true);
$capped = Hlidac\Scorer::applyCaps([
    ['key' => 'pn_other_law', 'points' => 20, 'label' => 'a'], ['key' => 'ai_bill', 'points' => 18, 'label' => 'b'],
    ['key' => 'ai', 'points' => 24, 'label' => 'c'], ['key' => 'mp_bill', 'points' => 15, 'label' => 'd'],
], Hlidac\Scorer::weights(hlidacConfig()));
check('strop obsahové skupiny 45', array_sum(array_column($capped, 'points')), 60);
check('krácený důvod zůstane vidět', count($capped), 4);
$quotes = Hlidac\AiReviewer::verifyQuotes([
    'Řízení o uložení odvodu za porušení rozpočtové kázně lze zahájit nejpozději do 4 let od porušení rozpočtové kázně.',
    'Lhůta pro vrácení dotací se zkracuje z 10 na 4 roky.', // vymyšlená citace
], $billSample);
check('ověřená citace zůstane, vymyšlená se zahodí', count($quotes), 1);
$noQuote = Hlidac\AiReviewer::sanitize(['zavaznost' => 8, 'citace' => [], 'titulek' => 'x']);
check('nález bez ověřené citace má nejvýš 2', $noQuote['zavaznost'], 2);
check('snížení bez citace je zapsané', count($noQuote['upravy'] ?? []), 1);
check('nález s citací zůstane', Hlidac\AiReviewer::sanitize(['zavaznost' => 8, 'citace' => $quotes, 'titulek' => 'x'])['zavaznost'], 8);
db()->run("INSERT INTO tisk_text (ct, text, chars, signals, ai_score, ai_json, fetched_at, ai_at) VALUES (221, ?, ?, ?, 8, ?, ?, ?)", [
    $billSample, mb_strlen($billSample), json_encode($ba['signals'], JSON_UNESCAPED_UNICODE),
    json_encode(['shrnuti' => 'Zkracuje lhůtu pro vymáhání dotací a použije ji zpětně.', 'zavaznost' => 8, 'zpetna_ucinnost' => true,
        'zkraceni_lhut' => true, 'duvod' => 'Zpětné zkrácení lhůty pro vymáhání dotací.', 'citace' => $quotes], JSON_UNESCAPED_UNICODE),
    date('Y-m-d H:i:s'), date('Y-m-d H:i:s'),
]);
$scorer->scoreBills();
check('tisk 221: AI potvrdila zpětnou lhůtu → pravidlové body se nepřičtou podruhé', in_array('retro_deadline', $keys(221), true), false);
check('tisk 221: rozbor textu návrhu', in_array('ai_bill', $keys(221), true), true);
check('tisk 221: 45 + 8×3 = 69', $sc(221), 69);
$html221 = testPage('/tisky/221');
check('detail tisku ukazuje rozbor a citaci', str_contains($html221, 'Rozbor textu návrhu') && str_contains($html221, 'nejpozději do 4 let'), true);
// celá cesta saveResult: nález bez ověřené citace se uloží se závažností 2 a do skóre tisku se nepromítne (issue #7, bod 2)
$saved221 = db()->one('SELECT ai_json, ai_score, ai_at, ai_model FROM tisk_text WHERE ct = 221');
$saveReviewer = new Hlidac\AiReviewer(db(), hlidacConfig());
$saveReviewer->saveResult('tisk', 221, ['zavaznost' => 8, 'titulek' => 'x', 'citace' => ['Vymyšlená věta, která v textu návrhu vůbec není.']]);
check('saveResult: vymyšlená citace → ai_score 2', (int)db()->value('SELECT ai_score FROM tisk_text WHERE ct = 221'), 2);
$scorer->scoreBills();
check('saveResult: bez ověřené citace bez ai_bill', in_array('ai_bill', $keys(221), true), false);
$saveReviewer->saveResult('tisk', 221, ['zavaznost' => 8, 'titulek' => 'x', 'citace' => ['   ', '']]);
check('saveResult: citace jen z mezer → ai_score 2', (int)db()->value('SELECT ai_score FROM tisk_text WHERE ct = 221'), 2);
check('uložený rozbor bez klíče citace se nesníží', Hlidac\AiReviewer::sanitize(['zavaznost' => 8, 'titulek' => 'x'])['zavaznost'], 8);
db()->run('UPDATE tisk_text SET ai_json = ?, ai_score = ?, ai_at = ?, ai_model = ? WHERE ct = 221',
    [$saved221['ai_json'], $saved221['ai_score'], $saved221['ai_at'], $saved221['ai_model']]);
$scorer->scoreBills();
check('tisk 221 po vrácení rozboru zase 69', $sc(221), 69);

echo "AI rozbor přes claude CLI (falešné CLI, bez sítě)\n";
$fakeCli = $tmp . '/claude.php';
file_put_contents($fakeCli, '#!' . PHP_BINARY . "\n" . <<<'FAKE'
<?php
$prompt = stream_get_contents(STDIN);
$args = implode(' ', $argv);
$ok = getenv('ANTHROPIC_API_KEY') === false && str_contains($args, '--tools') && str_contains($prompt, 'Text návrhu');
// silnější model (druhé kolo) vrátí jinou závažnost, ať se pozná, že se opravdu volal
$score = !$ok ? 0 : (str_contains($args, '--model claude-sonnet-5-5') ? 7 : 9);
$result = ['titulek' => 'Lhůta pro vymáhání dotací se zkracuje na 4 roky – i zpětně', 'perex' => str_repeat('Dlouhý perex. ', 30), 'shrnuti' => 'Zkracuje lhůtu a použije ji zpětně.', 'zavaznost' => $score, 'zpetna_ucinnost' => true, 'zpetnost_smer' => 'oslabuje_vymahani_nebo_kontrolu', 'zkraceni_lhut' => true,
    'duvod' => 'Zpětné zkrácení lhůty pro vymáhání dotací.',
    'citace' => ['Řízení o uložení odvodu za porušení rozpočtové kázně lze zahájit nejpozději do 4 let od porušení rozpočtové kázně.', 'Vymyšlená věta, která v textu není.']];
echo json_encode(['type' => 'result', 'is_error' => false, 'stop_reason' => 'end_turn', 'result' => "```json\n" . json_encode($result, JSON_UNESCAPED_UNICODE) . "\n```"]);
FAKE);
chmod($fakeCli, 0755);
putenv('ANTHROPIC_API_KEY=nesmi-se-pouzit');
$aiConfig = hlidacConfig();
$aiConfig['ai'] = ['enabled' => true, 'backend' => 'cli', 'cli_path' => $fakeCli, 'cli_prefix' => [PHP_BINARY], 'model' => 'claude-haiku-5-5'];
db()->run('UPDATE tisk_text SET ai_at = NULL, ai_score = NULL WHERE ct = 221');
$reviewer = new Hlidac\AiReviewer(db(), $aiConfig);
check('fronta: text tisku 221 s nápovědou pravidel', str_contains($reviewer->queue(5, 221)[0]['prompt'] ?? '', 'možné zpětné použití'), true);
check('rozbor přes CLI proběhl', $reviewer->run(5, 221), ['reviewed' => 1, 'errors' => 0]);
check('CLI bez API klíče a bez nástrojů (závažnost 9)', (int)db()->value('SELECT ai_score FROM tisk_text WHERE ct = 221'), 9);
check('vymyšlená citace z CLI zahozena', count(json_decode((string)db()->value('SELECT ai_json FROM tisk_text WHERE ct = 221'), true)['citace']), 1);
putenv('ANTHROPIC_API_KEY');
$aiConfig['ai']['cli_path'] = $tmp . '/neexistuje';
check('perex zkrácen pro homepage', mb_strlen(json_decode((string)db()->value('SELECT ai_json FROM tisk_text WHERE ct = 221'), true)['perex']) <= 220, true);
check('model prvního kola uložen', db()->value('SELECT ai_model FROM tisk_text WHERE ct = 221'), 'claude-haiku-5-5');
$aiConfig['ai']['cli_path'] = $fakeCli;
$aiConfig['ai']['verify_min'] = 6;
$reviewer = new Hlidac\AiReviewer(db(), $aiConfig);
check('fronta ověření: závažný nález 221', array_column($reviewer->queue(5, 221, true), 'id'), [221]);
check('ověření silnějším modelem', $reviewer->run(5, 221, true), ['reviewed' => 1, 'errors' => 0]);
check('výsledek ověření nahradí první kolo', [(int)db()->value('SELECT ai_score FROM tisk_text WHERE ct = 221'), db()->value('SELECT ai_model FROM tisk_text WHERE ct = 221')], [7, 'claude-sonnet-5-5']);
check('první kolo zůstane uložené', [(int)db()->value('SELECT ai_prev_score FROM tisk_text WHERE ct = 221'), db()->value('SELECT ai_prev_model FROM tisk_text WHERE ct = 221')], [9, 'claude-haiku-5-5']);
check('na detailu je vidět i první kolo', str_contains(Hlidac\View::aiRounds(db()->one('SELECT * FROM tisk_text WHERE ct = 221')), 'první kolo claude-haiku-5-5: 9/10'), true);
check('ověřený nález se znovu neověřuje', $reviewer->queue(5, 221, true), []);
check('rozbor nese aktuální verzi promptu', (int)db()->value('SELECT ai_prompt FROM tisk_text WHERE ct = 221'), Hlidac\AiReviewer::PROMPT_VERSION);
check('aktuální rozbor není ve frontě', $reviewer->queue(5, 221), []);
// rozbor podle starší verze promptu: zůstane zobrazený, ale vrátí se do fronty; ověření ho přeskočí
db()->run('UPDATE tisk_text SET ai_prompt = NULL WHERE ct = 221');
check('starší verze promptu se znovu posoudí', array_column($reviewer->queue(5, 221), 'id'), [221]);
check('starší rozbor se mezitím neověřuje', $reviewer->queue(5, 221, true), []);
check('starší rozbor zůstává zobrazený', (int)db()->value('SELECT ai_score FROM tisk_text WHERE ct = 221'), 7);
db()->run('UPDATE tisk_text SET ai_prompt = ? WHERE ct = 221', [Hlidac\AiReviewer::PROMPT_VERSION]);
check('„zpětně“ v titulku zůstane u oslabení vymáhání', str_contains(json_decode((string)db()->value('SELECT ai_json FROM tisk_text WHERE ct = 221'), true)['titulek'], 'zpětně'), true);
$aiConfig['ai']['cli_path'] = $tmp . '/neexistuje';

echo "Pojistky nad odpovědí AI\n";
$fav = Hlidac\AiReviewer::sanitize(['titulek' => 'Vyšší příspěvek na péči se vyplatí i zpětně od 1. 1. 2026', 'zavaznost' => 6, 'zpetnost_smer' => 've_prospech_verejnosti']);
check('příznivá zpětnost: závažnost nejvýš 3', $fav['zavaznost'], 3);
check('příznivá zpětnost: bez „zpětně“ v titulku', $fav['titulek'], 'Vyšší příspěvek na péči se vyplatí od 1. 1. 2026');
check('korekce je zapsaná', count($fav['upravy']), 2);
check('„– i zpětně“ na konci', Hlidac\AiReviewer::stripRetroWord('Stávající souhlasy zůstávají – i zpětně'), 'Stávající souhlasy zůstávají');
check('„, tedy i zpětně“', Hlidac\AiReviewer::stripRetroWord('Zákaz platí od 1. 7. 2026, tedy i zpětně'), 'Zákaz platí od 1. 7. 2026');
check('„zpětně“ na začátku', Hlidac\AiReviewer::stripRetroWord('Zpětně se ruší poplatek'), 'Se ruší poplatek');
$hard = Hlidac\AiReviewer::sanitize(['titulek' => 'Daň se použije i zpětně za rok 2026', 'zavaznost' => 8, 'zpetnost_smer' => 'nova_povinnost']);
check('zpětná nová daň zůstane beze změny', [$hard['zavaznost'], $hard['titulek'], isset($hard['upravy'])], [8, 'Daň se použije i zpětně za rok 2026', false]);
db()->run("UPDATE tisky SET posledni = '2020-01-01', faze = '1. čtení' WHERE ct = 221"); // rozpracovaný, dlouho bez aktivity
$scorer->scoreBills();
$home = testPage('/');
check('homepage: srozumitelný titulek místo názvu novely', str_contains($home, 'Lhůta pro vymáhání dotací se zkracuje na 4 roky – i zpětně'), true);
check('homepage: citace u nálezu', str_contains($home, 'nejpozději do 4 let'), true);
check('bez CLI se AI přeskočí', (new Hlidac\AiReviewer(db(), $aiConfig))->run(), ['reviewed' => 0, 'errors' => 0, 'skipped' => true]);

echo "Vypínač rozesílky notify.post\n";
db()->run('UPDATE alerts SET posted = 2 WHERE posted = 0');
$postAlert = fn(string $key, int $watched) => db()->run(
    "INSERT INTO alerts (alert_key, kind, ct, event_at, created_at, title, body, watched, posted, score) VALUES (?, 'cut', 311, '2026-11-18 10:00:00', '2026-11-18 10:00:00', 't', 'b', ?, 0, 90)",
    [$key, $watched]);
$postCfg = hlidacConfig();
$postCfg['notify']['dry_run'] = true;
$postAlert('test:off', 1);
$postCfg['notify']['post'] = 'off';
$st = (new Hlidac\Publisher(db(), $postCfg))->run();
check('post=off: nic se neodešle (ani v dry_run)', $st['sent'], 0);
check('post=off: upozornění zůstane jen na webu', (int)db()->value("SELECT posted FROM alerts WHERE alert_key = 'test:off'"), 3);
$postAlert('test:watch-no', 0);
$postAlert('test:watch-yes', 1);
$postCfg['notify']['post'] = 'watch';
$st = (new Hlidac\Publisher(db(), $postCfg))->run();
check('post=watch: odejde jen sledované', $st['sent'], 1);
check('post=watch: nesledované se neposílá', (int)db()->value("SELECT posted FROM alerts WHERE alert_key = 'test:watch-no'"), 3);
check('post=watch: sledované odesláno', (int)db()->value("SELECT posted FROM alerts WHERE alert_key = 'test:watch-yes'"), 1);
db()->run("DELETE FROM alerts WHERE alert_key LIKE 'test:%'");
db()->run("DELETE FROM post_log WHERE alert_key LIKE 'test:%'");

echo "Prompt AI bez jména předkladatele a typu návrhu\n";
$promptOf = function (string $method, array $row): string {
    $m = new ReflectionMethod(Hlidac\AiReviewer::class, $method);
    return $m->invoke(new Hlidac\AiReviewer(db(), hlidacConfig()), $row, 'text', []);
};
$pnPrompt = $promptOf('pnPrompt', ['uplny_nazev' => 'Návrh X', 'osoba' => 'Marian Jurečka', 'signals' => '{}']);
check('prompt PN neobsahuje jméno předkladatele', str_contains($pnPrompt, 'Jurečka'), false);
$billPrompt = $promptOf('billPrompt', ['uplny_nazev' => 'Návrh X', 'nazev' => 'X', 'typ_navrhu' => 'poslanecký', 'signals' => '{}']);
check('prompt návrhu neobsahuje typ návrhu', str_contains($billPrompt, 'poslanecký') || str_contains($billPrompt, 'poslanci'), false);

echo "Neúplný export nepřepíše data\n";
$bad = $tmp . '/src-bad';
@mkdir($bad, 0775, true);
foreach (['sd.zip', 'poslanci.zip'] as $f) {
    copy($tmp . '/src/' . $f, $bad . '/' . $f);
}
copy($tmp . '/src/tisky.zip', $bad . '/tisky.zip');
$zipBad = new ZipArchive();
$zipBad->open($bad . '/tisky.zip');
$zipBad->addFromString('tisky.unl', '');   // prázdný soubor se stejným jménem
$zipBad->close();
$countsBefore = [(int)db()->value('SELECT COUNT(*) FROM tisky'), (int)db()->value('SELECT COUNT(*) FROM kroky')];
$syncBefore = db()->getMeta('last_sync');
$importError = null;
try {
    (new Hlidac\Importer(db(), new Hlidac\Source('', $tmp . '/cache-bad', $bad)))->run();
} catch (RuntimeException $ex) {
    $importError = $ex->getMessage();
}
check('prázdný tisky.unl import zastaví', $importError !== null && str_contains($importError, 'Import zastaven'), true);
check('tisky a kroky zůstaly', [(int)db()->value('SELECT COUNT(*) FROM tisky'), (int)db()->value('SELECT COUNT(*) FROM kroky')], $countsBefore);
check('last_sync se neposunul', db()->getMeta('last_sync'), $syncBefore);
check('důvod zapsán v meta', str_contains((string)db()->getMeta('import_error'), 'propad počtu řádků kroky') || str_contains((string)db()->getMeta('import_error'), 'žádné tisky'), true);
(new Hlidac\Importer(db(), new Hlidac\Source('', $tmp . '/cache-bad', $tmp . '/src')))->run();
check('úplný import chybu smaže', db()->getMeta('import_error'), '');

echo "Stav rozboru textů\n";
$reviewsTest = new App\Services\ReviewStatusService();
$rs = $reviewsTest->statuses();
check('tisk 311: počet textů k posouzení = PN', $rs[311]['total'] ?? null, (int)db()->value('SELECT COUNT(*) FROM pn WHERE ct = 311') + (int)db()->value('SELECT COUNT(*) FROM tisk_text WHERE ct = 311'));
check('štítek bez rozboru', App\Services\ReviewStatusService::label(['total' => 3, 'done' => 0]), 'Text neposouzen');
check('štítek částečně', App\Services\ReviewStatusService::label(['total' => 3, 'done' => 1]), 'Posouzeno 1 z 3 textů');
check('štítek vše posouzeno', App\Services\ReviewStatusService::label(['total' => 3, 'done' => 3]), null);
check('jeden tisk = stejný výsledek jako hromadně', $reviewsTest->statuses(311)[311] ?? null, $rs[311] ?? null);

echo "Tokeny se neukládají do chyb\n";
$leak = Hlidac\Http::redact('HTTP chyba při volání https://api.telegram.org/bot123456:ABC-tajne/sendMessage: timeout; '
    . 'https://web.cz/ai_api.php?token=tajne123&action=queue; Authorization: Bearer tajneJWT.x; {"accessJwt":"tajne.jwt"}');
check('maskování tokenů v chybě', str_contains($leak, 'tajne'), false);
check('maskování ponechá zbytek zprávy', str_contains($leak, 'api.telegram.org/bot***/sendMessage: timeout'), true);
$reviewerLeak = new Hlidac\AiReviewer(db(), hlidacConfig());
$leakId = (int)db()->value('SELECT id_dokument FROM pn_text ORDER BY id_dokument LIMIT 1');
$reviewerLeak->saveError('pn', $leakId, 'HTTP 500 z https://web.cz/ai_api.php?token=tajne123&action=result: x');
check('saveError uloží chybu bez tokenu', str_contains((string)db()->value('SELECT error FROM pn_text WHERE id_dokument = ?', [$leakId]), 'tajne'), false);

echo "Krátký čas mezi PN a 3. čtením (tisk 198: „zahájeno“ datované po „ukončeno“)\n";
$gapRow = fn(int $id, string $d, int $typ, int $akce) => db()->run(
    'INSERT INTO kroky (id_hist, id_tisk, ct, datum, typ_stavu, stav, akce_id, akce) VALUES (?, 0, 311, ?, ?, ?, ?, ?)',
    [$id, "$d 00:00:00", $typ, '', $akce, '']);
db()->run('DELETE FROM kroky WHERE ct = 311 AND typ_stavu IN (5, 21)');
$gapRow(990001, '2026-06-30', 21, 23);
$gapRow(990002, '2026-06-30', 5, 86);   // administrativní krok se nepočítá
$gapRow(990003, '2026-07-02', 5, 71);   // projednávání ukončeno
$gapRow(990004, '2026-07-08', 5, 238);  // zahájeno (datované později)
(new Hlidac\Scorer(db(), hlidacConfig()))->scoreBills();
$gap = array_values(array_filter(json_decode((string)db()->value('SELECT reasons FROM tisky WHERE ct = 311'), true),
    fn(array $r): bool => $r['key'] === 'short_gap'));
check('odstup se počítá od nejdřívějšího kroku 3. čtení', $gap[0]['label'] ?? null, 'Mezi pozměňovacími návrhy a 3. čtením jen 2 dny');

echo "Předkladatel, § 90/2 a lhůta pro výbory z údajů tisku\n";
check('kraj podle id_navrh', Hlidac\Analyzer::typNavrhu(2, 'Návrh Zastupitelstva Jihomoravského kraje na vydání zákona', 16), 'krajský');
check('kraj podle názvu i s velkým Z', Hlidac\Analyzer::typNavrhu(2, 'Návrh Zastupitelstva hlavního města Prahy na vydání zákona'), 'krajský');
check('skupina poslanců', Hlidac\Analyzer::typNavrhu(2, 'Návrh poslanců …', 3), 'poslanecký');
check('vláda podle id_navrh', Hlidac\Analyzer::typNavrhu(1, 'Vládní návrh zákona', 1), 'vládní');
check('lhůta pro výbory 30 dní = zkrácená', [Hlidac\Analyzer::committeeCut(30), Hlidac\Analyzer::committeeCut(60), Hlidac\Analyzer::committeeCut(null)], [true, false, false]);
$imp = db()->one('SELECT p90, zl, lhuta, typ_navrhu FROM tisky WHERE ct = 221');
check('importované tisky mají lhůtu a typ', is_array($imp) && array_key_exists('lhuta', $imp), true);
db()->run("UPDATE tisky SET zl = 1, lhuta = 30 WHERE ct = 90");
(new Hlidac\Scorer(db(), hlidacConfig()))->scoreBills();
$cut = array_values(array_filter(json_decode((string)db()->value('SELECT reasons FROM tisky WHERE ct = 90'), true),
    fn(array $r): bool => $r['key'] === 'deadline_cut'));
check('důvod uvádí zkrácenou lhůtu pro výbory', $cut[0]['label'] ?? null, 'Zkrácená lhůta pro výbory: 30 dní místo 60');
db()->run("UPDATE tisky SET p90 = 1 WHERE ct = 90");
db()->run("DELETE FROM alerts WHERE alert_key = 'p90:90'");
(new Hlidac\AlertBuilder(db(), hlidacConfig()))->run();
check('návrh na § 90/2 z údajů tisku vytvoří upozornění', db()->value("SELECT kind FROM alerts WHERE alert_key = 'p90:90'"), 'p90');

echo "Opakované stažení neúspěšných textů\n";
$retryCt = (int)db()->value("SELECT ct FROM tisky WHERE faze <> 'Vyhlášeno' ORDER BY ct LIMIT 1");
db()->run('DELETE FROM tisk_text WHERE ct = ?', [$retryCt]);
db()->run("INSERT INTO tisk_text (ct, docx_url, text, chars, signals, fetched_at, error) VALUES (?, NULL, '', 0, '[]', ?, 'Chyba sítě')", [$retryCt, date('Y-m-d H:i:s')]);
$queue = fn(): array => array_map('intval', array_column(Hlidac\TiskText::queue(db(), 1000, 24), 'ct'));
check('čerstvá chyba se hned neopakuje', in_array($retryCt, $queue(), true), false);
db()->run('UPDATE tisk_text SET fetched_at = ? WHERE ct = ?', [date('Y-m-d H:i:s', time() - 25 * 3600), $retryCt]);
check('po 24 h se stažení zopakuje', in_array($retryCt, $queue(), true), true);
db()->run("UPDATE tisk_text SET text = 'obsah', error = NULL WHERE ct = ?", [$retryCt]);
check('úspěšně stažený text se neopakuje', in_array($retryCt, $queue(), true), false);

echo "Hledání bez diakritiky\n";
check('fold', App\Services\Pages\BillListingService::fold('Školský ZÁKON'), 'skolsky zakon');
$listing = app(App\Services\Pages\BillListingService::class);
$nameWord = explode(' ', (string)db()->value('SELECT nazev FROM tisky WHERE ct = 221'));
$hit = fn(string $q): bool => in_array(221, array_map(fn($t) => (int)$t['ct'], $listing->bills('', $q, 500)), true);
check('hledání „střetu“ najde tisk 221', $hit('STŘETU'), true);
check('hledání bez diakritiky „stretu zajmu“', $hit('stretu zajmu'), true);
check('hledání podle čísla tisku', $hit('221'), true);

echo "Čtvrtá várka: Word, zápor zpětnosti, ověřený rozbor, Bluesky\n";
$docx = $tmp . '/tracked.docx';
$dz = new ZipArchive();
$dz->open($docx, ZipArchive::CREATE | ZipArchive::OVERWRITE);
$dz->addFromString('word/document.xml', '<w:document><w:body><w:p><w:r><w:t>Lhůta činí </w:t></w:r>'
    . '<w:del w:id="1" w:author="X"><w:r><w:delText>10 let</w:delText></w:r></w:del>'
    . '<w:ins w:id="2" w:author="X"><w:r><w:t>4 roky</w:t></w:r></w:ins><w:r><w:t>.</w:t></w:r></w:p></w:body></w:document>');
$dz->close();
check('smazaný text ze sledovaných změn se nečte', Hlidac\PnText::docxToText((string)file_get_contents($docx)), 'Lhůta činí 4 roky.');
check('„nemá zpětnou účinnost“ není zpětnost', Hlidac\PnText::procedureSignals('Toto rozhodnutí nemá zpětnou účinnost, a to ani v případě zajištění zvířete.'), []);
check('„zpětné použití se vylučuje“ není zpětnost', Hlidac\PnText::negatesRetro('Zpětné použití se vylučuje.'), true);
check('skutečná zpětná účinnost zůstává', isset(Hlidac\PnText::procedureSignals('Ustanovení má zpětnou účinnost ke dni 1. ledna 2020.')['retro']), true);

$guardCt = (int)db()->value('SELECT ct FROM tisk_text ORDER BY ct LIMIT 1');
$verifier = Hlidac\AiReviewer::verifyModel(hlidacConfig());
db()->run("UPDATE tisk_text SET text = 'Lhůta činí 4 roky.', ai_json = '{}', ai_score = 7, ai_model = ?, ai_prompt = ?, ai_at = ? WHERE ct = ?",
    [$verifier, Hlidac\AiReviewer::PROMPT_VERSION, date('Y-m-d H:i:s'), $guardCt]);
$guard = new Hlidac\AiReviewer(db(), hlidacConfig());
$guard->saveResult('tisk', $guardCt, ['zavaznost' => 1, 'titulek' => 'x', 'citace' => []], Hlidac\AiReviewer::model(hlidacConfig()));
check('pozdní výsledek 1. kola nepřepíše ověřený rozbor', [(int)db()->value('SELECT ai_score FROM tisk_text WHERE ct = ?', [$guardCt]),
    db()->value('SELECT ai_model FROM tisk_text WHERE ct = ?', [$guardCt])], [7, $verifier]);
$guard->saveResult('tisk', $guardCt, ['zavaznost' => 5, 'titulek' => 'y', 'citace' => ['Lhůta činí 4 roky.']], $verifier);
check('ověřovací model rozbor přepsat smí', (int)db()->value('SELECT ai_score FROM tisk_text WHERE ct = ?', [$guardCt]), 5);

$bskyUrl = 'https://hlidac.test/tisky/221';
$fitted = Hlidac\Notifier\Bluesky::fit(str_repeat('á', 300), $bskyUrl);
check('Bluesky: text i s odkazem do 300 znaků', mb_strlen($fitted . "\n" . $bskyUrl) <= 300 && str_ends_with($fitted, '…'), true);
check('Bluesky: krátký text beze změny', Hlidac\Notifier\Bluesky::fit('Krátce', $bskyUrl), 'Krátce');
check('testy nepíšou do data/posts.log', is_file(APP_ROOT . '/data/posts.log') && filemtime(APP_ROOT . '/data/posts.log') >= $_SERVER['REQUEST_TIME'], false);

echo "Pátá várka: vrácený tisk, nával podle dne, PN před předlohou, nové období\n";
check('vrácený tisk (rozpočet 17)', Hlidac\Analyzer::analyze([
    ['typ_stavu' => 1, 'akce_id' => 29, 'datum' => '2025-11-12 00:00:00'],
    ['typ_stavu' => 6, 'akce_id' => 24, 'datum' => '2025-11-26 00:00:00'],
])['faze'], 'Vráceno předkladateli');

$surgeDay = date('Y-m-d', strtotime('-1 day'));
$addPn = function (int $from, int $count, int $ct, string $day): void {
    for ($i = 0; $i < $count; $i++) {
        db()->run('INSERT INTO pn (id_dokument, cislo, ct, id_osoba, osoba, cas) VALUES (?, ?, ?, ?, ?, ?)',
            [$from + $i, $from + $i, $ct, 1, 'Test Poslanec', $day . ' 1' . ($i % 10) . ':00:00']);
    }
};
$builder = new Hlidac\AlertBuilder(db(), hlidacConfig());
$addPn(970000, 6, 221, $surgeDay);
$builder->run();
check('6 PN za den ještě není nával', (int)db()->value("SELECT COUNT(*) FROM alerts WHERE kind = 'surge' AND ct = 221 AND event_at LIKE ?", [$surgeDay . '%']), 0);
$addPn(970100, 5, 221, $surgeDay);
$builder->run();
check('11 PN téhož dne ve dvou bězích = nával', (int)db()->value("SELECT COUNT(*) FROM alerts WHERE kind = 'surge' AND ct = 221 AND event_at LIKE ?", [$surgeDay . '%']), 1);
$addPn(970200, 3, 221, $surgeDay);
$builder->run();
check('další PN téhož dne už druhý nával nevytvoří', (int)db()->value("SELECT COUNT(*) FROM alerts WHERE kind = 'surge' AND ct = 221 AND event_at LIKE ?", [$surgeDay . '%']), 1);

$addPn(970300, 1, 9999, $surgeDay);
$builder->run();
check('PN k tisku, který ještě není v přehledu, čeká', db()->value('SELECT 1 FROM pn_seen WHERE id_dokument = 970300'), null);
db()->run('DELETE FROM pn WHERE id_dokument >= 970000 AND id_dokument < 980000');

// přelom období: tisk 221 nového období je jiný návrh než tisk 221 minulého
$keysBefore = (int)db()->value('SELECT COUNT(*) FROM alerts');
db()->run('UPDATE alerts SET obdobi = 9');
db()->setMeta('obdobi_id', '999');
db()->setMeta('obdobi_cislo', '9');
(new Hlidac\Importer(db(), new Hlidac\Source('', $tmp . '/cache-switch', $tmp . '/src')))->run();
check('stará upozornění dostala klíč s obdobím', (int)db()->value("SELECT COUNT(*) FROM alerts WHERE alert_key LIKE '9/%'"), $keysBefore);
check('texty minulého období smazané', [(int)db()->value('SELECT COUNT(*) FROM tisk_text'), (int)db()->value('SELECT COUNT(*) FROM pn_text')], [0, 0]);
check('nové období začne bootstrapem', db()->getMeta('bootstrapped'), null);
$created = (new Hlidac\AlertBuilder(db(), hlidacConfig()))->run();
check('nová upozornění vzniknou i se stejným číslem tisku', $created > 0 && db()->value("SELECT kind FROM alerts WHERE alert_key = 'newbill:221'") !== null, true);
check('nová upozornění jsou bootstrap (neposílají se)', (int)db()->value("SELECT COUNT(*) FROM alerts WHERE obdobi = 10 AND posted <> 2"), 0);
$old = db()->one("SELECT * FROM alerts WHERE alert_key LIKE '9/%' LIMIT 1");
check('staré upozornění vede na psp.cz', str_contains(Hlidac\View::alertUrl($old + ['is_past_period' => true]), 'psp.cz') && str_contains(Hlidac\View::alertUrl($old + ['is_past_period' => true]), 'o=9'), true);
check('detail tisku ukazuje jen upozornění aktuálního období', array_unique(array_map(fn($a) => (int)$a['obdobi'],
    App\Models\Alert::query()->currentPeriod()->where('ct', 221)->get()->map->getAttributes()->all())), [10]);

echo "Šestá várka: titulek jen s citací, lhůta a zpětnost, obnova textu tisku\n";
$hlCt = (int)db()->value("SELECT ct FROM tisky WHERE faze <> 'Vyhlášeno' ORDER BY ct LIMIT 1");
db()->run('DELETE FROM tisk_text WHERE ct = ?', [$hlCt]);
db()->run("INSERT INTO tisk_text (ct, docx_url, text, chars, signals, fetched_at, ai_json, ai_score, ai_at) VALUES (?, 'https://www.psp.cz/x', 'text', 4, '{}', ?, ?, 7, ?)",
    [$hlCt, date('Y-m-d H:i:s', time() - 8 * 86400), json_encode(['titulek' => 'Nález bez citace', 'citace' => []]), date('Y-m-d H:i:s')]);
check('nález bez ověřené citace nedá titulek na homepage', app(App\Services\BillPresentationService::class)->headline($hlCt), null);
db()->run('UPDATE tisk_text SET ai_json = ? WHERE ct = ?', [json_encode(['titulek' => 'Nález', 'perex' => '', 'citace' => ['doslovná věta']]), $hlCt]);
check('nález s citací titulek dá', app(App\Services\BillPresentationService::class)->headline($hlCt)['title'] ?? null, 'Nález');
check('text starší než týden se ověří proti psp.cz', in_array($hlCt, array_map('intval', array_column(Hlidac\TiskText::staleQueue(db(), 1000, 7), 'ct')), true), true);
db()->run('UPDATE tisk_text SET checked_at = ? WHERE ct = ?', [date('Y-m-d H:i:s'), $hlCt]);
check('právě ověřený text se znovu neověřuje', in_array($hlCt, array_map('intval', array_column(Hlidac\TiskText::staleQueue(db(), 1000, 7), 'ct')), true), false);

db()->run("UPDATE tisk_text SET signals = ?, ai_score = NULL, ai_at = NULL WHERE ct = ?", [json_encode(['retro' => ['x'], 'retro_deadline' => ['x']]), $hlCt]);
(new Hlidac\Scorer(db(), hlidacConfig()))->scoreBills();
$keys = array_column(json_decode((string)db()->value('SELECT reasons FROM tisky WHERE ct = ?', [$hlCt]), true) ?: [], 'key');
check('zpětnost bez změny lhůty v textu nedá body za lhůtu', in_array('retro_deadline', $keys, true), false);
db()->run("UPDATE tisk_text SET signals = ? WHERE ct = ?", [json_encode(['retro' => ['x'], 'retro_deadline' => ['x'], 'deadline' => ['y']]), $hlCt]);
(new Hlidac\Scorer(db(), hlidacConfig()))->scoreBills();
$keys = array_column(json_decode((string)db()->value('SELECT reasons FROM tisky WHERE ct = ?', [$hlCt]), true) ?: [], 'key');
check('změna lhůty i zpětné použití body dají', in_array('retro_deadline', $keys, true), true);
db()->run('DELETE FROM tisk_text WHERE ct = ?', [$hlCt]);

echo "Metodika skóre (#7): postup tisku, pozdní PN, ověřené AI u PN, přílepek proti textu návrhu\n";
$reasonsOf = function (int $ct): array {
    $out = [];
    foreach (json_decode((string)db()->value('SELECT reasons FROM tisky WHERE ct = ?', [$ct]), true) ?: [] as $r) {
        $out[$r['key']] = $r;
    }
    return $out;
};
$rescore = fn() => (new Hlidac\Scorer(db(), hlidacConfig()))->scoreBills();
$progressSteps = db()->all('SELECT * FROM kroky WHERE ct = 221 AND typ_stavu IN (2, 3, 4, 5, 21, 64, 12, 14, 63, 8, 13, 11)');
db()->run('DELETE FROM kroky WHERE ct = 221 AND typ_stavu IN (2, 3, 4, 5, 21, 64, 12, 14, 63, 8, 13, 11)');
db()->run('UPDATE tisky SET p90ok = 0 WHERE ct = 221');
$rescore();
check('poslanecký návrh v 1. čtení: 5 b.', $reasonsOf(221)['mp_bill']['points'] ?? null, 5);
db()->run("INSERT INTO kroky (id_hist, id_tisk, ct, datum, typ_stavu, stav, akce_id, akce) VALUES (990101, 43343, 221, '2026-10-01 00:00:00', 2, '', 0, '')");
$rescore();
check('poslanecký návrh ve 2. čtení: 15 b.', $reasonsOf(221)['mp_bill']['points'] ?? null, 15);
db()->run('DELETE FROM kroky WHERE id_hist = 990101');
foreach ($progressSteps as $k) {
    db()->insertMany('kroky', array_keys(array_filter($k, 'is_string', ARRAY_FILTER_USE_KEY)), [$k]);
}
db()->run("UPDATE tisky SET p90 = 1, p90ok = 0, faze = 'Vzat zpět' WHERE ct = 221");
$rescore();
check('§ 90/2 u vzatého zpět bez bodů', isset($reasonsOf(221)['p90_requested']), false);
db()->run("UPDATE tisky SET p90 = 0, faze = '1. čtení' WHERE ct = 221");

// AI u PN: neověřený vysoký nález nejvýš 5/10, po ověření naplno
$pnRow = db()->one('SELECT id_dokument FROM pn WHERE ct = 221 LIMIT 1');
db()->run('DELETE FROM pn_text WHERE id_dokument = ?', [(int)$pnRow['id_dokument']]);
db()->run("INSERT INTO pn_text (id_dokument, ct, cislo, text, chars, laws, signals, rule_score, ai_json, ai_score, ai_model, ai_at)
    VALUES (?, 221, 1, 'x', 1, '', '{}', 0, ?, 9, 'claude-haiku-5-5', ?)", [(int)$pnRow['id_dokument'], json_encode(['citace' => ['x']]), date('Y-m-d H:i:s')]);
$rescore();
check('neověřený nález PN 9/10 se počítá jako 5/10', $reasonsOf(221)['ai']['points'] ?? null, 15);
db()->run('UPDATE pn_text SET ai_model = ? WHERE id_dokument = ?', [Hlidac\AiReviewer::verifyModel(hlidacConfig()), (int)$pnRow['id_dokument']]);
$rescore();
check('ověřený nález PN 9/10 naplno', $reasonsOf(221)['ai']['points'] ?? null, 27);

// přílepek: PN mění zákon, který mění i samotný text návrhu → není přílepek
db()->run("UPDATE pn_text SET ai_score = NULL, ai_json = NULL, ai_model = NULL, signals = ?, rule_score = 20 WHERE id_dokument = ?",
    [json_encode(['other_laws' => ['89/2012']]), (int)$pnRow['id_dokument']]);
$rescore();
check('PN měnící jiný zákon = přílepek', isset($reasonsOf(221)['pn_other_law']), true);
$billBackup = db()->one('SELECT * FROM tisk_text WHERE ct = 221');
db()->run('DELETE FROM tisk_text WHERE ct = 221');
db()->run("INSERT INTO tisk_text (ct, text, chars, signals) VALUES (221, 'x', 1, ?)", [json_encode(['laws' => ['89/2012']])]);
$rescore();
check('zákon mění i text návrhu → není přílepek', isset($reasonsOf(221)['pn_other_law']), false);
check('zákony z textu návrhu', Hlidac\TiskText::analyze("Část druhá\nV zákoně č. 89/2012 Sb., občanský zákoník, se § 1 mění takto:")['signals']['laws'] ?? [], ['89/2012']);
db()->run('DELETE FROM pn_text WHERE id_dokument = ?', [(int)$pnRow['id_dokument']]);
db()->run('DELETE FROM tisk_text WHERE ct = 221');
if ($billBackup !== null) {
    db()->insertMany('tisk_text', array_keys(array_filter($billBackup, 'is_string', ARRAY_FILTER_USE_KEY)), [$billBackup]);
}
$rescore();

// pozdní PN relativně k tisku
$w = Hlidac\Scorer::weights(hlidacConfig());
check('výchozí váhy: citlivá oblast 5, podíl pozdních PN 50 %', [$w['topic'], $w['late_pn_share'], $w['mp_bill_early']], [5, 0.5, 5]);

echo "Stránky\n";
foreach (['index.php' => [], 'tisky.php' => ['f' => 'p90'], 'tisk.php' => ['ct' => '90'], 'upozorneni.php' => [], 'metodika.php' => [], 'rss.php' => []] as $page => $get) {
    try {
        $kernel = app(Illuminate\Contracts\Http\Kernel::class);
        $path = match ($page) {
            'index.php' => '/', 'tisky.php' => '/tisky', 'tisk.php' => '/tisky/90',
            'upozorneni.php' => '/upozorneni', 'metodika.php' => '/metodika', 'rss.php' => '/rss',
        };
        $request = Illuminate\Http\Request::create($path, 'GET', $get);
        $response = $kernel->handle($request);
        $html = $response->getContent();
        check("$page se vyrenderuje", $response->getStatusCode() === 200 && strlen($html) > 500 && !str_contains($html, 'Warning') && !str_contains($html, 'Fatal'), true);
        file_put_contents($tmp . '/' . str_replace('.php', '.html', $page), $html);
        $kernel->terminate($request, $response);
    } catch (Throwable $ex) {
        check("$page se vyrenderuje", $ex->getMessage(), 'bez chyby');
    }
}

echo "Náhledy pro sociální sítě\n";
$tiskHtml = (string)@file_get_contents($tmp . '/tisk.html');
check('tisk má og:image s vlastním obrázkem', (bool)preg_match('~<meta property="og:image" content="[^"]*og\?ct=90&amp;v=[0-9a-f]{16}"~', $tiskHtml), true);
check('tisk má twitter:card pro velký obrázek', str_contains($tiskHtml, '<meta name="twitter:card" content="summary_large_image">'), true);
check('tisk má og:url na detail', (bool)preg_match('~<meta property="og:url" content="[^"]*tisky/90"~', $tiskHtml), true);
check('přehled má obrázek stránky', str_contains((string)@file_get_contents($tmp . '/index.html'), 'og?p=prehled'), true);
check('název bez výčtu předkladatelů',
    Hlidac\OgCard::cleanTitle('Návrh poslanců Radka Vondráčka, Davida Pražáka a Filipa Turka na vydání zákona, kterým se mění zákon č. 159/2006 Sb., o střetu zájmů, ve znění pozdějších předpisů'),
    'Zákon, kterým se mění zákon č. 159/2006 Sb., o střetu zájmů');
check('vládní návrh ústavního zákona', Hlidac\OgCard::cleanTitle('Vládní návrh ústavního zákona o bezpečnosti České republiky'), 'Ústavní zákon o bezpečnosti České republiky');
check('krátký název zůstane', Hlidac\OgCard::cleanTitle('Návrh zákona'), 'Návrh zákona');
if (Hlidac\OgCard::available()) {
    $font = public_path('assets/fonts/NotoSans-Bold.ttf');
    $lines = Hlidac\OgCard::fit(str_repeat('slovo ', 200), 40, $font, 600, 2, $complete);
    check('dlouhý text se zalomí a zkrátí', [count($lines), $complete, str_ends_with($lines[1], '…')], [2, false, true]);
    $card = Hlidac\OgCard::forBill(db()->one('SELECT * FROM tisky WHERE ct = 90') + ['reasons_list' => []], hlidacConfig());
    check('karta tisku má skóre a důvod', [$card['score'] !== null, str_starts_with($card['text'], 'Běžné') || str_starts_with($card['text'], 'Proč:')], [true, true]);
    $png = Hlidac\OgCard::cached($card, $tmp . '/og');
    $size = getimagesize($png);
    check('obrázek má 1200 × 630 px', [$size[0] ?? 0, $size[1] ?? 0, $size['mime'] ?? ''], [1200, 630, 'image/png']);
    check('druhé volání použije cache', Hlidac\OgCard::cached($card, $tmp . '/og'), $png);
} else {
    echo "  (GD s FreeType není k dispozici, kreslení obrázku se netestuje)\n";
}
check('záložní obrázek existuje', is_file(public_path('assets/og-default.png')), true);
echo "\nVýstupy: $tmp\n";
echo $failures === 0 ? "VŠE OK\n" : "CHYB: $failures\n";
exit($failures === 0 ? 0 : 1);
