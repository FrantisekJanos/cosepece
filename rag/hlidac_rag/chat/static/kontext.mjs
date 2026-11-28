// Kontext obrazovky z URL a DOM (spec 5.1 až 5.2). Do kontextu jdou jen povolený typ stránky a celá čísla,
// nikdy text z adresy (q, filtry) ani z DOM (titulky). Tvar musí sedět s chat/kontext.py (z_json).

const MAX_CISLO = 2 ** 31 - 1;
const PRAH_ZAOSTRENI = 0.5;   // položka zabírající aspoň tolik výšky okna je zaostřená
const MAX_NABIDKA = 5;
const UZKE_OKNO = 640;

// Cesty bez bubliny; stejný výčet má mít zavaděč w.js (hlídá ho test F38)
export const BEZ_BUBLINY = ["/revize", "/mcp"];

const STATICKE = {
  "/": "prehled", "/tisky": "seznam_tisku", "/zakony": "zakony", "/hledat": "hledani", "/upozorneni": "upozorneni",
  "/pripravovane": "pripravovane", "/metodika": "staticka", "/zmeny": "staticka", "/data-a-api": "staticka",
  "/mcp": "staticka",
};

function cislo(text) {
  if (!/^[0-9]{1,10}$/.test(text)) return null;
  const n = Number(text);
  return n > 0 && n <= MAX_CISLO ? n : null;
}

function bezLomitka(p) {
  return p.length > 1 ? p.replace(/\/+$/, "") || "/" : p;
}

function zJedneCesty(pathname) {
  const p = bezLomitka(String(pathname || ""));
  let m = /^\/tisk\/([0-9]+)$/.exec(p);
  if (m) {
    const ct = cislo(m[1]);
    return ct ? {typ: "tisk", ct} : {typ: "jina"};
  }
  m = /^\/zakon\/([0-9]{1,4})\/([0-9]{4})$/.exec(p);
  if (m) return Number(m[1]) > 0 ? {typ: "zakon", z: `${Number(m[1])}/${m[2]}`} : {typ: "jina"};
  if (Object.hasOwn(STATICKE, p)) return {typ: STATICKE[p]};
  return {typ: "jina"};
}

export function zCesty(pathname, canonical) {
  const r = zJedneCesty(pathname);
  if (r.typ !== "jina" || !canonical) return r;
  try {
    return zJedneCesty(new URL(canonical, "https://cosepece.cz").pathname);
  } catch {
    return r;
  }
}

export function maBublinu(pathname) {
  return !BEZ_BUBLINY.includes(bezLomitka(String(pathname || "")));
}

export function zKotvy(hash) {
  const h = String(hash || "");
  let m = /^#pn-([0-9]+)$/.exec(h);
  if (m) {
    const pn = cislo(m[1]);
    return pn ? {pn} : {};
  }
  m = /^#prep-([A-Za-z0-9_-]{1,40})$/.exec(h);
  return m ? {pid: m[1]} : {};
}

// polozky: [{druh, id, top, bottom}] z getBoundingClientRect; nulový obdélník (zavřené details) a položky mimo okno se nepočítají
export function zaostreni(polozky, vyskaOkna) {
  const videt = [];
  for (const p of polozky) {
    if (!(p.bottom > p.top)) continue;
    const cast = Math.min(p.bottom, vyskaOkna) - Math.max(p.top, 0);
    if (cast > 0) videt.push({druh: p.druh, id: p.id, top: p.top, cast});
  }
  let nej = null;
  for (const p of videt) if (!nej || p.cast > nej.cast) nej = p;
  if (nej && nej.cast >= PRAH_ZAOSTRENI * vyskaOkna) return {zaostreno: {druh: nej.druh, id: nej.id}, nabidka: []};
  videt.sort((a, b) => a.top - b.top);
  return {zaostreno: null, nabidka: videt.slice(0, MAX_NABIDKA).map((p) => ({druh: p.druh, id: p.id}))};
}

export function zmer(document, window) {
  const out = [];
  for (const li of document.querySelectorAll('li.pn-item[id^="pn-"]')) {
    const m = /^pn-([0-9]+)$/.exec(li.id || "");
    const id = m && cislo(m[1]);
    if (!id) continue;
    const r = li.getBoundingClientRect();
    out.push({druh: "pn", id, top: r.top, bottom: r.bottom});
  }
  for (const li of document.querySelectorAll("li.vote")) {
    const a = li.querySelector('a[href*="hlasy.sqw?g="]');
    const m = a && /hlasy\.sqw\?g=([0-9]+)/.exec(a.getAttribute("href") || "");
    const id = m && cislo(m[1]);
    if (!id) continue;
    const r = li.getBoundingClientRect();
    out.push({druh: "hlasovani", id, top: r.top, bottom: r.bottom});
  }
  return out;
}

// Výška viditelné části okna: na mobilu s otevřenou klávesnicí je menší než innerHeight
export function vyskaOkna(window) {
  return window.visualViewport?.height ?? window.innerHeight;
}

export function kontext(document, window) {
  const loc = window.location;
  const kan = document.querySelector('link[rel="canonical"]');
  const k = zCesty(loc.pathname, kan && kan.href);
  const kotva = zKotvy(loc.hash);
  if (k.typ === "tisk") {
    if (kotva.pn) k.pn = kotva.pn;
    else {
      const z = zaostreni(zmer(document, window), vyskaOkna(window)).zaostreno;
      if (z) k[z.druh === "pn" ? "pn" : "hlasovani"] = z.id;
    }
  } else if (k.typ === "pripravovane" && kotva.pid) k.pid = kotva.pid;
  k.uzke = window.innerWidth < UZKE_OKNO;
  return k;
}
