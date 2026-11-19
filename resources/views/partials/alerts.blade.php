<?php
use Hlidac\View;
        if ($alerts === []): ?>
<p class="empty">Zatím žádná upozornění.</p>
<?php else:
        foreach ($alerts as $a): ?>
          <article class="alert">
            <div class="alert-when">{{ formatDate($a['event_at'], true) }}</div>
            <div class="alert-body">
              <div class="alert-meta">{!! View::scoreBadge((int)$a['score']) !!}{!! View::kindChip($a['kind']) !!}<a href="{{ View::alertUrl($a) }}">{{ (int)$a['ct'] > 0 ? 'tisk ' . (int)$a['ct'] : 'připravovaný zákon' }}{{ View::isPastPeriod($a) ? ' (' . (int)$a['obdobi'] . '. období)' : '' }}</a>{!! (int)$a['watched'] ? '<span class="chip chip-watch">Sledovaný</span>' : '' !!}</div>
              <h3><a href="{{ View::alertUrl($a) }}">{{ $a['title'] }}</a></h3>
              <p>{{ $a['body'] }}</p>
            </div>
          </article>
        <?php endforeach; endif; ?>
