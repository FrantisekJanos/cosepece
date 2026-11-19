<?php
$issues = (string)hlidacConfig('issues_url', '');
        ?>
<footer class="foot">
  <p>Zdroj: <a href="https://www.psp.cz/sqw/hp.sqw?k=1300">otevřená data Poslanecké sněmovny</a> (tisky, sněmovní dokumenty, poslanci). Počty PN jsou podané písemné pozměňovací návrhy; Sněmovna neeviduje, které z nich byly platně podány ve 2. čtení. Příznaky se počítají automaticky z historie tisku.</p>
  <p><a href="{{ route('changes', [], false) }}">Co jsme změnili</a></p>
  <?php if ($issues !== ''): ?>
    <p><a href="{{ $issues }}">Našli jste chybu nebo máte podnět? Napište nám.</a></p>
  <?php endif; ?>
</footer>
</main>
</body>
</html>
