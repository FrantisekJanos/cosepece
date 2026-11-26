// Souhlas s cookies pro Google Analytics 4 (Consent Mode v2 basic): gtag.js se načte až po souhlasu.
// Volba je v localStorage (cookie_consent = {"v":"granted"|"denied","t":ms}) a platí 365 dní.
(function () {
  'use strict';
  var script = document.currentScript || document.querySelector('script[data-ga4]');
  var id = script && script.getAttribute('data-ga4');
  var bar = document.getElementById('cookie-bar');
  if (!id || !bar) return;

  var KEY = 'cookie_consent';
  var MAX_AGE = 365 * 24 * 3600 * 1000;
  var memory = null; // náhrada, když localStorage nejde použít
  var loaded = false;

  function read() {
    var raw = null;
    try { raw = window.localStorage.getItem(KEY); } catch (e) { raw = memory; }
    if (!raw) return null;
    try {
      var c = JSON.parse(raw);
      if ((c.v === 'granted' || c.v === 'denied') && typeof c.t === 'number' && Date.now() - c.t < MAX_AGE) return c.v;
    } catch (e) { /* poškozená hodnota = žádná volba */ }
    return null;
  }

  function write(v) {
    var raw = JSON.stringify({ v: v, t: Date.now() });
    memory = raw;
    try { window.localStorage.setItem(KEY, raw); } catch (e) { /* zůstane jen pro tuto stránku */ }
  }

  function load() {
    if (loaded) return;
    loaded = true;
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { window.dataLayer.push(arguments); };
    window.gtag('consent', 'default', {
      analytics_storage: 'granted', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied'
    });
    window.gtag('js', new Date());
    window.gtag('config', id);
    var s = document.createElement('script');
    s.async = true;
    s.src = 'https://www.googletagmanager.com/gtag/js?id=' + encodeURIComponent(id);
    document.head.appendChild(s);
  }

  // smaže cookies _ga* pro hostitele, jeho tečkovou variantu i doménu bez www.
  function clear() {
    var host = location.hostname;
    var domains = ['', host, '.' + host];
    if (host.indexOf('www.') === 0) domains.push(host.slice(4), '.' + host.slice(4));
    document.cookie.split(';').forEach(function (part) {
      var name = part.split('=')[0].trim();
      if (name.indexOf('_ga') !== 0) return;
      domains.forEach(function (d) {
        document.cookie = name + '=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/' + (d ? '; domain=' + d : '');
      });
    });
  }

  bar.addEventListener('click', function (ev) {
    var btn = ev.target.closest('[data-consent]');
    if (!btn) return;
    var v = btn.getAttribute('data-consent');
    write(v);
    bar.hidden = true;
    if (v === 'granted') {
      load();
    } else {
      // načtený gtag nejdřív zastavit, jinak by cookies po smazání zapsal znovu
      if (loaded) window.gtag('consent', 'update', { analytics_storage: 'denied' });
      clear();
      if (loaded) location.reload(); // po obnovení se gtag už nenačte
    }
  });

  document.addEventListener('click', function (ev) {
    var link = ev.target.closest('[data-cookie-settings]');
    if (!link) return;
    ev.preventDefault();
    bar.hidden = false;
  });

  var choice = read();
  if (choice === 'granted') load();
  else if (choice === null) bar.hidden = false;
})();
