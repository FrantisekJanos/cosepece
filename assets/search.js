// Našeptávač vyhledávání: index (hledat.php?index=1) se stáhne jednou při prvním použití pole
// a hledá se přímo v prohlížeči stejným postupem jako app/SearchIndex.php (bez diakritiky,
// začátky slov, jeden překlep u slov od 5 písmen i u slova v jiném tvaru). Dotazy se nikam neposílají.
// Ovládání: šipky, Enter, Esc; klávesa „/“ skočí do vyhledávání.
(function () {
  'use strict';
  var KINDS = { tisk: 'Tisky', zakon: 'Zákony', pn: 'Pozměňovací návrhy', osoba: 'Autoři pozměňovacích návrhů', prip: 'Připravované u vlády', tema: 'Témata a stránky' };
  var LIMIT = { tisk: 4, zakon: 2, pn: 3, osoba: 2, prip: 2, tema: 2 };
  var indexPromise = null;

  // malá písmena bez diakritiky, znak po znaku (stejná délka jako původní text kvůli zvýraznění)
  function fold(s) {
    var out = '';
    s = String(s || '').toLowerCase();
    for (var i = 0; i < s.length; i++) {
      out += s[i].normalize('NFD').charAt(0);
    }
    return out;
  }
  function words(s) { return fold(s).split(/[^\p{L}\p{N}/]+/u).filter(Boolean); }
  function tokens(q) {
    q = fold(q.trim()).replace(/\b(snemovni\s+)?tisk(u)?\s+(?=\d)/g, '');
    var seen = {};
    return q.split(/[^\p{L}\p{N}/]+/u).filter(function (t) { if (!t || seen[t]) return false; seen[t] = 1; return true; }).slice(0, 8);
  }
  function nearlyEqual(a, b) {
    // nejvýš jedna úprava: záměna, vložení, vynechání nebo prohození dvou sousedních znaků
    if (a === b) return true;
    var la = a.length, lb = b.length, i;
    if (la === lb) {
      var diff = [];
      for (i = 0; i < la; i++) if (a[i] !== b[i]) diff.push(i);
      return diff.length === 1 || (diff.length === 2 && diff[1] === diff[0] + 1 && a[diff[0]] === b[diff[1]] && a[diff[1]] === b[diff[0]]);
    }
    if (Math.abs(la - lb) !== 1) return false;
    var s = la > lb ? a : b, t = la > lb ? b : a;
    for (i = 0; i < t.length && s[i] === t[i]; i++);
    return s.slice(i + 1) === t.slice(i);
  }
  function fuzzyPrefix(tok, w) {
    // je tok s nejvýš jedním překlepem začátkem slova w? začátky stejně dlouhé, o znak delší
    // (v dotazu písmeno chybí) a o znak kratší (písmeno je navíc)
    var n = tok.length, lens = [n, n + 1, n - 1];
    for (var i = 0; i < lens.length; i++) {
      if (lens[i] <= w.length && nearlyEqual(w.slice(0, lens[i]), tok)) return true;
    }
    return false;
  }
  function stem(tok) {
    // hrubé odříznutí české koncovky (jen u slov od 5 písmen), ať „zbraně“ najde „zbraní“
    if (tok.length < 5 || /\d/.test(tok)) return '';
    var s = tok.replace(/(ich|ech|ami|emi|ovi|ove|ou|um|[aeiouy])$/, '');
    return s !== tok && s.length >= 4 ? s : '';
  }
  function tokenScore(tok, list, fuzzy) {
    var best = 0, numeric = /^\d+$/.test(tok), st = numeric ? '' : stem(tok);
    for (var i = 0; i < list.length; i++) {
      var w = list[i];
      if (w === tok) return 1.5;
      if (!numeric && w.indexOf(tok) === 0) best = Math.max(best, 1);
      else if (st && w.indexOf(st) === 0) best = Math.max(best, 0.9);
      else if (fuzzy && !numeric && tok.length >= 5) {
        if (fuzzyPrefix(tok, w)) best = Math.max(best, 0.5);
        // překlep ve slově, které je v textu v jiném tvaru („zbarne“ → „zbraních“)
        else if (st.length >= 5 && fuzzyPrefix(st, w)) best = Math.max(best, 0.4);
      }
    }
    return best;
  }
  function search(items, q) {
    var toks = tokens(q);
    if (!toks.length) return [];
    function run(fuzzy) {
      var hits = [];
      items.forEach(function (it) {
        var score = 0;
        for (var i = 0; i < toks.length; i++) {
          var tok = toks[i];
          if ((it.k === 'tisk' || it.k === 'zakon') && tok === it._id) { score += 100; continue; }
          var s = tokenScore(tok, it._title, fuzzy) * 3;
          if (!s) s = tokenScore(tok, it._rest, fuzzy);
          if (!s) return;
          score += s;
        }
        hits.push([score, it]);
      });
      return hits;
    }
    var hits = run(false);
    if (!hits.length) hits = run(true);
    hits.sort(function (a, b) { return b[0] - a[0] || (b[1].w || 0) - (a[1].w || 0); });
    var groups = {}, order = [];
    hits.forEach(function (h) {
      var k = h[1].k;
      if (!groups[k]) { groups[k] = []; order.push(k); }
      if (groups[k].length < LIMIT[k]) groups[k].push(h[1]);
    });
    return order.map(function (k) { return [k, groups[k]]; });
  }
  function loadIndex(url) {
    if (!indexPromise) {
      // adresa obsahuje otisk poslední synchronizace, takže se po aktualizaci dat stáhne nový index
      indexPromise = fetch(url || 'hledat.php?index=1', { credentials: 'same-origin' })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (idx) {
          (idx.items || []).forEach(function (it) {
            it._id = fold(it.id);
            // u tisku je titulek z AI (d) vlastně hlavní název, váží jako titulek (stejně jako v PHP)
            var main = it.k === 'tisk';
            it._title = words(it.t + ' ' + it.id + (main ? ' ' + it.d : ''));
            it._rest = words(it.s + ' ' + (main ? '' : it.d) + ' ' + it.x);
          });
          return idx.items || [];
        })
        .catch(function (e) { indexPromise = null; throw e; });
    }
    return indexPromise;
  }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function highlight(text, toks) {
    // zvýrazní začátky slov, které odpovídají slovům dotazu
    var f = fold(text), marks = new Array(text.length + 1).join('0').split('');
    toks.concat(toks.map(stem).filter(Boolean)).forEach(function (t) {
      var re = new RegExp('(^|[^\\p{L}\\p{N}])(' + t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gu'), m;
      while ((m = re.exec(f))) {
        for (var i = m.index + m[1].length; i < m.index + m[0].length; i++) marks[i] = '1';
        if (m[0].length === 0) re.lastIndex++;
      }
    });
    var out = '', open = false;
    for (var i = 0; i < text.length; i++) {
      if (marks[i] === '1' && !open) { out += '<mark>'; open = true; }
      if (marks[i] !== '1' && open) { out += '</mark>'; open = false; }
      out += esc(text[i]);
    }
    return out + (open ? '</mark>' : '');
  }

  function setup(form) {
    var input = form.querySelector('input[name="q"]');
    var list = form.querySelector('.suggest');
    if (!input || !list) return;
    var active = -1, timer = null, seq = 0;

    function options() { return list.querySelectorAll('[role="option"]'); }
    function close() {
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
      active = -1;
    }
    function move(d) {
      var opts = options();
      if (!opts.length) return;
      if (active >= 0) opts[active].classList.remove('active');
      active = (active + d + opts.length) % opts.length;
      opts[active].classList.add('active');
      input.setAttribute('aria-activedescendant', opts[active].id);
      opts[active].scrollIntoView({ block: 'nearest' });
    }
    function render(q, groups) {
      var toks = tokens(q), html = '', n = 0, id = list.id;
      groups.forEach(function (g) {
        html += '<li class="suggest-group" role="presentation">' + esc(KINDS[g[0]] || g[0]) + '</li>';
        g[1].forEach(function (it) {
          var badge = it.k === 'tisk' ? '<span class="suggest-score s' + (it.w >= 50 ? 'major' : (it.w >= 25 ? 'watch' : 'normal')) + '">' + it.w + '</span>' : '';
          html += '<li role="option" id="' + id + '-' + (n++) + '" data-href="' + esc(it.u) + '"><a href="' + esc(it.u) + '" tabindex="-1">' + badge
            + '<span class="suggest-text"><span class="suggest-title">' + highlight(it.t, toks) + '</span>'
            + '<span class="suggest-sub">' + esc(it.s) + (it.d ? ' · ' + highlight(it.d, toks) : '') + '</span></span></a></li>';
        });
      });
      var all = 'hledat.php?q=' + encodeURIComponent(q);
      html += groups.length
        ? '<li role="option" id="' + id + '-' + n + '" class="suggest-all" data-href="' + esc(all) + '"><a href="' + esc(all) + '" tabindex="-1">Všechny výsledky pro „' + esc(q) + '“ →</a></li>'
        : '<li class="suggest-empty" role="presentation">Nic jsme nenašli. Zkuste začátek slova, číslo tisku nebo zákona.</li>';
      list.innerHTML = html;
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      active = -1;
    }
    function update() {
      var q = input.value;
      if (q.trim().length < 2 && !/^\d$/.test(q.trim())) { close(); return; }
      var my = ++seq;
      loadIndex(form.getAttribute('data-search')).then(function (items) {
        if (my === seq && document.activeElement === input) render(q, search(items, q));
      }, function () { close(); });
    }

    input.addEventListener('focus', function () { loadIndex(form.getAttribute('data-search')).catch(function () {}); if (input.value.trim()) update(); });
    input.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(update, 80); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); if (list.hidden) update(); else move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
      else if (e.key === 'Escape') { if (!list.hidden) { e.preventDefault(); close(); } else { input.blur(); } }
      else if (e.key === 'Enter' && active >= 0) {
        e.preventDefault();
        window.location.href = options()[active].getAttribute('data-href');
      }
    });
    list.addEventListener('mousedown', function (e) { e.preventDefault(); });
    document.addEventListener('click', function (e) { if (!form.contains(e.target)) close(); });
  }

  document.querySelectorAll('form[data-search]').forEach(setup);
  // klávesa „/“: skok do vyhledávání (mimo psaní do jiného pole)
  document.addEventListener('keydown', function (e) {
    var t = e.target, typing = t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
    if (e.key === '/' && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) {
      var input = document.querySelector('form[data-search] input[name="q"]');
      if (input) { e.preventDefault(); input.focus(); input.select(); }
    }
  });
})();
