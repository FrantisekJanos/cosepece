import test from "node:test";
import assert from "node:assert/strict";
import { obnovZeZrcadla, ctiProud, sledujTah, NECINNOST_MS } from "../../hlidac_rag/chat/static/proud.mjs";

const TED = 1_800_000_000_000;
const ID = "0123456789abcdef0123456789abcdef";

test("zrcadlo: čerstvá konverzace se obnoví beze změny", () => {
  const zpravy = [{role: "uzivatel", text: "A?"}, {role: "bot", text: "B", stav: "ok", kod: "x"}];
  const o = obnovZeZrcadla({id: ID, cas: TED - 1000, stranka: "tisk 1"}, zpravy, null, TED);
  assert.deepEqual(o.zpravy, zpravy);
  assert.equal(o.konv.id, ID);
  assert.equal(o.tah, null);
  assert.equal(o.nabidnoutPredchozi, null);
});

test("zrcadlo: po 2 h nečinnosti prázdný panel a nabídka předchozí konverzace", () => {
  const o = obnovZeZrcadla({id: ID, cas: TED - NECINNOST_MS - 1, otevreno: true}, [{role: "uzivatel", text: "A?"}],
    {id: ID, cas: TED - NECINNOST_MS - 1}, TED);
  assert.deepEqual(o.zpravy, []);
  assert.deepEqual(o.konv, {otevreno: true});
  assert.equal(o.nabidnoutPredchozi.id, ID);
});

test("zrcadlo: nová karta (nic v sessionStorage) nabídne poslední konverzaci z localStorage", () => {
  const o = obnovZeZrcadla(null, null, {id: ID, cas: TED}, TED);
  assert.deepEqual(o.zpravy, []);
  assert.equal(o.nabidnoutPredchozi.id, ID);
  assert.equal(obnovZeZrcadla(null, null, {id: 5}, TED).nabidnoutPredchozi, null);
});

test("zrcadlo: běžící tah se obnoví i po nečinnosti a poslední odpověď je rozepsaná", () => {
  const o = obnovZeZrcadla({id: ID, cas: 0, tah: {id: "t1", od: 7}},
    [{role: "uzivatel", text: "A?"}, {role: "bot", text: "Roz", stav: "probiha"}], null, TED);
  assert.deepEqual(o.tah, {id: "t1", od: 7});
  assert.deepEqual(o.zpravy.at(-1), {role: "bot", text: "Roz", stav: "probiha"});
});

test("zrcadlo: tah bez zprávy bota (oddělovač na konci) dostane novou rozepsanou odpověď", () => {
  const o = obnovZeZrcadla({id: ID, cas: TED, tah: {id: "t1", od: 0}},
    [{role: "uzivatel", text: "A?"}, {role: "oddelovac", text: "— teď na: tisk 2 —"}], null, TED);
  assert.equal(o.zpravy.length, 3);
  assert.deepEqual(o.zpravy.at(-1), {role: "bot", text: "", stav: "probiha"});
});

test("zrcadlo: poškozená data se zahodí", () => {
  const o = obnovZeZrcadla({id: ID, cas: TED, tah: {id: 3, od: "x"}}, [null, 5, {role: "hacker", text: "x"},
    {role: "bot", text: {}}, {role: "uzivatel", text: "ok"}], "nesmysl", TED);
  assert.equal(o.tah, null);
  assert.deepEqual(o.zpravy, [{role: "uzivatel", text: "ok"}]);
  assert.deepEqual(obnovZeZrcadla([1, 2], "x", null, TED).zpravy, []);
});

function odpoved(...kusy) {
  const kod = new TextEncoder();
  let i = 0;
  return {
    status: 200, ok: true,
    body: {getReader: () => ({
      read: async () => (i < kusy.length ? {value: kod.encode(kusy[i++]), done: false} : {done: true}),
      cancel: async () => {},
    })},
  };
}

test("ctiProud: offset z id a konec čtení po konec", async () => {
  const b = {od: 0, hotovo: false};
  const typy = [];
  const n = await ctiProud(odpoved('id: 0\nevent: start\ndata: {"tah_id":"t"}\n\nid: 1\nevent: del',
    'ta\ndata: {"text":"A"}\n\nid: 2\nevent: konec\ndata: {}\n\nid: 3\nevent: delta\ndata: {"text":"po konci"}\n\n'),
  b, (u) => { typy.push(u.typ); if (u.typ === "konec") b.hotovo = true; });
  assert.deepEqual(typy, ["start", "delta", "konec"]);
  assert.equal(b.od, 3);
  assert.equal(n, 3);
});

test("sledujTah: po výpadku se připojí od offsetu", async () => {
  const b = {tahId: "t", od: 0, hotovo: false};
  const adresy = [];
  const fetchFn = async (url) => {
    adresy.push(url);
    return odpoved('id: 2\nevent: konec\ndata: {}\n\n');
  };
  const v = await sledujTah(b, {odpoved: odpoved('id: 0\nevent: start\ndata: {}\n\nid: 1\nevent: delta\ndata: {"text":"A"}\n\n'),
    naUdalost: (u) => { if (u.typ === "konec") b.hotovo = true; }, fetchFn, spi: async () => {}});
  assert.equal(v, "hotovo");
  assert.deepEqual(adresy, ["/chat-api/tah/t/stream?od=2"]);
});

test("sledujTah: 404 = tah už v procesu není", async () => {
  const b = {tahId: "t", od: 4, hotovo: false};
  const v = await sledujTah(b, {naUdalost: () => {}, fetchFn: async () => ({status: 404, ok: false}), spi: async () => {}});
  assert.equal(v, "ztraceno");
});

test("sledujTah: spojení spadne před start, bez tahu se nepřipojuje", async () => {
  const b = {tahId: null, od: 0, hotovo: false};
  let volano = 0;
  const v = await sledujTah(b, {odpoved: odpoved(""), naUdalost: () => {}, fetchFn: async () => { volano++; }});
  assert.equal(v, "ztraceno");
  assert.equal(volano, 0);
});

test("sledujTah: nejvýš 3 pokusy o připojení", async () => {
  const b = {tahId: "t", od: 0, hotovo: false};
  let volano = 0;
  const v = await sledujTah(b, {naUdalost: () => {}, fetchFn: async () => { volano++; throw new Error("síť"); },
    spi: async () => {}});
  assert.equal(v, "ztraceno");
  assert.equal(volano, 3);
});
