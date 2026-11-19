@extends('layouts.app')
@section('content')
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
      <?php if (isset($issued['token'])): ?>
        <div class="token-box" role="status">
          <p><strong>Váš token</strong> – zkopírujte si ho hned, znovu ho už neuvidíte (ukládáme jen jeho otisk):</p>
          <div class="token-row"><code id="mcp-token"><?= e($issued['token']) ?></code><button type="button" class="btn" data-copy="mcp-token">Kopírovat</button></div>
          <p class="small muted">Níže v návodu je token už doplněný. Token nikomu neposílejte a nezveřejňujte; kdo ho zná, čerpá vaše limity. Při úniku si vytvořte nový a <?php if ($issues !== ''): ?><a href="<?= e($issues) ?>">napište nám</a>, ať starý zrušíme<?php else: ?>dejte vědět, ať starý zrušíme<?php endif; ?>.</p>
        </div>
      <?php elseif ($canIssue): ?>
        <?php if (isset($issued['error'])): ?><p class="token-error" role="alert"><?= e($issued['error']) ?></p><?php endif; ?>
        <form class="token-form" method="post" action="{{ route('mcp-issue', [], false) }}#pristup">
          <label for="ucel">K čemu MCP použijete? <span class="muted small">(nepovinné, pomůže nám s plánováním; nepište osobní údaje)</span></label>
          <input id="ucel" name="ucel" type="text" maxlength="80" placeholder="např. rešerše k článku, výzkum, vlastní projekt" autocomplete="off">
          <input type="text" name="web" value="" tabindex="-1" autocomplete="off" class="hp" aria-hidden="true">
          <input type="hidden" name="formular" value="<?= e($formToken) ?>">
          <button type="submit" class="btn">Získat token</button>
          <p class="small muted">Bez registrace a bez e-mailu. Z jedné adresy nejvýš <?= (int)$tokenLimits['per_ip'] ?> tokeny denně. Token se zobrazí jen jednou.</p>
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
      <p>Data jsou stejná jako na webu, ale odpověď skládá váš asistent a může se mýlit: špatně shrnout, přehlédnout souvislost nebo si citaci domyslet. Doslovné citace si nechte ověřit nástrojem <code>over_citaci</code> a důležité údaje zkontrolujte na psp.cz. Hodnocení AI v datech (skóre, titulky) má stejná omezení jako na webu, viz <a href="/metodika#odpovednost">omezení odpovědnosti</a>. Jiná strojová data (JSON, RSS) jsou na stránce <a href="/data">Data a API</a>.</p>
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
@endsection
