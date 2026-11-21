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

// vydání tokenu (formulář níže); výsledek se ukáže jen v této odpovědi, nic se neukládá do mezipaměti
$tokens = new Hlidac\McpTokens(db(), config());
$canIssue = $tokens->available();
$issued = null;
if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'POST' && $canIssue) {
    $form = array_map(fn($v) => is_string($v) ? $v : '', $_POST);
    $issued = $tokens->issue($form, (string)($_SERVER['REMOTE_ADDR'] ?? ''), $_SERVER);
    // stránka s čerstvým tokenem nenačítá měřicí skript
    View::$noAnalytics = isset($issued['token']);
    if (!headers_sent()) {
        header('Cache-Control: no-store');
    }
}
$tokenValue = $issued['token'] ?? 'VÁŠ_TOKEN';

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
        ['hledej_v_textech', 'hledání v textech návrhů a PN podle slov i podle významu, i podle měněného zákona'],
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
        ['hledej_v_projevech', 'hledání ve všech projevech podle slov i podle významu'],
        ['zmeny_projevu', 'projevy, jejichž text se po zveřejnění změnil (autorizace, korektury)'],
        ['najdi_osobu', 'poslanec nebo člen vlády podle jména'],
        ['program_schuze', 'body pořadu schůze Sněmovny'],
    ],
    'Hlasování' => [
        ['hlasovani_k_tisku', 'všechna hlasování k tisku s výsledky a rozdělením podle klubů'],
        ['get_hlasovani', 'detail jednoho hlasování: kluby, hlas každého poslance a projevy, které se k němu vztahují'],
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
      <li><a href="#jak-funguje">Jak to funguje</a></li>
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
        <li>Hledání podle slov i podle významu: najde i projevy, které o tématu mluví jinými slovy.</li>
        <li>Ověření citace: asistent si může zkontrolovat, že výrok opravdu zazněl, než ho někomu přisoudí.</li>
      </ul>
    </section>

    <section class="card pad" id="jak-funguje">
      <h2>Jak to funguje</h2>
      <h3>Kdo co dělá</h3>
      <ul>
        <li><b>Váš asistent</b> (Claude, ChatGPT a další) přemýšlí, co hledat, čte výsledky a píše odpověď. Běží u vás a na váš účet.</li>
        <li><b>Náš server</b> jen vydává data z databáze: tisk, paragraf, projev, hlasování. Žádná umělá inteligence u nás odpovědi nepíše ani neupravuje.</li>
        <li>Váš rozhovor s asistentem nevidíme. K nám dorazí jen jednotlivá volání nástrojů, například „najdi projevy o zdražování energií“ nebo „dej mi § 44a tisku 221“.</li>
      </ul>
      <h3>Hledání podle slov a podle významu</h3>
      <p>Nástroje <code>hledej_v_projevech</code> a <code>hledej_v_textech</code> hledají dvěma způsoby a výsledky spojí:</p>
      <ul>
        <li><b>Podle slov</b> (fulltext): najde místa, kde hledaná slova opravdu zazněla, bez ohledu na diakritiku. Probíhá celé u nás.</li>
        <li><b>Podle významu</b>: najde i místa řečená jinak. Dotaz „zdražování energií“ tak najde i projev o „rostoucích cenách elektřiny“.</li>
      </ul>
      <p>Hledání podle významu funguje takto: texty projevů a paragrafů máme předem převedené na „otisk významu“ (řadu čísel, takzvaný embedding). Při hledání se stejně převede i váš dotaz a v databázi se najdou nejbližší otisky. Převod dělá pomocný model <code>text-embedding-3-large</code> společnosti OpenAI. Není to chatovací AI: nic nepíše ani nehodnotí, jen převádí text na čísla. Dotaz musí převést stejný model jako uložené texty, proto to nemůže udělat váš asistent.</p>
      <h3>Co odchází mimo náš server</h3>
      <ul>
        <li><b>Při hledání podle významu odchází text hledaného dotazu do OpenAI</b> (několik slov, která asistent hledá). Posílá ho náš server svým jménem; OpenAI nedostane váš token, adresu ani zbytek rozhovoru. Zpracování se řídí podmínkami OpenAI pro jejich API.</li>
        <li>Nechcete-li to, řekněte asistentovi, ať hledá jen podle slov (parametr <code>rezim="fulltext"</code>). Ostatní nástroje nic ven neposílají.</li>
        <li>Hledání podle významu má denní strop pro všechny uživatele dohromady. Když se vyčerpá nebo OpenAI neodpoví do 3 sekund, hledá se jen podle slov; odpověď vždy uvádí, který způsob se použil (<code>rezim_pouzity</code>).</li>
      </ul>
      <h3>Co si zaznamenáváme</h3>
      <p>U každého volání jen to, který nástroj se volal, kterým tokenem, kdy a jak dlouho trval (kvůli limitům a ladění). Text dotazů ani odpovědí neukládáme.</p>
    </section>

    <section class="card pad" id="pristup">
      <h2>Přístup</h2>
      <dl class="docs-dl">
        <dt>Adresa</dt><dd><code><?= e($mcpUrl) ?></code></dd>
        <dt>Přenos</dt><dd>Streamable HTTP, jen čtení</dd>
        <dt>Přihlášení</dt><dd>hlavička <code>Authorization: Bearer &lt;token&gt;</code></dd>
        <dt>Limity</dt><dd>60 volání za minutu a 2 000 za den na jeden token</dd>
      </dl>
      <?php if (isset($issued['token'])): ?>
        <div class="token-box" role="status">
          <p><strong>Váš token</strong> – zkopírujte si ho hned, znovu ho už neuvidíte (ukládáme jen jeho otisk):</p>
          <div class="token-row"><code id="mcp-token"><?= e($issued['token']) ?></code><button type="button" class="btn" data-copy="mcp-token">Kopírovat</button></div>
          <p class="small muted">Níže v návodu je token už doplněný. Token nikomu neposílejte a nezveřejňujte; kdo ho zná, čerpá vaše limity. Při úniku si vytvořte nový a <?php if ($issues !== ''): ?><a href="<?= e($issues) ?>">napište nám</a>, ať starý zrušíme<?php else: ?>dejte vědět, ať starý zrušíme<?php endif; ?>.</p>
        </div>
      <?php elseif ($canIssue): ?>
        <?php if (isset($issued['error'])): ?><p class="token-error" role="alert"><?= e($issued['error']) ?></p><?php endif; ?>
        <form class="token-form" method="post" action="mcp.php#pristup">
          <label for="ucel">K čemu MCP použijete? <span class="muted small">(nepovinné, pomůže nám s plánováním; nepište osobní údaje)</span></label>
          <input id="ucel" name="ucel" type="text" maxlength="80" placeholder="např. rešerše k článku, výzkum, vlastní projekt" autocomplete="off">
          <input type="text" name="web" value="" tabindex="-1" autocomplete="off" class="hp" aria-hidden="true">
          <input type="hidden" name="formular" value="<?= e($tokens->formToken((string)($_SERVER['REMOTE_ADDR'] ?? ''))) ?>">
          <button type="submit" class="btn">Získat token</button>
          <p class="small muted">Bez registrace a bez e-mailu. Z jedné adresy nejvýš <?= (int)$tokens->limits()['per_ip'] ?> tokeny denně. Token se zobrazí jen jednou. Token, který do týdne nepoužijete nebo ho tři měsíce nepoužíváte, přestane platit; pak si vytvořte nový.</p>
        </form>
      <?php else: ?>
        <p>Token zatím vydáváme na požádání.<?php if ($issues !== ''): ?> <a href="<?= e($issues) ?>">Napište nám</a>, k čemu ho chcete použít; token pak pošleme soukromě, nikdy ho nezveřejňujte v issue ani jinde.<?php endif; ?></p>
      <?php endif; ?>
    </section>

    <section class="card pad" id="pripojeni">
      <h2>Připojení</h2>
      <h3>Claude Code</h3>
      <pre class="code"><code>claude mcp add --transport http hlidac <?= e($mcpUrl) ?> --header "Authorization: Bearer <?= e($tokenValue) ?>"</code></pre>
      <h3>Claude Desktop a další klienti s konfiguračním souborem</h3>
      <p class="small">Klienti, kteří neumí poslat vlastní hlavičku, se připojí přes most <code>mcp-remote</code> (potřebuje Node.js):</p>
      <pre class="code"><code>{
  "mcpServers": {
    "hlidac": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "<?= e($mcpUrl) ?>",
               "--header", "Authorization: Bearer <?= e($tokenValue) ?>"]
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
<script>
// kopírování tokenu do schránky
document.querySelectorAll('[data-copy]').forEach(function (b) {
  b.addEventListener('click', function () {
    var t = document.getElementById(b.getAttribute('data-copy')).textContent;
    (navigator.clipboard ? navigator.clipboard.writeText(t) : Promise.reject()).then(function () { b.textContent = 'Zkopírováno'; }, function () { b.textContent = 'Označte a zkopírujte ručně'; });
  });
});
</script>
<?php
View::footer();
