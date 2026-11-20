// Šablona Reelu: loadCard(card) naplní scény, fit() vměstná texty do bezpečné zóny,
// seek(t) nastaví deterministický stav animace v čase t (žádné časovače, render snímek po snímku).
(function () {
  'use strict';

  var STAMPS = { deadline_cut: 'ZKRÁTILI', pn_other_law: 'PŘÍLEPEK', p90_passed: '§ 90/2' };
  var ENTER = 0.35;      // délka náběhu prvku (s)
  var STAGGER = 0.18;    // rozestup prvků ve scéně (s)
  var scenes = [];       // [{el, start, end}]
  var total = 0;
  var card = null;

  function $(id) { return document.getElementById(id); }

  function czDate(iso) {
    var p = (iso || '').split('-');
    return p.length === 3 ? (+p[2]) + '. ' + (+p[1]) + '. ' + p[0] : '';
  }

  function shortReason(label) {
    // jen první část popisku (před „ – “), bez závorky se jménem, nejvýš 70 znaků
    var s = String(label).split(' – ')[0].replace(/\s*\([^)]*\)/g, '').trim();
    return s.length > 70 ? cutWords(s, 70) : s;
  }

  function cutWords(text, max) {
    // zkrátí na celé slovo a přidá „…“
    if (text.length <= max) return text;
    var cut = text.slice(0, max);
    var sp = cut.lastIndexOf(' ');
    if (sp > max * 0.5) cut = cut.slice(0, sp);
    return cut.replace(/[\s,;:–-]+$/, '') + '…';
  }

  function stampsFor(c) {
    var out = [];
    (c.reasons || []).forEach(function (r) {
      if (STAMPS[r.key] && out.indexOf(STAMPS[r.key]) < 0) out.push(STAMPS[r.key]);
    });
    return out;
  }

  window.loadCard = function (c) {
    card = c;
    $('top-ct').textContent = 'Tisk ' + c.ct;
    $('hook-name').textContent = c.nazev;
    $('hook-faze').textContent = 'Fáze: ' + c.faze;
    $('title').textContent = c.title;
    $('perex').textContent = c.perex || '';
    $('quote').textContent = (c.quote || '').replace(/^[„"“]+|[“"”]+$/g, '');
    $('quote-source').textContent = 'citace: ' + c.quote_source;
    $('end-ct').textContent = 'Tisk ' + c.ct + ' · ' + c.faze;
    $('ai-note').textContent = 'Shrnutí AI, doloženo citací' + (c.verified ? ', ověřeno druhým modelem' : '');
    $('footer').textContent = 'tisk ' + c.ct + ' · cosepece.cz · psp.cz · data k ' + czDate(c.data_date);

    var list = $('reasons');
    list.innerHTML = '';
    (c.reasons || []).slice().sort(function (a, b) { return b.points - a.points; }).slice(0, 4).forEach(function (r) {
      var li = document.createElement('div');
      li.className = 'li anim';
      li.textContent = shortReason(r.label);
      var pts = document.createElement('span');
      pts.className = 'pts';
      pts.textContent = '+' + r.points;
      li.appendChild(pts);
      list.appendChild(li);
    });
    var st = $('stamps');
    st.innerHTML = '';
    stampsFor(c).forEach(function (s) {
      var d = document.createElement('div');
      d.className = 'stamp anim stamp-in';
      d.textContent = s;
      st.appendChild(d);
    });

    // časová osa: hook 3 s, titulek 5 s, perex 5 s (jen když je), citace 5 s, důvody 4 s, konec 3 s
    var plan = [['s-hook', 3], ['s-title', 5]];
    if (c.perex) plan.push(['s-perex', 5]);
    if (c.quote) plan.push(['s-quote', 5]);
    plan.push(['s-reasons', 4], ['s-end', 3]);
    ['votes', 'clock', 'compare'].forEach(function (k) {
      // volitelné bloky zatím bez dat; s daty by se zařadily před konec
      if (c[k]) plan.splice(plan.length - 1, 0, ['s-' + k, 4]);
    });
    scenes = [];
    var t = 0;
    plan.forEach(function (p) {
      scenes.push({ el: $(p[0]), start: t, end: t + p[1] });
      t += p[1];
    });
    total = t;
    window.seek(0);
  };

  window.duration = function () { return total; };

  function overflows(body) {
    return body.scrollHeight > body.clientHeight + 1 || body.scrollWidth > body.clientWidth + 1;
  }

  window.fit = function () {
    // každou scénu s textem zmenšuje po 4 %, nejvýš na 60 %; pak zkracuje text na celá slova
    scenes.forEach(function (s) {
      var body = s.el.querySelector('.body');
      if (!body) return;
      s.el.style.display = 'block';
      var els = Array.prototype.slice.call(body.querySelectorAll('[data-fit]'));
      els.forEach(function (el) {
        el.dataset.base = parseFloat(getComputedStyle(el).fontSize);
        el.dataset.full = el.textContent;
      });
      var scale = 1;
      while (overflows(body) && scale > 0.6) {
        scale -= 0.04;
        els.forEach(function (el) { el.style.fontSize = (el.dataset.base * scale) + 'px'; });
      }
      els.forEach(function (el) {
        var full = el.dataset.full;
        var max = full.length;
        while (overflows(body) && max > 20) {
          max = Math.floor(max * 0.92);
          el.textContent = cutWords(full, max);
          el.dataset.truncated = '1';
        }
      });
      s.el.style.display = '';
    });
  };

  window.measureBlocks = function () {
    // obdélníky textových bloků po fit() (každá scéna rozvinutá, bez animace) a kontrola zkrácení
    var out = {};
    ['title', 'perex', 'quote'].forEach(function (id) {
      var el = $(id);
      var sc = el.closest('.scene');
      sc.style.display = 'block';
      var r = el.getBoundingClientRect();
      var full = el.dataset.full || el.textContent;
      var shown = el.textContent;
      var midWord = false;
      if (el.dataset.truncated === '1') {
        var kept = shown.replace(/…$/, '');
        var next = full.charAt(kept.length);
        midWord = next !== '' && !/[\s,;:.–-]/.test(next);
      }
      out[id] = { top: r.top, bottom: r.bottom, left: r.left, right: r.right, cut_mid_word: midWord };
      sc.style.display = '';
    });
    return out;
  };

  function ease(x) { x = Math.max(0, Math.min(1, x)); return 1 - Math.pow(1 - x, 3); }

  window.seek = function (t) {
    scenes.forEach(function (s) {
      var on = t >= s.start && t < s.end;
      s.el.style.display = on ? 'block' : 'none';
      if (!on) return;
      var local = t - s.start;
      var items = s.el.querySelectorAll('.anim');
      for (var i = 0; i < items.length; i++) {
        var el = items[i];
        var p = ease((local - i * STAGGER) / ENTER);
        if (el.classList.contains('stamp-in')) {
          // razítko dopadne: z 1,6× na 1× s mírným pootočením
          var sc = 1.6 - 0.6 * p;
          el.style.transform = 'rotate(-8deg) scale(' + sc + ')';
          el.style.opacity = p;
        } else {
          el.style.transform = 'translateY(' + (40 * (1 - p)) + 'px)';
          el.style.opacity = p;
        }
      }
      if (s.el.id === 's-hook' && card) {
        // počítadlo skóre naroste za 1,2 s
        $('score').textContent = Math.round(card.score * ease(local / 1.2));
      }
    });
    // patka je červená scéna → tmavý text
    var end = scenes.length ? scenes[scenes.length - 1] : null;
    $('footer').style.color = end && t >= end.start ? '#0E0E10' : '';
    $('frame').querySelector('.top').style.color = end && t >= end.start ? '#0E0E10' : '';
  };
})();
