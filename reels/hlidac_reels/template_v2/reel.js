// Šablona Reelu v2. Rozhraní shodné s v1: loadCard(card, orientation), fit(), seek(t), duration(), measureBlocks();
// navíc audioCues() (časy zvukových efektů pro hlidac_reels/audio.py).
// Stav obrazu závisí jen na t (žádné časovače, rAF ani CSS animace), render jde snímek po snímku.
// Volitelná pole karty (blok se ukáže jen s daty): typ_navrhu, predlozeno, shift, timeline, pn, deadline,
// predkladatel (scéna „Kdo to přináší“) a predkladatel.citace (scéna „Úvodní slovo na plénu“).
(function () {
  'use strict';

  var STAMPS = { deadline_cut: 'ZKRÁTILI', pn_other_law: 'PŘÍLEPEK', p90_passed: '§ 90/2' };
  var MAX_TOTAL = 30;    // strop délky videa (s)
  var MIN_TOTAL = 15;
  var LEAD = 0.12;       // scéna začíná animovat až po odkrytí stěrkou
  var WIPE = 0.2;        // délka zakrytí i odkrytí stěrkou (s)
  var scenes = [];       // [{id, el, start, end, dur}]
  var total = 0;
  var card = null;
  var footBase = '', footSaid = '';   // patka; ve scéně s citací z pléna uvádí schůzi
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

  function czDate(iso) {
    var p = String(iso || '').slice(0, 10).split('-');
    return p.length === 3 ? (+p[2]) + '. ' + (+p[1]) + '. ' + p[0] : '';
  }

  function cutWords(text, max) {
    // zkrátí na celé slovo a přidá „…“
    if (text.length <= max) return text;
    var cut = text.slice(0, max);
    var sp = cut.lastIndexOf(' ');
    if (sp > max * 0.5) cut = cut.slice(0, sp);
    return cut.replace(/[\s,;:–-]+$/, '') + '…';
  }

  function shortReason(label) {
    // jen první část popisku (před „ – “), bez závorky se jménem, nejvýš 70 znaků
    var s = String(label).split(' – ')[0].replace(/\s*\([^)]*\)/g, '').trim();
    return s.length > 70 ? cutWords(s, 70) : s;
  }

  function stampsFor(c) {
    var out = [];
    (c.reasons || []).forEach(function (r) {
      if (STAMPS[r.key] && out.indexOf(STAMPS[r.key]) < 0) out.push(STAMPS[r.key]);
    });
    return out;
  }

  function initials(name) {
    // monogram: první písmeno jména a příjmení
    var p = String(name).split(/\s+/).filter(Boolean);
    return ((p[0] || '').charAt(0) + (p.length > 1 ? p[p.length - 1].charAt(0) : '')).toUpperCase();
  }

  function setOpt(el, text) {
    el.textContent = text || '';
    el.style.display = text ? '' : 'none';
  }

  function plural(n, one, few, many) {
    return n === 1 ? one : (n >= 2 && n <= 4 ? few : many);
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
        if (k) wi.appendChild(document.createTextNode(' '));
        if (highlight && /\d/.test(tok)) wi.appendChild(mk('b', 'num', tok));
        else wi.appendChild(document.createTextNode(tok));
      });
      w.appendChild(wi);
      el.appendChild(w);
      el.appendChild(document.createTextNode(' '));
    }
  }

  function setTyped(el, text) {
    // citace: slova včetně mezery jako řádkové prvky (psací stroj + zvýrazňovač)
    el.innerHTML = '';
    el.dataset.shown = text;
    String(text).split(/\s+/).filter(Boolean).forEach(function (tok) {
      el.appendChild(mk('span', 'qw', tok + ' '));
    });
  }

  function fill(el, text, setter) {
    el._set = setter;
    el.dataset.full = text;
    delete el.dataset.truncated;
    el.style.fontSize = '';
    setter(text);
  }

  function indexLines(el) {
    // číslo řádku každého slova (pro náběh po řádcích); scéna musí být zobrazená
    var top = null, line = -1;
    Array.prototype.forEach.call(el.querySelectorAll('.w'), function (w) {
      if (top === null || Math.abs(w.offsetTop - top) > 4) { line++; top = w.offsetTop; }
      w.dataset.line = line;
    });
  }

  function addStamps(box, list) {
    box.innerHTML = '';
    list.forEach(function (s) { box.appendChild(mk('div', 'stamp', s)); });
  }

  // ---------- naplnění karty ----------

  function validOptional(c) {
    var o = {};
    var s = c.shift;
    if (s && s.from != null && s.to != null && String(s.from) !== '' && String(s.to) !== '') o.shift = s;
    var d = c.deadline;
    if (d && isFinite(+d.normal) && isFinite(+d.actual) && +d.normal > 0 && +d.actual >= 0 && +d.actual < +d.normal) o.deadline = d;
    if (Array.isArray(c.timeline)) {
      var steps = c.timeline.filter(function (x) { return x && x.label; });
      if (steps.length >= 2) o.timeline = steps;
    }
    if (c.pn && +c.pn.count > 0) o.pn = c.pn;
    // předkladatel: bez jména a typu návrhu se scéna neukáže; citace jen s textem a odkazem na stenozáznam
    var pk = c.predkladatel;
    if (pk && pk.jmeno && pk.typ) {
      o.who = pk;
      if (pk.citace && pk.citace.text && pk.citace.url) o.said = pk.citace;
    }
    return o;
  }

  function buildPlan(c, opt) {
    var tl = (c.title || '').length, pl = (c.perex || '').length, ql = (c.quote || '').length;
    var extras = [];   // v pořadí, v jakém se při nedostatku času vyhazují
    if (opt.timeline || opt.pn) extras.push('s-path');
    if (opt.deadline) extras.push('s-deadline');
    if (opt.shift) extras.push('s-shift');
    // Reel o předkladateli: slovo má předkladatel, citace z textu návrhu ustoupí jako poslední, když se video nevejde
    var quoteOptional = !!(opt.said && ql);
    if (quoteOptional) extras.push('s-quote');
    for (;;) {
      var plan = [['s-hook', 2.8]];
      if (opt.who) plan.push(['s-who', opt.who.skupina && +opt.who.skupina.count > 1 ? 4.8 : 4.0]);
      if (opt.said) plan.push(['s-said', lim(2.6 + String(opt.said.text).length / 45, 4.0, 6.5)]);
      plan.push(['s-title', lim(2.4 + tl / 50, 3.2, 5.2)]);
      if (extras.indexOf('s-shift') >= 0) plan.push(['s-shift', 3.4]);
      if (pl) plan.push(['s-perex', lim(1.8 + pl / 42, 3.5, 5.5)]);
      if (ql && (!quoteOptional || extras.indexOf('s-quote') >= 0)) plan.push(['s-quote', lim(2.2 + ql / 50, 3.4, 5.5)]);
      if (extras.indexOf('s-path') >= 0) plan.push(['s-path', opt.timeline && opt.pn ? 4.6 : 3.4]);
      if (extras.indexOf('s-deadline') >= 0) plan.push(['s-deadline', 3.6]);
      plan.push(['s-reasons', 4.4], ['s-end', 3.0]);
      var sum = plan.reduce(function (a, p) { return a + p[1]; }, 0);
      var f = 1;
      if (sum > MAX_TOTAL) f = MAX_TOTAL / sum;
      if (sum < MIN_TOTAL) f = MIN_TOTAL / sum;
      if (f < 0.9 && extras.length) { extras.shift(); continue; }   // moc dlouhé: vypustit nejméně důležitý blok
      return plan.map(function (p) { return [p[0], p[1] * f]; });
    }
  }

  window.loadCard = function (c, orientation) {
    card = c;
    if (orientation) setOrientation(orientation);
    var opt = validOptional(c);
    var stamps = stampsFor(c);

    $('top-ct').textContent = 'Tisk ' + c.ct;
    footBase = 'tisk ' + c.ct + ' · cosepece.cz · psp.cz · data k ' + czDate(c.data_date);
    footSaid = footBase;
    $('footer').textContent = footBase;

    // hook
    var ticks = $('ticks');
    ticks.innerHTML = '';
    for (var i = 0; i < 50; i++) ticks.appendChild(mk('i'));
    fill($('hook-name'), c.nazev || '', function (t) { setWords($('hook-name'), t, false); });
    $('hook-faze').textContent = 'Fáze: ' + c.faze;
    var meta = [];
    if (c.typ_navrhu) meta.push(String(c.typ_navrhu));
    if (c.predlozeno) meta.push('předloženo ' + czDate(c.predlozeno));
    $('hook-meta').textContent = meta.join(' · ');
    $('hook-meta').style.display = meta.length ? '' : 'none';

    // titulek, perex, citace
    fill($('title'), c.title || '', function (t) { setWords($('title'), t, true); });
    fill($('perex'), c.perex || '', function (t) { setWords($('perex'), t, true); });
    var quote = (c.quote || '').replace(/^[„"“]+|[“"”]+$/g, '');
    fill($('quote'), quote, function (t) { setTyped($('quote'), t); });
    // krátká citace větším písmem, ať výstřižek nepůsobí ztraceně
    $('quote').className = 'q' + (quote.length <= 90 ? ' short' : '');
    $('quote-source').textContent = 'citace: ' + c.quote_source;

    // volitelně: předkladatel (kdo návrh přináší) a jeho slova na plénu
    if (opt.who) {
      var w = opt.who;
      $('who-typ').textContent = w.typ;
      $('who-ini').textContent = initials(w.jmeno);
      fill($('who-name'), String(w.jmeno), function (t) { setWords($('who-name'), t, false); });
      setOpt($('who-funkce'), w.funkce);
      setOpt($('who-klub'), w.klub ? 'klub ' + w.klub : '');
      setOpt($('who-predlozeno'), w.predlozeno ? 'předloženo ' + czDate(w.predlozeno) : '');
      $('who-meta').style.display = w.klub || w.predlozeno ? '' : 'none';
      var g = w.skupina && +w.skupina.count > 1 ? w.skupina : null;
      $('who-sig').style.display = g ? '' : 'none';
      var sl = $('who-sig-slips'), kl = $('who-sig-kluby');
      sl.innerHTML = '';
      kl.innerHTML = '';
      if (g) {
        var gn = +g.count;
        $('who-sig-word').textContent = plural(gn, 'poslanec', 'poslanci', 'poslanců');
        for (var q = 0; q < Math.min(gn, 40); q++) sl.appendChild(mk('i'));
        (g.kluby || []).forEach(function (k) {
          if (k && k.klub) kl.appendChild(mk('span', 'klub', k.klub + (+k.count > 0 ? ' ' + (+k.count) : '')));
        });
        kl.style.display = kl.firstChild ? '' : 'none';
      }
      fill($('who-note'), w.poznamka || '', function (t) { $('who-note').textContent = t; $('who-note').dataset.shown = t; });
      $('who-note').style.display = w.poznamka ? '' : 'none';
    }
    if (opt.said) {
      var ci = opt.said;
      var rc = ci.recnik && ci.recnik.jmeno ? ci.recnik : opt.who;
      $('said-lab').textContent = 'Úvodní slovo na plénu' + (ci.cteni ? ' · ' + ci.cteni : '');
      var said = String(ci.text).replace(/^[„"“]+|[“"”]+$/g, '');
      fill($('said-quote'), said, function (t) { setTyped($('said-quote'), t); });
      $('said-quote').className = 'q' + (said.length <= 90 ? ' short' : '');
      $('said-name').textContent = rc.jmeno + (ci.za ? ' · ' + ci.za : '');
      // funkci opakuje jen u jiného řečníka, než je předkladatel z předchozí scény
      setOpt($('said-funkce'), [rc === opt.who ? '' : rc.funkce, rc.klub ? 'klub ' + rc.klub : ''].filter(Boolean).join(' · '));
      var src = [];
      if (ci.schuze) src.push(ci.schuze + '. schůze PS');
      if (ci.datum) src.push(czDate(ci.datum));
      $('said-source').textContent = src.length ? src.join(' · ') : 'stenozáznam psp.cz';
      // neautorizovaný stenozáznam musí být vidět; chybějící údaj se bere jako neautorizováno
      $('said-unauth').style.display = ci.autorizovano === true ? 'none' : '';
      if (ci.schuze) footSaid = 'tisk ' + c.ct + ' · ' + ci.schuze + '. schůze · psp.cz · data k ' + czDate(c.data_date);
    }

    // volitelně: změna X → Y
    if (opt.shift) {
      $('shift-lab').textContent = opt.shift.label || '';
      $('shift-from').textContent = opt.shift.from;
      $('shift-to').textContent = opt.shift.to;
      $('shift-row').className = 'shiftrow' + ((String(opt.shift.from) + String(opt.shift.to)).length > 4 ? ' small' : '');
      $('shift-unit').textContent = opt.shift.unit || '';
      $('shift-unit').style.display = opt.shift.unit ? '' : 'none';
      fill($('shift-note'), opt.shift.note || '', function (t) { $('shift-note').textContent = t; $('shift-note').dataset.shown = t; });
      $('shift-note').style.display = opt.shift.note ? '' : 'none';
    }

    // volitelně: průběh projednávání a počet pozměňovacích návrhů
    var rows = $('tl-rows');
    rows.innerHTML = '';
    $('tl').style.display = opt.timeline ? '' : 'none';
    $('pn').style.display = opt.pn ? '' : 'none';
    $('pn').className = opt.timeline ? '' : 'solo';
    $('path-lab').textContent = opt.timeline ? 'Průběh projednávání' : 'Pozměňovací návrhy';
    if (opt.timeline) {
      var steps = opt.timeline.slice(-(opt.pn ? 4 : 6));
      $('tl').style.setProperty('--n', steps.length);
      steps.forEach(function (s, k) {
        var row = mk('div', 'step' + (k === steps.length - 1 ? ' last' : ''));
        row.appendChild(mk('i', 'dot'));
        var body = mk('div', 'stepbody');
        body.appendChild(mk('div', 'd', czDate(s.date) || String(s.date || '')));
        body.appendChild(mk('div', 's', cutWords(String(s.label), 60)));
        row.appendChild(body);
        rows.appendChild(row);
      });
    }
    if (opt.pn) {
      var n = +opt.pn.count;
      $('pn-word').textContent = plural(n, 'pozměňovací návrh', 'pozměňovací návrhy', 'pozměňovacích návrhů');
      var a = +opt.pn.authors;
      $('pn-authors').textContent = a > 0 ? 'od ' + a + ' ' + plural(a, 'poslance', 'poslanců', 'poslanců') : '';
      var slips = $('pn-slips');
      slips.innerHTML = '';
      var shown = Math.min(n, 48);
      var late = Math.min(+opt.pn.late_count || 0, n);
      var lateShown = Math.round(shown * late / n);
      for (var j = 0; j < shown; j++) slips.appendChild(mk('i', j >= shown - lateShown ? 'late' : ''));
      $('pn-late').textContent = late > 0 && opt.pn.late_label ? late + ' ' + opt.pn.late_label : '';
      $('pn-late').style.display = $('pn-late').textContent ? '' : 'none';
    }

    // volitelně: NORMÁLNĚ × TADY
    if (opt.deadline) {
      var unit = opt.deadline.unit || 'dní';
      $('dl-lab').textContent = opt.deadline.label || 'Lhůta';
      $('dl-normal').textContent = +opt.deadline.normal;
      $('dl-actual').textContent = +opt.deadline.actual;
      $('dl-unit-n').textContent = unit;
      $('dl-unit-a').textContent = unit;
      addStamps($('dl-stamps'), stamps.indexOf('ZKRÁTILI') >= 0 ? ['ZKRÁTILI'] : []);
    }

    // důvody skóre: nejvýš 4 (se dvěma a více razítky 3), bez nulových
    var list = $('reasons');
    list.innerHTML = '';
    var reasons = (c.reasons || []).filter(function (r) { return r.points > 0; })
      .sort(function (x, y) { return y.points - x.points; }).slice(0, stamps.length >= 2 ? 3 : 4);
    var maxPts = reasons.length ? reasons[0].points : 1;
    reasons.forEach(function (r) {
      var li = mk('div', 'li' + (STAMPS[r.key] ? ' hot' : ''));
      li.dataset.points = r.points;
      li.dataset.share = r.points / maxPts;
      var row = mk('div', 'lirow');
      row.appendChild(mk('span', 'litxt', shortReason(r.label)));
      row.appendChild(mk('span', 'pts', '+' + r.points));
      li.appendChild(row);
      var track = mk('div', 'track');
      track.appendChild(mk('i'));
      li.appendChild(track);
      list.appendChild(li);
    });
    addStamps($('stamps'), stamps);

    // konec
    var logo = $('end-logo');
    logo.innerHTML = '';
    'cosepece.cz'.split('').forEach(function (ch) { logo.appendChild(mk('span', '', ch)); });
    $('end-ct').textContent = 'Tisk ' + c.ct + ' · ' + c.faze;
    $('end-url').textContent = String(c.url || ('cosepece.cz/tisk.php?ct=' + c.ct)).replace(/^https?:\/\//, '');
    $('ai-note').textContent = 'Shrnutí AI, doloženo citací' + (c.verified ? ', ověřeno druhým modelem' : '');

    // časová osa
    scenes = [];
    var t = 0;
    buildPlan(c, opt).forEach(function (p) {
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
    // každou scénu zmenšuje po 4 % (nejvýš na 60 %), potom zkracuje text na celá slova
    scenes.forEach(function (s, si) {
      var body = s.el.querySelector('.body');
      // na šířku se měří ustálený stav scény: počítadla mají konečnou hodnotu, a tím i konečnou šířku sloupce
      if (LAND) window.seek(si === scenes.length - 1 ? total - 0.05 : s.end - WIPE - 0.1);
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
      els.forEach(function (el) {
        var full = el.dataset.full;
        var max = full.length;
        while (overflows(body) && max > 20) {
          max = Math.floor(max * 0.92);
          el._set(cutWords(full, max));
          el.dataset.truncated = '1';
        }
      });
      if (s.id === 's-perex') indexLines($('perex'));
      lockSides(body);
      s.el.style.display = 'none';
    });
    window.seek(0);
  };

  window.measureBlocks = function () {
    // obdélníky textových bloků v ustáleném stavu scény (po náběhu, před stěrkou) a kontrola zkrácení
    var out = {};
    ['title', 'perex', 'quote'].forEach(function (id) {
      var el = $(id);
      var sc = el.closest('.scene');
      var s = scenes.filter(function (x) { return x.el === sc; })[0];
      if (s) window.seek(s.end - WIPE - 0.1);
      else sc.style.display = 'block';
      var r = el.getBoundingClientRect();
      var full = el.dataset.full || '';
      var shown = el.dataset.shown || '';
      var midWord = false;
      if (el.dataset.truncated === '1') {
        var kept = shown.replace(/…$/, '');
        var next = full.charAt(kept.length);
        midWord = next !== '' && !/[\s,;:.–-]/.test(next);
      }
      out[id] = { top: r.top, bottom: r.bottom, left: r.left, right: r.right, cut_mid_word: midWord };
      if (!s) sc.style.display = 'none';
    });
    // volitelné scény předkladatele: jen když jsou v osnově
    [['who', 'who-name', 's-who'], ['said', 'said-quote', 's-said'], ['said_unauth', 'said-unauth', 's-said']].forEach(function (x) {
      var s = scenes.filter(function (y) { return y.id === x[2]; })[0];
      if (!s) return;
      window.seek(s.end - WIPE - 0.1);
      var el = $(x[1]);
      var r = el.getBoundingClientRect();
      var full = el.dataset.full || '', shown = el.dataset.shown || '', midWord = false;
      if (el.dataset.truncated === '1') {
        var next = full.charAt(shown.replace(/…$/, '').length);
        midWord = next !== '' && !/[\s,;:.–-]/.test(next);
      }
      out[x[0]] = { top: r.top, bottom: r.bottom, left: r.left, right: r.right, cut_mid_word: midWord,
        visible: r.width > 0 && r.height > 0 && +getComputedStyle(el).opacity > 0.99, text: el.textContent };
    });
    // obálka každé scény v ustáleném stavu (klíč "scene:<id>"): sjednocení viditelných prvků těla, přetečení těla
    scenes.forEach(function (s, i) {
      window.seek(i === scenes.length - 1 ? total - 0.05 : s.end - WIPE - 0.1);
      var body = s.el.querySelector('.body');
      var u = { top: 1e9, left: 1e9, bottom: -1e9, right: -1e9 };
      Array.prototype.forEach.call(body.querySelectorAll('*'), function (el) {
        if (el.offsetParent === null || /^side-/.test(el.className)) return;
        var r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return;
        u.top = Math.min(u.top, r.top); u.left = Math.min(u.left, r.left);
        u.bottom = Math.max(u.bottom, r.bottom); u.right = Math.max(u.right, r.right);
      });
      out['scene:' + s.id] = { top: u.top, bottom: u.bottom, left: u.left, right: u.right, overflow: overflows(body) };
    });
    window.seek(0);
    return out;
  };

  // ---------- zvukové události ----------

  window.audioCues = function () {
    // Seznam {t, type, gain?, pos?, dur?} podle skutečné časové osy karty (volat po loadCard a fit).
    // Časy opisují konstanty z ANIM níže; při změně animace uprav i tady.
    // Typy: whoosh (stěrka), tick (počítadlo), hit (dosednutí skóre), stamp (dopad razítka), type (slovo citace),
    // highlight (zvýrazňovač, s dur), drop (dopad písmene), line (řádek textu), slide (příjezd bloku), strike (škrt),
    // pop (bod osy), outro (začátek závěrečné scény). pos 0–1 = pořadí v řadě (výška tónu, strana).
    var cues = [];
    function inv(pow) { return function (y) { return 1 - Math.pow(1 - y, 1 / pow); }; }
    function invInOut(y) { return y < 0.5 ? Math.pow(y / 4, 1 / 3) : 1 - Math.pow(2 * (1 - y), 1 / 3) / 2; }
    scenes.forEach(function (s, i) {
      var lead = i ? LEAD : 0;
      function add(local, type, extra) {
        if (local < 0 || local + lead > s.dur - 0.05) return;   // po konci scény už není co slyšet
        var c = { t: Math.round((s.start + lead + local) * 1000) / 1000, type: type };
        for (var k in (extra || {})) c[k] = extra[k];
        cues.push(c);
      }
      function counter(t0, d, n, invEase, gain, skipLast) {
        // tiky počítadla rozložené podle průběhu čísla (inverze easingu)
        for (var k = 1; k <= n - (skipLast ? 1 : 0); k++) add(t0 + d * invEase(k / n), 'tick', { gain: gain, pos: k / n });
      }
      function stamp(t0) { add(t0 + 0.24, 'stamp', { gain: 1 }); }

      if (i) cues.push({ t: Math.round(s.start * 1000) / 1000, type: 'whoosh', gain: 1, pos: i % 2 });
      if (i === scenes.length - 1) cues.push({ t: Math.round(s.start * 1000) / 1000, type: 'outro' });

      if (s.id === 's-hook') {
        counter(0.1, 1.05, 14, inv(3), 0.8, true);
        add(1.15, 'hit', { gain: 0.8 });
        add(0.95, 'slide', { gain: 0.5, dur: 0.5 });
        add(1.4, 'pop', { gain: 0.6 });
      } else if (s.id === 's-who') {
        add(0.1, 'pop', { gain: 0.8 });                       // typ návrhu
        add(0.3, 'drop', { gain: 1, pos: 0.2 });              // monogram dosedne
        add(0.5, 'slide', { gain: 0.7, dur: 0.5 });           // jméno
        add(1.0, 'line', { gain: 0.8, pos: 0 });              // funkce
        if ($('who-meta').style.display !== 'none') add(1.2, 'pop', { gain: 0.6 });
        var sg = $('who-sig').style.display !== 'none' ? $('who-sig-slips').children.length : 0;
        if (sg) {
          // každý navrhovatel jeden tik, potom štítky klubů
          var sstep = Math.min(0.09, 0.9 / sg);
          for (var g = 0; g < sg; g++) add(1.75 + g * sstep, 'tick', { gain: 0.7, pos: sg > 1 ? g / (sg - 1) : 0 });
          var kn = $('who-sig-kluby').children.length;
          for (var kk = 0; kk < kn; kk++) add(1.85 + sg * sstep + kk * 0.14, 'line', { gain: 0.7, pos: kn > 1 ? kk / (kn - 1) : 0 });
        }
      } else if (s.id === 's-said') {
        var an = $('said-quote').children.length;
        var astep = Math.min(0.06, 1.5 / Math.max(1, an));
        add(0.05, 'slide', { gain: 1, dur: 0.55 });
        for (var a = 0; a < an; a++) add(0.5 + a * astep, 'type', { gain: 0.8, pos: an > 1 ? a / (an - 1) : 0 });
        if (an) add(0.5 + an * astep + 0.2, 'highlight', { gain: 1, dur: 0.9 });
        add(0.5 + an * astep + 0.1, 'line', { gain: 0.7, pos: 1 });
        if ($('said-unauth').style.display !== 'none') add(0.5 + an * astep + 0.55, 'pop', { gain: 0.9 });
      } else if (s.id === 's-title') {
        var tw = $('title').querySelectorAll('.wi').length;
        var tstep = Math.min(0.07, 1.1 / Math.max(1, tw));
        add(0.1, 'slide', { gain: 0.7, dur: Math.max(0.4, tw * tstep + 0.3) });
        add(0.1 + tw * tstep + 0.2, 'strike', { gain: 0.5 });
      } else if (s.id === 's-shift') {
        add(0.1, 'pop', { gain: 0.8 });
        add(0.75, 'strike', { gain: 1 });
        add(1.0, 'slide', { gain: 0.5, dur: 0.25 });
        stamp(1.25);
      } else if (s.id === 's-perex') {
        var lines = 0;
        Array.prototype.forEach.call($('perex').querySelectorAll('.w'), function (w) {
          lines = Math.max(lines, +(w.dataset.line || 0) + 1);
        });
        for (var l = 0; l < lines; l++) add(0.15 + l * 0.13 + 0.1, 'line', { gain: 0.8, pos: lines > 1 ? l / (lines - 1) : 0 });
      } else if (s.id === 's-quote') {
        var qn = $('quote').children.length;
        var qstep = Math.min(0.06, 1.5 / Math.max(1, qn));
        add(0.05, 'slide', { gain: 1, dur: 0.55 });
        for (var q = 0; q < qn; q++) add(0.5 + q * qstep, 'type', { gain: 0.8, pos: qn > 1 ? q / (qn - 1) : 0 });
        if (qn) add(0.5 + qn * qstep + 0.2, 'highlight', { gain: 1, dur: 0.9 });
      } else if (s.id === 's-path') {
        var n = $('tl-rows').children.length;
        for (var d = 0; d < n; d++) add(0.15 + d * 0.28, 'pop', { gain: d === n - 1 ? 1 : 0.7, pos: n > 1 ? d / (n - 1) : 0 });
        if (card.pn && $('pn').style.display !== 'none') counter(0.2 + n * 0.28 + 0.05, 0.7, 8, inv(3), 0.6, false);
      } else if (s.id === 's-deadline') {
        add(0.15, 'slide', { gain: 0.6, dur: 0.5 });
        add(0.75, 'slide', { gain: 0.6, dur: 0.35 });
        // číslo se počítá dolů: tiky klesají
        for (var k = 1; k <= 10; k++) add(1.1 + 0.9 * invInOut(k / 10), 'tick', { gain: 0.7, pos: 1 - k / 10 });
        if ($('dl-stamps').firstChild) stamp(2.15);
      } else if (s.id === 's-reasons') {
        var rows = $('reasons').children.length;
        for (var r = 0; r < rows; r++) {
          add(0.1 + r * 0.2, 'line', { gain: 1, pos: rows > 1 ? r / (rows - 1) : 0 });
          counter(0.1 + r * 0.2 + 0.15, 0.7, 4, inv(5), 0.5, false);
        }
        var sn = $('stamps').children.length;
        for (var j = 0; j < sn; j++) stamp(0.5 + rows * 0.2 + 0.5 + j * 0.38);
      } else if (s.id === 's-end') {
        var ln = $('end-logo').children.length;
        // písmeno dosedne zhruba v 65 % svého náběhu (easing back)
        for (var e = 0; e < ln; e++) add(0.05 + e * 0.04 + 0.26, 'drop', { gain: e === ln - 1 ? 1 : 0.7, pos: ln > 1 ? e / (ln - 1) : 0 });
        add(0.8, 'pop', { gain: 0.8 });
      }
    });
    cues.sort(function (a, b) { return a.t - b.t; });
    return cues;
  };

  // ---------- animace ----------

  function show(el, p, dx, dy) {
    // náběh: průhlednost + dojezd z posunu (dx, dy)
    var e = out3(p);
    el.style.opacity = clamp(p * 2.2);
    el.style.transform = 'translate(' + (dx * (1 - e)) + 'px,' + (dy * (1 - e)) + 'px)';
  }

  function pop(el, p, extra) {
    el.style.opacity = clamp(p * 3);
    el.style.transform = 'scale(' + (p <= 0 ? 0 : back(p)) + ')' + (extra || '');
  }

  function maskWords(el, L, t0, span) {
    // slova vyjíždějí zdola zpod masky; vrací čas konce
    var ws = el.querySelectorAll('.wi');
    var step = Math.min(0.07, span / Math.max(1, ws.length));
    for (var i = 0; i < ws.length; i++) {
      var p = out5(P(L, t0 + i * step, 0.5));
      ws[i].style.transform = 'translateY(' + (115 * (1 - p)) + '%)';
    }
    return t0 + ws.length * step + 0.5;
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

  function scoreColor(v) { return v >= 75 ? '#E4002B' : v >= 50 ? '#FFD400' : '#F4F1EA'; }

  var ANIM = {
    's-hook': function (L, s) {
      var kids = [s.el.querySelector('.lab')];
      show(kids[0], P(L, 0, 0.35), 0, 30);
      // počítadlo skóre a stupnice
      var p = P(L, 0.1, 1.05);
      var val = Math.round(card.score * out3(p));
      $('score').textContent = val;
      $('score').style.color = scoreColor(val);
      var tau = L - 1.15;
      var punch = 0.86 + 0.14 * out3(p);
      if (tau >= 0) {
        var decay = Math.exp(-8 * tau);
        punch = 1 + 0.1 * decay * Math.cos(tau * 28);
        fx.x += Math.sin(tau * 63) * 7 * decay;
        fx.y += Math.cos(tau * 49) * 5 * decay;
      }
      $('score-row').style.opacity = clamp(P(L, 0.05, 0.2));
      $('score-row').style.transform = 'scale(' + punch + ')';
      show($('gauge'), P(L, 0.05, 0.3), 0, 30);
      var front = card.score / 2 * out3(p);
      var ticks = $('ticks').children;
      for (var i = 0; i < ticks.length; i++) {
        var lit = i < front;
        var wave = Math.exp(-Math.pow(i - front, 2) / 5) * (p < 1 ? 0.55 : 0);
        var idle = lit && p >= 1 ? 0.07 * Math.sin(L * 5 - i * 0.45) : 0;
        ticks[i].style.background = lit ? scoreColor((i + 1) * 2) : '';
        ticks[i].style.transform = 'scaleY(' + ((lit ? 0.82 : 0.5) + wave + idle) + ')';
      }
      maskWords($('hook-name'), L, 0.95, 0.4);
      pop($('hook-faze'), P(L, 1.4, 0.35));
      show($('hook-meta'), P(L, 1.6, 0.35), -30, 0);
    },

    's-who': function (L, s) {
      var kids = [s.el.querySelector('.lab')];
      show(kids[0], P(L, 0, 0.3), -30, 0);
      $('who-sq').style.opacity = Math.floor(Math.max(0, L) * 2.5) % 2 ? 0.25 : 1;
      pop($('who-typ'), P(L, 0.1, 0.35));
      // monogram vyroste zdola jako portrét, iniciály dojedou o chvíli později
      var m = P(L, 0.3, 0.4);
      $('who-mono').style.opacity = clamp(m * 3);
      $('who-mono').style.transform = 'scaleY(' + (m <= 0 ? 0 : back(m)) + ')';
      show($('who-ini'), P(L, 0.5, 0.35), 0, 40);
      $('who-corner').style.transform = 'scale(' + out5(P(L, 0.7, 0.3)) + ')';
      maskWords($('who-name'), L, 0.5, 0.3);
      show($('who-funkce'), P(L, 1.0, 0.35), -30, 0);
      pop($('who-klub'), P(L, 1.2, 0.35));
      show($('who-predlozeno'), P(L, 1.35, 0.35), -30, 0);
      var slips = $('who-sig-slips').children;
      var n = $('who-sig').style.display !== 'none' ? slips.length : 0;
      var step = Math.min(0.09, 0.9 / Math.max(1, n));
      show($('who-sig'), P(L, 1.6, 0.3), 0, 30);
      var shownN = 0;
      for (var i = 0; i < n; i++) {
        pop(slips[i], P(L, 1.75 + i * step, 0.22));
        if (L >= 1.75 + i * step) shownN = i + 1;
      }
      var gc = card.predkladatel.skupina ? +card.predkladatel.skupina.count : 0;
      $('who-sig-count').textContent = shownN >= n ? gc : shownN;
      var kl = $('who-sig-kluby').children;
      for (var k = 0; k < kl.length; k++) pop(kl[k], P(L, 1.85 + n * step + k * 0.14, 0.3));
      show($('who-note'), P(L, n ? 2.1 + n * step : 1.6, 0.4), 0, 30);
    },

    's-said': function (L, s) {
      show($('said-lab'), P(L, 0, 0.3), -30, 0);
      var p = out5(P(L, 0.05, 0.55));
      $('said-paper').style.opacity = clamp(P(L, 0.05, 0.12));
      $('said-paper').style.transform = 'translateX(' + (-900 * (1 - p)) + 'px) rotate(' + (1 - 9 * (1 - p)) + 'deg)';
      pop($('said-qm'), P(L, 0.4, 0.35));
      var ws = $('said-quote').children;
      var step = Math.min(0.06, 1.5 / Math.max(1, ws.length));
      var typedEnd = 0.5 + ws.length * step;
      var hl = P(L, typedEnd + 0.2, 0.9) * ws.length;
      for (var i = 0; i < ws.length; i++) {
        ws[i].style.opacity = L >= 0.5 + i * step ? 1 : 0;
        ws[i].style.backgroundSize = (100 * clamp(hl - i)) + '% 76%';
      }
      show($('said-who'), P(L, typedEnd + 0.1, 0.35), -30, 0);
      show($('said-source'), P(L, typedEnd + 0.3, 0.35), 0, 30);
      pop($('said-unauth'), P(L, typedEnd + 0.55, 0.3));
    },

    's-title': function (L, s) {
      var kids = [s.el.querySelector('.lab')];
      show(kids[0], P(L, 0, 0.3), -30, 0);
      $('title-sq').style.opacity = Math.floor(L * 2.5) % 2 ? 0.25 : 1;
      var end = maskWords($('title'), L, 0.1, 1.1);
      $('title').style.transform = 'translateY(' + (-3 * Math.max(0, L)) + 'px)';
      $('title-rule').style.transform = 'scaleX(' + out5(P(L, end - 0.3, 0.5)) + ')';
    },

    's-shift': function (L, s) {
      show($('shift-lab'), P(L, 0, 0.3), -30, 0);
      pop($('shift-from-box'), P(L, 0.1, 0.4));
      var st = P(L, 0.75, 0.22);
      $('shift-strike').style.transform = 'rotate(-9deg) scaleX(' + out3(st) + ')';
      var ar = out3(P(L, 1.0, 0.25));
      $('shift-arrow').style.clipPath = 'inset(0 ' + (100 * (1 - ar)) + '% 0 0)';
      stampIn($('shift-to'), L, 1.25, 0);
      show($('shift-unit'), P(L, 1.6, 0.35), 0, 40);
      show($('shift-note'), P(L, 1.9, 0.4), 0, 40);
    },

    's-perex': function (L, s) {
      var kids = [s.el.querySelector('.lab')];
      show(kids[0], P(L, 0, 0.3), -30, 0);
      $('perex-bar').style.transform = 'scaleY(' + out5(P(L, 0.05, 0.6)) + ')';
      var ws = $('perex').querySelectorAll('.w');
      for (var i = 0; i < ws.length; i++) {
        var line = +(ws[i].dataset.line || 0);
        show(ws[i], P(L, 0.15 + line * 0.13 + (i % 7) * 0.012, 0.45), -46, 0);
      }
    },

    's-quote': function (L, s) {
      var kids = [s.el.querySelector('.lab')];
      show(kids[0], P(L, 0, 0.3), -30, 0);
      var p = out5(P(L, 0.05, 0.55));
      $('paper').style.opacity = clamp(P(L, 0.05, 0.12));
      $('paper').style.transform = 'translateX(' + (900 * (1 - p)) + 'px) rotate(' + (-1 + 9 * (1 - p)) + 'deg)';
      pop($('qm'), P(L, 0.4, 0.35));
      var ws = $('quote').children;
      var step = Math.min(0.06, 1.5 / Math.max(1, ws.length));
      var typedEnd = 0.5 + ws.length * step;
      var hl = P(L, typedEnd + 0.2, 0.9) * ws.length;
      for (var i = 0; i < ws.length; i++) {
        ws[i].style.opacity = L >= 0.5 + i * step ? 1 : 0;
        ws[i].style.backgroundSize = (100 * clamp(hl - i)) + '% 76%';
      }
      show($('quote-source'), P(L, typedEnd, 0.35), 0, 30);
    },

    's-path': function (L, s) {
      show($('path-lab'), P(L, 0, 0.3), -30, 0);
      var steps = $('tl-rows').children;
      var n = steps.length;
      $('tl-line').style.transform = (LAND ? 'scaleX(' : 'scaleY(') + out3(P(L, 0.1, 0.28 * n + 0.1)) + ')';
      for (var i = 0; i < n; i++) {
        var t0 = 0.15 + i * 0.28;
        var dot = steps[i].firstChild;
        var pulse = i === n - 1 && L > t0 + 0.4 ? 1 + 0.22 * Math.abs(Math.sin((L - t0) * 3.2)) : 1;
        dot.style.opacity = clamp(P(L, t0, 0.1));
        dot.style.transform = 'scale(' + (P(L, t0, 0.3) <= 0 ? 0 : back(P(L, t0, 0.3)) * pulse) + ')';
        show(steps[i].lastChild, P(L, t0 + 0.05, 0.35), -40, 0);
      }
      var b = 0.2 + n * 0.28;
      if (card.pn) {
        show($('pn'), P(L, b, 0.3), 0, 30);
        $('pn-count').textContent = Math.round((+card.pn.count || 0) * out3(P(L, b + 0.05, 0.7)));
        var slips = $('pn-slips').children;
        var stepS = 0.9 / Math.max(1, slips.length);
        for (var j = 0; j < slips.length; j++) pop(slips[j], P(L, b + 0.2 + j * stepS, 0.22));
        show($('pn-late'), P(L, b + 1.15, 0.35), -30, 0);
      }
    },

    's-deadline': function (L, s) {
      var d = card.deadline;
      var normal = +d.normal, actual = +d.actual;
      show($('dl-lab'), P(L, 0, 0.3), -30, 0);
      show($('dl-row-n'), P(L, 0.1, 0.35), 0, 40);
      $('dl-bar-n').style.transform = 'scaleX(' + out5(P(L, 0.15, 0.6)) + ')';
      show($('dl-row-a'), P(L, 0.7, 0.35), 0, 40);
      // TADY: pruh i číslo začínají na běžné lhůtě a smrští se na skutečnou
      var cut = inOut(P(L, 1.1, 0.9));
      var grow = out5(P(L, 0.75, 0.4));
      $('dl-bar-a').style.transform = 'scaleX(' + (grow * (1 - cut * (1 - actual / normal))) + ')';
      $('dl-actual').textContent = Math.round(normal - (normal - actual) * cut);
      var st = $('dl-stamps').firstChild;
      if (st) stampIn(st, L, 2.15, -7);
    },

    's-reasons': function (L, s) {
      var kids = [s.el.querySelector('.lab')];
      show(kids[0], P(L, 0, 0.3), -30, 0);
      var rows = $('reasons').children;
      for (var i = 0; i < rows.length; i++) {
        var t0 = 0.1 + i * 0.2;
        show(rows[i], P(L, t0, 0.4), 90, 0);
        var g = out5(P(L, t0 + 0.15, 0.7));
        rows[i].querySelector('.track i').style.transform = 'scaleX(' + (g * rows[i].dataset.share) + ')';
        rows[i].querySelector('.pts').textContent = '+' + Math.round(rows[i].dataset.points * g);
      }
      var stamps = $('stamps').children;
      var base = 0.5 + rows.length * 0.2 + 0.5;
      for (var j = 0; j < stamps.length; j++) stampIn(stamps[j], L, base + j * 0.38, j % 2 ? -4 : -8);
    },

    's-end': function (L, s) {
      var kids = [s.el.querySelector('.lab')];
      show(kids[0], P(L, 0, 0.3), 0, 30);
      var letters = $('end-logo').children;
      for (var i = 0; i < letters.length; i++) {
        var p = P(L, 0.05 + i * 0.04, 0.4);
        letters[i].style.opacity = clamp(p * 3);
        letters[i].style.transform = 'translateY(' + (-140 * (1 - (p <= 0 ? 0 : back(p)))) + 'px)';
      }
      show($('end-ct'), P(L, 0.6, 0.35), 0, 30);
      pop($('end-url-box'), P(L, 0.8, 0.35));
      $('end-cursor').style.opacity = Math.floor(Math.max(0, L) * 2.5) % 2 ? 0 : 1;
      show($('ai-note'), P(L, 1.05, 0.35), 0, 30);
      show($('end-motto'), P(L, 1.25, 0.35), 0, 30);
    }
  };

  window.seek = function (t) {
    if (!scenes.length) return;
    fx.x = 0; fx.y = 0; fx.flash = 0;
    var last = scenes[scenes.length - 1];
    scenes.forEach(function (s, i) {
      var on = t >= s.start && (t < s.end || s === last);
      s.el.style.display = on ? 'block' : 'none';
      // ukazatel průběhu nahoře
      $('prog').children[i].firstChild.style.transform = 'scaleX(' + clamp((t - s.start) / s.dur) + ')';
      if (on) ANIM[s.id](t - s.start - (i ? LEAD : 0), s);
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
    var onSaid = scenes.some(function (s) { return s.id === 's-said' && t >= s.start && t < s.end; });
    $('footer').textContent = onSaid ? footSaid : footBase;
  };
})();
