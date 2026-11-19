<?php
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
    <link><?= e(\Hlidac\View::alertUrl($a, true)) ?></link>
    <guid isPermaLink="false"><?= e($a['alert_key']) ?></guid>
    <pubDate><?= date(DATE_RSS, strtotime($a['event_at'])) ?></pubDate>
    <description><?= e($a['body']) ?></description>
  </item>
  <?php endforeach; ?>
</channel>
</rss>
