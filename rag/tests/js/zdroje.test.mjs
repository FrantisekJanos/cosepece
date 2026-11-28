// zdroje.mjs nad minimálním falešným DOM: data ze serveru (náhled, titulky zdrojů, poznámky) jdou do stránky
// jen jako textové uzly, innerHTML dostane jen výstup markdown-it.
import test from "node:test";
import assert from "node:assert/strict";

class Uzel {
  constructor(tag, text = "") {
    this.tagName = tag ? tag.toUpperCase() : "#text";
    this.deti = [];
    this.atributy = {};
    this.dataset = {};
    this.posluchaci = {};
    this.parentNode = null;
    this.vlastniText = text;
    this.innerHTMLZapsano = null;
    this.classList = {toggle() {}, add() {}, remove() {}};
  }
  set className(v) { this.atributy.class = v; }
  get className() { return this.atributy.class || ""; }
  setAttribute(k, v) {
    this.atributy[k] = String(v);
    if (k.startsWith("data-")) this.dataset[k.slice(5)] = String(v);
  }
  getAttribute(k) { return this.atributy[k] ?? null; }
  addEventListener(typ, f) { (this.posluchaci[typ] ||= []).push(f); }
  append(...uzly) {
    for (const u of uzly) {
      const x = typeof u === "string" ? new Uzel(null, u) : u;
      x.parentNode = this;
      this.deti.push(x);
    }
  }
  replaceChildren(...uzly) { this.deti = []; this.append(...uzly); }
  remove() {
    if (this.parentNode) this.parentNode.deti = this.parentNode.deti.filter((d) => d !== this);
    this.parentNode = null;
  }
  set textContent(v) { this.deti = [new Uzel(null, String(v))]; }
  get textContent() { return this.tagName === "#text" ? this.vlastniText : this.deti.map((d) => d.textContent).join(""); }
  set innerHTML(v) { this.innerHTMLZapsano = String(v); this.deti = []; }
  get isConnected() { return true; }
  focus() { globalThis.document.activeElement = this; }
  scrollIntoView() {}
  vse() { return this.deti.flatMap((d) => [d, ...d.vse()]); }
  querySelector(sel) {
    const tag = /^[a-z]+$/.test(sel) ? sel.toUpperCase() : null;
    return this.vse().find((d) => tag && d.tagName === tag) || null;
  }
  querySelectorAll() { return []; }
}

globalThis.document = {
  createElement: (tag) => new Uzel(tag),
  createTextNode: (t) => new Uzel(null, t),
  body: new Uzel("body"),
  activeElement: null,
};
globalThis.window = globalThis;

const { otevriNahled, zavriNahled, doplnky, vyplnText, nastav } = await import("../../hlidac_rag/chat/static/zdroje.mjs");

const ZLE = "<img src=x onerror=alert(1)>";

function vsechnyUzly() {
  return document.body.vse();
}

test("náhled: text, titulek a cesta ze serveru jako text, zvýraznění <mark>, jen povolené odkazy", async () => {
  const dotazy = [];
  globalThis.fetch = async (url) => {
    dotazy.push(url);
    return {ok: true, status: 200, json: async () => ({
      typ: "projev", titulek: `<b>${ZLE}</b>`, cesta: [ZLE], text: `ab${ZLE}cd`, zv: [2, 2 + ZLE.length],
      znacky: ["neautorizovano"], predchozi: null, dalsi: null, tabulka: true, kluby: null, verze: null,
      zkraceno: false, odkazy: {web: "/tisk/1", psp: "javascript:alert(1)", docx: "https://evil.com/x"},
      zdroj: "Poslanecká sněmovna PČR, psp.cz",
    })};
  };
  await otevriNahled("projev:10/5/1/2", [2, 30]);
  assert.equal(dotazy[0], "/chat-api/nahled?k=projev%3A10%2F5%2F1%2F2&zv=2-30");
  const uzly = vsechnyUzly();
  assert.ok(uzly.every((u) => u.innerHTMLZapsano === null), "náhled nesmí používat innerHTML");
  assert.ok(!uzly.some((u) => ["IMG", "B", "SCRIPT"].includes(u.tagName)));
  const mark = uzly.find((u) => u.tagName === "MARK");
  assert.equal(mark.textContent, ZLE);
  const text = document.body.textContent;
  assert.ok(text.includes(`<b>${ZLE}</b>`) && text.includes("Tabulka, viz originál.") && text.includes("neautorizováno"));
  assert.ok(text.includes("Zdroj: Poslanecká sněmovna PČR, psp.cz"));
  const odkazy = uzly.filter((u) => u.tagName === "A").map((a) => [a.textContent, a.getAttribute("href")]);
  assert.deepEqual(odkazy, [["Otevřít na cosepece", "/tisk/1"]]);
  zavriNahled();
  assert.equal(document.body.deti.length, 0);
});

test("náhled: chyba serveru je hláška, ne text ze serveru", async () => {
  globalThis.fetch = async () => ({ok: false, status: 404, json: async () => ({kod: "nenalezeno"})});
  await otevriNahled("tisk:1", null);
  assert.ok(document.body.textContent.includes("Náhled tohoto zdroje není k dispozici."));
  zavriNahled();
});

test("odpověď: titulky zdrojů a poznámky ověření jako text, bez zdroje, patička s kódem", () => {
  nastav({api: "/chat-api"});
  const z = {role: "bot", stav: "ok", kod: "abcd1234", text: `Ano [1] a [2]. ${ZLE}`,
    zdroje: [{n: 1, typ: "tisk", titulek: ZLE, znacky: ["odvozeny_ai"], klic: "tisk:1"},
      {n: 2, typ: "upozorneni", titulek: "Upozornění", znacky: [], klic: null}],
    citaty: [{n: 1, shoda: "priblizne", od: 1, do: 4, poznamka: ZLE,
      zaznelo_v: {recnik: ZLE, klic: "projev:1/2/3/4", datum: "2026-11-12", url: null}}]};
  const obal = new Uzel("div");
  obal.append(...doplnky(z));
  const uzly = obal.vse();
  assert.ok(uzly.every((u) => u.innerHTMLZapsano === null));
  assert.ok(!uzly.some((u) => u.tagName === "IMG"));
  const text = obal.textContent;
  assert.ok(text.includes(`Zdroje (2)`) && text.includes(`[1] ${ZLE}`) && text.includes("rozbor AI"));
  assert.ok(text.includes(`zaznělo v projevu ${ZLE} (12. 11. 2026)`) && text.includes("Kód abcd1234"));
  const bezNahledu = uzly.find((u) => u.dataset.zdroj === "2");
  assert.equal(bezNahledu.tagName, "SPAN");       // klic null: jen název, bez náhledu
  const kod = uzly.find((u) => u.tagName === "A");
  assert.equal(kod.getAttribute("href"), "/chat-api/overit?kod=abcd1234");

  const bez = new Uzel("div");
  bez.append(...doplnky({stav: "ok", kod: "abcd1234", text: "Obecně: …", zdroje: []}));
  assert.ok(bez.textContent.includes("bez zdroje"));
  assert.deepEqual(doplnky({stav: "probiha", text: "x"}), []);
  assert.deepEqual(doplnky({stav: "odmitnuto", text: ""}), []);
});

test("text odpovědi: innerHTML jen z markdown-it, [N] jen u známých zdrojů", () => {
  const el = new Uzel("div");
  vyplnText(el, {text: `**A** [1] [3] ${ZLE}`, zdroje: [{n: 1, klic: "tisk:1"}]});
  assert.match(el.innerHTMLZapsano, /<strong>A<\/strong> <button[^>]*data-n="1"[^>]*>\[1\]<\/button> \[3\] &lt;img/);
});
