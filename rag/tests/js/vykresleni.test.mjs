import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import {
  pripravText, povolenyOdkaz, nasWeb, nahradCitace, vykresliMarkdown, htmlOdpovedi, cipyOvereni, zvyrazneniZdroje,
  useky, odkazyNahledu, zdrojeZpravy, datum,
} from "../../hlidac_rag/chat/static/vykresleni.mjs";

test("tabulka se během streamu drží jako text", () => {
  const r = pripravText("Výsledek:\n\n| Klub | Pro |\n|---|---|\n| ANO | 70", true);
  assert.equal(r.hotove.trim(), "Výsledek:"); assert.match(r.rozpracovane, /\| ANO \| 70/);
  assert.equal(pripravText("| A |\n|---|\n| 1 |\n\nDál", true).rozpracovane, "Dál");
  assert.deepEqual(pripravText("a\n\nb", false), {hotove: "a\n\nb", rozpracovane: ""});
});

test("odkazy", () => {
  for (const u of ["https://www.psp.cz/sqw/x", "https://cosepece.cz/tisk/1", "https://stat.vlada.cz/"]) assert.ok(povolenyOdkaz(u));
  for (const u of ["http://psp.cz/", "https://psp.cz.evil.com/", "javascript:alert(1)", "https://evilpsp.cz/", "//psp.cz/x"]) assert.ok(!povolenyOdkaz(u));
  for (const u of ["data:text/html,<script>alert(1)</script>", "https://psp.cz@evil.com/", "https://user@psp.cz/",
    "https:psp.cz", "/tisk/1", "", null, "JAVASCRIPT:alert(1)", "https://psp.cz\\@evil.com"]) assert.ok(!povolenyOdkaz(u), u);
  assert.ok(nasWeb("https://cosepece.cz/tisk/1") && nasWeb("https://www.cosepece.cz/") && !nasWeb("https://psp.cz/"));
});

test("citace jen známé", () => {
  const h = nahradCitace("<p>Ano [1] a [7].</p>", new Set([1]));
  assert.match(h, /data-n="1"/); assert.ok(!h.includes('data-n="7"')); assert.ok(h.includes("[7]"));
  assert.equal(nahradCitace('<a href="https://psp.cz/" title="[1]">x</a>', new Set([1])),
    '<a href="https://psp.cz/" title="[1]">x</a>');
  assert.ok(!nahradCitace("<p><code>[1]</code></p>", new Set([1])).includes("button"));
});

test("markdown: tabulka, seznam, odkaz z whitelistu", () => {
  const h = vykresliMarkdown("| Klub | Pro |\n|---|---|\n| ANO | 70 |\n\n- a\n- b\n\n[tisk](https://www.psp.cz/sqw/x) a [web](https://cosepece.cz/tisk/1)");
  assert.match(h, /<table>/); assert.match(h, /<td>ANO<\/td>/); assert.match(h, /<ul>\s*<li>a<\/li>/);
  assert.match(h, /<a href="https:\/\/www\.psp\.cz\/sqw\/x" target="_blank" rel="noopener noreferrer">tisk<\/a>/);
  assert.match(h, /<a href="https:\/\/cosepece\.cz\/tisk\/1">web<\/a>/);
});

test("markdown: tabulka na úzké obrazovce jako výčet", () => {
  const h = vykresliMarkdown("Před\n\n| Klub | Pro | Proti |\n|---|---|---|\n| ANO | 70 | 0 |\n| ODS | 1 | 20 |\n\nPo", {uzke: true});
  assert.ok(!h.includes("<table"));
  assert.match(h, /<li><strong>ANO<\/strong>: Pro 70 · Proti 0<\/li><li><strong>ODS<\/strong>: Pro 1 · Proti 20<\/li>/);
  assert.match(h, /<p>Před<\/p>/); assert.match(h, /<p>Po<\/p>/);
});

test("XSS: HTML, javascript:, data: a obrázky v textu modelu", () => {
  const vstupy = [
    "<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "[klik](javascript:alert(1))",
    "[klik](JaVaScRiPt:alert(1))", "[klik](data:text/html;base64,PHNjcmlwdD4=)", "![obr](https://psp.cz/x.png)",
    "![<img src=x onerror=alert(1)>](https://psp.cz/x.png)", "[a](https://evil.com/) a <https://evil.com/>",
    "[a](https://psp.cz/\" onclick=\"alert(1))", "| <b>x</b> |\n|---|\n| <i onclick=alert(1)>y</i> |",
    "[1]: https://evil.com/\n\nViz [1].", "<a href=\"https://psp.cz\" onclick=\"alert(1)\">x</a>",
  ];
  for (const v of vstupy) {
    for (const uzke of [false, true]) {
      for (const streamuje of [false, true]) {
        const h = htmlOdpovedi(v, {zname: new Set([1]), uzke, streamuje});
        assert.ok(!/<(script|img|iframe|svg|i|b)[\s>]/i.test(h), `${v} → ${h}`);
        assert.ok(!/<[^>]+\son\w+=/i.test(h), `${v} → ${h}`);
        assert.ok(!/href="(?!https:\/\/(www\.)?(psp|cosepece)\.cz)/i.test(h), `${v} → ${h}`);
        assert.ok(!/evil\.com\//.test((h.match(/href="[^"]*"/g) || []).join(" ")), `${v} → ${h}`);
      }
    }
  }
  assert.equal(htmlOdpovedi("![popis](https://psp.cz/x.png)").trim(), "<p>popis</p>");
  assert.equal(htmlOdpovedi("[a](https://evil.com/) b").trim(), "<p>a b</p>");
  // definice odkazu je vypnutá: [1] zůstane citací, ne odkazem
  assert.match(htmlOdpovedi("[1]: https://psp.cz/\n\nViz [1].", {zname: new Set([1])}), /data-n="1"/);
});

test("rozpracovaná tabulka jde ven jako escapovaný text", () => {
  const h = htmlOdpovedi("Výsledek:\n\n| Klub | <b>Pro</b> |\n|---|", {streamuje: true});
  assert.match(h, /<p>Výsledek:<\/p>/);
  assert.match(h, /<p class="cz-chat-rozpracovane">\| Klub \| &lt;b&gt;Pro&lt;\/b&gt; \|\n\|---\|<\/p>/);
  assert.match(htmlOdpovedi("A\n\n**tučně**", {streamuje: true}), /<strong>tučně<\/strong>/);
});

test("čipy ověření citátu", () => {
  const zv = {recnik: "Jan Novák (ODS)", klic: "projev:10/5/123/4", datum: "2026-11-12", url: "https://www.psp.cz/eknih/x"};
  const c = cipyOvereni([
    {n: 2, shoda: "presne", od: 5, do: 20, zaznelo_v: zv, poznamka: null},
    {n: 2, shoda: "presne", od: 5, do: 20, zaznelo_v: zv, poznamka: null},     // stejný čip jen jednou
    {n: 3, shoda: "priblizne", od: 1, do: 9, zaznelo_v: zv, poznamka: "řečník zde cituje"},
    {n: 4, shoda: "priblizne", od: 1, do: 9, zaznelo_v: null, poznamka: null},
    {n: 5, shoda: "nenalezeno", od: null, do: null, zaznelo_v: null, poznamka: null},
    {n: 6, shoda: "neovereno", od: null, do: null, zaznelo_v: null, poznamka: null},
  ]);
  assert.deepEqual(c, [
    {text: "zaznělo v projevu Jan Novák (ODS) (12. 11. 2026)", poznamka: "", klic: "projev:10/5/123/4", zv: [5, 20]},
    {text: "zaznělo v projevu Jan Novák (ODS) (12. 11. 2026)", poznamka: "řečník zde cituje", klic: "projev:10/5/123/4", zv: [1, 9]},
    {text: "[4]", poznamka: "znění upraveno, viz zdroj", klic: null, zv: null},
  ]);
  assert.deepEqual(cipyOvereni(null), []);
  assert.deepEqual(zvyrazneniZdroje([{n: 1, shoda: "nenalezeno"}, {n: 1, shoda: "presne", od: 3, do: 8}], 1), [3, 8]);
  assert.equal(zvyrazneniZdroje([{n: 1, shoda: "presne", od: 3, do: 8}], 2), null);
});

test("náhled: zvýraznění a odkazy", () => {
  assert.deepEqual(useky("abcdef", [1, 3]), [{text: "a", zv: false}, {text: "bc", zv: true}, {text: "def", zv: false}]);
  assert.deepEqual(useky("<img src=x onerror=alert(1)>", null), [{text: "<img src=x onerror=alert(1)>", zv: false}]);
  for (const zv of [[3, 1], [0, 99], [-1, 2], ["0", "2"], [1]]) assert.deepEqual(useky("abc", zv), [{text: "abc", zv: false}]);
  assert.deepEqual(odkazyNahledu({web: "/tisk/221#pn-5", psp: "https://www.psp.cz/sqw/sd.sqw?cd=5&o=10",
    docx: "https://www.psp.cz/sqw/text/orig2.sqw?idd=1"}).map((o) => [o.text, o.nove]),
  [["Otevřít na cosepece", false], ["Originál na psp.cz", true], ["Word na psp.cz", true]]);
  assert.deepEqual(odkazyNahledu({web: "//evil.com/", psp: "javascript:alert(1)", docx: "https://evil.com/"}), []);
  assert.deepEqual(odkazyNahledu(null), []);
});

test("zdroje zprávy z obnovy a datum", () => {
  const zdroje = [{n: 1, titulek: "Tisk 1"}, {n: 3, titulek: "PN 3"}];
  assert.deepEqual(zdrojeZpravy([3, 9], zdroje), [{n: 3, titulek: "PN 3"}]);
  assert.deepEqual(zdrojeZpravy(undefined, zdroje), []);
  assert.equal(datum("2026-01-05T10:00:00"), "5. 1. 2026");
  assert.equal(datum("12. 11. 2026"), "12. 11. 2026");
});

test("vendorovaný markdown-it: verze, licence a otisk z README", () => {
  const cesta = (p) => fileURLToPath(new URL(`../../hlidac_rag/chat/static/vendor/${p}`, import.meta.url));
  const js = readFileSync(cesta("markdown-it.min.js"));
  assert.match(js.subarray(0, 120).toString(), /^\/\*! markdown-it 14\.\d+\.\d+ .* @license MIT \*\//);
  const readme = readFileSync(cesta("README"), "utf8");
  assert.ok(readme.includes(createHash("sha256").update(js).digest("hex")));
  assert.match(readFileSync(cesta("LICENSE-markdown-it"), "utf8"), /Permission is hereby granted/);
});
