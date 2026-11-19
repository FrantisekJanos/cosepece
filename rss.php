<?php
// RSS kanál upozornění. Filtry (#28): ?min_score=60, ?zakon=159/2006, ?ct=221, ?since=YYYY-MM-DD; ?vse=1 = i běžná agenda.

declare(strict_types=1);

require_once __DIR__ . '/app/bootstrap.php';

use Hlidac\Api;
use Hlidac\Repo;
use Hlidac\View;

Hlidac\RateLimit::guard('rss', 30, false);
$repo = new Repo(db());
// do RSS jde jen to důležité (?vse=1 = vše); filtry stejné jako v api.php
$query = $_GET;
if (!empty($query['vse']) && !isset($query['min_score'])) {
    $query['min_score'] = 0;
}
$query['limit'] = min(100, (int)($query['limit'] ?? 50));
try {
    $filters = Api::filters($query);
} catch (InvalidArgumentException $ex) {
    http_response_code(400);
    exit($ex->getMessage());
}
$alerts = (new Api(db(), $repo, config()))->alertRows($filters);

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
    <pubDate><?= date(DATE_RSS, strtotime((string)$a['event_at']) ?: time()) ?></pubDate>
    <description><?= e($a['body']) ?></description>
  </item>
  <?php endforeach; ?>
</channel>
</rss>
