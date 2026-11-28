// Zavaděč chatu cosepece.cz (spec 4, 7.1): klasický skript, ne modul. Vykreslí bublinu a teprve po kliknutí
// načte panel (chat.mjs a chat.css). Značku hashe v adrese MODUL nahradí služba otiskem statických souborů (app.ZNACKA_HASHE).
(function () {
  "use strict";
  var API = "/chat-api";
  var MODUL = API + "/s/__CHAT_HASH__/chat.mjs";
  // Cesty bez bubliny; stejný výčet má kontext.mjs (hlídá test F38)
  var BEZ_BUBLINY = ["/revize", "/mcp"];
  var MEZERA = 16;

  if (window.__czChat) return;
  window.__czChat = true;

  function cesta() {
    var p = location.pathname;
    return p.length > 1 ? p.replace(/\/+$/, "") || "/" : p;
  }
  if (BEZ_BUBLINY.indexOf(cesta()) >= 0) return;

  function cti(klic) {
    try { return JSON.parse(sessionStorage.getItem(klic) || "null"); } catch (e) { return null; }
  }

  // ?chat=<kód> odemkne skrytý režim (cookie chat_beta); parametr z adresy hned zmizí
  function beta() {
    var q = new URLSearchParams(location.search);
    var kod = q.get("chat");
    if (kod === null) return Promise.resolve();
    q.delete("chat");
    var zbytek = q.toString();
    history.replaceState(history.state, "", location.pathname + (zbytek ? "?" + zbytek : "") + location.hash);
    return fetch(API + "/beta", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({kod: kod})}).catch(function () {});
  }

  var bublina, modul;

  function poloha() {
    if (!bublina) return;
    var lista = document.querySelector("#cookie-bar:not([hidden])");
    var dole = MEZERA;
    if (lista) dole = Math.max(MEZERA, innerHeight - lista.getBoundingClientRect().top + 12);
    bublina.style.bottom = dole + "px";
  }

  function otevri(stav) {
    if (!modul) {
      bublina.setAttribute("aria-busy", "true");
      modul = import(MODUL).then(function (m) {
        m.spust({bublina: bublina, stav: stav, poloha: poloha});
        return m;
      });
      modul.catch(function () { modul = null; bublina.removeAttribute("aria-busy"); });
    } else {
      modul.then(function (m) { m.otevri(); });
    }
  }

  function vykresli(stav) {
    bublina = document.createElement("button");
    bublina.type = "button";
    bublina.className = "cz-chat-bublina";
    bublina.setAttribute("aria-label", "Zeptat se asistenta");
    bublina.setAttribute("aria-haspopup", "dialog");
    bublina.title = "Zeptat se asistenta";
    bublina.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483000;width:56px;height:56px;" +
      "border-radius:50%;border:0;padding:0;cursor:pointer;background:#1D4ED8;color:#fff;" +
      "box-shadow:0 6px 20px rgba(21,23,28,.25);display:flex;align-items:center;justify-content:center";
    // statická ikona, žádná data ze serveru
    bublina.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>';
    bublina.addEventListener("click", function () { otevri(stav); });
    document.body.appendChild(bublina);
    poloha();
    // tlačítka cookie lišty ji schovají nebo znovu ukážou: poloha se přepočítá po kliknutí
    document.addEventListener("click", function (e) {
      var t = e.target;
      if (t && t.closest && (t.closest("#cookie-bar button") || t.closest("[data-cookie-settings]"))) {
        setTimeout(poloha, 0);
      }
    });
    addEventListener("resize", poloha);
    // rozběhnutá odpověď z předchozí stránky: panel se otevře sám a k tahu se připojí (spec 7.3)
    var konv = cti("cz-chat-konv");
    if (konv && (konv.tah || (konv.otevreno && innerWidth >= 640))) otevri(stav);
  }

  function start() {
    beta().then(function () {
      return fetch(API + "/stav", {credentials: "same-origin"});
    }).then(function (r) {
      if (!r.ok) return null;          // skrytý režim bez cookie (404) nebo výpadek: bez bubliny
      return r.json();
    }).then(function (stav) {
      if (stav && stav.kod !== "vypnuto") vykresli(stav);
    }).catch(function () {});
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
