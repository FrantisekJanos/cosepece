import test from "node:test";
import assert from "node:assert/strict";
import { hlaska, zbyvaText } from "../../hlidac_rag/chat/static/hlasky.mjs";

test("hlášky limitů", () => {
  assert.match(hlaska("limit_den", {}).text, /Dnešní kapacita asistenta je vyčerpaná/);
  assert.ok(hlaska("limit_den", {ct: 221}).odkazy.some(o => o.href === "/mcp"));
  assert.ok(hlaska("limit_den", {ct: 221}).odkazy.some(o => o.href.includes("historie.sqw?o=10&t=221")));
  assert.equal(hlaska("limit_den", {}).blokuje, true);
  assert.match(hlaska("limit_hodina", {znovu_v: "2026-11-28T10:20:00+01:00"}).text, /za \d+ minut/);
  assert.equal(hlaska("obsazeno", {}).opakovat, true); assert.equal(hlaska("turnstile", {}).blokuje, false);
  assert.match(hlaska("limit_ip", {}).text, /Z vaší sítě/); assert.match(hlaska("neznamy_kod", {}).text, /Něco se nepovedlo/);
});

test("texty doslova ze spec 11.3", () => {
  assert.equal(hlaska("limit_den", {}).text,
    "Dnešní kapacita asistenta je vyčerpaná. Běží na malém denním rozpočtu, znovu odpoví zítra.");
  assert.equal(hlaska("limit_ip", {}).text,
    "Z vaší sítě dnes přišlo hodně dotazů (může jít o školu nebo firmu se společnou adresou). Zkuste to zítra.");
  assert.equal(hlaska("limit_konverzace", {}).text, "Tahle konverzace je dlouhá. Začněte novou, kontext stránky převezmu.");
  assert.equal(hlaska("obsazeno", {}).text, "Asistent je vytížený, zkouším to znovu…");
  assert.equal(hlaska("overeni_nedostupne", {}).text, "Ověření, že nejste robot, teď nefunguje. Zkuste to za chvíli.");
  assert.equal(hlaska("vypnuto", {}).text, "Asistent je dočasně vypnutý.");
  assert.equal(hlaska("limit_relace", {otazek: 6}).text,
    "Dnes jste položili 6 otázek, víc jich asistent na jednoho návštěvníka denně nezvládne. Zítra pokračujte.");
});

test("limit_hodina: minuty od teď, skloňování, nejméně 1", () => {
  const ted = Date.parse("2026-11-28T10:00:00+01:00");
  assert.equal(hlaska("limit_hodina", {znovu_v: "2026-11-28T10:20:00+01:00"}, ted).text,
    "Asistent má teď hodně dotazů. Zkuste to znovu za 20 minut.");
  assert.match(hlaska("limit_hodina", {znovu_v: "2026-11-28T10:01:00+01:00"}, ted).text, /za 1 minutu\./);
  assert.match(hlaska("limit_hodina", {znovu_v: "2026-11-28T10:03:00+01:00"}, ted).text, /za 3 minuty\./);
  assert.match(hlaska("limit_hodina", {znovu_v: "2026-11-28T09:00:00+01:00"}, ted).text, /za 1 minutu\./);
  assert.match(hlaska("limit_hodina", {}, ted).text, /za chvíli/);
  assert.equal(hlaska("limit_hodina", {}).blokuje, true);
});

test("odkazy jen u limit_den a limit_ip, tisk na psp.cz jen se stránkou tisku", () => {
  assert.deepEqual(hlaska("limit_den", {}).odkazy.map(o => o.href), ["/hledat", "/mcp"]);
  assert.equal(hlaska("limit_ip", {ct: 5}).odkazy.length, 3);
  assert.deepEqual(hlaska("limit_relace", {}).odkazy, []);
  assert.ok(!hlaska("limit_den", {ct: "5\"><script>"}).odkazy.some(o => o.href.includes("script")));
});

test("akce a příznaky", () => {
  assert.equal(hlaska("limit_konverzace", {}).akce, "novy_chat");
  assert.equal(hlaska("limit_relace", {}).blokuje, true);
  assert.equal(hlaska("vypnuto", {}).blokuje, true);
  assert.equal(hlaska("overeni_nedostupne", {}).blokuje, false);
  assert.equal(hlaska("odmitnuto", {}).blokuje, false);
  assert.equal(hlaska("chyba", {}).opakovat, false);
  for (const k of ["limit_den", "obsazeno", "nic"]) {
    const h = hlaska(k, {});
    assert.equal(typeof h.text, "string"); assert.ok(Array.isArray(h.odkazy));
    assert.equal(typeof h.blokuje, "boolean"); assert.equal(typeof h.opakovat, "boolean");
  }
});

test("zbývající otázky", () => {
  assert.equal(zbyvaText(2), "Dnes zbývají 2 otázky");
  assert.equal(zbyvaText(1), "Dnes zbývá 1 otázka");
  assert.equal(zbyvaText(0), "Dnes už nezbývá žádná otázka");
  assert.equal(zbyvaText(3), "");
  assert.equal(zbyvaText(null), "");
});
