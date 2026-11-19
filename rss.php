<?php
// RSS kanál upozornění.

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\Repo;
use Hlidac\Scorer;
use Hlidac\View;

$repo = new Repo(db());
// do RSS jde jen to důležité (?vse=1 = vše)
$min = !empty($_GET['vse']) ? 0 : (int)Scorer::weights(config())['level_watch'];
$alerts = $repo->alerts(50, 0, null, $min);

// v CLI testu už výstup běží, hlavičku pak nelze poslat
if (!headers_sent()) {
    header('Content-Type: application/rss+xml; charset=utf-8');
}
echo '<?xml version="1.0" encoding="utf-8"?>', "\n";
?>
<rss version="2.0">
<channel>
  <title>Hlídač zákonů – upozornění</title>
  <link><?= e(siteUrl()) ?></link>
  <description>Pozměňovací návrhy, zrychlená projednávání a zkrácené lhůty v Poslanecké sněmovně.</description>
  <language>cs</language>
  <?php foreach ($alerts as $a): ?>
  <item>
    <title><?= e($a['title']) ?></title>
    <link><?= e(View::alertUrl($a, true)) ?></link>
    <guid isPermaLink="false"><?= e($a['alert_key']) ?></guid>
    <pubDate><?= date(DATE_RSS, strtotime($a['event_at'])) ?></pubDate>
    <description><?= e($a['body']) ?></description>
  </item>
  <?php endforeach; ?>
</channel>
</rss>
