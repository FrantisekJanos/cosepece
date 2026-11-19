<?php
$issues = (string)hlidacConfig('issues_url', '');
        ?>
<footer class="foot">
  <p class="foot-disclaimer"><strong>Upozornění:</strong> Obsah vzniká automaticky z otevřených dat a část rozborů (shrnutí, titulky a hodnocení závažnosti textů) připravuje umělá inteligence, která se může mýlit. Údaje mají informativní povahu, nejsou právní radou ani oficiálním stanoviskem Sněmovny a před dalším použitím je ověřte v původních zdrojích. <a href="{{ route('methodology', [], false) }}#odpovednost">Omezení odpovědnosti</a></p>
  <p>Zdroj: <a href="https://www.psp.cz/sqw/hp.sqw?k=1300">otevřená data Poslanecké sněmovny</a> (tisky, sněmovní dokumenty, poslanci). Počty PN jsou podané písemné pozměňovací návrhy; Sněmovna neeviduje, které z nich byly platně podány ve 2. čtení. Příznaky se počítají automaticky z historie tisku.</p>
  <p><a href="{{ route('changes', [], false) }}">Co jsme změnili</a> · <a href="{{ route('laws', [], false) }}">Zákony</a> · <a href="{{ route('api-docs', [], false) }}">Data a API</a> · <a href="{{ route('mcp-info', [], false) }}">Pro AI asistenty (MCP)</a> · <a href="https://github.com/FrantisekJanos/cosepece">Zdrojový kód (AGPL-3.0)</a></p>
  <?php if ($issues !== ''): ?>
    <p><a href="{{ $issues }}">Našli jste chybu nebo máte podnět? Napište nám.</a></p>
  <?php endif; ?>
</footer>
</main>
</body>
</html>
