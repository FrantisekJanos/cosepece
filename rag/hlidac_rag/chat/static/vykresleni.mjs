// Vykreslení odpovědi bota (spec 7.2, 9.1, 12): markdown → HTML řetězec bez DOM, aby šel testovat v Node.
// Bezpečnost: markdown-it bez HTML (`html: false`), bez odkazových definic a linkify, obrázek jen jako alt text,
// odkaz jen https na doménu z whitelistu (jinak zůstane jeho text bez odkazu), `[N]` jako tlačítko zdroje.
// Výsledek jde do innerHTML jen jako celek z markdown-it; text náhledů a čipů vkládá widget přes textContent.

import * as vendor from "./vendor/markdown-it.min.js";

// UMD: v Node (CommonJS) vrací export, v prohlížeči se jako modul zapíše do globalThis.markdownit;
// globál se hned zachytí a smaže, ať na stránce webu nezůstane
const markdownit = vendor.default || globalThis.markdownit;
delete globalThis.markdownit;
if (typeof markdownit !== "function") throw new Error("vykresleni.mjs: vendor/markdown-it.min.js se nenačetl");

export const DOMENY = ["cosepece.cz", "psp.cz", "senat.cz", "e-sbirka.cz", "zakonyprolidi.cz", "odok.gov.cz", "vlada.cz"];
const NAS_WEB = "cosepece.cz";

function hostOdkazu(href) {
  if (typeof href !== "string" || !/^https:\/\/[a-z0-9.-]+(?::\d+)?(?:[/?#]|$)/i.test(href)) return null;
  let u;
  try { u = new URL(href); } catch { return null; }
  if (u.protocol !== "https:" || u.username || u.password || u.port !== "") return null;
  return u.hostname.toLowerCase();
}

function naDomene(host, d) {
  return host === d || host.endsWith("." + d);
}

// https a doména z whitelistu spec 12 včetně subdomén
export function povolenyOdkaz(href) {
  const host = hostOdkazu(href);
  return host !== null && DOMENY.some((d) => naDomene(host, d));
}

// Soubory psp.cz (orig2.sqw, robots.txt zakazuje): z textu modelu nikdy odkaz; „Word na psp.cz“ jen z dat náhledu
function vedeNaOrig2(href) {
  try {
    return decodeURIComponent(new URL(href).pathname).toLowerCase().includes("orig2");
  } catch {
    return true;        // nedekódovatelná cesta: raději bez odkazu
  }
}

const DOCX = /^https:\/\/www\.psp\.cz\/sqw\/text\/orig2\.sqw\?idd=[0-9]{1,12}$/;    // jako _DOCX v nahled.py

// odkaz na cosepece.cz zůstává ve stejném okně, ostatní se otevřou v nové kartě
export function nasWeb(href) {
  const host = hostOdkazu(href);
  return host !== null && naDomene(host, NAS_WEB);
}

// Během streamu se poslední blok (za posledním prázdným řádkem) drží zvlášť: rozpracovaná tabulka by se jinak
// při každém kousku překreslila jinak. Hotové bloky se vykreslí jako markdown.
export function pripravText(text, streamuje) {
  const t = String(text || "");
  if (!streamuje) return {hotove: t, rozpracovane: ""};
  const i = t.lastIndexOf("\n\n");
  if (i < 0) return {hotove: "", rozpracovane: t};
  return {hotove: t.slice(0, i + 2), rozpracovane: t.slice(i + 2)};
}

const md = markdownit({html: false, linkify: false, typographer: false, breaks: true});
md.disable(["reference"]);      // [1]: https://… by z citace [1] udělalo odkaz

const esc = md.utils.escapeHtml;

md.renderer.rules.image = (tokens, idx) => esc(tokens[idx].content);

// odkaz mimo whitelist: zmizí link_open i link_close, text mezi nimi zůstane
md.core.ruler.push("cz_odkazy", (state) => {
  for (const blok of state.tokens) {
    if (blok.type !== "inline" || !blok.children) continue;
    const deti = [];
    let zahodit = false;
    for (const t of blok.children) {
      if (t.type === "link_open") {
        const href = t.attrGet("href");
        zahodit = !povolenyOdkaz(href) || vedeNaOrig2(href);
        if (zahodit) continue;
        t.attrs = [["href", href]];
        if (!nasWeb(href)) {
          t.attrSet("target", "_blank");
          t.attrSet("rel", "noopener noreferrer");
        }
      } else if (t.type === "link_close" && zahodit) {
        zahodit = false;
        continue;
      }
      deti.push(t);
    }
    blok.children = deti;
  }
});

// Tabulka na úzké obrazovce: výčet řádků „ANO: Pro 70 · Proti 3“ místo tabulky (spec 7.2)
function vycetTabulky(tokens, od, env) {
  const radky = [];
  let i = od, radek = null;
  for (; i < tokens.length && tokens[i].type !== "table_close"; i++) {
    const t = tokens[i];
    if (t.type === "tr_open") radek = [];
    else if (t.type === "tr_close") radky.push(radek);
    else if (t.type === "inline" && radek) radek.push(md.renderer.renderInline(t.children, md.options, env));
  }
  const [hlava, ...telo] = radky;
  const polozky = telo.map((r) => {
    const dvojice = r.slice(1).map((b, j) => (hlava && hlava[j + 1] ? `${hlava[j + 1]} ${b}` : b)).filter(Boolean);
    return `<li><strong>${r[0] || ""}</strong>${dvojice.length ? ": " + dvojice.join(" · ") : ""}</li>`;
  });
  return {html: `<ul class="cz-chat-vycet">${polozky.join("")}</ul>\n`, konec: i};
}

export function vykresliMarkdown(text, {uzke = false} = {}) {
  const env = {};
  const tokens = md.parse(String(text || ""), env);
  if (!uzke) return md.renderer.render(tokens, md.options, env);
  let html = "", od = 0;
  for (let i = 0; i < tokens.length; i++) {
    if (tokens[i].type !== "table_open") continue;
    html += md.renderer.render(tokens.slice(od, i), md.options, env);
    const v = vycetTabulky(tokens, i, env);
    html += v.html;
    i = v.konec;
    od = i + 1;
  }
  return html + md.renderer.render(tokens.slice(od), md.options, env);
}

// `[N]` známého zdroje → tlačítko; jen v textu mimo značky a mimo kód, neznámé číslo zůstane textem
export function nahradCitace(html, zname) {
  let vKodu = 0;
  return String(html).split(/(<[^>]*>)/).map((cast) => {
    if (cast.startsWith("<")) {
      // uvnitř kódu a odkazu (tlačítko v <a> by nešlo použít) zůstává [N] textem
      if (/^<(code|pre|a)[\s>]/i.test(cast)) vKodu++;
      else if (/^<\/(code|pre|a)>/i.test(cast)) vKodu = Math.max(0, vKodu - 1);
      return cast;
    }
    if (vKodu) return cast;
    return cast.replace(/\[(\d{1,4})\]/g, (cely, c) => {
      const n = Number(c);
      return zname.has(n) ? `<button type="button" class="cz-chat-cit" data-n="${n}" aria-label="Zdroj ${n}">[${n}]</button>`
        : cely;
    });
  }).join("");
}

// Celá odpověď: hotové bloky jako markdown, rozpracovaná tabulka jako prostý text, ostatní rozpracované jako markdown
// Po částech: `hotove` (zdrojový text hotových bloků, klíč keše) a jejich HTML zvlášť od rozpracovaného konce,
// ať widget během streamu přepisuje jen konec (výběr textu v hotových blocích se neruší)
export function castiOdpovedi(text, {streamuje = false, zname = new Set(), uzke = false} = {}) {
  const {hotove, rozpracovane} = pripravText(text, streamuje);
  let konec = "";
  if (rozpracovane) {
    konec = /^\s*\|/m.test(rozpracovane) ? `<p class="cz-chat-rozpracovane">${esc(rozpracovane)}</p>`
      : vykresliMarkdown(rozpracovane, {uzke});
  }
  return {hotove, hotoveHtml: nahradCitace(hotove ? vykresliMarkdown(hotove, {uzke}) : "", zname),
    konecHtml: nahradCitace(konec, zname)};
}

export function htmlOdpovedi(text, volby = {}) {
  const c = castiOdpovedi(text, volby);
  return c.hotoveHtml + c.konecHtml;
}

// ---------------------------------------------------------------- zdroje, ověření a náhled (data pro DOM)

const ZNACKY = {neautorizovano: "neautorizováno", odvozeny_ai: "rozbor AI"};

export function popisZnacky(z) {
  return ZNACKY[z] || "";
}

// datum „2026-11-12…“ → „12. 11. 2026“; jiný tvar beze změny
export function datum(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s || ""));
  return m ? `${Number(m[3])}. ${Number(m[2])}. ${m[1]}` : String(s || "");
}

// Zdroje zprávy z obnovy konverzace (GET /konverzace/{id}): `citovane` čísla → položky z `zdroje`
export function zdrojeZpravy(citovane, zdroje) {
  if (!Array.isArray(citovane) || !Array.isArray(zdroje)) return [];
  return citovane.map((n) => zdroje.find((z) => z && z.n === n)).filter(Boolean);
}

// Čipy ověření citátů z události `overeni` (spec 9.2): projev se shodou → „zaznělo v projevu …“,
// přibližná shoda → poznámka. Neověřený citát čip nemá, značku „(neověřeno ve zdroji)“ vkládá do textu server.
export function cipyOvereni(citaty) {
  const out = [], videno = new Set();
  for (const c of Array.isArray(citaty) ? citaty : []) {
    if (!c || (c.shoda !== "presne" && c.shoda !== "priblizne")) continue;
    const zv = Number.isInteger(c.od) && Number.isInteger(c.do) && c.od < c.do ? [c.od, c.do] : null;
    const poznamka = c.shoda === "priblizne" ? (c.poznamka || "znění upraveno, viz zdroj") : (c.poznamka || "");
    const z = c.zaznelo_v;
    let cip;
    if (z && typeof z.recnik === "string" && z.recnik) {
      const kdy = z.datum ? ` (${datum(z.datum)})` : "";
      cip = {text: `zaznělo v projevu ${z.recnik}${kdy}`, poznamka, klic: typeof z.klic === "string" ? z.klic : null, zv};
    } else if (poznamka) {
      cip = {text: Number.isInteger(c.n) ? `[${c.n}]` : "citace", poznamka, klic: null, zv: null};
    } else continue;
    const k = `${cip.text}|${cip.poznamka}`;
    if (videno.has(k)) continue;
    videno.add(k);
    out.push(cip);
  }
  return out;
}

// Zvýraznění citace v textu náhledu: k zdroji N první ověřený citát s offsety
export function zvyrazneniZdroje(citaty, n) {
  const c = (Array.isArray(citaty) ? citaty : []).find((x) => x && x.n === n && Number.isInteger(x.od)
    && Number.isInteger(x.do) && x.od < x.do && (x.shoda === "presne" || x.shoda === "priblizne"));
  return c ? [c.od, c.do] : null;
}

// Text náhledu rozdělený na úseky; `zv` = [od, do] se vykreslí jako <mark> (vše přes textContent)
export function useky(text, zv) {
  const t = String(text || "");
  if (!Array.isArray(zv) || zv.length !== 2) return [{text: t, zv: false}];
  const [od, do_] = zv;
  if (!Number.isInteger(od) || !Number.isInteger(do_) || od < 0 || do_ > t.length || od >= do_) return [{text: t, zv: false}];
  return [{text: t.slice(0, od), zv: false}, {text: t.slice(od, do_), zv: true}, {text: t.slice(do_), zv: false}]
    .filter((u) => u.text);
}

// Tlačítka náhledu jen z odkazů, které server poslal: web od kořene webu, psp.cz a Word přes whitelist
export function odkazyNahledu(odkazy) {
  const o = odkazy && typeof odkazy === "object" ? odkazy : {};
  const out = [];
  if (typeof o.web === "string" && /^\/(?!\/)[^\s\\]*$/.test(o.web)) out.push({text: "Otevřít na cosepece", href: o.web, nove: false});
  if (povolenyOdkaz(o.psp)) out.push({text: "Originál na psp.cz", href: o.psp, nove: true});
  if (typeof o.docx === "string" && DOCX.test(o.docx)) out.push({text: "Word na psp.cz", href: o.docx, nove: true});
  return out;
}
