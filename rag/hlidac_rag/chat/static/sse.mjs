// Rozbor streamu SSE ze služby (beh.sse): bloky oddělené prázdným řádkem, pole `id`, `event`, `data`.
// Komentáře (`: ping`) a bloky bez `data` se přeskočí, nečitelný JSON také. Nedokončený blok zůstane ve `zbytek`.

export function rozeber(buffer) {
  const text = String(buffer || "").replace(/\r\n/g, "\n");
  const udalosti = [];
  let start = 0;
  for (;;) {
    const konec = text.indexOf("\n\n", start);
    if (konec < 0) break;
    const u = blok(text.slice(start, konec));
    if (u) udalosti.push(u);
    start = konec + 2;
  }
  return {udalosti, zbytek: text.slice(start)};
}

function blok(s) {
  let typ = "message", id = null;
  const data = [];
  for (const radek of s.split("\n")) {
    if (!radek || radek.startsWith(":")) continue;
    const i = radek.indexOf(":");
    const pole = i < 0 ? radek : radek.slice(0, i);
    let hodnota = i < 0 ? "" : radek.slice(i + 1);
    if (hodnota.startsWith(" ")) hodnota = hodnota.slice(1);
    if (pole === "event") typ = hodnota;
    else if (pole === "data") data.push(hodnota);
    else if (pole === "id" && /^[0-9]{1,9}$/.test(hodnota)) id = Number(hodnota);
  }
  if (!data.length) return null;
  try {
    return {typ, id, data: JSON.parse(data.join("\n"))};
  } catch {
    return null;
  }
}
