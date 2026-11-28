import test from "node:test";
import assert from "node:assert/strict";
import { rozeber } from "../../hlidac_rag/chat/static/sse.mjs";

test("rozeber skládá události přes hranice bloků", () => {
  const a = rozeber('event: delta\ndata: {"text":"Ah'); assert.equal(a.udalosti.length, 0);
  const b = rozeber(a.zbytek + 'oj"}\n\n: ping\n\nevent: konec\ndata: {"kod":"AB12CD34"}\n\n');
  assert.deepEqual(b.udalosti.map(u => u.typ), ["delta", "konec"]); assert.equal(b.udalosti[0].data.text, "Ahoj");
});

test("rozeber čte id události (offset pro opětovné připojení)", () => {
  const r = rozeber('id: 0\nevent: start\ndata: {"tah_id":"x"}\n\nid: 1\nevent: stav\ndata: {"text":"Přemýšlím…"}\n\n');
  assert.deepEqual(r.udalosti.map(u => u.id), [0, 1]);
  assert.equal(r.zbytek, "");
});

test("rozeber: rozdělený oddělovač bloků, CRLF a víc řádků data", () => {
  const a = rozeber('event: delta\ndata: {"text":"a"}\n');
  assert.equal(a.udalosti.length, 0);
  const b = rozeber(a.zbytek + '\nevent: delta\r\ndata: {"text":\r\ndata: "b"}\r\n\r\n');
  assert.deepEqual(b.udalosti.map(u => u.data.text), ["a", "b"]);
});

test("rozeber: nečitelná data a blok bez data se přeskočí", () => {
  const r = rozeber('event: delta\ndata: {nejson\n\nevent: konec\n\n: ping\n\nevent: konec\ndata: {}\n\n');
  assert.deepEqual(r.udalosti.map(u => u.typ), ["konec"]);
});

test("rozeber: událost bez jména je message", () => {
  assert.equal(rozeber('data: {"a":1}\n\n').udalosti[0].typ, "message");
});
