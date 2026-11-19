<?php
// Napojení pro AI asistenty (MCP server ze služby rag/, viz rag/README.md): co umí, jak se připojit,
// přehled nástrojů a ukázkové dotazy. Server běží zvlášť (služba mcp v Dockeru), přístup je na token.

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\View;

// adresa MCP serveru: config.php → mcp_url, jinak subdoména mcp. u adresy webu
$mcpUrl = (string)config('mcp_url', '');
if ($mcpUrl === '') {
    $host = (string)parse_url(siteUrl(), PHP_URL_HOST);
    $mcpUrl = $host !== '' ? 'https://mcp.' . preg_replace('~^www\.~', '', $host) . '/mcp' : '';
}
$issues = (string)config('issues_url', '');

// nástroje podle okruhů: [název, popis]
$tools = [
    'Návrhy zákonů a zákony' => [
        ['get_tisk', 'detail sněmovního tisku: předkladatel, fáze, skóre s důvody, měněné zákony, číslo ve Sbírce'],
        ['seznam_tisku', 'seznam návrhů s hledáním a řazením podle skóre nebo data'],
        ['tisky_menici_zakon', 'tisky a pozměňovací návrhy, které mění daný zákon (např. 586/1992)'],
        ['tisk_podle_sbirky', 'tisk, který byl vyhlášen ve Sbírce pod daným číslem'],
    ],
    'Texty návrhů a pozměňovacích návrhů' => [
        ['osnova_textu', 'osnova textu (části, články, paragrafy, body), původní i schválené znění'],
        ['get_cel', 'jeden celek textu, např. „§ 17c“ nebo „Čl. I“'],
        ['hledej_v_textech', 'fulltext v textech návrhů a PN, i podle měněného zákona'],
    ],
    'Pozměňovací návrhy' => [
        ['pn_k_tisku', 'pozměňovací návrhy k tisku s autory a hodnocením AI'],
        ['get_pn', 'detail pozměňovacího návrhu včetně projevů, které ho zmiňují'],
    ],
    'Rozprava a projevy (stenoprotokoly)' => [
        ['rozprava', 'celá rozprava k tisku v pořadí, jak zazněla, s reakcemi a zmíněnými PN'],
        ['projevy_k_tisku', 'rejstřík projevů k tisku s filtry podle řečníka, klubu a data'],
        ['projevy_recnika', 'projevy jedné osoby, i s hledanými slovy'],
        ['get_projev', 'celý text projevu s historií verzí a stavem autorizace'],
        ['hledej_v_projevech', 'fulltextové hledání ve všech projevech'],
        ['zmeny_projevu', 'projevy, jejichž text se po zveřejnění změnil (autorizace, korektury)'],
        ['najdi_osobu', 'poslanec nebo člen vlády podle jména'],
        ['program_schuze', 'body pořadu schůze Sněmovny'],
    ],
    'Hlasování' => [
        ['hlasovani_k_tisku', 'všechna hlasování k tisku s výsledky a rozdělením podle klubů'],
        ['get_hlasovani', 'detail jednoho hlasování a projevy, které se k němu vztahují'],
    ],
    'Ověření a stav' => [
        ['over_citaci', 'ověří, že doslovná citace opravdu zazněla v projevu nebo stojí v textu zákona či PN'],
        ['stav_dat', 'stav dat a poslední synchronizace'],
    ],
];
$toolCount = array_sum(array_map('count', $tools));

View::header('Pro AI asistenty (MCP)', 'mcp', 'Napojení AI asistentů (Claude a další) na data Hlídače zákonů přes MCP: návrhy zákonů, texty, pozměňovací návrhy, rozpravy a hlasování.');
?>
<section class="hero">
  <div>
    <h1>Pro AI asistenty (MCP)</h1>
    <p class="lead">Váš AI asistent (Claude a další, které podporují Model Context Protocol) se může ptát přímo naší databáze: návrhů zákonů, jejich textů, pozměňovacích návrhů, rozprav ve Sněmovně a hlasování. Jen čtení, <?= $toolCount ?> nástrojů.</p>
  </div>
</section>

<div class="docs">
  <nav class="docs-toc card pad" aria-label="Obsah">
    <strong>Obsah</strong>
    <ol>
      <li><a href="#co-umi">Co to umí</a></li>
      <li><a href="#pristup">Přístup</a></li>
      <li><a href="#pripojeni">Připojení</a></li>
      <li><a href="#nastroje">Nástroje</a></li>
      <li><a href="#priklady">Ukázkové dotazy</a></li>
      <li><a href="#upozorneni">Upozornění</a></li>
    </ol>
  </nav>

  <div class="docs-body">
    <section class="card pad" id="co-umi">
      <h2>Co to umí</h2>
      <p>MCP (Model Context Protocol) je standard, kterým se AI asistent připojí k externím datům. Místo aby odpovídal z paměti, zavolá naše nástroje a odpověď opře o skutečné údaje: tisk a jeho skóre, konkrétní paragraf návrhu, kdo co řekl v rozpravě, jak se hlasovalo.</p>
      <ul>
        <li>Návrhy zákonů 10. volebního období se skóre a důvody, jako na webu.</li>
        <li>Texty návrhů a pozměňovacích návrhů po paragrafech, včetně schváleného znění po 3. čtení.</li>
        <li>Stenoprotokoly: projevy, celé rozpravy k tiskům, změny textu po autorizaci.</li>
        <li>Hlasování k tiskům s rozdělením podle klubů.</li>
        <li>Ověření citace: asistent si může zkontrolovat, že výrok opravdu zazněl, než ho někomu přisoudí.</li>
      </ul>
    </section>

    <section class="card pad" id="pristup">
      <h2>Přístup</h2>
      <dl class="docs-dl">
        <dt>Adresa</dt><dd><code><?= e($mcpUrl) ?></code></dd>
        <dt>Přenos</dt><dd>Streamable HTTP, jen čtení</dd>
        <dt>Přihlášení</dt><dd>hlavička <code>Authorization: Bearer &lt;token&gt;</code></dd>
        <dt>Limity</dt><dd>60 volání za minutu a 2 000 za den na jeden token</dd>
      </dl>
      <p>Token zatím vydáváme na požádání (novináři, výzkum, neziskové organizace i jednotlivci).<?php if ($issues !== ''): ?> <a href="<?= e($issues) ?>">Napište nám</a>, k čemu ho chcete použít; token pak pošleme soukromě, nikdy ho nezveřejňujte v issue ani jinde.<?php endif; ?></p>
    </section>

    <section class="card pad" id="pripojeni">
      <h2>Připojení</h2>
      <h3>Claude Code</h3>
      <pre class="code"><code>claude mcp add --transport http hlidac <?= e($mcpUrl) ?> --header "Authorization: Bearer VÁŠ_TOKEN"</code></pre>
      <h3>Claude Desktop a další klienti s konfiguračním souborem</h3>
      <p class="small">Klienti, kteří neumí poslat vlastní hlavičku, se připojí přes most <code>mcp-remote</code> (potřebuje Node.js):</p>
      <pre class="code"><code>{
  "mcpServers": {
    "hlidac": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "<?= e($mcpUrl) ?>",
               "--header", "Authorization: Bearer VÁŠ_TOKEN"]
    }
  }
}</code></pre>
      <p class="muted small">Token patří jen do vaší konfigurace. Kdo ho zná, čerpá vaše limity; při úniku napište a vydáme nový.</p>
    </section>

    <section class="card pad" id="nastroje">
      <h2>Nástroje</h2>
      <p class="small muted">Podrobné parametry si asistent načte sám ze serveru; tady je přehled, co je k dispozici.</p>
      <?php foreach ($tools as $group => $list): ?>
        <h3><?= e($group) ?></h3>
        <div class="table-wrap"><table class="docs-table">
          <tbody>
            <?php foreach ($list as [$name, $desc]): ?>
              <tr><td class="nowrap"><code><?= e($name) ?></code></td><td><?= e($desc) ?></td></tr>
            <?php endforeach; ?>
          </tbody>
        </table></div>
      <?php endforeach; ?>
    </section>

    <section class="card pad" id="priklady">
      <h2>Ukázkové dotazy</h2>
      <ul>
        <li>„Jak probíhala rozprava k tisku 221 a kdo v ní mluvil o zkrácení lhůty pro vymáhání dotací? Citace ověř.“</li>
        <li>„Které pozměňovací návrhy k tisku 311 mění jiný zákon než ten, který se projednává?“</li>
        <li>„Jak hlasovaly jednotlivé kluby o novele zákona o daních z příjmů?“</li>
        <li>„Co přesně mění tisk 221 v § 44a rozpočtových pravidel a od kdy to platí?“</li>
        <li>„Které projevy se po zveřejnění změnily a jak?“</li>
      </ul>
    </section>

    <section class="card pad" id="upozorneni">
      <h2>Upozornění</h2>
      <p>Data jsou stejná jako na webu, ale odpověď skládá váš asistent a může se mýlit: špatně shrnout, přehlédnout souvislost nebo si citaci domyslet. Doslovné citace si nechte ověřit nástrojem <code>over_citaci</code> a důležité údaje zkontrolujte na psp.cz. Hodnocení AI v datech (skóre, titulky) má stejná omezení jako na webu, viz <a href="metodika.php#odpovednost">omezení odpovědnosti</a>. Jiná strojová data (JSON, RSS) jsou na stránce <a href="data.php">Data a API</a>.</p>
    </section>
  </div>
</div>
<?php
View::footer();
