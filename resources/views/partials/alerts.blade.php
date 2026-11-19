<?php
use Hlidac\View;
        if ($alerts === []): ?>
<p class="empty">Zatím žádná upozornění.</p>
<?php else:
        foreach ($alerts as $a): ?>
          <article class="alert">
            <div class="alert-when">{{ formatDate($a['event_at'], true) }}</div>
            <div class="alert-body">
              <div class="alert-meta">{!! View::scoreBadge((int)$a['score']) !!}{!! View::kindChip($a['kind']) !!}<a href="tisk.php?ct={{ (int)$a['ct'] }}">tisk {{ (int)$a['ct'] }}</a>{!! (int)$a['watched'] ? '<span class="chip chip-watch">Sledovaný</span>' : '' !!}</div>
              <h3><a href="tisk.php?ct={{ (int)$a['ct'] }}">{{ $a['title'] }}</a></h3>
              <p>{{ $a['body'] }}</p>
            </div>
          </article>
        <?php endforeach; endif; ?>
