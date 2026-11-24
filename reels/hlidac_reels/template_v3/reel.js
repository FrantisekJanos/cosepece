// Šablona Reelu v3: vysvětlující formáty z dat MCP cosepece. Rozhraní shodné s v2:
// loadCard(card, orientation), fit(), seek(t), duration(), sceneIds(), measureBlocks(), audioCues().
// Stav obrazu závisí jen na t (žádné časovače, rAF ani CSS animace), render jde snímek po snímku.
// Formát vybírá karta.format; scény formátu se zařadí jen s jeho daty:
//   "cesta"    karta.cesta    = {kroky, dni, dni_popis, normalne, ferove} + volitelně karta.pn_dny
//   "zmena"    karta.zmena    = {body: [{kde, co, pred, po, citace, zdroj}], proc, pozor}
//   "rozprava" karta.rozprava = {projevu, recniku, sub, kluby, neautorizovano, pro, namitky, odpovedi, ai_z_projevu}
//   "pn"       karta.pn       = {count, authors, late_count, autori, jine_zakony, top} + volitelně karta.pn_dny
// Animace a zvuk se popisují jednou (stavitel A(id)): každý krok má čas, průběh a případně zvukovou událost,
// takže audioCues() vždy odpovídá obrazu.
(function () {
  'use strict';

  var STAMPS = { deadline_cut: 'ZKRÁTILI', pn_other_law: 'PŘÍLEPEK', p90_passed: '§ 90/2' };
  var RUBRIKA = { cesta: 'Cesta zákona', zmena: 'Co se doopravdy mění', rozprava: 'Rozprava v kostce', pn: 'Kdo co přidal' };
  var KLUBY = { ANO2011: 'ANO', TOP09: 'TOP 09' };
  var MAX_TOTAL = 40;    // strop délky videa (s); vysvětlující formáty potřebují víc času na čtení než v2
  var MIN_TOTAL = 15;
  var LEAD = 0.12;       // scéna začíná animovat až po odkrytí stěrkou
  var WIPE = 0.2;        // délka zakrytí i odkrytí stěrkou (s)
  var scenes = [];       // [{id, el, start, end, dur}]
  var SC = {};           // id scény -> {beats: [fn(L)], cues: [], end, hold, opt}
  var total = 0;
  var card = null;
  var fx = { x: 0, y: 0, flash: 0 };   // otřes a záblesk při dopadu razítka, sčítá se během seek()

  var LAND = false;      // orientace na šířku (1920×1080): třída landscape na <html>, rozvržení v landscape.css

  function setOrientation(o) {
    LAND = o === 'landscape';
    document.documentElement.classList.toggle('landscape', LAND);
  }

  function visibleKids(side) {
    return Array.prototype.filter.call(side.children, function (k) {
      return k.style.display !== 'none' && k.childNodes.length > 0;
    }).length;
  }

  function lockSides(body) {
    // sloupec se šířkou podle obsahu (flex: 0 0 auto) dostane pevnou šířku z ustáleného stavu,
    // aby se druhá strana během počítání čísla nepřelévala
    Array.prototype.forEach.call(body.children, function (side) {
      if (!/^side-/.test(side.className)) return;
      var cs = getComputedStyle(side);
      side.style.minWidth = LAND && cs.display === 'flex' && cs.flexGrow === '0' ? Math.ceil(side.getBoundingClientRect().width) + 'px' : '';
    });
  }

  function markSolo() {
    // Dvousloupcové scény (.body.two > .side-a + .side-b, na výšku bez vlivu): když jedné straně chybí obsah
    // (méně viditelných prvků než data-min, výchozí 1), scéna se na šířku sází do jednoho sloupce.
    Array.prototype.forEach.call(document.querySelectorAll('.body.two'), function (body) {
      var solo = false;
      Array.prototype.forEach.call(body.children, function (side) {
        side.style.minWidth = '';
        if (visibleKids(side) < +(side.dataset.min || 1)) solo = true;
      });
      body.classList.toggle('solo', solo);
    });
  }

  function $(id) { return document.getElementById(id); }
  function clamp(x) { return x < 0 ? 0 : x > 1 ? 1 : x; }
  function lim(x, a, b) { return Math.max(a, Math.min(b, x)); }
  function P(local, t0, d) { return clamp((local - t0) / d); }
  function out3(x) { return 1 - Math.pow(1 - x, 3); }
  function out5(x) { return 1 - Math.pow(1 - x, 5); }
  function in3(x) { return x * x * x; }
  function inOut(x) { return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }
  function back(x) { var c = 1.70158; return 1 + (c + 1) * Math.pow(x - 1, 3) + c * Math.pow(x - 1, 2); }

  function mk(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function add(parent) {
    for (var i = 1; i < arguments.length; i++) if (arguments[i]) parent.appendChild(arguments[i]);
    return parent;
  }

  function czDate(iso) {
    var p = String(iso || '').slice(0, 10).split('-');
    return p.length === 3 ? (+p[2]) + '. ' + (+p[1]) + '. ' + p[0] : '';
  }

  function czShort(iso) {
    var p = String(iso || '').slice(0, 10).split('-');
    return p.length === 3 ? (+p[2]) + '. ' + (+p[1]) + '.' : String(iso || '');
  }

  function dayDiff(a, b) {
    var x = Date.parse(String(a).slice(0, 10) + 'T00:00:00Z'), y = Date.parse(String(b).slice(0, 10) + 'T00:00:00Z');
    return isFinite(x) && isFinite(y) ? Math.round((y - x) / 86400000) : null;
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

  function plural(n, one, few, many) {
    return n === 1 ? one : (n >= 2 && n <= 4 ? few : many);
  }

  function klubLabel(k) { return KLUBY[k] || String(k || ''); }

  function klubChip(k, small) {
    // kluby jen šedým štítkem, žádné stranické barvy
    return mk('span', 'klub' + (small ? ' sm' : ''), klubLabel(k));
  }

  function setWords(el, text, highlight) {
    // rozdělí text na slova (.w > .wi); jednopísmenné předložky a spojky drží u dalšího slova,
    // čísla se při highlight zvýrazní žlutě
    el.innerHTML = '';
    el.dataset.shown = text;
    var toks = String(text).split(/\s+/).filter(Boolean);
    for (var i = 0; i < toks.length; i++) {
      var group = [toks[i]];
      while (/^[A-Za-zÀ-ž]$/.test(group[group.length - 1]) && i + 1 < toks.length) { i++; group.push(toks[i]); }
      var w = mk('span', 'w');
      var wi = mk('span', 'wi');
      group.forEach(function (tok, k) {
        if (k) wi.appendChild(document.createTextNode(' '));
        if (highlight && /\d/.test(tok)) wi.appendChild(mk('b', 'num', tok));
        else wi.appendChild(document.createTextNode(tok));
      });
      w.appendChild(wi);
      el.appendChild(w);
      el.appendChild(document.createTextNode(' '));
    }
  }

  function fill(el, text, setter) {
    el._set = setter;
    el.dataset.full = text;
    delete el.dataset.truncated;
    el.style.fontSize = '';
    setter(text);
  }

  function fillWords(el, text, highlight) {
    // text po slovech (náběh slov), vměstnává se přes fit()
    el.dataset.fit = '1';
    fill(el, String(text || ''), function (t) { setWords(el, t, !!highlight); });
    el.style.display = text ? '' : 'none';
    return el;
  }

  function fillPlain(el, text) {
    el.dataset.fit = '1';
    fill(el, String(text || ''), function (t) { el.textContent = t; el.dataset.shown = t; });
    el.style.display = text ? '' : 'none';
    return el;
  }

  function setOpt(el, text) {
    el.textContent = text || '';
    el.style.display = text ? '' : 'none';
  }

  function addStamps(box, list) {
    box.innerHTML = '';
    list.forEach(function (s) { box.appendChild(mk('div', 'stamp', s)); });
  }

  function readTime(chars) {
    // čas na přečtení textu scény po doběhnutí animace (zhruba 24 znaků za sekundu)
    return lim(chars / 24, 1.2, 5);
  }

  // ---------- základní pohyby ----------

  function show(el, p, dx, dy) {
    // náběh: průhlednost + dojezd z posunu (dx, dy)
    var e = out3(p);
    el.style.opacity = clamp(p * 2.2);
    el.style.transform = 'translate(' + (dx * (1 - e)) + 'px,' + (dy * (1 - e)) + 'px)';
  }

  function pop(el, p) {
    el.style.opacity = clamp(p * 3);
    el.style.transform = 'scale(' + (p <= 0 ? 0 : back(p)) + ')';
  }

  function maskWords(el, L, t0, span) {
    // slova vyjíždějí zdola zpod masky
    var ws = el.querySelectorAll('.wi');
    var step = Math.min(0.07, span / Math.max(1, ws.length));
    for (var i = 0; i < ws.length; i++) {
      var p = out5(P(L, t0 + i * step, 0.5));
      ws[i].style.transform = 'translateY(' + (115 * (1 - p)) + '%)';
    }
  }

  function stampIn(el, L, t0, rot) {
    // razítko padá shora (zrychluje), po dopadu krátký odraz, otřes obrazu a záblesk
    var p = P(L, t0, 0.24);
    var tau = L - t0 - 0.24;
    var sc = 2.2 - 1.2 * in3(p);
    if (tau >= 0) {
      var decay = Math.exp(-9 * tau);
      sc = 1 + 0.07 * decay * Math.cos(tau * 34);
      fx.x += Math.sin(tau * 71) * 9 * decay;
      fx.y += Math.cos(tau * 57) * 7 * decay;
      fx.flash = Math.max(fx.flash, 0.22 * Math.exp(-14 * tau));
    }
    el.style.opacity = clamp(p * 4);
    el.style.transform = 'rotate(' + rot + 'deg) scale(' + sc + ')';
  }

  // ---------- stavitel scény: pohyb i zvuk z jednoho popisu ----------

  function A(id, hold, opt) {
    var s = { beats: [], cues: [], end: 0, hold: hold, opt: !!opt };
    SC[id] = s;
    var a = {
      at: function (t, d, fn) {
        s.beats.push(function (L) { fn(P(L, t, d), L); });
        s.end = Math.max(s.end, t + d);
        return a;
      },
      bg: function (fn) {
        // trvalý pohyb (blikání, pulz); délku scény neprodlužuje
        s.beats.push(function (L) { fn(L); });
        return a;
      },
      cue: function (t, type, gain, extra) {
        var c = { t: t, type: type, gain: gain == null ? 1 : gain };
        for (var k in (extra || {})) c[k] = extra[k];
        s.cues.push(c);
        return a;
      },
      show: function (el, t, dx, dy) {
        return a.at(t, 0.35, function (p) { show(el, p, dx || 0, dy == null ? 30 : dy); });
      },
      pop: function (el, t, gain) {
        a.at(t, 0.35, function (p) { pop(el, p); });
        return gain ? a.cue(t, 'pop', gain) : a;
      },
      words: function (el, t, span) {
        a.at(t, span + 0.5, function (p, L) { maskWords(el, L, t, span); });
        return a.cue(t, 'slide', 0.7, { dur: span + 0.3 });
      },
      text: function (el, t, gain) {
        // odstavec: slova přijíždějí zleva v pořadí čtení
        a.at(t, 1.0, function (p, L) {
          var ws = el.querySelectorAll('.w');
          var step = Math.min(0.03, 0.6 / Math.max(1, ws.length));
          for (var i = 0; i < ws.length; i++) show(ws[i], P(L, t + i * step, 0.4), -40, 0);
        });
        return a.cue(t + 0.08, 'line', gain || 0.8, { pos: 0.2 });
      },
      bar: function (el, t, share, d) {
        return a.at(t, d || 0.6, function (p) { el.style.transform = 'scaleX(' + (out5(p) * share) + ')'; });
      },
      count: function (el, t, d, to, gain, prefix) {
        a.at(t, d, function (p) { el.textContent = (prefix || '') + Math.round(to * out3(p)); });
        var n = Math.min(10, Math.max(1, Math.round(to)));
        for (var k = 1; k <= n; k++) a.cue(t + d * (1 - Math.pow(1 - k / n, 1 / 3)), 'tick', gain || 0.6, { pos: k / n });
        return a;
      },
      stamp: function (el, t, rot) {
        a.at(t, 0.7, function (p, L) { stampIn(el, L, t, rot); });
        return a.cue(t + 0.24, 'stamp', 1);
      }
    };
    return a;
  }

  // ---------- společné scény ----------

  function buildHook(c) {
    var h = c.hook && c.hook.big != null && String(c.hook.big) !== '' ? c.hook : null;
    var a = A('s-hook', lim((c.title || '').length / 40, 0.8, 2));
    $('hk-rub').textContent = RUBRIKA[c.format] || 'Hlídač zákonů';
    $('hk-row').style.display = h ? '' : 'none';
    setOpt($('hk-sub'), h ? h.sub : '');
    fill($('hk-title'), c.title || c.nazev || '', function (t) { setWords($('hk-title'), t, true); });
    $('hk-faze').textContent = 'Fáze: ' + c.faze;
    var meta = [];
    if (c.typ_navrhu) meta.push(String(c.typ_navrhu));
    if (c.predlozeno) meta.push('předloženo ' + czDate(c.predlozeno));
    setOpt($('hk-meta'), meta.join(' · '));

    a.show($('hk-rub').parentNode, 0, -30, 0);
    a.bg(function (L) { $('hk-sq').style.opacity = Math.floor(Math.max(0, L) * 2.5) % 2 ? 0.25 : 1; });
    var t = 0.1;
    if (h) {
      var num = /^\d+$/.test(String(h.big));
      $('hk-big').className = 'big' + (num && String(h.big).length <= 3 ? '' : ' txt');
      $('hk-big').textContent = h.big;
      $('hk-unit').textContent = h.unit || '';
      a.at(0.05, 0.2, function (p) { $('hk-row').style.opacity = p; });
      if (num) {
        a.count($('hk-big'), 0.1, 0.9, +h.big, 0.8);
        a.bg(function (L) {
          // dosednutí čísla: krátký ráz a otřes
          var tau = L - 1.0, punch = 0.86 + 0.14 * out3(P(L, 0.1, 0.9));
          if (tau >= 0) {
            var decay = Math.exp(-8 * tau);
            punch = 1 + 0.1 * decay * Math.cos(tau * 28);
            fx.x += Math.sin(tau * 63) * 7 * decay;
            fx.y += Math.cos(tau * 49) * 5 * decay;
          }
          $('hk-row').style.transform = 'scale(' + punch + ')';
        });
        a.cue(1.0, 'hit', 0.8);
      } else {
        a.pop($('hk-row'), 0.1, 0.9);
      }
      a.show($('hk-sub'), 1.05, -30, 0);
      t = 1.15;
    }
    a.words($('hk-title'), t, 0.9);
    a.pop($('hk-faze'), t + 1.0, 0.6);
    a.show($('hk-meta'), t + 1.15, -30, 0);
  }

  function buildCtx(c) {
    if (!c.perex) return false;
    var a = A('s-ctx', readTime(c.perex.length));
    fillPlain($('cx-name'), c.nazev || '');
    fillWords($('cx-perex'), c.perex, true);
    setOpt($('cx-typ'), c.typ_navrhu ? String(c.typ_navrhu) : '');
    setOpt($('cx-kdo'), c.kdo ? String(c.kdo) : '');
    $('cx-who').style.display = c.typ_navrhu || c.kdo ? '' : 'none';
    a.show($('s-ctx').querySelector('.lab'), 0, -30, 0).show($('cx-name'), 0.1, -30, 0);
    a.at(0.15, 0.6, function (p) { $('perex-bar').style.transform = 'scaleY(' + out5(p) + ')'; });
    a.text($('cx-perex'), 0.25).cue(0.55, 'line', 0.7, { pos: 0.7 });
    a.pop($('cx-typ'), 1.2, 0.6).show($('cx-kdo'), 1.35, -30, 0);
    return true;
  }

  function buildEnd(c) {
    var a = A('s-end', 1.8);
    var logo = $('end-logo');
    logo.innerHTML = '';
    'cosepece.cz'.split('').forEach(function (ch) { logo.appendChild(mk('span', '', ch)); });
    $('end-ct').textContent = 'Tisk ' + c.ct + ' · ' + c.faze;
    $('end-url').textContent = String(c.url || ('cosepece.cz/tisk.php?ct=' + c.ct)).replace(/^https?:\/\//, '');
    $('ai-note').textContent = aiNote(c);
    setOpt($('end-src'), c.zdroje || 'Zdroj: psp.cz, historie tisku a stenozáznamy');
    a.show($('s-end').querySelector('.lab'), 0, 0, 30);
    var letters = logo.children;
    a.at(0.05, 0.9, function (p, L) {
      for (var i = 0; i < letters.length; i++) {
        var q = P(L, 0.05 + i * 0.04, 0.4);
        letters[i].style.opacity = clamp(q * 3);
        letters[i].style.transform = 'translateY(' + (-140 * (1 - (q <= 0 ? 0 : back(q)))) + 'px)';
      }
    });
    for (var e = 0; e < letters.length; e++) {
      a.cue(0.05 + e * 0.04 + 0.26, 'drop', e === letters.length - 1 ? 1 : 0.7, { pos: e / (letters.length - 1) });
    }
    a.show($('end-ct'), 0.6, 0, 30).pop($('end-url-box'), 0.8, 0.8);
    a.bg(function (L) { $('end-cursor').style.opacity = Math.floor(Math.max(0, L) * 2.5) % 2 ? 0 : 1; });
    a.show($('ai-note'), 1.05, 0, 30).show($('end-src'), 1.2, 0, 30).show($('end-motto'), 1.35, 0, 30);
  }

  function aiNote(c) {
    // co ve videu shrnula AI, musí být vidět; výchozí text podle formátu a dat
    if (c.ai_note) return String(c.ai_note);
    if (c.format === 'rozprava') {
      var n = c.rozprava && +c.rozprava.ai_z_projevu;
      return 'Shrnutí AI' + (n > 0 ? ' z ' + n + ' ' + plural(n, 'projevu', 'projevů', 'projevů') : '') + ', odkazy na cosepece.cz';
    }
    if (c.format === 'zmena') return 'Doslovné citace z textu návrhu, výběr a popisky: AI';
    if (c.format === 'pn') return c.pn && c.pn.top && c.pn.top.length ? 'Počty: data psp.cz · titulky návrhů: shrnutí AI' : 'Data psp.cz, bez shrnutí AI';
    return 'Data: historie tisku psp.cz, bez shrnutí AI';
  }

  // ---------- formát „cesta“ ----------

  function buildOsa(d) {
    var steps = d.kroky.slice(0, 6);
    var rows = $('co-rows');
    rows.innerHTML = '';
    var chars = 0;
    $('co-tl').className = steps.length > 5 ? 'dense' : '';
    $('co-tl').style.setProperty('--n', steps.length);
    steps.forEach(function (s, k) {
      var row = mk('div', 'step' + (k === steps.length - 1 ? ' last' : '') + (s.hot ? ' hot' : ''));
      var body = mk('div', 'stepbody');
      var dline = mk('div', 'd', czDate(s.date) || String(s.date || ''));
      var gap = k ? dayDiff(steps[k - 1].date, s.date) : null;
      if (gap != null && gap > 0) dline.appendChild(mk('span', 'gapd', '+' + gap + ' ' + plural(gap, 'den', 'dny', 'dní')));
      else if (gap === 0) dline.appendChild(mk('span', 'gapd', 'týž den'));
      var lab = mk('div', 's');
      fillPlain(lab, cutWords(String(s.label), 64));
      chars += String(s.label).length;
      add(row, mk('i', 'dot'), add(body, dline, lab));
      rows.appendChild(row);
    });
    var days = d.dni != null ? +d.dni : dayDiff(steps[0].date, steps[steps.length - 1].date);
    $('co-sum').style.display = days > 0 ? '' : 'none';
    $('co-days-w').textContent = plural(days, 'den', 'dny', 'dní');
    $('co-days-sub').textContent = d.dni_popis || 'od předložení k poslednímu kroku';

    var a = A('s-c-osa', readTime(chars * 0.7));
    var n = steps.length;
    a.show($('s-c-osa').querySelector('.lab'), 0, -30, 0);
    a.at(0.1, 0.3 * n + 0.1, function (p) { $('co-line').style.transform = (LAND ? 'scaleX(' : 'scaleY(') + out3(p) + ')'; });
    Array.prototype.forEach.call(rows.children, function (row, i) {
      var t0 = 0.15 + i * 0.3;
      var dot = row.firstChild;
      a.at(t0, 0.3, function (p, L) {
        var pulse = (i === n - 1 || steps[i].hot) && L > t0 + 0.4 ? 1 + 0.2 * Math.abs(Math.sin((L - t0) * 3.2)) : 1;
        dot.style.opacity = clamp(p * 3);
        dot.style.transform = 'scale(' + (p <= 0 ? 0 : back(p) * pulse) + ')';
      });
      a.show(row.lastChild, t0 + 0.05, -40, 0);
      a.cue(t0, 'pop', steps[i].hot || i === n - 1 ? 1 : 0.7, { pos: n > 1 ? i / (n - 1) : 0 });
    });
    if (days > 0) {
      var b = 0.25 + n * 0.3;
      a.show($('co-sum'), b, 0, 30).count($('co-days'), b + 0.05, 0.7, days, 0.6);
    }
    return true;
  }

  function buildNorm(d, stamps) {
    var rowsData = (d.normalne || []).filter(function (r) { return r && r.label; }).slice(0, 3);
    if (!rowsData.length) return false;
    var box = $('cn-rows');
    box.innerHTML = '';
    var a = A('s-c-norm', 1.6 + rowsData.length * 0.5 + (d.ferove ? readTime(String(d.ferove).length) * 0.5 : 0), true);
    a.show($('s-c-norm').querySelector('.lab'), 0, -30, 0);
    var t = 0.15;
    rowsData.forEach(function (r) {
      var numeric = isFinite(+r.normal) && isFinite(+r.actual) && r.normal != null && r.actual != null && +r.normal > 0;
      var hot = r.hot != null ? !!r.hot : (numeric && +r.actual < +r.normal);
      var row = mk('div', 'cmp' + (hot ? ' hot' : '') + (numeric ? '' : ' txt'));
      var head = add(mk('div', 'cmphead'), mk('span', 't2', r.label), r.par ? mk('span', 'src', r.par) : null);
      var unit = r.unit ? ' ' + r.unit : '';
      function line(cls, key, val) {
        var l = mk('div', 'cmpline ' + cls);
        var bar = mk('i');
        add(l, mk('span', 'cmpk', key), add(mk('div', 'track'), bar), mk('span', 'cmpv', val));
        return { el: l, bar: bar, val: l.lastChild };
      }
      var ln = line('norm', 'běžně', numeric ? (+r.normal) + unit : String(r.norm_txt || ''));
      var lt = line('tady', 'tady', numeric ? (+r.actual) + unit : String(r.tady_txt || ''));
      add(row, head, ln.el, lt.el);
      box.appendChild(row);
      var max = numeric ? Math.max(+r.normal, +r.actual) : 1;
      a.show(head, t, -30, 0).show(ln.el, t + 0.12, 0, 30);
      if (numeric) a.bar(ln.bar, t + 0.15, +r.normal / max).cue(t + 0.15, 'slide', 0.5, { dur: 0.5 });
      a.show(lt.el, t + 0.5, 0, 30);
      if (numeric) {
        // TADY: pruh i číslo začínají na běžné hodnotě a posunou se na skutečnou
        var nv = +r.normal, av = +r.actual, t1 = t + 0.75;
        a.at(t + 0.55, 1.2, function (p, L) {
          var grow = out5(P(L, t + 0.55, 0.35)), cut = inOut(P(L, t1, 0.8));
          lt.bar.style.transform = 'scaleX(' + (grow * (nv + (av - nv) * cut) / max) + ')';
          lt.val.textContent = Math.round(nv + (av - nv) * cut) + unit;
        });
        for (var k = 1; k <= 8; k++) a.cue(t1 + 0.8 * k / 8, 'tick', 0.6, { pos: av < nv ? 1 - k / 8 : k / 8 });
        t += 1.75;
      } else {
        a.cue(t + 0.5, 'line', 0.8, { pos: 0.6 });
        t += 1.0;
      }
    });
    var st = stamps.filter(function (s) { return s === 'ZKRÁTILI' || s === '§ 90/2'; });
    addStamps($('cn-stamps'), st);
    Array.prototype.forEach.call($('cn-stamps').children, function (el, j) { a.stamp(el, t + 0.1 + j * 0.38, j % 2 ? -4 : -7); });
    if (st.length) t += 0.5 + st.length * 0.38;
    fillPlain($('cn-fer'), d.ferove || '');
    if (d.ferove) a.show($('cn-fer'), t + 0.1, 0, 30);
    return true;
  }

  function buildDny(pd) {
    var days = pd && Array.isArray(pd.dny) ? pd.dny.filter(function (x) { return x && +x.count > 0; }).slice(-7) : [];
    if (days.length < 2) return false;
    var chart = $('dn-chart'), dates = $('dn-dates');
    chart.innerHTML = '';
    dates.innerHTML = '';
    var max = Math.max.apply(null, days.map(function (x) { return +x.count; }));
    var a = A('s-dny', readTime(String(pd.note || '').length + String(pd.sub || '').length) + 0.4, true);
    a.show($('s-dny').querySelector('.lab'), 0, -30, 0);
    days.forEach(function (x, i) {
      var col = mk('div', 'col' + (x.late ? ' late' : ''));
      var cv = mk('div', 'cv', '0'), cb = mk('div', 'cb');
      cb.style.height = Math.max(2, 78 * (+x.count) / max) + '%';
      add(col, cv, cb);
      chart.appendChild(col);
      dates.appendChild(mk('span', '', x.label ? String(x.label) : czShort(x.date)));
      var t0 = 0.2 + i * 0.22;
      a.at(t0, 0.55, function (p) {
        cb.style.transform = 'scaleY(' + out5(p) + ')';
        cv.style.opacity = clamp(p * 3);
        cv.textContent = Math.round(+x.count * out3(p));
      });
      a.cue(t0, x.late ? 'hit' : 'pop', x.late ? 0.7 : 0.6, { pos: i / (days.length - 1) });
    });
    var t = 0.2 + days.length * 0.22 + 0.45;
    fillWords($('dn-note'), pd.note || '', true);
    fillPlain($('dn-sub'), pd.sub || '');
    if (pd.note) a.text($('dn-note'), t);
    if (pd.sub) a.show($('dn-sub'), t + 0.5, 0, 30);
    return true;
  }

  // ---------- formát „zmena“ ----------

  function validBody(z) {
    // změna se ukáže jen s doslovnou citací a označením místa v textu (dohledatelnost)
    return (z && Array.isArray(z.body) ? z.body : []).filter(function (b) {
      return b && b.kde && b.citace && b.zdroj && (b.po != null && String(b.po) !== '');
    }).slice(0, 3);
  }

  function buildZ(idx, b, count) {
    var id = 's-z-' + (idx + 1);
    var body = $(id).querySelector('.body');
    body.innerHTML = '';
    var pred = b.pred != null && String(b.pred) !== '' ? String(b.pred) : String(b.bez_pred || 'v zákoně není');
    var po = String(b.po);
    var len = Math.max(pred.length, po.length);
    var hasPred = b.pred != null && String(b.pred) !== '';
    var lab = add(mk('div', 'lab'), mk('i', 'sq'), mk('span', '', 'Co se mění' + (count > 1 ? ' ' + (idx + 1) + '/' + count : '')));
    var kde = mk('div', 'whotyp', b.kde);
    var co = fillPlain(mk('div', 't2'), b.co || '');
    var row = mk('div', 'zrow ' + (len <= 8 ? 'big' : len <= 18 ? 'mid' : 'txt'));
    var b1 = mk('div', 'zbox pred'), b2 = mk('div', 'zbox po');
    var v1 = mk('div', 'zv'), v2 = mk('div', 'zv');
    var strike = mk('i', 'strike');
    if (len > 18) { fillPlain(v1, pred); fillPlain(v2, po); } else { v1.textContent = pred; v2.textContent = po; }
    if (hasPred) { v1.appendChild(strike); v1.className = 'zv old'; }
    add(b1, mk('div', 'zk', b.pred_lab || 'Dnes'), v1);
    add(b2, mk('div', 'zk', b.po_lab || 'Po novele'), v2);
    add(row, b1, b2);
    var paper = mk('div', 'paper small');
    var cit = fillPlain(mk('div', 'q'), String(b.citace));
    cit.dataset.m = 'zcit' + (idx + 1);
    add(paper, mk('div', 'zlab', 'doslova z návrhu'), cit);
    var src = mk('div', 'chip', 'zdroj: ' + b.zdroj);
    src.dataset.m = 'zsrc' + (idx + 1);
    // dvě strany pro rozvržení na šířku (na výšku display: contents)
    add(body, add(mk('div', 'side-a'), lab, kde, co, row), add(mk('div', 'side-b'), paper, src));

    var a = A(id, readTime(String(b.co || '').length + String(b.citace).length * 0.8 + len) + 0.3, idx > 0);
    a.show(lab, 0, -30, 0).pop(kde, 0.1, 0.8).show(co, 0.3, -30, 0);
    a.pop(b1, 0.55, 0.7);
    if (hasPred && len <= 18) {
      a.at(1.1, 0.22, function (p) { strike.style.transform = 'rotate(-4deg) scaleX(' + out3(p) + ')'; });
      a.cue(1.1, 'strike', 1);
    }
    a.stamp(b2, 1.4, 0);
    a.at(1.9, 0.55, function (p) {
      var e = out5(p);
      paper.style.opacity = clamp(p * 6);
      paper.style.transform = 'translateX(' + (900 * (1 - e)) + 'px) rotate(' + (-1 + 7 * (1 - e)) + 'deg)';
    });
    a.cue(1.9, 'slide', 0.9, { dur: 0.55 }).show(src, 2.4, 0, 30);
    return true;
  }

  function buildProc(z) {
    var pr = z.proc && z.proc.text && z.proc.zdroj ? z.proc : null;
    var po = z.pozor && z.pozor.text && z.pozor.zdroj ? z.pozor : null;
    if (!pr && !po) return false;
    var a = A('s-z-proc', readTime((pr ? pr.text.length : 0) + (po ? po.text.length : 0)) + 0.5, false);
    $('zp-proc').style.display = pr ? '' : 'none';
    $('zp-pozor').style.display = po ? '' : 'none';
    var t = 0;
    if (pr) {
      fillWords($('zp-proc-t'), pr.text, true);
      $('zp-proc-src').textContent = 'zdroj: ' + pr.zdroj;
      a.show($('zp-proc').firstElementChild, t, -30, 0).text($('zp-proc-t'), t + 0.15).show($('zp-proc-src'), t + 0.9, 0, 30);
      t += 1.3;
    }
    if (po) {
      $('zp-pozor-lab').textContent = po.lab || 'Na co se dívat';
      fillWords($('zp-pozor-t'), po.text, true);
      $('zp-pozor-src').textContent = 'zdroj: ' + po.zdroj;
      a.show($('zp-pozor-lab'), t, -30, 0).text($('zp-pozor-t'), t + 0.15).show($('zp-pozor-src'), t + 0.9, 0, 30);
    }
    return true;
  }

  // ---------- formát „rozprava“ ----------

  function validPoints(side) {
    // bod shrnutí se ukáže jen s odkazem na projev (dohledatelnost)
    return (side && Array.isArray(side.body) ? side.body : []).filter(function (b) { return b && b.text && b.url; }).slice(0, 3);
  }

  function klubBars(box, rows, a, t0, nameKey) {
    // řádky „štítek · pruh · počet“; všechny pruhy stejnou šedou
    box.innerHTML = '';
    var max = Math.max.apply(null, rows.map(function (r) { return +r.n; }).concat([1]));
    rows.forEach(function (r, i) {
      var row = mk('div', 'kbar' + (nameKey ? ' nar' : ''));
      var bar = mk('i'), kv = mk('span', 'kv', '0');
      if (nameKey) row.appendChild(mk('span', 'kname', r.name));
      if (r.klub) row.appendChild(klubChip(r.klub));
      add(row, add(mk('div', 'track'), bar), kv);
      box.appendChild(row);
      var t = t0 + i * 0.13;
      a.show(row, t, -40, 0).bar(bar, t + 0.1, +r.n / max, 0.6);
      a.at(t + 0.1, 0.6, function (p) { kv.textContent = Math.round(+r.n * out3(p)); });
      a.cue(t, 'line', 0.7, { pos: rows.length > 1 ? i / (rows.length - 1) : 0 });
    });
    return t0 + rows.length * 0.13 + 0.6;
  }

  function buildKdo(r) {
    var a = A('s-r-kdo', 2.2);
    var n = +r.projevu;
    $('rk-w').textContent = plural(n, 'vystoupení', 'vystoupení', 'vystoupení');
    var sub = [];
    if (+r.recniku > 0) sub.push(r.recniku + ' ' + plural(+r.recniku, 'řečník', 'řečníci', 'řečníků'));
    if (r.sub) sub.push(String(r.sub));
    $('rk-sub').textContent = sub.join(' · ');
    var kl = (r.kluby || []).filter(function (k) { return k && k.klub && +k.projevu > 0; })
      .sort(function (x, y) { return y.projevu - x.projevu; }).slice(0, 8)
      .map(function (k) { return { klub: k.klub, n: +k.projevu }; });
    a.show($('s-r-kdo').querySelector('.lab'), 0, -30, 0);
    a.show($('rk-n').parentNode, 0.1, 0, 30).count($('rk-n'), 0.15, 0.8, n, 0.7);
    var t = klubBars($('rk-kluby'), kl, a, 0.8, false);
    var un = +r.neautorizovano;
    $('rk-unauth').textContent = un > 0 ? un + ' z ' + n + ' zatím neautorizováno' : '';
    $('rk-meta').style.display = un > 0 ? '' : 'none';
    if (un > 0) a.pop($('rk-unauth'), t, 0.8);
    setOpt($('rk-note'), r.pozn || 'bez předsedajících · zdroj: stenozáznamy psp.cz');
    a.show($('rk-note'), t + 0.2, 0, 30);
    return true;
  }

  function buildBul(id, label, side, points) {
    var body = $(id).querySelector('.body');
    body.innerHTML = '';
    var badge = mk('span', 'aibadge', 'shrnutí AI');
    badge.dataset.m = id.slice(2).replace(/-/g, '_') + '_ai';
    var labrow = add(mk('div', 'labrow'), mk('div', 'lab', label), badge);
    var who = side.kdo ? mk('div', 'who2', side.kdo) : null;
    var list = mk('div', 'bul');
    var chars = 0;
    var a = A(id, 0, id === 's-r-odp');
    a.show(labrow.firstChild, 0, -30, 0).pop(badge, 0.25, 0.5);
    if (who) a.show(who, 0.15, -30, 0);
    points.forEach(function (b, i) {
      var row = mk('div', 'bu');
      var bn = mk('div', 'bn', String(i + 1));
      var bt = fillWords(mk('div', 'bt'), b.text, true);
      bt.dataset.m = id.slice(2).replace(/-/g, '_') + '_' + (i + 1);
      var bs = mk('div', 'bs');
      if (b.zdroj) bs.appendChild(mk('span', 'src', b.zdroj));
      (b.kluby || []).forEach(function (k) { bs.appendChild(klubChip(k, true)); });
      // neautorizovaný stenozáznam musí být vidět; chybějící údaj se bere jako neautorizováno
      if (b.autorizovano !== true) {
        var un = mk('span', 'unauth sm', 'neautorizováno');
        un.dataset.m = id.slice(2).replace(/-/g, '_') + '_unauth_' + (i + 1);
        bs.appendChild(un);
      }
      add(row, bn, add(mk('div', 'bb'), bt, bs));
      list.appendChild(row);
      chars += String(b.text).length;
      var t = 0.4 + i * 0.75;
      a.pop(bn, t, 0.8).text(bt, t + 0.08).show(bs, t + 0.5, 0, 20);
    });
    var note = side.pozn ? fillPlain(mk('div', 'm'), side.pozn) : null;
    if (note) { a.show(note, 0.4 + points.length * 0.75 + 0.1, 0, 30); chars += side.pozn.length * 0.6; }
    add(body, labrow, who, list, note);
    SC[id].hold = readTime(chars) * 0.75;
    return true;
  }

  // ---------- formát „pn“ ----------

  function buildKolik(pn) {
    var a = A('s-p-kolik', 2.2);
    var n = +pn.count;
    $('pk-w').textContent = plural(n, 'pozměňovací návrh', 'pozměňovací návrhy', 'pozměňovacích návrhů');
    var au = +pn.authors;
    $('pk-auth').textContent = au > 0 ? 'od ' + au + ' ' + plural(au, 'poslance', 'poslanců', 'poslanců') : '';
    var slips = $('pk-slips');
    slips.innerHTML = '';
    var shown = Math.min(n, 60);
    var late = Math.min(+pn.late_count || 0, n);
    var lateShown = Math.round(shown * late / n);
    for (var j = 0; j < shown; j++) slips.appendChild(mk('i', j >= shown - lateShown ? 'late' : ''));
    a.show($('s-p-kolik').querySelector('.lab'), 0, -30, 0);
    a.show($('pk-n').parentNode, 0.1, 0, 30).count($('pk-n'), 0.15, 0.8, n, 0.7);
    var step = 0.8 / Math.max(1, shown);
    a.at(0.3, 0.8 + 0.22, function (p, L) {
      for (var i = 0; i < slips.children.length; i++) pop(slips.children[i], P(L, 0.3 + i * step, 0.22));
    });
    var rows = (pn.autori || []).filter(function (x) { return x && x.jmeno && +x.count > 0; }).slice(0, 5)
      .map(function (x) { return { name: x.jmeno, klub: x.klub, n: +x.count }; });
    var t = rows.length ? klubBars($('pk-autori'), rows, a, 1.2, true) : 1.3;
    if (!rows.length) $('pk-autori').innerHTML = '';
    fillPlain($('pk-note'), pn.pozn || '');
    if (pn.pozn) a.show($('pk-note'), t, 0, 30);
    return true;
  }

  function buildJine(pn, stamps) {
    var laws = (pn.jine_zakony || []).filter(function (x) { return x && x.sb; }).slice(0, 4);
    if (!laws.length) return false;
    var a = A('s-p-jine', 1.4 + laws.length * 0.5 + (pn.jine_pozn ? readTime(pn.jine_pozn.length) * 0.5 : 0), true);
    var list = $('pj-list');
    list.innerHTML = '';
    a.show($('s-p-jine').querySelector('.lab'), 0, -30, 0);
    laws.forEach(function (x, i) {
      var row = mk('div', 'jz');
      var ids = (x.pn || []).slice(0, 4);
      add(row, mk('span', 'jsb', x.sb + ' Sb.'), x.nazev ? mk('span', 'jn', x.nazev) : null,
        ids.length ? mk('span', 'src', 'PN ' + ids.join(', ') + ((x.pn || []).length > 4 ? ' …' : '')) : null);
      list.appendChild(row);
      a.show(row, 0.15 + i * 0.3, 90, 0).cue(0.15 + i * 0.3, 'line', 0.9, { pos: laws.length > 1 ? i / (laws.length - 1) : 0 });
    });
    var t = 0.15 + laws.length * 0.3 + 0.4;
    // razítko jen s důkazem v klíči důvodu skóre
    var st = stamps.indexOf('PŘÍLEPEK') >= 0 ? ['PŘÍLEPEK'] : [];
    addStamps($('pj-stamps'), st);
    if (st.length) { a.stamp($('pj-stamps').firstChild, t, -7); t += 0.8; }
    fillPlain($('pj-note'), pn.jine_pozn || '');
    if (pn.jine_pozn) a.show($('pj-note'), t, 0, 30);
    return true;
  }

  function buildTop(pn) {
    var top = (pn.top || []).filter(function (x) { return x && x.cislo && x.titulek; }).slice(0, 3);
    if (!top.length) return false;
    var a = A('s-p-top', 0, true);
    var list = $('pt-list');
    list.innerHTML = '';
    var chars = 0;
    a.show($('s-p-top').querySelector('.lab'), 0, -30, 0).pop($('pt-ai'), 0.25, 0.5);
    top.forEach(function (x, i) {
      var pc = mk('div', 'pc');
      var head = add(mk('div', 'pch'), mk('span', 'pcn', 'PN ' + x.cislo), x.autor ? mk('span', 'src', x.autor) : null,
        x.klub ? klubChip(x.klub, true) : null);
      var tt = fillPlain(mk('div', 't2'), x.titulek);
      tt.dataset.m = 'pt_' + (i + 1);
      add(pc, head, tt);
      list.appendChild(pc);
      chars += x.titulek.length;
      a.show(pc, 0.35 + i * 0.6, 90, 0).cue(0.35 + i * 0.6, 'slide', 0.7, { dur: 0.35 });
    });
    setOpt($('pt-note'), pn.top_pozn || 'celé znění a rozbor: cosepece.cz');
    a.show($('pt-note'), 0.35 + top.length * 0.6 + 0.1, 0, 30);
    SC['s-p-top'].hold = readTime(chars) * 0.8;
    return true;
  }

  // ---------- naplnění karty ----------

  function buildFormat(c, stamps) {
    // vrací pořadí scén formátu; bez platných dat formátu nevrací nic
    var ids = [];
    function on(id, ok) { if (ok) ids.push(id); }
    if (c.format === 'cesta') {
      var d = c.cesta;
      if (!d || !Array.isArray(d.kroky)) return ids;
      d = Object.assign({}, d, { kroky: d.kroky.filter(function (x) { return x && x.label && x.date; }) });
      if (d.kroky.length < 2) return ids;
      on('s-c-osa', buildOsa(d));
      on('s-c-norm', buildNorm(d, stamps));
      on('s-dny', buildDny(c.pn_dny));
    } else if (c.format === 'zmena') {
      var body = validBody(c.zmena);
      if (!body.length) return ids;
      body.forEach(function (b, i) { on('s-z-' + (i + 1), buildZ(i, b, body.length)); });
      on('s-z-proc', buildProc(c.zmena));
    } else if (c.format === 'rozprava') {
      var r = c.rozprava;
      if (!r || !(+r.projevu > 0)) return ids;
      on('s-r-kdo', buildKdo(r));
      // vyváženost: shrnutí argumentů jen když má doložené body předkladatel i námitky
      var pro = validPoints(r.pro), proti = validPoints(r.namitky);
      if (pro.length && proti.length) {
        on('s-r-pro', buildBul('s-r-pro', r.pro.lab || 'Proč to předkladatel chce', r.pro, pro));
        on('s-r-proti', buildBul('s-r-proti', r.namitky.lab || 'Hlavní námitky', r.namitky, proti));
        var odp = validPoints(r.odpovedi);
        if (odp.length) on('s-r-odp', buildBul('s-r-odp', r.odpovedi.lab || 'Odpověď předkladatele', r.odpovedi, odp));
      }
    } else if (c.format === 'pn') {
      var pn = c.pn;
      if (!pn || !(+pn.count > 0)) return ids;
      on('s-p-kolik', buildKolik(pn));
      on('s-dny', buildDny(c.pn_dny));
      on('s-p-jine', buildJine(pn, stamps));
      on('s-p-top', buildTop(pn));
    }
    return ids;
  }

  function schedule(ids) {
    // délka scény = náběh + čas na čtení; nad strop se krátí čtení, pak se vypouštějí volitelné scény od konce
    for (;;) {
      var base = 0, hold = 0;
      ids.forEach(function (id, i) { base += (i ? LEAD : 0) + SC[id].end + (i < ids.length - 1 ? WIPE : 0); hold += SC[id].hold; });
      var f = 1;
      if (base + hold > MAX_TOTAL) f = (MAX_TOTAL - base) / hold;
      if (base + hold < MIN_TOTAL) f = (MIN_TOTAL - base) / hold;
      if (f < 0.5) {
        var drop = -1;
        for (var k = ids.length - 2; k > 0; k--) if (SC[ids[k]].opt) { drop = k; break; }
        if (drop > 0) { ids.splice(drop, 1); continue; }
        f = Math.max(f, 0.25);
      }
      return ids.map(function (id, i) {
        return [id, (i ? LEAD : 0) + SC[id].end + (i < ids.length - 1 ? WIPE : 0) + SC[id].hold * f];
      });
    }
  }

  window.loadCard = function (c, orientation) {
    card = c;
    if (orientation) setOrientation(orientation);
    SC = {};
    var stamps = stampsFor(c);
    $('top-ct').textContent = 'Tisk ' + c.ct;
    $('footer').textContent = 'tisk ' + c.ct + ' · cosepece.cz · psp.cz · data k ' + czDate(c.data_date);

    buildHook(c);
    var ids = ['s-hook'];
    if (buildCtx(c)) ids.push('s-ctx');
    ids = ids.concat(buildFormat(c, stamps));
    buildEnd(c);
    ids.push('s-end');

    scenes = [];
    var t = 0;
    schedule(ids).forEach(function (p) {
      scenes.push({ id: p[0], el: $(p[0]), start: t, end: t + p[1], dur: p[1] });
      t += p[1];
    });
    total = Math.round(t * 30) / 30;
    var prog = $('prog');
    prog.innerHTML = '';
    scenes.forEach(function () { var seg = mk('div', 'seg'); seg.appendChild(mk('i')); prog.appendChild(seg); });
    Array.prototype.forEach.call(document.querySelectorAll('.scene'), function (el) { el.style.display = 'none'; });
    markSolo();
    window.seek(0);
  };

  window.duration = function () { return total; };
  window.sceneIds = function () { return scenes.map(function (s) { return s.id; }); };

  // ---------- vměstnání textů ----------

  function overflows(body) {
    if (body.scrollHeight > body.clientHeight + 1 || body.scrollWidth > body.clientWidth + 1) return true;
    // na šířku navíc: obsah nesmí přetéct ze svého sloupce do sousedního
    for (var i = 0; LAND && i < body.children.length; i++) {
      var k = body.children[i];
      if (/^side-/.test(k.className) && k.clientWidth && k.scrollWidth > k.clientWidth + 1) return true;
    }
    return false;
  }

  window.fit = function () {
    // každou scénu zmenšuje po 4 % (nejvýš na 60 %), potom zkracuje texty od konce na celá slova
    scenes.forEach(function (s, si) {
      var body = s.el.querySelector('.body');
      // měří se ustálený stav scény (i na výšku): počítadla mají konečnou hodnotu („800 mil. Kč“, ne „0“),
      // a tím i konečnou šířku; v čase 0 by hotová scéna přetekla
      window.seek(si === scenes.length - 1 ? total - 0.05 : s.end - WIPE - 0.1);
      s.el.style.display = 'block';
      // měří se bez animačních posunů (transformace by zkreslily scrollHeight)
      Array.prototype.forEach.call(s.el.querySelectorAll('*'), function (e) { if (e.style) e.style.transform = ''; });
      var els = Array.prototype.filter.call(body.querySelectorAll('[data-fit]'), function (el) {
        return el.dataset.full && el.offsetParent !== null;
      });
      els.forEach(function (el) {
        el.style.fontSize = '';
        el.dataset.base = parseFloat(getComputedStyle(el).fontSize);
      });
      var scale = 1;
      while (overflows(body) && scale > 0.6) {
        scale -= 0.04;
        els.forEach(function (el) { el.style.fontSize = (el.dataset.base * scale) + 'px'; });
      }
      // nejdřív se krátí nejdelší text
      els.slice().sort(function (x, y) { return y.dataset.full.length - x.dataset.full.length; }).forEach(function (el) {
        var full = el.dataset.full;
        var max = full.length;
        while (overflows(body) && max > 20) {
          max = Math.floor(max * 0.92);
          el._set(cutWords(full, max));
          el.dataset.truncated = '1';
        }
      });
      lockSides(body);
      s.el.style.display = 'none';
    });
    window.seek(0);
  };

  function midWord(el) {
    if (el.dataset.truncated !== '1') return false;
    var full = el.dataset.full || '', shown = el.dataset.shown || '';
    var next = full.charAt(shown.replace(/…$/, '').length);
    return next !== '' && !/[\s,;:.–-]/.test(next);
  }

  // Kolik px scéna přetéká a které textové prvky za to můžou (pro zpětnou vazbu generátoru).
  // Bezpečná zóna opisuje SAFE z generator/validate.py (na výšku) a LANDSCAPE_SAFE z render.py (na šířku).
  function overflowInfo(body) {
    // rozdíl do 1 px je zaokrouhlení, stejně jako v overflows()
    function excess(d) { return d > 1 ? d : 0; }
    var px = Math.max(excess(body.scrollHeight - body.clientHeight), excess(body.scrollWidth - body.clientWidth));
    for (var i = 0; LAND && i < body.children.length; i++) {
      var k = body.children[i];
      if (/^side-/.test(k.className) && k.clientWidth) px = Math.max(px, excess(k.scrollWidth - k.clientWidth));
    }
    var b = body.getBoundingClientRect();
    var safe = LAND ? { top: 60, left: 60, bottom: window.innerHeight - 60, right: window.innerWidth - 60 }
                    : { top: 150, left: 60, bottom: 1920 - 340, right: 1020 };
    var lim = { top: Math.max(b.top, safe.top), left: Math.max(b.left, safe.left),
                bottom: Math.min(b.bottom, safe.bottom), right: Math.min(b.right, safe.right) };
    var over = [], seen = {};
    // přesahující prvky a prvky, které fit() musel zkrátit (podílejí se na nedostatku místa);
    // jméno = data-m (pole karty), jinak id; délka = celý text karty, ne zkrácený
    Array.prototype.forEach.call(body.querySelectorAll('[data-m],[data-fit]'), function (el) {
      var name = el.dataset.m || el.id;
      if (!name || seen[name] || el.offsetParent === null) return;
      var r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return;
      var out = r.top < lim.top - 1 || r.left < lim.left - 1 || r.bottom > lim.bottom + 1 || r.right > lim.right + 1;
      if (!out && el.dataset.truncated !== '1') return;
      seen[name] = true;
      over.push({ m: name, len: (el.dataset.full || el.textContent || '').trim().length });
    });
    return { px: Math.ceil(px), over: over };
  }

  window.measureBlocks = function () {
    // Ustálený stav každé scény (po náběhu, před stěrkou): obdélníky prvků s data-m a obálka celé scény
    // (klíč "scene:<id>": sjednocení všech viditelných prvků těla, přetečení těla, zkrácení uprostřed slova).
    var out = {};
    scenes.forEach(function (s, i) {
      window.seek(i === scenes.length - 1 ? total - 0.05 : s.end - WIPE - 0.1);
      var body = s.el.querySelector('.body');
      var u = { top: 1e9, left: 1e9, bottom: -1e9, right: -1e9 };
      var cut = false;
      Array.prototype.forEach.call(body.querySelectorAll('*'), function (el) {
        if (el.offsetParent === null || /^side-/.test(el.className)) return;
        var r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return;
        u.top = Math.min(u.top, r.top); u.left = Math.min(u.left, r.left);
        u.bottom = Math.max(u.bottom, r.bottom); u.right = Math.max(u.right, r.right);
        if (el.dataset.fit && midWord(el)) cut = true;
        if (el.dataset.m) {
          out[el.dataset.m] = { top: r.top, bottom: r.bottom, left: r.left, right: r.right, cut_mid_word: midWord(el),
            visible: +getComputedStyle(el).opacity > 0.99, text: el.textContent, truncated: el.dataset.truncated === '1' };
        }
      });
      var info = overflowInfo(body);
      out['scene:' + s.id] = { top: u.top, bottom: u.bottom, left: u.left, right: u.right, cut_mid_word: cut,
        overflow: overflows(body), overflow_px: info.px, over: info.over };
    });
    window.seek(0);
    return out;
  };

  // ---------- zvukové události ----------

  window.audioCues = function () {
    // Seznam {t, type, gain?, pos?, dur?} podle skutečné časové osy karty (volat po loadCard a fit).
    // Typy shodné s v2: whoosh, tick, hit, stamp, type, highlight, drop, line, slide, strike, pop, outro.
    var cues = [];
    scenes.forEach(function (s, i) {
      var lead = i ? LEAD : 0;
      if (i) cues.push({ t: Math.round(s.start * 1000) / 1000, type: 'whoosh', gain: 1, pos: i % 2 });
      if (i === scenes.length - 1) cues.push({ t: Math.round(s.start * 1000) / 1000, type: 'outro' });
      SC[s.id].cues.forEach(function (c) {
        if (c.t < 0 || c.t + lead > s.dur - 0.05) return;   // po konci scény už není co slyšet
        var o = {};
        for (var k in c) o[k] = c[k];
        o.t = Math.round((s.start + lead + c.t) * 1000) / 1000;
        cues.push(o);
      });
    });
    cues.sort(function (a, b) { return a.t - b.t; });
    return cues;
  };

  // ---------- přehrávání ----------

  window.seek = function (t) {
    if (!scenes.length) return;
    fx.x = 0; fx.y = 0; fx.flash = 0;
    var last = scenes[scenes.length - 1];
    scenes.forEach(function (s, i) {
      var on = t >= s.start && (t < s.end || s === last);
      s.el.style.display = on ? 'block' : 'none';
      // ukazatel průběhu nahoře
      $('prog').children[i].firstChild.style.transform = 'scaleX(' + clamp((t - s.start) / s.dur) + ')';
      if (on) {
        var L = t - s.start - (i ? LEAD : 0);
        SC[s.id].beats.forEach(function (b) { b(L); });
      }
    });

    // stěrka mezi scénami: zakryje zleva (nebo shora), po střihu odkryje
    var wipe = $('wipe');
    wipe.style.transform = 'scaleX(0)';
    for (var i = 1; i < scenes.length; i++) {
      var d = t - scenes[i].start;
      if (d < -WIPE || d >= WIPE) continue;
      var axis = i % 2 ? 'X' : 'Y';
      if (d < 0) {
        wipe.style.transformOrigin = '0 0';
        wipe.style.transform = 'scale' + axis + '(' + inOut((d + WIPE) / WIPE) + ')';
      } else {
        wipe.style.transformOrigin = '100% 100%';
        wipe.style.transform = 'scale' + axis + '(' + (1 - inOut(d / WIPE)) + ')';
      }
    }

    // pozadí v pomalém pohybu, blikající tečka v hlavičce
    $('dots').style.transform = 'translate(' + (-(t * 9) % 54) + 'px,' + (-(t * 14) % 54) + 'px)';
    $('ring').style.transform = 'rotate(' + (t * 5) + 'deg)';
    $('rec').style.opacity = 0.55 + 0.45 * Math.cos(t * 5);

    $('stage').style.transform = 'translate(' + fx.x.toFixed(2) + 'px,' + fx.y.toFixed(2) + 'px)';
    $('flash').style.opacity = fx.flash;
    $('frame').className = t >= last.start ? 'end' : '';
  };
})();
