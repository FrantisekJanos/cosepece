// Odpověď bota v panelu (spec 6, 7.1, 9.1, 9.2): text z markdownu (vykresleni.mjs), řádek „Zdroje (N)“, čipy
// ověření citátů, patička (palec, kopírovat, kód odpovědi, nahlásit) a modál náhledu zdroje z GET /nahled.
// Data ze serveru (titulky, text náhledu, poznámky) jdou do stránky jen přes textContent; innerHTML dostane jen
// výstup markdown-it bez HTML.

import { e, tlacitko, udalost } from "./dom.mjs";
import {
  htmlOdpovedi, cipyOvereni, zvyrazneniZdroje, useky, odkazyNahledu, popisZnacky,
} from "./vykresleni.mjs";

const NAZVY_ZNENI = {schvalene: "schválené znění", puvodni: "původní znění"};
const MAX_DUVOD = 1000;

const Z = {api: "/chat-api", uloz: () => {}, nahled: null, pozadavek: 0};

// api = předpona adres služby, uloz = uložení zrcadla po změně zprávy (palec)
export function nastav({api, uloz}) {
  if (api) Z.api = api;
  if (uloz) Z.uloz = uloz;
}

function zdrojeZpravy(z) {
  return Array.isArray(z.zdroje) ? z.zdroje.filter((x) => x && Number.isInteger(x.n)) : [];
}

// ---------------------------------------------------------------- text odpovědi

export function vyplnText(el, z, {streamuje = false, uzke = false} = {}) {
  const zdroje = zdrojeZpravy(z);
  el.innerHTML = htmlOdpovedi(z.text || "", {streamuje, uzke, zname: new Set(zdroje.map((x) => x.n))});
  el.onclick = (ev) => {
    const b = ev.target.closest?.(".cz-chat-cit");
    if (!b) return;
    const n = Number(b.dataset.n);
    const zdroj = zdroje.find((x) => x.n === n);
    if (!zdroj) return;
    udalost("citation_open", {typ: zdroj.typ});
    if (zdroj.klic) otevriNahled(zdroj.klic, zvyrazneniZdroje(z.citaty, n), b);
    else el.parentNode?.querySelector(`[data-zdroj="${n}"]`)?.focus();
  };
}

// ---------------------------------------------------------------- zdroje, ověření a patička pod odpovědí

function radekZdroju(z) {
  const zdroje = zdrojeZpravy(z);
  if (!zdroje.length) {
    return e("div", {class: "cz-chat-zdroje"}, e("span", {class: "cz-chat-znacka", text: "bez zdroje"}));
  }
  const polozky = zdroje.map((x) => {
    const popis = `[${x.n}] ${x.titulek || x.typ || "zdroj"}`;
    const cip = x.klic
      ? e("button", {type: "button", class: "cz-chat-zdroj", "data-zdroj": x.n, text: popis,
        onclick: (ev) => otevriNahled(x.klic, null, ev.currentTarget)})
      : e("span", {class: "cz-chat-zdroj cz-chat-zdroj-bez", "data-zdroj": x.n, tabindex: -1, text: popis});
    const znacky = (Array.isArray(x.znacky) ? x.znacky : []).map(popisZnacky).filter(Boolean)
      .map((t) => e("span", {class: "cz-chat-znacka", text: t}));
    return e("li", {}, cip, ...znacky);
  });
  return e("div", {class: "cz-chat-zdroje"}, e("span", {class: "cz-chat-zdroje-nadpis", text: `Zdroje (${zdroje.length})`}),
    e("ul", {}, ...polozky));
}

function radekOvereni(z) {
  const cipy = cipyOvereni(z.citaty);
  if (!cipy.length) return null;
  return e("ul", {class: "cz-chat-overeni", "aria-label": "Ověření citací"}, ...cipy.map((c) => {
    const pozn = c.poznamka ? e("span", {class: "cz-chat-znacka", text: c.poznamka}) : null;
    const cip = c.klic
      ? e("button", {type: "button", class: "cz-chat-zdroj", text: c.text,
        onclick: (ev) => otevriNahled(c.klic, c.zv, ev.currentTarget)})
      : e("span", {class: "cz-chat-zdroj cz-chat-zdroj-bez", text: c.text});
    return e("li", {}, cip, pozn);
  }));
}

async function posli(cesta, telo) {
  try {
    const r = await fetch(`${Z.api}/${cesta}`, {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(telo), cache: "no-store"});
    if (r.status === 204) return "ok";
    try { return (await r.json()).kod || "chyba"; } catch { return "chyba"; }
  } catch {
    return "spojeni";
  }
}

function pata(z) {
  const zprava = e("span", {class: "cz-chat-pata-zprava", "aria-live": "polite"});
  const palce = [[1, "👍", "Odpověď pomohla"], [-1, "👎", "Odpověď nepomohla"]].map(([palec, ikona, popis]) => {
    const b = e("button", {type: "button", class: "cz-chat-mini", "aria-label": popis, title: popis, text: ikona,
      "aria-pressed": String(z.palec === palec)});
    b.addEventListener("click", async () => {
      const v = await posli("hodnoceni", {kod: z.kod, palec});
      if (v !== "ok") return void (zprava.textContent = "Hodnocení se nepodařilo uložit.");
      z.palec = palec;
      Z.uloz();
      for (const [p, el] of palce) el.setAttribute("aria-pressed", String(p === palec));
      zprava.textContent = "Díky za hodnocení.";
      udalost("chat_feedback", {palec});
    });
    return [palec, b];
  });
  const kopirovat = tlacitko("Kopírovat", async () => {
    try {
      await navigator.clipboard.writeText(z.text || "");
      zprava.textContent = "Zkopírováno.";
    } catch {
      zprava.textContent = "Kopírování prohlížeč nepovolil.";
    }
  }, "cz-chat-mini");
  const formular = e("div", {class: "cz-chat-nahlasit", hidden: true});
  const nahlasit = tlacitko("Nahlásit", () => {
    formular.hidden = !formular.hidden;
    if (!formular.hidden) formular.querySelector("textarea").focus();
  }, "cz-chat-mini");
  const duvod = e("textarea", {rows: 2, maxlength: MAX_DUVOD, "aria-label": "Co je na odpovědi špatně",
    placeholder: "Co je na odpovědi špatně?"});
  formular.append(duvod, e("div", {class: "cz-chat-system-akce"},
    tlacitko("Odeslat hlášení", async () => {
      const text = duvod.value.trim();
      if (!text) return duvod.focus();
      const v = await posli("nahlasit", {kod: z.kod, duvod: text.slice(0, MAX_DUVOD)});
      if (v === "ok") {
        formular.remove();
        nahlasit.disabled = true;
        zprava.textContent = "Děkujeme, hlášení jsme přijali.";
      } else {
        zprava.textContent = v === "limit_nahlaseni" ? "Hlášení teď nejde odeslat, zkuste to zítra."
          : "Hlášení se nepodařilo odeslat.";
      }
    }),
    tlacitko("Zrušit", () => { formular.hidden = true; nahlasit.focus(); })));
  const kod = e("a", {class: "cz-chat-kod", href: `${Z.api}/overit?kod=${encodeURIComponent(z.kod)}`, target: "_blank",
    rel: "noopener", title: "Kód odpovědi: ověří, že odpověď opravdu napsal asistent", text: `Kód ${z.kod}`});
  return e("div", {class: "cz-chat-odpoved-pata"},
    e("div", {class: "cz-chat-odpoved-akce"}, ...palce.map(([, b]) => b), kopirovat, nahlasit, kod), zprava, formular);
}

// Prvky pod dokončenou odpovědí (prázdné pole u rozepsané, odmítnuté nebo prázdné)
export function doplnky(z) {
  if (z.stav === "probiha" || z.stav === "odmitnuto" || !z.text) return [];
  const out = [radekZdroju(z), radekOvereni(z)];
  if (typeof z.kod === "string" && /^[a-z0-9]{8}$/.test(z.kod)) out.push(pata(z));
  return out.filter(Boolean);
}

// ---------------------------------------------------------------- modál náhledu (na mobilu list přes panel)

function fokusovatelne(el) {
  return [...el.querySelectorAll("button, a[href], [tabindex]:not([tabindex='-1'])")].filter((x) => !x.disabled);
}

export function zavriNahled() {
  const m = Z.nahled;
  if (!m) return;
  Z.nahled = null;
  Z.pozadavek++;
  m.el.remove();
  m.opener?.isConnected && m.opener.focus();
}

function postavModal(opener) {
  if (Z.nahled) {
    Z.nahled.opener = Z.nahled.opener || opener;
    return Z.nahled;
  }
  const nadpis = e("h3", {id: "cz-chat-nahled-nadpis", class: "cz-chat-nahled-nadpis", text: "Náhled zdroje"});
  const obsah = e("div", {class: "cz-chat-nahled-obsah"});
  const dialog = e("div", {class: "cz-chat-nahled", role: "dialog", "aria-modal": "true",
    "aria-labelledby": "cz-chat-nahled-nadpis", tabindex: -1},
  e("div", {class: "cz-chat-nahled-hlava"}, nadpis,
    e("button", {type: "button", class: "cz-chat-tl cz-chat-zavrit", "aria-label": "Zavřít náhled", text: "×",
      onclick: () => zavriNahled()})),
  obsah);
  const el = e("div", {class: "cz-chat-nahled-pozadi"}, dialog);
  el.addEventListener("click", (ev) => { if (ev.target === el) zavriNahled(); });
  el.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      ev.preventDefault();
      ev.stopPropagation();
      return zavriNahled();
    }
    if (ev.key !== "Tab") return;
    const f = fokusovatelne(dialog);
    if (!f.length) return;
    const prvni = f[0], posledni = f[f.length - 1];
    if (ev.shiftKey && document.activeElement === prvni) {
      ev.preventDefault();
      posledni.focus();
    } else if (!ev.shiftKey && document.activeElement === posledni) {
      ev.preventDefault();
      prvni.focus();
    }
  });
  document.body.append(el);
  Z.nahled = {el, dialog, nadpis, obsah, opener};
  return Z.nahled;
}

function textNahledu(text, zv) {
  const el = e("div", {class: "cz-chat-nahled-text"});
  for (const u of useky(text, zv)) el.append(u.zv ? e("mark", {text: u.text}) : document.createTextNode(u.text));
  return el;
}

function tabulkaKlubu(kluby) {
  if (!Array.isArray(kluby) || !kluby.length) return null;
  const sloupce = [["klub", "Klub"], ["pro", "Pro"], ["proti", "Proti"], ["zdrzel", "Zdržel se"], ["nepritomen", "Nepřít."]];
  const bunka = (tag, v) => e(tag, {text: v === null || v === undefined ? "–" : String(v)});
  return e("table", {class: "cz-chat-kluby"},
    e("thead", {}, e("tr", {}, ...sloupce.map(([, t]) => bunka("th", t)))),
    e("tbody", {}, ...kluby.map((k) => e("tr", {}, ...sloupce.map(([s]) => bunka(s === "klub" ? "th" : "td", k && k[s]))))));
}

function vyplnNahled(m, d, zv) {
  m.nadpis.textContent = String(d.titulek || "Náhled zdroje");
  const deti = [];
  const cesta = (Array.isArray(d.cesta) ? d.cesta : []).map(String).filter(Boolean);
  if (cesta.length) deti.push(e("p", {class: "cz-chat-nahled-cesta", text: cesta.join(" / ")}));
  const znacky = (Array.isArray(d.znacky) ? d.znacky : []).map((x) => x === "odvozeny_ai" ? "odvozený text AI" : popisZnacky(x))
    .filter(Boolean);
  if (typeof d.verze === "string" && NAZVY_ZNENI[d.verze]) znacky.push(NAZVY_ZNENI[d.verze]);
  if (znacky.length) deti.push(e("p", {}, ...znacky.map((t) => e("span", {class: "cz-chat-znacka", text: t}))));
  if (d.tabulka) deti.push(e("p", {class: "cz-chat-poznamka", text: "Tabulka, viz originál."}));
  const text = textNahledu(d.text, Array.isArray(d.zv) ? d.zv : null);
  deti.push(text);
  if (d.zkraceno) deti.push(e("p", {class: "cz-chat-poznamka", text: "Text je zkrácený, celý je v originále."}));
  const kluby = tabulkaKlubu(d.kluby);
  if (kluby) deti.push(kluby);
  const nav = [];
  if (typeof d.predchozi === "string") nav.push(tlacitko("← Předchozí", () => otevriNahled(d.predchozi, null)));
  if (typeof d.dalsi === "string") nav.push(tlacitko("Další →", () => otevriNahled(d.dalsi, null)));
  if (nav.length) deti.push(e("div", {class: "cz-chat-nahled-nav"}, ...nav));
  const odkazy = odkazyNahledu(d.odkazy).map((o) => e("a", {class: "cz-chat-tl", href: o.href, text: o.text,
    ...(o.nove ? {target: "_blank", rel: "noopener noreferrer"} : {})}));
  if (odkazy.length) deti.push(e("div", {class: "cz-chat-nahled-odkazy"}, ...odkazy));
  deti.push(e("p", {class: "cz-chat-nahled-zdroj", text: `Zdroj: ${d.zdroj || "Poslanecká sněmovna PČR"}`}));
  m.obsah.replaceChildren(...deti);
  m.obsah.scrollTop = 0;
  const mark = text.querySelector("mark");
  if (mark) mark.scrollIntoView?.({block: "center"});
}

const CHYBY_NAHLEDU = {nenalezeno: "Náhled tohoto zdroje není k dispozici.",
  limit_pozadavku: "Příliš mnoho náhledů najednou, zkuste to za chvíli."};

export async function otevriNahled(klic, zv, opener = null) {
  const m = postavModal(opener);
  const cislo = ++Z.pozadavek;
  m.obsah.replaceChildren(e("p", {class: "cz-chat-poznamka", text: "Načítám náhled…"}));
  m.dialog.focus();
  const q = `k=${encodeURIComponent(klic)}${zv ? `&zv=${zv[0]}-${zv[1]}` : ""}`;
  let d = null, chyba = null;
  try {
    const r = await fetch(`${Z.api}/nahled?${q}`);
    try { d = await r.json(); } catch { d = null; }
    if (!r.ok) chyba = (d && d.kod) || "chyba";
  } catch {
    chyba = "spojeni";
  }
  if (cislo !== Z.pozadavek) return;      // mezitím zavřeno nebo otevřen jiný náhled
  if (chyba || !d || typeof d.text !== "string") {
    m.nadpis.textContent = "Náhled zdroje";
    m.obsah.replaceChildren(e("p", {text: CHYBY_NAHLEDU[chyba] || "Náhled se teď nepodařilo načíst. Zkuste to znovu."}));
    return;
  }
  vyplnNahled(m, d, zv);
  udalost("preview_open", {typ: String(d.typ || "")});
}
