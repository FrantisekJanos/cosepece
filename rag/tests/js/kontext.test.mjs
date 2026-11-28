import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { zCesty, zKotvy, zaostreni, maBublinu, zmer, kontext, vyskaOkna, BEZ_BUBLINY } from "../../hlidac_rag/chat/static/kontext.mjs";

test("cesty", () => {
  assert.deepEqual(zCesty("/tisk/221"), {typ: "tisk", ct: 221});
  assert.deepEqual(zCesty("/tisk/221/"), {typ: "tisk", ct: 221});
  assert.deepEqual(zCesty("/zakon/159/2006"), {typ: "zakon", z: "159/2006"});
  for (const [p, t] of [["/", "prehled"], ["/tisky", "seznam_tisku"], ["/zakony", "zakony"], ["/hledat", "hledani"],
       ["/upozorneni", "upozorneni"], ["/pripravovane", "pripravovane"], ["/metodika", "staticka"], ["/zmeny", "staticka"],
       ["/data-a-api", "staticka"], ["/neco/jineho", "jina"]]) assert.equal(zCesty(p).typ, t);
  assert.deepEqual(zCesty("/tisk.php", "https://cosepece.cz/tisk/300"), {typ: "tisk", ct: 300});
});

test("cesty: nesmysly a přetečení jdou do jina", () => {
  assert.equal(zCesty("/tisk/abc").typ, "jina");
  assert.equal(zCesty("/tisk/99999999999").typ, "jina");
  assert.equal(zCesty("/tisk/0").typ, "jina");
  assert.equal(zCesty("/zakon/159/20066").typ, "jina");
  assert.equal(zCesty("/tisk.php", "neplatna adresa").typ, "jina");
});

test("bublina není na revizích a mcp", () => {
  assert.equal(maBublinu("/revize"), false);
  assert.equal(maBublinu("/revize/"), false);
  assert.equal(maBublinu("/mcp"), false);
  assert.equal(maBublinu("/tisk/1"), true);
});

test("F38: w.js má stejný výčet cest bez bubliny jako kontext.mjs", () => {
  const w = readFileSync(fileURLToPath(new URL("../../hlidac_rag/chat/static/w.js", import.meta.url)), "utf8");
  const m = w.match(/BEZ_BUBLINY\s*=\s*\[([^\]]*)\]/);
  assert.ok(m, "w.js nemá pole BEZ_BUBLINY");
  assert.deepEqual([...m[1].matchAll(/["'](\/[^"']*)["']/g)].map((x) => x[1]).sort(), [...BEZ_BUBLINY].sort());
});

test("kotva", () => {
  assert.deepEqual(zKotvy("#pn-2329"), {pn: 2329});
  assert.deepEqual(zKotvy("#prep-ABC_1"), {pid: "ABC_1"});
  assert.deepEqual(zKotvy("#pn-x"), {});
  assert.deepEqual(zKotvy("#senat"), {});
  assert.deepEqual(zKotvy("#pn-99999999999"), {});
  assert.deepEqual(zKotvy("#prep-" + "a".repeat(41)), {});
});

test("jedna položka přes půl okna je zaostřená", () => {
  assert.deepEqual(zaostreni([{druh: "pn", id: 5, top: 100, bottom: 700}, {druh: "pn", id: 6, top: 700, bottom: 900}], 800).zaostreno, {druh: "pn", id: 5});
});

test("víc malých položek jde do nabídky, nejvýš 5", () => {
  const p = Array.from({length: 8}, (_, i) => ({druh: "pn", id: i, top: i * 90, bottom: i * 90 + 80}));
  const r = zaostreni(p, 800); assert.equal(r.zaostreno, null); assert.equal(r.nabidka.length, 5);
  assert.deepEqual(r.nabidka.map((x) => x.id), [0, 1, 2, 3, 4]);
});

test("položka s nulovým obdélníkem (zavřené details) se nepočítá", () => {
  assert.deepEqual(zaostreni([{druh: "pn", id: 1, top: 0, bottom: 0}], 800), {zaostreno: null, nabidka: []});
});

test("položka mimo okno se nepočítá", () => { assert.equal(zaostreni([{druh: "hlasovani", id: 9, top: 900, bottom: 1500}], 800).nabidka.length, 0); });

test("viditelná část se ořízne oknem", () => {
  // 1000 px vysoká položka, v okně 800 px je jí vidět 450 (>= 400)
  assert.deepEqual(zaostreni([{druh: "pn", id: 3, top: -550, bottom: 450}], 800).zaostreno, {druh: "pn", id: 3});
});

// minimální DOM
function el(over) { return {getBoundingClientRect: () => over.rect, id: over.id, querySelector: () => over.a ? {getAttribute: () => over.a} : null}; }
function dok({pn = [], vote = [], canonical = null} = {}) {
  return {
    querySelectorAll: (s) => (s.startsWith("li.pn-item") ? pn : s === "li.vote" ? vote : []),
    querySelector: (s) => (s.includes("canonical") && canonical ? {href: canonical} : null),
  };
}
const okno = (pathname, hash = "", w = 1200, h = 800) => ({location: {pathname, hash}, innerWidth: w, innerHeight: h});

test("zmer čte PN a hlasování z DOM", () => {
  const d = dok({
    pn: [el({id: "pn-12", rect: {top: 10, bottom: 50}}), el({id: "pn-x", rect: {top: 0, bottom: 5}})],
    vote: [el({a: "https://www.psp.cz/sqw/hlasy.sqw?g=777", rect: {top: 60, bottom: 90}}), el({a: null, rect: {top: 0, bottom: 1}})],
  });
  assert.deepEqual(zmer(d, okno("/tisk/1")), [{druh: "pn", id: 12, top: 10, bottom: 50}, {druh: "hlasovani", id: 777, top: 60, bottom: 90}]);
});

test("kontext: tisk se zaostřeným PN a úzkým oknem", () => {
  const d = dok({pn: [el({id: "pn-2329", rect: {top: 0, bottom: 700}})]});
  assert.deepEqual(kontext(d, okno("/tisk/221", "", 500)), {typ: "tisk", ct: 221, pn: 2329, uzke: true});
});

test("kontext: kotva #pn má přednost před měřením", () => {
  const d = dok({pn: [el({id: "pn-5", rect: {top: 0, bottom: 700}})]});
  assert.equal(kontext(d, okno("/tisk/221", "#pn-9")).pn, 9);
});

test("kontext: bez položek jen tisk, zakon a pripravovane", () => {
  assert.deepEqual(kontext(dok(), okno("/tisk/221")), {typ: "tisk", ct: 221, uzke: false});
  assert.deepEqual(kontext(dok(), okno("/zakon/159/2006")), {typ: "zakon", z: "159/2006", uzke: false});
  assert.deepEqual(kontext(dok(), okno("/pripravovane", "#prep-AB_1")), {typ: "pripravovane", pid: "AB_1", uzke: false});
});

test("kontext: žádný volný text z URL ani DOM (jen čísla a povolený typ)", () => {
  const d = dok({pn: [el({id: "pn-3", rect: {top: 0, bottom: 700}})]});
  const w = okno("/hledat", "#pn-3");
  w.location.search = "?q=tajny+text";
  assert.deepEqual(kontext(d, w), {typ: "hledani", uzke: false});
  const k = kontext(dok({canonical: "https://cosepece.cz/tisk/5?q=nejaky%20text"}), okno("/tisk.php", "#prep-x y"));
  assert.deepEqual(k, {typ: "tisk", ct: 5, uzke: false});
  for (const v of Object.values(kontext(d, okno("/tisk/221")))) assert.ok(typeof v === "number" || typeof v === "boolean" || ["tisk"].includes(v));
});

test("mobil: zaostření podle visualViewport (otevřená klávesnice), ne podle innerHeight", () => {
  // 375 × 667, klávesnice nechá vidět 400 px; PN zabírá 250 px viditelné části (>= 200, ale < 333)
  const d = dok({pn: [el({id: "pn-41", rect: {top: 100, bottom: 350}})]});
  const w = okno("/tisk/221", "", 375, 667);
  assert.equal(kontext(d, w).pn, undefined);
  w.visualViewport = {height: 400};
  assert.equal(vyskaOkna(w), 400);
  assert.deepEqual(kontext(d, w), {typ: "tisk", ct: 221, pn: 41, uzke: true});
});

test("bez visualViewport platí innerHeight", () => {
  assert.equal(vyskaOkna({innerHeight: 667}), 667);
});
