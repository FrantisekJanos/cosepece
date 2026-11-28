// Panel chatu cosepece.cz (spec 7.1, 7.3, 11.2, 11.3): otevírá ho zavaděč w.js po kliknutí na bublinu.
// Text ze serveru (odpověď, hlášky, zdroje) jde do stránky jen přes textContent; markdown a náhledy řeší úloha 18.
// Úložiště: sessionStorage cz-chat-konv (konverzace a běžící tah této karty), cz-chat-zrcadlo (posledních 50 zpráv),
// localStorage cz-chat-posledni = {id, cas} (nabídka „Pokračovat v předchozí konverzaci“ v nové kartě).

import { rozeber } from "./sse.mjs";
import { hlaska, zbyvaText } from "./hlasky.mjs";
import { kontext, zaostreni, zmer, vyskaOkna } from "./kontext.mjs";

const API = "/chat-api";
const K_KONV = "cz-chat-konv";
const K_ZRCADLO = "cz-chat-zrcadlo";
const K_POSLEDNI = "cz-chat-posledni";
const MAX_ZRCADLO = 50;
const NECINNOST_MS = 2 * 3600 * 1000;    // po 2 hodinách nečinnosti začíná panel prázdný
const TURNSTILE_JS = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
const TURNSTILE_CEKANI_MS = 120000;       // na výzvu Turnstile (zaškrtávátko) čeká widget nejvýš tak dlouho
const MAX_TURNSTILE = 2;                  // kolikrát po 428 zkusit nový token
const MAX_OBSAZENO = 2;                   // spec 11.3: obsazeno zopakuje widget 2× sám
const MAX_PRIPOJENI = 3;                  // pokusy o opětovné připojení ke streamu
const PREKRESLENI_MS = 80;                // spec 7.2: překreslení nejvýš jednou za 80 ms
const ULOZENI_MS = 300;
const UZKE = 640;
const MAX_ZNAKU = 500;

const TRETI_STRANY = "Dotazy zpracovávají AI služby Anthropic a OpenAI. Anonymně je procházíme kvůli zlepšování. " +
  "Nepište osobní údaje.";

// 3 pevné otázky podle typu stránky (spec 7.1, 5.3)
const OTAZKY = {
  tisk: ["O čem tento návrh je?", "Proč má tento tisk takové skóre?", "Jaké pozměňovací návrhy k němu padly?"],
  zakon: ["Které tisky mění tento zákon?", "Co se v zákoně teď mění?", "V jaké fázi jsou jeho novely?"],
  pripravovane: ["Co vláda teď připravuje?", "Které materiály mají krátkou lhůtu na připomínky?",
    "Jak probíhá připomínkové řízení?"],
  vychozi: ["Které tisky mají teď nejvyšší skóre a proč?", "Co znamená projednání podle § 90 odst. 2?",
    "Jak se pozná přílepek v pozměňovacím návrhu?"],
};

const NAZVY_STRANEK = {
  prehled: "přehled", seznam_tisku: "seznam tisků", zakony: "zákony", hledani: "hledání",
  upozorneni: "upozornění", pripravovane: "připravované zákony", staticka: "tato stránka", jina: "jiná stránka",
};

// ---------------------------------------------------------------- úložiště (prohlížeč může přístup odmítnout)

function cti(uloziste, klic) {
  try { return JSON.parse(uloziste.getItem(klic) || "null"); } catch { return null; }
}

function zapis(uloziste, klic, hodnota) {
  try {
    if (hodnota === null) uloziste.removeItem(klic);
    else uloziste.setItem(klic, JSON.stringify(hodnota));
  } catch { /* plné nebo zakázané úložiště: panel funguje dál bez zrcadla */ }
}

// ---------------------------------------------------------------- stav modulu

const S = {
  bublina: null, poloha: null,
  stav: null, maxZnaku: MAX_ZNAKU, sitekey: "",
  konv: {}, zpravy: [], nabidnoutPredchozi: null,
  volba: "auto",          // "auto" | "tisk" | "obecny" | {druh, id}
  bezi: null,             // {tahId, od, bot, el, hotovo, konec: Promise, dokonci, pokusu}
  odesila: false,
  blok: null,             // {kod, el, casovac}
  token: null, tsId: null, tsCekaji: [], tsNacteno: null,
  ukladani: null, prekresleni: null,
  el: {},
};

// ---------------------------------------------------------------- DOM pomocníci

function e(tag, vlastnosti = {}, ...deti) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(vlastnosti)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "text") el.textContent = v;
    else if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const d of deti) if (d) el.append(d);
  return el;
}

function tlacitko(text, onclick, trida = "cz-chat-tl") {
  return e("button", {type: "button", class: trida, text, onclick});
}

function dole(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 40;
}

// ---------------------------------------------------------------- kontext a stránka

function zakladniKontext() {
  return kontext(document, window);
}

function aktualniKontext() {
  const k = zakladniKontext();
  const v = S.volba;
  if (v === "obecny") return {typ: "jina", uzke: k.uzke};
  if (k.typ === "tisk" && v !== "auto") {
    delete k.pn;
    delete k.hlasovani;
    if (typeof v === "object") k[v.druh === "pn" ? "pn" : "hlasovani"] = v.id;
  }
  return k;
}

function popisStranky(k) {
  if (k.typ === "tisk") return `tisk ${k.ct}`;
  if (k.typ === "zakon") return `zákon ${k.z}`;
  return NAZVY_STRANEK[k.typ] || NAZVY_STRANEK.jina;
}

function popisKontextu(k) {
  if (k.typ === "jina") return "obecný dotaz";
  if (k.typ === "tisk" && k.pn) return `PN ${k.pn} · tisk ${k.ct}`;
  if (k.typ === "tisk" && k.hlasovani) return `hlasování ${k.hlasovani} · tisk ${k.ct}`;
  return popisStranky(k);
}

// Oddělovač „— teď na: tisk 300 —“, když se konverzace přenesla na jinou stránku (spec 5.5).
// Během rozepsané odpovědi se odloží až za ni (dokonciTah), ať nerozdělí odpověď z předchozí stránky.
function zkontrolujStranku() {
  if (S.bezi) return;
  const st = popisStranky(zakladniKontext());
  const maZpravy = S.zpravy.some((z) => z.role !== "oddelovac");
  if (maZpravy && S.konv.stranka && S.konv.stranka !== st) {
    const o = {role: "oddelovac", text: `— teď na: ${st} —`};
    S.zpravy.push(o);
    pripojZpravu(o);
  }
  S.konv.stranka = st;
  ulozHned();
}

// ---------------------------------------------------------------- uložení

function ulozHned() {
  clearTimeout(S.ukladani);
  S.ukladani = null;
  zapis(sessionStorage, K_KONV, S.konv);
  zapis(sessionStorage, K_ZRCADLO, S.zpravy.slice(-MAX_ZRCADLO));
}

function uloz() {
  if (S.ukladani === null) S.ukladani = setTimeout(ulozHned, ULOZENI_MS);
}

function oznacAktivitu() {
  S.konv.cas = Date.now();
  if (S.konv.id) zapis(localStorage, K_POSLEDNI, {id: S.konv.id, cas: S.konv.cas});
}

function nactiUlozene() {
  const konv = cti(sessionStorage, K_KONV) || {};
  const zpravy = cti(sessionStorage, K_ZRCADLO);
  const cerstve = konv.id && (konv.tah || Date.now() - (konv.cas || 0) < NECINNOST_MS);
  if (cerstve) {
    S.konv = konv;
    S.zpravy = Array.isArray(zpravy) ? zpravy : [];
  } else {
    S.konv = {otevreno: konv.otevreno};
    S.zpravy = [];
  }
  const posledni = cti(localStorage, K_POSLEDNI);
  if (!S.konv.id && posledni && typeof posledni.id === "string") S.nabidnoutPredchozi = posledni;
}

// ---------------------------------------------------------------- stavba panelu

function nactiCss() {
  if (document.querySelector("link[data-cz-chat]")) return;
  document.head.append(e("link", {rel: "stylesheet", href: new URL("chat.css", import.meta.url).href, "data-cz-chat": true}));
}

function postav() {
  nactiCss();
  const el = S.el;
  el.nadpis = e("h2", {id: "cz-chat-nadpis", class: "cz-chat-nadpis", text: "Asistent cosepece.cz"});
  el.cip = e("button", {type: "button", class: "cz-chat-cip", "aria-haspopup": "true", "aria-expanded": "false",
    "aria-controls": "cz-chat-volby", onclick: () => prepniVolby()});
  el.volby = e("div", {id: "cz-chat-volby", class: "cz-chat-volby", role: "group", "aria-label": "K čemu se ptáte", hidden: true});
  el.novy = tlacitko("Nový chat", () => novyChat());
  el.smazat = tlacitko("Smazat konverzaci", () => smazatKonverzaci());
  el.zavrit = e("button", {type: "button", class: "cz-chat-tl cz-chat-zavrit", "aria-label": "Zavřít", text: "×",
    onclick: () => zavri()});
  const hlava = e("div", {class: "cz-chat-hlava"},
    e("div", {class: "cz-chat-hlava-radek"}, el.nadpis, e("div", {class: "cz-chat-akce"}, el.novy, el.smazat, el.zavrit)),
    e("div", {class: "cz-chat-kontext"}, el.cip, el.volby));

  el.zpravy = e("div", {class: "cz-chat-zpravy"});
  el.stav = e("div", {class: "cz-chat-stav", role: "status", "aria-live": "polite"});
  el.oznameni = e("div", {class: "cz-chat-sr", "aria-live": "polite"});

  el.nabidka = e("div", {class: "cz-chat-nabidka", hidden: true});
  el.ts = e("div", {class: "cz-chat-ts"});
  el.tretiStrany = e("p", {class: "cz-chat-treti"}, TRETI_STRANY + " ",
    e("a", {href: `${API}/zasady`, target: "_blank", rel: "noopener", text: "Zásady chatu"}));
  el.pole = e("textarea", {class: "cz-chat-pole", rows: 2, "aria-label": "Vaše otázka", placeholder: "Zeptejte se…",
    maxlength: S.maxZnaku});
  el.pole.addEventListener("input", () => obnovCitac());
  el.pole.addEventListener("focus", () => obnovNabidku());
  el.pole.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
      ev.preventDefault();
      odeslatZPole();
    }
  });
  el.citac = e("span", {class: "cz-chat-citac", "aria-live": "off"});
  el.odeslat = e("button", {type: "submit", class: "cz-chat-odeslat", text: "Odeslat"});
  el.formular = e("form", {class: "cz-chat-formular", onsubmit: (ev) => { ev.preventDefault(); odeslatNeboZastavit(); }},
    el.pole, e("div", {class: "cz-chat-formular-pata"}, el.citac, el.odeslat));
  el.zbyva = e("p", {class: "cz-chat-zbyva", hidden: true});
  const pata = e("div", {class: "cz-chat-pata"}, el.nabidka, el.ts, el.tretiStrany, el.formular, el.zbyva,
    e("p", {class: "cz-chat-info"}, "Odpovědi píše AI, ověřujte ve zdrojích. ",
      e("a", {href: `${API}/zasady`, target: "_blank", rel: "noopener", text: "Zásady"})));

  el.panel = e("div", {class: "cz-chat-panel", role: "dialog", "aria-modal": "true", "aria-labelledby": "cz-chat-nadpis",
    hidden: true}, hlava, el.zpravy, el.stav, pata, el.oznameni);
  el.panel.addEventListener("keydown", klavesy);
  document.body.append(el.panel);

  if (window.visualViewport) {
    visualViewport.addEventListener("resize", rozmer);
    visualViewport.addEventListener("scroll", rozmer);
  }
  addEventListener("resize", rozmer);
  addEventListener("pagehide", ulozHned);
}

// mobil: celá obrazovka, výška z visualViewport (pole zprávy zůstane nad klávesnicí)
function rozmer() {
  const p = S.el.panel;
  if (!p || p.hidden) return;
  if (innerWidth < UZKE) {
    const vv = window.visualViewport;
    p.style.height = `${vv ? vv.height : innerHeight}px`;
    p.style.top = `${vv ? vv.offsetTop : 0}px`;
  } else {
    p.style.height = "";
    p.style.top = "";
  }
}

// ---------------------------------------------------------------- přístupnost: fokus a Esc

function fokusovatelne() {
  return [...S.el.panel.querySelectorAll("button, a[href], textarea, input, [tabindex]:not([tabindex='-1'])")]
    .filter((x) => !x.disabled && x.getClientRects().length > 0);
}

function klavesy(ev) {
  if (ev.key === "Escape") {
    ev.preventDefault();
    if (!S.el.volby.hidden) {
      prepniVolby(false);
      S.el.cip.focus();
    } else zavri();
    return;
  }
  if (ev.key !== "Tab") return;
  const f = fokusovatelne();
  if (!f.length) return;
  const prvni = f[0], posledni = f[f.length - 1];
  if (ev.shiftKey && document.activeElement === prvni) {
    ev.preventDefault();
    posledni.focus();
  } else if (!ev.shiftKey && document.activeElement === posledni) {
    ev.preventDefault();
    prvni.focus();
  }
}

// ---------------------------------------------------------------- čip kontextu a nabídka

function obnovCip() {
  S.el.cip.textContent = `Ptáte se k: ${popisKontextu(aktualniKontext())} ▾`;
}

function nastavVolbu(v) {
  S.volba = v;
  prepniVolby(false);
  obnovCip();
  obnovNabidku();
  S.el.pole.focus();
}

function prepniVolby(ukaz) {
  const el = S.el;
  const otevrit = ukaz === undefined ? el.volby.hidden : ukaz;
  el.volby.hidden = !otevrit;
  el.cip.setAttribute("aria-expanded", String(otevrit));
  if (!otevrit) return;
  const k = zakladniKontext();
  const moznosti = [];
  moznosti.push({v: "auto", text: popisKontextu(k)});
  if (k.typ === "tisk" && (k.pn || k.hlasovani)) moznosti.push({v: "tisk", text: `celý tisk ${k.ct}`});
  if (k.typ !== "jina") moznosti.push({v: "obecny", text: "obecný dotaz"});
  el.volby.replaceChildren(...moznosti.map((m) => {
    const vybrano = JSON.stringify(m.v) === JSON.stringify(S.volba);
    return e("button", {type: "button", class: "cz-chat-volba", "aria-pressed": String(vybrano), text: m.text,
      onclick: () => nastavVolbu(m.v)});
  }));
  el.volby.querySelector("button")?.focus();
}

// Nabídka viditelných PN a hlasování, když na stránce tisku nic nezabírá půl okna (spec 5.2 krok 3)
function obnovNabidku() {
  const el = S.el;
  const k = zakladniKontext();
  let nabidka = [];
  if (k.typ === "tisk" && !k.pn && !k.hlasovani && S.volba === "auto") {
    nabidka = zaostreni(zmer(document, window), vyskaOkna(window)).nabidka;
  }
  el.nabidka.hidden = !nabidka.length;
  if (!nabidka.length) return el.nabidka.replaceChildren();
  el.nabidka.replaceChildren(e("span", {text: "K návrhu?"}), ...nabidka.map((p) => e("button", {
    type: "button", class: "cz-chat-volba", text: p.druh === "pn" ? `PN ${p.id}` : `hlasování ${p.id}`,
    onclick: () => nastavVolbu({druh: p.druh, id: p.id}),
  })));
}

// ---------------------------------------------------------------- vykreslení zpráv

function systemovyPrvek(h, navic = []) {
  const deti = [e("p", {text: h.text})];
  if (h.odkazy.length) {
    deti.push(e("ul", {class: "cz-chat-odkazy"}, ...h.odkazy.map((o) => e("li", {},
      e("a", {href: o.href, text: o.text, ...(o.href.startsWith("http") ? {target: "_blank", rel: "noopener"} : {})})))));
  }
  if (h.akce === "novy_chat") navic = [...navic, tlacitko("Nový chat", () => novyChat())];
  if (navic.length) deti.push(e("div", {class: "cz-chat-system-akce"}, ...navic));
  return e("div", {class: "cz-chat-system", role: "note"}, ...deti);
}

function prvekZpravy(z) {
  if (z.role === "oddelovac") return e("div", {class: "cz-chat-oddelovac", role: "separator", text: z.text});
  if (z.role === "uzivatel") return e("div", {class: "cz-chat-zprava cz-chat-uzivatel", text: z.text});
  if (z.stav === "odmitnuto") return systemovyPrvek(hlaska("odmitnuto"));
  const obal = e("div", {class: "cz-chat-zprava cz-chat-bot"});
  const text = e("div", {class: "cz-chat-text", text: z.text || ""});
  obal.append(text);
  if (z.stav === "probiha" && !z.text) obal.classList.add("cz-chat-ceka");
  if (z.stav === "preruseno") obal.append(e("p", {class: "cz-chat-poznamka", text: "Odpověď jste zastavili."}));
  if (z.stav === "chyba") obal.append(systemovyPrvek(hlaska("chyba")));
  return obal;
}

function prazdnyStav() {
  const k = zakladniKontext();
  const otazky = OTAZKY[k.typ] || OTAZKY.vychozi;
  const deti = [e("p", {class: "cz-chat-uvod", text: "Zeptejte se na tisky, pozměňovací návrhy, hlasování nebo zákony."}),
    e("ul", {class: "cz-chat-otazky"}, ...otazky.map((o) => e("li", {}, tlacitko(o, () => odesli(o), "cz-chat-otazka"))))];
  if (S.nabidnoutPredchozi) {
    deti.push(tlacitko("Pokračovat v předchozí konverzaci", () => pokracovat(), "cz-chat-tl cz-chat-pokracovat"));
  }
  return e("div", {class: "cz-chat-prazdne"}, ...deti);
}

function vykresliVse() {
  const el = S.el;
  const zpravy = S.zpravy.filter((z) => z.role !== "oddelovac");
  el.zpravy.replaceChildren(...(zpravy.length ? S.zpravy.map(prvekZpravy) : [prazdnyStav()]));
  if (S.bezi) S.bezi.el = el.zpravy.lastElementChild;
  if (S.blok) el.zpravy.append(S.blok.el);
  el.tretiStrany.hidden = S.zpravy.some((z) => z.role === "uzivatel");
  el.zpravy.scrollTop = el.zpravy.scrollHeight;
  obnovTlacitka();
}

function pripojZpravu(z) {
  const el = S.el.zpravy;
  if (!el) return null;
  const prazdne = el.querySelector(".cz-chat-prazdne");
  if (prazdne) prazdne.remove();
  const bylDole = dole(el);
  const p = prvekZpravy(z);
  if (S.blok && S.blok.el.parentNode === el) el.insertBefore(p, S.blok.el);
  else el.append(p);
  if (bylDole) el.scrollTop = el.scrollHeight;
  return p;
}

function nahradPrvek(z, stary) {
  const novy = prvekZpravy(z);
  if (stary && stary.parentNode) stary.replaceWith(novy);
  return novy;
}

function prekresliBezici() {
  if (S.prekresleni !== null) return;
  S.prekresleni = setTimeout(() => {
    S.prekresleni = null;
    const b = S.bezi;
    if (!b || !b.el) return;
    const el = S.el.zpravy;
    const bylDole = dole(el);
    const t = b.el.querySelector(".cz-chat-text");
    if (t) t.textContent = b.bot.text;
    b.el.classList.toggle("cz-chat-ceka", !b.bot.text);
    if (bylDole) el.scrollTop = el.scrollHeight;
  }, PREKRESLENI_MS);
}

// přechodná systémová hláška (opakování, výzva Turnstile); vrací funkci, která ji odstraní
function docasnaHlaska(kod, data) {
  const p = systemovyPrvek(hlaska(kod, data));
  S.el.zpravy.append(p);
  S.el.zpravy.scrollTop = S.el.zpravy.scrollHeight;
  return () => p.remove();
}

function ukazHlasku(kod, data = {}) {
  const h = hlaska(kod, {ct: zakladniKontext().ct, ...data});
  if (h.blokuje) return zablokuj(kod, data);
  pripojSystem(systemovyPrvek(h));
}

function pripojSystem(p) {
  S.el.zpravy.querySelector(".cz-chat-prazdne")?.remove();
  S.el.zpravy.append(p);
  S.el.zpravy.scrollTop = S.el.zpravy.scrollHeight;
  S.el.oznameni.textContent = p.textContent;
}

// ---------------------------------------------------------------- tvrdé limity (pole zprávy zablokované)

function zablokuj(kod, data = {}) {
  zrusBlok();
  const d = {ct: zakladniKontext().ct, ...data};
  const el = systemovyPrvek(hlaska(kod, d));
  S.blok = {kod, el, casovac: null};
  if (kod === "limit_hodina" && data.znovu_v) {
    // odpočet: text se obnovuje, pole se odblokuje samo
    S.blok.casovac = setInterval(() => {
      if (Date.now() >= Date.parse(data.znovu_v)) {
        zrusBlok();
        obnovStav();
      } else el.querySelector("p").textContent = hlaska(kod, d).text;
    }, 15000);
  }
  pripojSystem(el);
  obnovTlacitka();
}

function zrusBlok() {
  if (!S.blok) return;
  clearInterval(S.blok.casovac);
  S.blok.el.remove();
  S.blok = null;
  obnovTlacitka();
}

function obnovTlacitka() {
  const el = S.el;
  if (!el.pole) return;
  const zablokovano = Boolean(S.blok);
  el.pole.disabled = zablokovano;
  el.odeslat.textContent = S.bezi ? "Zastavit" : "Odeslat";
  el.odeslat.disabled = zablokovano && !S.bezi;
  el.odeslat.classList.toggle("cz-chat-zastavit", Boolean(S.bezi));
  el.novy.disabled = Boolean(S.bezi);
  el.smazat.disabled = Boolean(S.bezi) || !S.konv.id;
  obnovCitac();
}

function obnovCitac() {
  const el = S.el;
  const n = el.pole.value.length;
  el.citac.textContent = `${n} / ${S.maxZnaku}`;
  el.citac.classList.toggle("cz-chat-citac-plno", n >= S.maxZnaku * 0.9);
}

function obnovZbyva(n) {
  const t = zbyvaText(n);
  S.el.zbyva.textContent = t;
  S.el.zbyva.hidden = !t;
}

// ---------------------------------------------------------------- /stav

async function obnovStav() {
  let d = null, kod = null;
  try {
    const r = await fetch(`${API}/stav`, {cache: "no-store"});
    if (r.ok) d = await r.json();
    else kod = "vypnuto";       // skrytý režim bez platné cookie: asistent tu teď není
  } catch {
    kod = "spojeni";
  }
  if (d) {
    S.stav = d;
    S.maxZnaku = Number.isInteger(d.max_znaku_zpravy) && d.max_znaku_zpravy > 0 ? d.max_znaku_zpravy : MAX_ZNAKU;
    S.el.pole.maxLength = S.maxZnaku;
    S.sitekey = typeof d.turnstile_sitekey === "string" ? d.turnstile_sitekey : "";
    kod = d.kod || null;
    obnovZbyva(d.zbyva_otazek);
    if (S.sitekey) pripravTurnstile();
  }
  if (kod === "spojeni") return ukazHlasku(kod);
  if (kod) return zablokuj(kod, d || {});
  if (S.blok && S.blok.kod !== "limit_konverzace") zrusBlok();
  obnovTlacitka();
}

// ---------------------------------------------------------------- Turnstile (spec 11.2)

function nactiTurnstile() {
  if (S.tsNacteno) return S.tsNacteno;
  S.tsNacteno = new Promise((ok, chyba) => {
    if (window.turnstile) return ok(window.turnstile);
    const s = document.createElement("script");
    s.src = TURNSTILE_JS;
    s.async = true;
    s.onload = () => (window.turnstile ? ok(window.turnstile) : chyba(new Error("turnstile")));
    s.onerror = () => { S.tsNacteno = null; chyba(new Error("turnstile")); };
    document.head.append(s);
  });
  return S.tsNacteno;
}

function predejToken(t) {
  S.token = t;
  const cekaji = S.tsCekaji;
  S.tsCekaji = [];
  for (const c of cekaji) c(t);
}

async function pripravTurnstile() {
  if (S.tsId !== null) return;
  try {
    const ts = await nactiTurnstile();
    if (S.tsId !== null) return;
    S.tsId = ts.render(S.el.ts, {
      sitekey: S.sitekey, action: "chat", appearance: "interaction-only", language: "cs",
      "refresh-expired": "auto",
      callback: (t) => predejToken(t),
      "expired-callback": () => { S.token = null; },
      "error-callback": () => { S.token = null; },
    });
  } catch { /* skript se nenačetl: server vrátí 428 a widget to zkusí znovu */ }
}

// Nový jednorázový token: starý se spotřeboval (nebo ho server odmítl). null = nepovedlo se.
async function novyToken() {
  S.token = null;
  try {
    await pripravTurnstile();
    if (S.tsId === null) return null;
    const cekani = new Promise((ok) => {
      S.tsCekaji.push(ok);
      setTimeout(() => ok(null), TURNSTILE_CEKANI_MS);
    });
    window.turnstile.reset(S.tsId);
    return await cekani;
  } catch {
    return null;
  }
}

function zahodToken() {
  S.token = null;
  if (S.tsId !== null && window.turnstile) {
    try { window.turnstile.reset(S.tsId); } catch { /* nic */ }
  }
}

// ---------------------------------------------------------------- odeslání

function odeslatZPole() {
  const text = S.el.pole.value.trim();
  if (!text) return;
  if (S.bezi) return nabidniZastaveni(text);
  odesli(text);
}

function odeslatNeboZastavit() {
  if (S.bezi) return zastav();
  odeslatZPole();
}

function nabidniZastaveni(text) {
  const p = systemovyPrvek({text: "Předchozí odpověď ještě běží.", odkazy: []}, [
    tlacitko("Zastavit předchozí a zeptat se", async () => {
      p.remove();
      const b = S.bezi;
      if (b) {
        zastav();
        await b.konec;
      }
      odesli(text);
    }),
  ]);
  pripojSystem(p);
}

const spi = (ms) => new Promise((ok) => setTimeout(ok, ms));

async function posliZpravu(text, k) {
  let tsPokusu = 0, obsazeno = 0;
  for (;;) {
    let r;
    try {
      r = await fetch(`${API}/zprava`, {
        method: "POST", headers: {"Content-Type": "application/json"}, cache: "no-store",
        body: JSON.stringify({text, konverzace_id: S.konv.id || null, kontext: k, turnstile: S.token || null}),
      });
    } catch {
      return {kod: "spojeni"};
    }
    if (r.ok && (r.headers.get("content-type") || "").startsWith("text/event-stream")) return {odpoved: r};
    let d = {};
    try { d = await r.json(); } catch { /* tělo bez JSON */ }
    const kod = typeof d.kod === "string" ? d.kod : "chyba";
    if (kod === "turnstile" && S.sitekey && tsPokusu < MAX_TURNSTILE) {
      // token se spotřeboval nebo vypršel: nový token a stejná zpráva znovu, bez zásahu uživatele
      tsPokusu++;
      const skryj = docasnaHlaska("turnstile");
      const t = await novyToken();
      skryj();
      if (!t) return {kod: "overeni_nedostupne"};
      continue;
    }
    if (kod === "turnstile") return {kod: "overeni_nedostupne"};
    if (kod === "overeni_nedostupne") {
      zahodToken();     // token se spotřeboval už před ověřením; příště nový
      return {kod};
    }
    if (kod === "obsazeno") {
      if (obsazeno >= MAX_OBSAZENO) return {kod: "obsazeno_trvale"};
      obsazeno++;
      const s = Number(r.headers.get("retry-after"));
      const skryj = docasnaHlaska("obsazeno");
      await spi((Number.isFinite(s) && s > 0 ? Math.min(s, 30) : 5) * 1000);
      skryj();
      continue;
    }
    return {kod, data: d};
  }
}

async function odesli(text) {
  text = String(text || "").trim().slice(0, S.maxZnaku);
  if (!text || S.blok || S.bezi || S.odesila) return;
  S.odesila = true;
  S.el.odeslat.disabled = true;
  S.nabidnoutPredchozi = null;
  zkontrolujStranku();
  const k = aktualniKontext();
  const otazka = {role: "uzivatel", text};
  S.zpravy.push(otazka);
  const prvek = pripojZpravu(otazka);
  S.el.tretiStrany.hidden = true;
  S.el.pole.value = "";
  obnovCitac();
  S.el.stav.textContent = "Odesílám…";
  const v = await posliZpravu(text, k);
  S.odesila = false;
  S.el.stav.textContent = "";
  if (!v.odpoved) {
    // zpráva neodešla: zmizí z konverzace a vrátí se do pole, ať ji uživatel nemusí psát znovu
    S.zpravy.splice(S.zpravy.indexOf(otazka), 1);
    prvek?.remove();
    S.el.pole.value = text;
    S.el.tretiStrany.hidden = S.zpravy.some((z) => z.role === "uzivatel");
    ulozHned();
    obnovTlacitka();
    ukazHlasku(v.kod, v.data || {});
    return;
  }
  const bot = {role: "bot", text: "", stav: "probiha"};
  S.zpravy.push(bot);
  zacniTah(bot, pripojZpravu(bot), null, 0);
  ulozHned();
  ctiProud(v.odpoved);
}

// ---------------------------------------------------------------- stream tahu

function zacniTah(bot, el, tahId, od) {
  let dokonci;
  const konec = new Promise((ok) => { dokonci = ok; });
  S.bezi = {tahId, od, bot, el, hotovo: false, konec, dokonci, pokusu: 0};
  obnovTlacitka();
}

async function ctiProud(odpoved) {
  const b = S.bezi;
  if (!b) return;
  try {
    const cteni = odpoved.body.getReader();
    const dekoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const {value, done} = await cteni.read();
      if (done) break;
      buf += dekoder.decode(value, {stream: true});
      const r = rozeber(buf);
      buf = r.zbytek;
      for (const u of r.udalosti) zpracuj(b, u);
      if (b.hotovo) {
        cteni.cancel().catch(() => {});
        return;
      }
    }
  } catch { /* spojení spadlo: připojíme se znovu */ }
  if (!b.hotovo && S.bezi === b) pripojZnovu(b);
}

function zpracuj(b, u) {
  if (Number.isInteger(u.id)) b.od = u.id + 1;
  const d = u.data || {};
  switch (u.typ) {
    case "start":
      if (typeof d.konverzace_id === "string") S.konv.id = d.konverzace_id;
      if (typeof d.tah_id === "string") b.tahId = d.tah_id;
      oznacAktivitu();
      break;
    case "stav":
      if (typeof d.text === "string") S.el.stav.textContent = d.text;
      break;
    case "delta":
      if (typeof d.text === "string") {
        b.bot.text += d.text;
        if (S.el.stav.textContent !== "Píšu odpověď…") S.el.stav.textContent = "Píšu odpověď…";
        prekresliBezici();
      }
      break;
    case "overeni":
      // značky ověření citátu dokreslí úloha 18; text „(neověřeno ve zdroji)“ vkládá server sám
      if (Array.isArray(d.citaty)) b.bot.citaty = [...(b.bot.citaty || []), ...d.citaty];
      break;
    case "zdroje":
      if (Array.isArray(d.zdroje)) b.bot.zdroje = d.zdroje;
      break;
    case "chyba":
      if (d.kod === "odmitnuto") {
        // odmítnutí uprostřed streamu: celý zobrazený text odpovědi se stáhne
        b.bot.text = "";
        b.bot.stav = "odmitnuto";
        b.bot.citaty = [];
        b.bot.zdroje = [];
      } else b.bot.stav = "chyba";
      break;
    case "konec":
      dokonciTah(b, d);
      return;
    default:
      return;
  }
  S.konv.tah = b.tahId ? {id: b.tahId, od: b.od} : null;
  uloz();
}

function dokonciTah(b, d) {
  b.hotovo = true;
  clearTimeout(S.prekresleni);
  S.prekresleni = null;
  if (typeof d.kod === "string") b.bot.kod = d.kod;
  if (b.bot.stav !== "odmitnuto") b.bot.stav = typeof d.stav === "string" ? d.stav : b.bot.stav;
  if (b.bot.stav === "probiha") b.bot.stav = "ok";
  b.el = nahradPrvek(b.bot, b.el);
  S.el.stav.textContent = "";
  S.el.oznameni.textContent = b.bot.stav === "odmitnuto" ? hlaska("odmitnuto").text : b.bot.text;
  if (Number.isInteger(d.zbyva_otazek)) obnovZbyva(d.zbyva_otazek);
  if (S.bezi === b) S.bezi = null;
  S.konv.tah = null;
  oznacAktivitu();
  if (!S.el.panel.hidden) zkontrolujStranku();
  ulozHned();
  obnovTlacitka();
  b.dokonci();
}

async function pripojZnovu(b) {
  while (S.bezi === b && !b.hotovo && b.pokusu < MAX_PRIPOJENI) {
    if (!b.tahId) break;
    b.pokusu++;
    if (b.pokusu > 1) await spi(1000 * 2 ** (b.pokusu - 2));
    let r;
    try {
      r = await fetch(`${API}/tah/${encodeURIComponent(b.tahId)}/stream?od=${b.od}`, {cache: "no-store"});
    } catch {
      continue;
    }
    if (r.status === 404) break;       // tah už v procesu není: výsledek je na serveru
    if (!r.ok) continue;
    b.pokusu = 0;
    return ctiProud(r);
  }
  if (S.bezi === b && !b.hotovo) await dohledejZeServeru(b);
}

// Tah se nepodařilo dočíst: konečný stav odpovědi z GET /konverzace/{id} (server je zdroj pravdy)
async function dohledejZeServeru(b) {
  const d = await nactiKonverzaci(S.konv.id);
  const posledni = d && [...d.zpravy].reverse().find((z) => z.role === "bot");
  if (posledni && posledni.stav !== "probiha") {
    b.bot.text = posledni.text || "";
    dokonciTah(b, {kod: posledni.kod, stav: posledni.stav});
    return;
  }
  dokonciTah(b, {stav: b.bot.text ? "preruseno" : "chyba"});
}

async function zastav() {
  const b = S.bezi;
  if (!b || !b.tahId) return;
  S.el.stav.textContent = "Zastavuji…";
  try {
    await fetch(`${API}/tah/${encodeURIComponent(b.tahId)}/stop`, {method: "POST", cache: "no-store"});
  } catch { /* stream doběhne sám */ }
}

// ---------------------------------------------------------------- konverzace: obnova, nový chat, smazání

async function nactiKonverzaci(id) {
  if (typeof id !== "string" || !/^[0-9a-f]{32}$/.test(id)) return null;
  try {
    const r = await fetch(`${API}/konverzace/${id}`, {cache: "no-store"});
    if (!r.ok) return null;
    const d = await r.json();
    return d && Array.isArray(d.zpravy) ? d : null;
  } catch {
    return null;
  }
}

async function pokracovat() {
  const p = S.nabidnoutPredchozi;
  S.nabidnoutPredchozi = null;
  const d = p && await nactiKonverzaci(p.id);
  if (!d) {
    zapis(localStorage, K_POSLEDNI, null);
    vykresliVse();
    return ukazHlasku("konverzace_nedostupna");
  }
  S.konv = {...S.konv, id: p.id, tah: null, stranka: popisStranky(zakladniKontext())};
  S.zpravy = d.zpravy.map((z) => z.role === "uzivatel" ? {role: "uzivatel", text: String(z.text || "")}
    : {role: "bot", text: String(z.text || ""), kod: z.kod, stav: z.stav === "probiha" ? "preruseno" : z.stav})
    .slice(-MAX_ZRCADLO);
  oznacAktivitu();
  ulozHned();
  vykresliVse();
  S.el.pole.focus();
}

function vycisti() {
  S.konv = {otevreno: true, stranka: popisStranky(zakladniKontext())};
  S.zpravy = [];
  S.volba = "auto";
  if (S.blok && S.blok.kod === "limit_konverzace") zrusBlok();
  ulozHned();
  obnovCip();
  vykresliVse();
}

function novyChat() {
  if (S.bezi) return;
  vycisti();
  obnovStav();
  S.el.pole.focus();
}

async function smazatKonverzaci() {
  const id = S.konv.id;
  if (S.bezi || !id) return;
  if (!window.confirm("Smazat tuto konverzaci? Smaže se hned i na serveru.")) return;
  let r = null;
  try {
    r = await fetch(`${API}/smazat`, {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({konverzace_id: id}), cache: "no-store"});
  } catch { /* níž */ }
  if (!r || (r.status !== 204 && r.status !== 404)) {
    let kod = "chyba";
    try { kod = (await r.json()).kod || kod; } catch { /* nic */ }
    return ukazHlasku(r ? kod : "spojeni");
  }
  const posledni = cti(localStorage, K_POSLEDNI);
  if (posledni && posledni.id === id) zapis(localStorage, K_POSLEDNI, null);
  vycisti();
  S.el.oznameni.textContent = "Konverzace je smazaná.";
  S.el.pole.focus();
}

// ---------------------------------------------------------------- otevření a zavření

export function otevri() {
  const el = S.el;
  if (!el.panel.hidden) return;
  el.panel.hidden = false;
  S.bublina.style.display = "none";     // inline styl bubliny z w.js přebije atribut hidden
  document.documentElement.classList.toggle("cz-chat-zamceno", innerWidth < UZKE);
  S.konv.otevreno = true;
  rozmer();
  zkontrolujStranku();
  obnovCip();
  obnovNabidku();
  obnovStav();
  el.zpravy.scrollTop = el.zpravy.scrollHeight;     // skrytý panel měl nulovou výšku, posun se dožene teď
  el.pole.focus();
}

export function zavri() {
  const el = S.el;
  if (el.panel.hidden) return;
  prepniVolby(false);
  el.panel.hidden = true;
  document.documentElement.classList.remove("cz-chat-zamceno");
  S.bublina.style.display = "flex";
  S.poloha?.();
  S.konv.otevreno = false;
  ulozHned();
  S.bublina.focus();
}

// Volá w.js po prvním kliknutí na bublinu (nebo sám, když na předchozí stránce běžela odpověď)
export function spust({bublina, stav, poloha}) {
  if (S.el.panel) return otevri();
  S.bublina = bublina;
  S.poloha = poloha;
  S.stav = stav || null;
  bublina.removeAttribute("aria-busy");
  nactiUlozene();
  postav();
  obnovCitac();
  // rozběhnutý tah z předchozí stránky: připojí se od posledního přijatého offsetu (spec 7.3)
  const tah = S.konv.tah;
  let bot = null;
  if (tah && typeof tah.id === "string" && Number.isInteger(tah.od)) {
    bot = S.zpravy[S.zpravy.length - 1];
    if (!bot || bot.role !== "bot") {
      bot = {role: "bot", text: "", stav: "probiha"};
      S.zpravy.push(bot);
    }
    bot.stav = "probiha";
    zacniTah(bot, null, tah.id, tah.od);
  }
  vykresliVse();         // nastaví i S.bezi.el na prvek rozepsané odpovědi
  otevri();
  if (bot) {
    S.el.stav.textContent = "Připojuji se k rozepsané odpovědi…";
    pripojZnovu(S.bezi);
  }
}
