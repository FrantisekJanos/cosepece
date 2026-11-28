// DOM pomocníci panelu chatu: prvky jen přes createElement a textContent, události Umami bez textu (spec 14.1).

export function e(tag, vlastnosti = {}, ...deti) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(vlastnosti)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "text") el.textContent = v;
    else if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const d of deti) if (d) el.append(d);
  return el;
}

export function tlacitko(text, onclick, trida = "cz-chat-tl") {
  return e("button", {type: "button", class: trida, text, onclick});
}

// Umami: jen název a krátká data (typ stránky, kód limitu, palec), nikdy text dotazu nebo odpovědi
export function udalost(nazev, data) {
  try { window.umami?.track?.(nazev, data); } catch { /* analytika nesmí rozbít panel */ }
}
