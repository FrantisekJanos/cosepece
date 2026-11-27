// Šablona Reelu „příběh jednoho pozměňovacího návrhu“ (template_pn_v1). Vzhled, pohyb a zvuk podle v3,
// jiný je scénář: kdo PN podal → co mění → kdy do zákona vstoupil → proč si ho všímáme → jak dopadl → co to znamená.
// Rozhraní shodné s v2 a v3: loadCard(card, orientation), fit(), seek(t), duration(), sceneIds(), measureBlocks(),
// audioCues(). Stav obrazu závisí jen na t (žádné časovače, rAF ani CSS animace), render jde snímek po snímku.
// Karta: format = "pribeh", data v karta.pribeh:
//   cislo, autor {jmeno, klub, spolu}, podano ("RRRR-MM-DD HH:MM"), podano_pozn
//   co {kde, popis, pred, po, pred_lab, po_lab, kontext, citace, zdroj}        (bez citace a zdroje se scéna nezařadí)
//   osa [{date, cas, label, pn, hot}], osa_souhrn {cislo, jednotka, popis}
//   naval {dny: [{date, label, count, own, late}], note, sub}                   (volitelné)
//   znaky [{typ, nadpis, text, zdroj, ai}], ferove                              (znak bez zdroje vypadne)
//   vysledek {stav: prijat|zamitnut|castecne|ceka|nehlasovatelny|nezaznel, text, hlasovani: [{o_cem, cislo, datum,
//             vysledek, pro, proti, zdrzel, nehlasoval, kluby: [{klub, pro, proti, zdrzel}], url}], dal, stanoviska}
//   dopad {text, ai, fakta: [{k, v}], zdroj}
// Háček: volitelně karta.pn_autor = [{cislo, jmeno, klub}] ze serveru (klub ke dni podání) má přednost před autor.klub.
// Animace a zvuk se popisují jednou (stavitel A(id)), takže audioCues() vždy odpovídá obrazu.
(function () {
  'use strict';

  var STAMPS = { deadline_cut: 'ZKRÁTILI', pn_other_law: 'PŘÍLEPEK', p90_passed: '§ 90/2' };
  var RUBRIKA = { pribeh: 'Pozměňovák pod lupou' };
  // záloha pro zkratky psp.cz; server posílá už popisek z Clubs::label() (PHP), páry se musí shodovat
  var KLUBY = { ANO2011: 'ANO', TOP09: 'TOP 09', MS: 'Motoristé', 'Nezařaz': 'nezařazení', 'Nezařazení': 'nezařazení' };
  var MAX_TOTAL = 45;    // strop délky videa (s) podle docs/reels-styl.md; příběh PN má až 9 scén
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

  function pnAuthors(list) {
    // autoři zadaných PN (karta.pn_autor = [{cislo, jmeno, klub}] ze serveru, klub už jako popisek) seskupení
    // podle jména a klubu v pořadí karty; záznam bez jména nebo čísla se vynechá
    var groups = [];
    (Array.isArray(list) ? list : []).forEach(function (x) {
      if (!x || !x.jmeno || x.cislo == null || String(x.cislo) === '') return;
      var jmeno = String(x.jmeno), klub = x.klub ? String(x.klub) : '';
      var g = groups.filter(function (y) { return y.jmeno === jmeno && y.klub === klub; })[0];
      if (!g) groups.push(g = { jmeno: jmeno, klub: klub, cisla: [] });
      g.cisla.push(String(x.cislo));
    });
    return groups;
  }

  function fillPnAuthors(row, list) {
    // „PN 2376 · Jan Novák“ a za ním šedý štítek klubu (jen když klub je), nejvýš dva autoři, pak „a další“;
    // text a štítek jednoho autora drží jeden obal, aby se štítek při zalomení neoddělil od svého jména.
    // Mezi jménem a štítkem je mezera (ne okraj): na začátku zalomeného řádku zmizí a štítek nebude odsazený
    var groups = pnAuthors(list);
    row.innerHTML = '';
    groups.slice(0, 2).forEach(function (g) {
      var nums = g.cisla.slice(0, 2).join(', ') + (g.cisla.length > 2 ? ' a další' : '');
      var pna = add(mk('span', 'pna'), mk('span', 'm', 'PN ' + nums + ' · ' + g.jmeno));
      if (g.klub) add(pna, document.createTextNode(' '), klubChip(g.klub, true));
      row.appendChild(pna);
    });
    if (groups.length > 2) row.appendChild(mk('span', 'm', 'a další'));
    row.style.display = groups.length ? '' : 'none';
    return groups.length > 0;
  }

  // ---------- příběh jednoho pozměňovacího návrhu ----------

  // stavy shodné s tabulkou pn_vysledek webu (app/PnOutcome.php); neznámý stav se neukáže jako verdikt
  var VERDIKT = { prijat: 'PŘIJAT', zamitnut: 'ZAMÍTNUT', castecne: 'PŘIJAT ZČÁSTI', ceka: 'ČEKÁ',
    nehlasovatelny: 'NEHLASOVALO SE', nezaznel: 'NEZAZNĚL' };
  var DNY = ['neděle', 'pondělí', 'úterý', 'středa', 'čtvrtek', 'pátek', 'sobota'];

  function splitCas(s) {
    // „2026-09-22 09:56:45“ → {date: „2026-09-22“, time: „9:56“}; bez času jen datum
    var m = /^(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}):(\d{2}))?/.exec(String(s || ''));
    if (!m) return null;
    return { date: m[1], time: m[2] != null ? (+m[2]) + ':' + m[3] : '', h: m[2] != null ? +m[2] : null, min: m[2] != null ? +m[3] : null };
  }

  function weekday(iso) {
    var d = Date.parse(iso + 'T00:00:00Z');
    return isFinite(d) ? DNY[new Date(d).getUTCDay()] : '';
  }

  function initials(name) {
    var p = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!p.length) return '';
    return (p[0].charAt(0) + (p.length > 1 ? p[p.length - 1].charAt(0) : '')).toUpperCase();
  }

  function story(c) {
    // příběh se staví jen s číslem PN a jménem autora; jinak zůstanou společné scény
    var p = c.format === 'pribeh' ? c.pribeh : null;
    if (!p || p.cislo == null || String(p.cislo) === '' || !p.autor || !p.autor.jmeno) return null;
    return p;
  }

  function buildHook(c, p) {
    var a = A('s-hook', lim((c.title || '').length / 40, 0.8, 2));
    $('hk-rub').textContent = RUBRIKA[c.format] || 'Hlídač zákonů';
    $('hk-row').style.display = p ? '' : 'none';
    $('hk-big').textContent = p ? String(p.cislo) : '';
    fillPlain($('hk-sub'), p ? 'pozměňovací návrh k tisku ' + c.ct : '');
    fill($('hk-title'), c.title || c.nazev || '', function (t) { setWords($('hk-title'), t, true); });
    // klub ke dni podání posílá server v pn_autor (generátor); bez něj platí autor z karty
    var own = (Array.isArray(c.pn_autor) ? c.pn_autor : []).filter(function (x) { return p && x && String(x.cislo) === String(p.cislo); });
    var authors = fillPnAuthors($('hk-pn'), own.length ? own : (p ? [{ cislo: p.cislo, jmeno: p.autor.jmeno, klub: p.autor.klub }] : []));
    $('hk-faze').textContent = 'Fáze: ' + c.faze;
    var meta = [];
    if (c.typ_navrhu) meta.push(String(c.typ_navrhu));
    if (c.predlozeno) meta.push('předloženo ' + czDate(c.predlozeno));
    fillPlain($('hk-meta'), meta.join(' · '));

    a.show($('hk-rub').parentNode, 0, -30, 0);
    a.bg(function (L) { $('hk-sq').style.opacity = Math.floor(Math.max(0, L) * 2.5) % 2 ? 0.25 : 1; });
    var t = 0.1;
    if (p) {
      a.pop($('hk-row'), 0.1, 0.9).cue(0.34, 'hit', 0.8);
      a.show($('hk-sub'), 0.5, -30, 0);
      t = 0.6;
    }
    a.words($('hk-title'), t, 0.9);
    if (authors) a.show($('hk-pn'), t + 0.8, -30, 0);
    a.pop($('hk-faze'), t + 1.0, 0.6);
    a.show($('hk-meta'), t + 1.15, -30, 0);
  }

  function buildKdo(c, p) {
    var au = p.autor;
    var a = A('s-n-kdo', 0, true);
    $('kd-ini').textContent = initials(au.jmeno);
    fillPlain($('kd-name'), String(au.jmeno));
    setOpt($('kd-klub'), au.klub ? klubLabel(au.klub) : '');
    var n = +au.spolu || 0;
    fillPlain($('kd-spolu'), n > 0 ? 'a ' + n + ' ' + plural(n, 'další poslanec', 'další poslanci', 'dalších poslanců') : (au.pozn ? String(au.pozn) : ''));
    $('kd-meta').style.display = au.klub || n > 0 || au.pozn ? '' : 'none';
    var cas = splitCas(p.podano);
    $('kd-when').style.display = cas ? '' : 'none';
    if (cas) {
      $('kd-date').textContent = czDate(cas.date);
      $('kd-time').textContent = cas.time;
      $('kd-time').style.display = cas.time ? '' : 'none';
      setOpt($('kd-day'), weekday(cas.date));
      fillPlain($('kd-when-note'), p.podano_pozn ? String(p.podano_pozn) : '');
    }
    $('kd-ct').textContent = 'k tisku ' + c.ct;
    fillPlain($('kd-nazev'), c.nazev || '');
    var who = [];
    if (c.typ_navrhu) who.push(String(c.typ_navrhu));
    if (c.kdo) who.push(String(c.kdo));
    fillPlain($('kd-typ'), who.join(' · '));

    a.show($('s-n-kdo').querySelector('.lab'), 0, -30, 0);
    a.at(0.1, 0.4, function (q) {
      $('kd-mono').style.opacity = clamp(q * 3);
      $('kd-mono').style.transform = 'scaleY(' + (q <= 0 ? 0 : back(q)) + ')';
    }).cue(0.1, 'pop', 0.8);
    a.show($('kd-name'), 0.2, -40, 0).cue(0.25, 'line', 0.8, { pos: 0.3 });
    if (au.klub) a.pop($('kd-klub'), 0.6, 0.6);
    a.show($('kd-spolu'), 0.7, -30, 0);
    var t = 0.9;
    if (cas) {
      a.show($('kd-when'), t, 0, 30);
      if (cas.time) {
        // časomíra doběhne na čas podání
        var tot = cas.h * 60 + cas.min;
        a.at(t + 0.1, 0.7, function (q) {
          var v = Math.round(tot * out3(q));
          var mm = v % 60;
          $('kd-time').textContent = Math.floor(v / 60) + ':' + (mm < 10 ? '0' : '') + mm;
        });
        for (var k = 1; k <= 8; k++) a.cue(t + 0.1 + 0.7 * (1 - Math.pow(1 - k / 8, 1 / 3)), 'tick', 0.6, { pos: k / 8 });
        a.cue(t + 0.8, 'hit', 0.6);
      }
      a.pop($('kd-day'), t + 0.85, 0.5);
      t += 1.1;
    }
    a.show($('kd-ct').parentNode, t, 0, 30).cue(t, 'slide', 0.6, { dur: 0.35 });
    SC['s-n-kdo'].hold = readTime(String(c.nazev || '').length * 0.6 + String(au.jmeno).length + 30);
    return true;
  }

  function buildCo(p) {
    // změna se ukáže jen s doslovnou citací a zdrojem (dohledatelnost)
    var b = p.co;
    if (!b || !b.citace || !b.zdroj || b.po == null || String(b.po) === '') return false;
    var body = $('s-n-co').querySelector('.body');
    body.innerHTML = '';
    var hasPred = b.pred != null && String(b.pred) !== '';
    var pred = hasPred ? String(b.pred) : String(b.bez_pred || 'v návrhu není');
    var po = String(b.po);
    var len = Math.max(pred.length, po.length);
    var lab = add(mk('div', 'lab'), mk('i', 'sq'), mk('span', '', 'Co mění'));
    var kde = b.kde ? mk('div', 'whotyp', cutWords(String(b.kde), 40)) : null;
    var co = fillPlain(mk('div', 't2'), b.popis || '');
    co.dataset.m = 'co_popis';
    var row = mk('div', 'zrow ' + (len <= 8 ? 'big' : len <= 16 ? 'mid' : 'txt'));
    var b1 = mk('div', 'zbox pred'), b2 = mk('div', 'zbox po');
    var v1 = mk('div', 'zv'), v2 = mk('div', 'zv');
    var strike = mk('i', 'strike');
    fillPlain(v1, pred); fillPlain(v2, po);
    v1.dataset.m = 'co_pred'; v2.dataset.m = 'co_po';
    if (hasPred) { v1.appendChild(strike); v1.className = 'zv old'; v1._set = function (t) { v1.textContent = t; v1.dataset.shown = t; v1.appendChild(strike); }; }
    add(b1, mk('div', 'zk', cutWords(String(b.pred_lab || 'Návrh'), 24)), v1);
    add(b2, mk('div', 'zk', cutWords(String(b.po_lab || 'Po PN'), 24)), v2);
    add(row, b1, b2);
    var ktx = fillPlain(mk('div', 'm'), b.kontext || '');
    ktx.dataset.m = 'co_kontext';
    var paper = mk('div', 'paper small');
    var cit = fillPlain(mk('div', 'q'), String(b.citace));
    cit.dataset.m = 'co_cit';
    add(paper, mk('div', 'zlab', 'doslova z PN'), cit);
    var src = fillPlain(mk('div', 'chip'), 'zdroj: ' + b.zdroj);
    src.dataset.m = 'co_src';
    add(body, add(mk('div', 'side-a'), lab, kde, co, row, ktx), add(mk('div', 'side-b'), paper, src));

    var a = A('s-n-co', readTime(String(b.popis || '').length + String(b.citace).length * 0.8 + len + String(b.kontext || '').length * 0.6) + 0.3);
    a.show(lab, 0, -30, 0);
    if (kde) a.pop(kde, 0.1, 0.8);
    a.show(co, 0.3, -30, 0).pop(b1, 0.55, 0.7);
    if (hasPred && len <= 16) {
      a.at(1.1, 0.22, function (q) { strike.style.transform = 'rotate(-4deg) scaleX(' + out3(q) + ')'; });
      a.cue(1.1, 'strike', 1);
    }
    a.stamp(b2, 1.4, 0);
    if (b.kontext) a.show(ktx, 1.8, 0, 20);
    a.at(1.9, 0.55, function (q) {
      var e = out5(q);
      paper.style.opacity = clamp(q * 6);
      paper.style.transform = 'translateX(' + (900 * (1 - e)) + 'px) rotate(' + (-1 + 7 * (1 - e)) + 'deg)';
    });
    a.cue(1.9, 'slide', 0.9, { dur: 0.55 }).show(src, 2.4, 0, 30);
    return true;
  }

  function buildKdy(p) {
    var steps = (Array.isArray(p.osa) ? p.osa : []).filter(function (x) { return x && x.label && x.date; }).slice(0, 5);
    if (steps.length < 2) return false;
    var rows = $('ky-rows');
    rows.innerHTML = '';
    var chars = 0;
    $('ky-tl').className = '';
    $('ky-tl').style.setProperty('--n', steps.length);
    steps.forEach(function (s, k) {
      var row = mk('div', 'step' + (s.pn ? ' own' : '') + (s.hot ? ' hot' : '') + (k && steps[k - 1].pn ? ' aft' : ''));
      var body = mk('div', 'stepbody');
      var dline = mk('div', 'd', (czDate(s.date) || String(s.date)) + (s.cas ? ' ' + s.cas : ''));
      if (s.pn) dline.appendChild(mk('span', 'pntag', 'PN ' + p.cislo));
      var gap = k ? dayDiff(steps[k - 1].date, s.date) : null;
      if (gap != null && gap > 0) dline.appendChild(mk('span', 'gapd', '+' + gap + ' ' + plural(gap, 'den', 'dny', 'dní')));
      else if (gap === 0) dline.appendChild(mk('span', 'gapd', 'týž den'));
      var lab = fillPlain(mk('div', 's'), cutWords(String(s.label), 60));
      chars += Math.min(60, String(s.label).length);
      add(row, mk('i', 'dot'), add(body, dline, lab));
      rows.appendChild(row);
    });
    var sum = p.osa_souhrn && p.osa_souhrn.cislo != null && isFinite(+p.osa_souhrn.cislo) ? p.osa_souhrn : null;
    $('ky-sum').style.display = sum ? '' : 'none';
    fillPlain($('ky-w'), sum ? String(sum.jednotka || '') : '');
    fillPlain($('ky-sub'), sum ? String(sum.popis || '') : '');
    if (sum) chars += String(sum.popis || '').length;

    var a = A('s-n-kdy', readTime(chars * 0.7), true);
    var n = steps.length;
    a.show($('s-n-kdy').querySelector('.lab'), 0, -30, 0);
    a.at(0.1, 0.3 * n + 0.1, function (q) { $('ky-line').style.transform = (LAND ? 'scaleX(' : 'scaleY(') + out3(q) + ')'; });
    Array.prototype.forEach.call(rows.children, function (row, i) {
      var t0 = 0.15 + i * 0.3;
      var dot = row.firstChild;
      a.at(t0, 0.3, function (q, L) {
        var pulse = (steps[i].pn || steps[i].hot) && L > t0 + 0.4 ? 1 + 0.2 * Math.abs(Math.sin((L - t0) * 3.2)) : 1;
        dot.style.opacity = clamp(q * 3);
        dot.style.transform = 'scale(' + (q <= 0 ? 0 : back(q) * pulse) + ')';
      });
      a.show(row.lastChild, t0 + 0.05, -40, 0);
      a.cue(t0, steps[i].pn ? 'hit' : 'pop', steps[i].pn || steps[i].hot ? 0.9 : 0.7, { pos: n > 1 ? i / (n - 1) : 0 });
    });
    if (sum) {
      var b = 0.25 + n * 0.3;
      a.show($('ky-sum'), b, 0, 30).count($('ky-n'), b + 0.05, 0.7, +sum.cislo, 0.6);
    }
    return true;
  }

  function buildNaval(p) {
    var pd = p.naval;
    var days = pd && Array.isArray(pd.dny) ? pd.dny.filter(function (x) { return x && +x.count > 0; }).slice(-8) : [];
    if (days.length < 2) return false;
    var chart = $('nv-chart'), dates = $('nv-dates');
    chart.innerHTML = '';
    dates.innerHTML = '';
    var max = Math.max.apply(null, days.map(function (x) { return +x.count; }));
    // číslo nad sloupcem se musí vejít do jeho šířky (víc dní nebo víc číslic = menší písmo; číslice Antonu ~0,4 em)
    var colw = ((LAND ? 940 : 936) - (days.length - 1) * 16) / days.length;
    var cvSize = Math.floor(lim(colw * 0.9 / (String(Math.round(max)).length * 0.4), 24, 72));
    var a = A('s-n-naval', readTime(String(pd.note || '').length + String(pd.sub || '').length) + 0.4);
    a.show($('s-n-naval').querySelector('.lab'), 0, -30, 0);
    days.forEach(function (x, i) {
      var cls = x.own ? ' own' : (x.late ? ' late' : '');
      var col = mk('div', 'col' + cls);
      var cv = mk('div', 'cv', '0'), cb = mk('div', 'cb');
      cv.style.fontSize = cvSize + 'px';
      cb.style.height = Math.max(2, 78 * (+x.count) / max) + '%';
      add(col, cv, cb);
      chart.appendChild(col);
      dates.appendChild(mk('span', x.own ? 'own' : '', cutWords(x.label ? String(x.label) : czShort(x.date), 8)));
      var t0 = 0.2 + i * 0.2;
      a.at(t0, 0.55, function (q) {
        cb.style.transform = 'scaleY(' + out5(q) + ')';
        cv.style.opacity = clamp(q * 3);
        cv.textContent = Math.round(+x.count * out3(q));
      });
      a.cue(t0, x.own ? 'hit' : 'pop', x.own ? 0.8 : 0.6, { pos: i / (days.length - 1) });
    });
    var t = 0.2 + days.length * 0.2 + 0.45;
    fillWords($('nv-note'), pd.note || '', true);
    fillPlain($('nv-sub'), pd.sub || '');
    if (pd.note) a.text($('nv-note'), t);
    if (pd.sub) a.show($('nv-sub'), t + 0.5, 0, 30);
    return true;
  }

  function buildZnaky(p, stamps) {
    // znak se ukáže jen se zdrojem; hodnocení AI nese štítek
    var flags = (Array.isArray(p.znaky) ? p.znaky : []).filter(function (x) { return x && x.nadpis && x.text && x.zdroj; }).slice(0, 3);
    if (!flags.length) return false;
    var list = $('zn-list');
    list.innerHTML = '';
    var a = A('s-n-znaky', 0);
    var chars = 0;
    a.show($('s-n-znaky').querySelector('.lab'), 0, -30, 0);
    flags.forEach(function (f, i) {
      var row = mk('div', 'zn');
      var zi = mk('div', 'zi', String(i + 1));
      var zh = fillPlain(mk('div', 'zh'), cutWords(String(f.nadpis), 48));
      var zt = fillWords(mk('div', 'zt'), String(f.text), true);
      zt.dataset.m = 'znak_' + (i + 1);
      var bs = add(mk('div', 'bs'), fillPlain(mk('span', 'src'), 'zdroj: ' + f.zdroj));
      if (f.ai) {
        var badge = mk('span', 'aibadge sm', 'hodnocení AI');
        badge.dataset.m = 'znak_ai_' + (i + 1);
        bs.appendChild(badge);
      }
      add(row, zi, add(mk('div', 'znb'), zh, zt, bs));
      list.appendChild(row);
      chars += String(f.text).length + String(f.nadpis).length;
      var t = 0.25 + i * 0.7;
      a.pop(zi, t, 0.8).show(zh, t + 0.05, -40, 0).text(zt, t + 0.15).show(bs, t + 0.5, 0, 20);
    });
    var t2 = 0.25 + flags.length * 0.7 + 0.2;
    // razítko jen s důkazem v klíči důvodu skóre tisku a se stejným znakem u tohoto PN
    var st = [];
    function has(typ) { return flags.some(function (f) { return f.typ === typ; }); }
    if (stamps.indexOf('PŘÍLEPEK') >= 0 && has('prilepek')) st.push('PŘÍLEPEK');
    if (stamps.indexOf('ZKRÁTILI') >= 0 && has('lhuta')) st.push('ZKRÁTILI');
    st = st.slice(0, 1);
    addStamps($('zn-stamps'), st);
    if (st.length) { a.stamp($('zn-stamps').firstChild, t2, -7); t2 += 0.8; }
    fillPlain($('zn-fer'), p.ferove || '');
    if (p.ferove) { a.show($('zn-fer'), t2, 0, 30); chars += String(p.ferove).length * 0.6; }
    SC['s-n-znaky'].hold = readTime(chars) * 0.8;
    return true;
  }

  function validVotes(v) {
    // hlasování se ukáže jen s počty a odkazem na psp.cz
    var list = Array.isArray(v.hlasovani) ? v.hlasovani : (v.hlasovani ? [v.hlasovani] : []);
    return list.filter(function (h) {
      return h && h.url && isFinite(+h.pro) && isFinite(+h.proti) && h.pro != null && h.proti != null && +h.pro + +h.proti > 0;
    }).slice(0, 2);
  }

  function voteBlock(h, a, t0, withClubs, idx) {
    var pro = +h.pro, proti = +h.proti, ost = (+h.zdrzel || 0) + (+h.nehlasoval || 0);
    var all = Math.max(200, pro + proti + ost);
    var passed = h.vysledek ? /^p/i.test(String(h.vysledek)) : pro > proti;
    var el = mk('div', 'vote');
    var res = mk('span', 'vres' + (passed ? '' : ' ne'), passed ? 'přijato' : 'zamítnuto');
    res.dataset.m = 'hl_vysledek_' + (idx + 1);
    var meta = [];
    if (h.cislo) meta.push('hlasování č. ' + h.cislo);
    if (h.datum) meta.push(czDate(h.datum));
    var head = add(mk('div', 'vhead'), res, mk('span', 'src', meta.join(' · ')));
    var co = h.o_cem ? fillPlain(mk('div', 'vco'), String(h.o_cem)) : null;
    if (co) co.dataset.m = 'hl_o_cem_' + (idx + 1);
    var bar = mk('div', 'vbar');
    var segs = [['vpro', pro], ['vproti', proti], ['vost', ost]].map(function (s) {
      var d = mk('div', s[0]), i = mk('i');
      d.style.width = (100 * s[1] / all) + '%';
      d.appendChild(i);
      bar.appendChild(d);
      return i;
    });
    var nums = mk('div', 'vnums');
    function num(cls, val, label) {
      var b = mk('b', '', '0');
      nums.appendChild(add(mk('div', 'vn ' + cls), b, mk('span', '', label)));
      return b;
    }
    var nPro = num('pro', pro, 'pro'), nProti = num('proti', proti, 'proti');
    var nOst = ost > 0 ? num('ost', ost, 'zdrželo se / nehlasovalo') : null;
    add(el, head, co, bar, nums);
    a.show(head, t0, 0, 30).pop(res, t0 + 0.1, 0.7);
    if (co) a.show(co, t0 + 0.15, -30, 0);
    a.at(t0 + 0.3, 1.0, function (q, L) {
      // pruh se plní zleva: pro, proti, ostatní
      segs[0].style.transform = 'scaleX(' + out3(P(L, t0 + 0.3, 0.5)) + ')';
      segs[1].style.transform = 'scaleX(' + out3(P(L, t0 + 0.7, 0.35)) + ')';
      segs[2].style.transform = 'scaleX(' + out3(P(L, t0 + 0.95, 0.3)) + ')';
      nPro.textContent = Math.round(pro * out3(P(L, t0 + 0.3, 0.5)));
      nProti.textContent = Math.round(proti * out3(P(L, t0 + 0.7, 0.35)));
      if (nOst) nOst.textContent = Math.round(ost * out3(P(L, t0 + 0.95, 0.3)));
    });
    a.show(nums, t0 + 0.3, 0, 20);
    for (var k = 1; k <= 6; k++) a.cue(t0 + 0.3 + 0.5 * k / 6, 'tick', 0.6, { pos: k / 6 });
    a.cue(t0 + 0.8, passed ? 'pop' : 'hit', 0.8);
    var t = t0 + 1.3;
    if (withClubs && Array.isArray(h.kluby) && h.kluby.length) {
      // klub se řadí podle toho, jak hlasovala většina jeho přítomných; číslo = počet těch hlasů
      var groups = [['pro', 'pro'], ['proti', 'proti'], ['zdrzel', 'zdrželi se']];
      groups.forEach(function (g) {
        var rows = h.kluby.filter(function (kl) {
          if (!kl || !kl.klub) return false;
          var v = { pro: +kl.pro || 0, proti: +kl.proti || 0, zdrzel: +kl.zdrzel || 0 };
          var best = v.pro >= v.proti && v.pro >= v.zdrzel ? 'pro' : (v.proti >= v.zdrzel ? 'proti' : 'zdrzel');
          return best === g[0] && v[g[0]] > 0;
        }).sort(function (x, y) { return (+y[g[0]] || 0) - (+x[g[0]] || 0); }).slice(0, 9);
        if (!rows.length) return;
        var line = add(mk('div', 'vkl'), mk('span', 'vkk', g[1]));
        rows.forEach(function (kl) { line.appendChild(mk('span', 'klub', klubLabel(kl.klub) + ' ' + (+kl[g[0]]))); });
        el.appendChild(line);
        a.show(line, t, -30, 0).cue(t, 'line', 0.6, { pos: 0.5 });
        t += 0.2;
      });
    }
    return { el: el, end: t };
  }

  function buildVys(p) {
    var v = p.vysledek;
    if (!v || !VERDIKT[v.stav]) return false;
    var votes = validVotes(v);
    var decided = v.stav === 'prijat' || v.stav === 'zamitnut' || v.stav === 'castecne';
    // verdikt přijat / zamítnut jen s doloženým hlasováním
    if (decided && !votes.length) return false;
    var cls = decided || v.stav === 'ceka' ? v.stav : 'jine';
    $('vy-verd').className = 'verd ' + cls;
    $('vy-word').textContent = VERDIKT[v.stav];
    fillWords($('vy-text'), v.text || '', true);
    var a = A('s-n-vys', 0);
    a.show($('s-n-vys').querySelector('.lab'), 0, -30, 0);
    if (v.stav === 'ceka') {
      a.pop($('vy-verd'), 0.15, 0.8);
      a.bg(function (L) { $('vy-verd').style.borderColor = Math.floor(Math.max(0, L) * 1.6) % 2 ? 'rgba(255,212,0,.45)' : ''; });
    } else {
      a.stamp($('vy-verd'), 0.15, 0);
      if (v.stav === 'zamitnut') {
        a.at(0.75, 0.25, function (q) { $('vy-strike').style.transform = 'rotate(-3deg) scaleX(' + out3(q) + ')'; });
        a.cue(0.75, 'strike', 1);
      }
    }
    if (v.text) a.text($('vy-text'), 0.8);
    var box = $('vy-votes'), dal = $('vy-dal');
    box.innerHTML = '';
    dal.innerHTML = '';
    var t = 1.3, chars = String(v.text || '').length;
    votes.forEach(function (h, i) {
      var vb = voteBlock(h, a, t, votes.length === 1, i);
      box.appendChild(vb.el);
      t = vb.end + 0.1;
      chars += String(h.o_cem || '').length + 20;
    });
    var next = (Array.isArray(v.dal) ? v.dal : []).filter(function (x) { return x && (x.label || typeof x === 'string'); }).slice(0, 4);
    if (!votes.length) {
      next.forEach(function (x, i) {
        var label = typeof x === 'string' ? x : String(x.label);
        var row = mk('div', 'dal' + (x.ted ? ' now' : ''));
        var dn = mk('div', 'dn', String(i + 1));
        add(row, dn, fillPlain(mk('div', 'dt'), cutWords(label, 80)));
        dal.appendChild(row);
        a.pop(dn, t, 0.7).show(row.lastChild, t + 0.05, -40, 0);
        t += 0.35;
        chars += Math.min(80, label.length);
      });
    }
    fillPlain($('vy-stan'), v.stanoviska ? String(v.stanoviska) : '');
    if (v.stanoviska) { a.show($('vy-stan'), t, 0, 20); chars += String(v.stanoviska).length * 0.5; }
    SC['s-n-vys'].hold = readTime(chars) * 0.8 + 0.4;
    return true;
  }

  function buildDopad(p) {
    var d = p.dopad;
    if (!d || !d.text) return false;
    var a = A('s-n-dopad', readTime(String(d.text).length) + 0.3);
    // shrnutí je od AI, pokud karta výslovně neřekne, že jde o doslovný údaj z dat
    var ai = d.ai !== false;
    $('dp-ai').style.display = ai ? '' : 'none';
    fillWords($('dp-text'), String(d.text), true);
    var box = $('dp-fakta');
    box.innerHTML = '';
    var facts = (Array.isArray(d.fakta) ? d.fakta : []).filter(function (x) { return x && x.k && x.v != null && String(x.v) !== ''; }).slice(0, 2);
    a.show($('s-n-dopad').querySelector('.lab'), 0, -30, 0);
    if (ai) a.pop($('dp-ai'), 0.25, 0.5);
    a.at(0.15, 0.6, function (q) { $('perex-bar').style.transform = 'scaleY(' + out5(q) + ')'; });
    a.text($('dp-text'), 0.25).cue(0.55, 'line', 0.7, { pos: 0.7 });
    facts.forEach(function (f, i) {
      var row = mk('div', 'fk');
      var fv = fillPlain(mk('div', 'fkv'), cutWords(String(f.v), 40));
      fv.dataset.m = 'dopad_fakt_' + (i + 1);
      add(row, fillPlain(mk('div', 'fkk'), cutWords(String(f.k), 48)), fv);
      box.appendChild(row);
      a.show(row, 1.2 + i * 0.35, 0, 30).cue(1.2 + i * 0.35, 'pop', 0.7);
    });
    fillPlain($('dp-src'), d.zdroj ? 'zdroj: ' + d.zdroj : '');
    if (d.zdroj) a.show($('dp-src'), 1.3 + facts.length * 0.35, 0, 20);
    return true;
  }

  function buildEnd(c, p) {
    var a = A('s-end', 1.8);
    var logo = $('end-logo');
    logo.innerHTML = '';
    'cosepece.cz'.split('').forEach(function (ch) { logo.appendChild(mk('span', '', ch)); });
    fillPlain($('end-ct'), (p ? 'PN ' + p.cislo + ' · ' : '') + 'tisk ' + c.ct + ' · ' + c.faze);
    $('end-url').textContent = cutWords(String(c.url || ('cosepece.cz/tisk/' + c.ct)).replace(/^https?:\/\//, ''), 60);
    $('ai-note').textContent = cutWords(c.ai_note ? String(c.ai_note) : 'Data a hlasování: psp.cz · popisky a dopad: shrnutí AI', 90);
    setOpt($('end-src'), cutWords(String(c.zdroje || 'Zdroj: psp.cz, sněmovní dokumenty, hlasování a stenozáznamy'), 110));
    a.show($('s-end').querySelector('.lab'), 0, 0, 30);
    var letters = logo.children;
    a.at(0.05, 0.9, function (q, L) {
      for (var i = 0; i < letters.length; i++) {
        var r = P(L, 0.05 + i * 0.04, 0.4);
        letters[i].style.opacity = clamp(r * 3);
        letters[i].style.transform = 'translateY(' + (-140 * (1 - (r <= 0 ? 0 : back(r)))) + 'px)';
      }
    });
    for (var e = 0; e < letters.length; e++) {
      a.cue(0.05 + e * 0.04 + 0.26, 'drop', e === letters.length - 1 ? 1 : 0.7, { pos: e / (letters.length - 1) });
    }
    a.show($('end-ct'), 0.6, 0, 30).pop($('end-url-box'), 0.8, 0.8);
    a.bg(function (L) { $('end-cursor').style.opacity = Math.floor(Math.max(0, L) * 2.5) % 2 ? 0 : 1; });
    a.show($('ai-note'), 1.05, 0, 30).show($('end-src'), 1.2, 0, 30).show($('end-motto'), 1.35, 0, 30);
  }

  // ---------- naplnění karty ----------

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
    var p = story(c);
    $('top-ct').textContent = 'Tisk ' + c.ct + (p ? ' · PN ' + p.cislo : '');
    $('footer').textContent = 'tisk ' + c.ct + ' · cosepece.cz · psp.cz · data k ' + czDate(c.data_date);

    buildHook(c, p);
    var ids = ['s-hook'];
    function on(id, ok) { if (ok) ids.push(id); }
    if (p) {
      on('s-n-kdo', buildKdo(c, p));
      on('s-n-co', buildCo(p));
      on('s-n-kdy', buildKdy(p));
      on('s-n-naval', buildNaval(p));
      on('s-n-znaky', buildZnaky(p, stamps));
      on('s-n-vys', buildVys(p));
      on('s-n-dopad', buildDopad(p));
    }
    buildEnd(c, p);
    ids.push('s-end');

    scenes = [];
    var t = 0;
    schedule(ids).forEach(function (s) {
      scenes.push({ id: s[0], el: $(s[0]), start: t, end: t + s[1], dur: s[1] });
      t += s[1];
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
  // obálka: render.py dá do snímku 0 ustálenou 1. scénu (náhled na sítích), přehrávání běží od t = 0 beze změny
  window.coverTime = function () {
    if (!scenes.length) return 0;
    return scenes.length === 1 ? total - 0.05 : scenes[0].end - WIPE - 0.1;
  };
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

  function fitRows(body) {
    // řádky [data-fit-row] (háček: velké číslo + jednotka) zmenšuje po 4 % (nejvýš na 60 %), dokud se jejich
    // obsah nevejde do šířky sloupce; děti mají vlastní velikost písma, zmenšují se všechny stejným poměrem
    Array.prototype.forEach.call(body.querySelectorAll('[data-fit-row]'), function (row) {
      if (row.offsetParent === null) return;
      var kids = Array.prototype.slice.call(row.children);
      kids.forEach(function (k) {
        k.style.fontSize = '';
        k.dataset.base = parseFloat(getComputedStyle(k).fontSize);
      });
      for (var step = 1; step <= 10 && row.scrollWidth > row.clientWidth + 1; step++) {
        kids.forEach(function (k) { k.style.fontSize = (k.dataset.base * (1 - 0.04 * step)) + 'px'; });
      }
    });
  }

  window.fit = function () {
    // každou scénu zmenšuje po 4 % (nejvýš na 50 %), potom zkracuje texty od konce na celá slova
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
      fitRows(body);
      var scale = 1;
      while (overflows(body) && scale > 0.5) {
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
