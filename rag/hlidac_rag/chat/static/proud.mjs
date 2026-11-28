// Stream tahu a opětovné připojení (spec 7.3): čtení SSE z odpovědi, připojení od offsetu po přechodu na jinou
// stránku nebo výpadku spojení, zastavení tahu a obnova stavu panelu ze zrcadla v sessionStorage.
// Bez DOM: události předává volajícímu (chat.mjs), který je vykreslí.

import { rozeber } from "./sse.mjs";

export const MAX_PRIPOJENI = 3;
export const NECINNOST_MS = 2 * 3600 * 1000;    // po 2 hodinách nečinnosti začíná panel prázdný

const cekej = (ms) => new Promise((ok) => setTimeout(ok, ms));

// Stav panelu z uloženého zrcadla. Vstupy jsou hodnoty ze sessionStorage (konv, zpravy) a localStorage (posledni),
// které mohou být cokoli (poškozené, cizí). Výsledek:
// {konv, zpravy, nabidnoutPredchozi, tah: {id, od} | null}; při tahu je poslední zpráva rozepsaná odpověď bota.
export function obnovZeZrcadla(konv, zpravy, posledni, ted, necinnostMs = NECINNOST_MS) {
  const k = konv && typeof konv === "object" && !Array.isArray(konv) ? konv : {};
  const tah = k.tah && typeof k.tah.id === "string" && Number.isInteger(k.tah.od) && k.tah.od >= 0
    ? {id: k.tah.id, od: k.tah.od} : null;
  const cerstve = typeof k.id === "string" && (tah || ted - (Number(k.cas) || 0) < necinnostMs);
  let out;
  if (cerstve) {
    const z = Array.isArray(zpravy) ? zpravy.filter((x) => x && typeof x === "object"
      && ["uzivatel", "bot", "oddelovac"].includes(x.role) && typeof (x.text ?? "") === "string") : [];
    out = {konv: {...k, tah}, zpravy: z, nabidnoutPredchozi: null, tah};
  } else {
    out = {konv: {otevreno: Boolean(k.otevreno)}, zpravy: [], nabidnoutPredchozi: null, tah: null};
  }
  if (tah) {
    let bot = out.zpravy[out.zpravy.length - 1];
    if (!bot || bot.role !== "bot") {
      bot = {role: "bot", text: ""};
      out.zpravy.push(bot);
    }
    bot.stav = "probiha";
  }
  if (!out.konv.id && posledni && typeof posledni.id === "string") out.nabidnoutPredchozi = posledni;
  return out;
}

// Přečte jednu odpověď SSE; `b.od` = id další události, `b.hotovo` nastaví volající v naUdalost (konec).
// Vrací počet přijatých událostí.
export async function ctiProud(odpoved, b, naUdalost) {
  let pocet = 0;
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
      for (const u of r.udalosti) {
        if (Number.isInteger(u.id)) b.od = u.id + 1;
        pocet++;
        naUdalost(u);
        if (b.hotovo || b.zruseno) {
          cteni.cancel().catch(() => {});
          return pocet;
        }
      }
    }
  } catch { /* spojení spadlo */ }
  return pocet;
}

// Sleduje tah do konce: nejdřív `odpoved` (POST /zprava), po výpadku GET /tah/{id}/stream?od=.
// Vrací "hotovo", "zruseno", nebo "ztraceno" (dočíst nejde: tah v procesu už není, nebo spadl před `start`).
export async function sledujTah(b, {odpoved = null, naUdalost, api = "/chat-api", fetchFn = fetch, spi = cekej,
  maxPokusu = MAX_PRIPOJENI} = {}) {
  let r = odpoved, pokusu = 0;
  for (;;) {
    if (r) {
      const prijato = await ctiProud(r, b, naUdalost);
      if (b.hotovo) return "hotovo";
      if (b.zruseno) return "zruseno";
      if (prijato) pokusu = 0;
      r = null;
    }
    if (!b.tahId) return "ztraceno";
    while (!r) {
      if (pokusu >= maxPokusu || b.zruseno) return b.zruseno ? "zruseno" : "ztraceno";
      pokusu++;
      if (pokusu > 1) await spi(1000 * 2 ** (pokusu - 2));
      try {
        const x = await fetchFn(`${api}/tah/${encodeURIComponent(b.tahId)}/stream?od=${b.od}`, {cache: "no-store"});
        if (x.status === 404) return "ztraceno";
        if (x.ok) r = x;
      } catch { /* další pokus */ }
    }
  }
}

export async function zastavTah(tahId, {api = "/chat-api", fetchFn = fetch} = {}) {
  try {
    await fetchFn(`${api}/tah/${encodeURIComponent(tahId)}/stop`, {method: "POST", cache: "no-store"});
  } catch { /* stream doběhne sám */ }
}
