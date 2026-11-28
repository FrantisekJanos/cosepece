// Cloudflare Turnstile ve widgetu (spec 11.2). Skript se načte až s otevřením panelu, režim managed
// (`appearance: interaction-only`: většina lidí nic nevidí), `action: "chat"`. Token je jednorázový: po každém
// POST, který ho nesl, se zahodí a widget si vyžádá nový.

const SKRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
const CEKANI_MS = 120000;       // na výzvu (zaškrtávátko) se čeká nejvýš tak dlouho

let nacteni = null;

function nactiSkript() {
  if (nacteni) return nacteni;
  nacteni = new Promise((ok, chyba) => {
    if (window.turnstile) return ok(window.turnstile);
    const s = document.createElement("script");
    s.src = SKRIPT;
    s.async = true;
    s.onload = () => (window.turnstile ? ok(window.turnstile) : chyba(new Error("turnstile")));
    s.onerror = () => { nacteni = null; chyba(new Error("turnstile")); };
    document.head.append(s);
  });
  return nacteni;
}

export class Turnstile {
  constructor(kontejner) {
    this.kontejner = kontejner;
    this.sitekey = "";
    this.token = null;
    this.id = null;
    this.cekaji = [];
  }

  _predej(t) {
    this.token = t;
    const cekaji = this.cekaji;
    this.cekaji = [];
    for (const c of cekaji) c(t);
  }

  // Vykreslí widget (jednou); bez sitekey nic. Nepovede-li se skript načíst, server vrátí 428 a zkusí se znovu.
  async priprav(sitekey) {
    if (sitekey) this.sitekey = sitekey;
    if (this.id !== null || !this.sitekey) return;
    try {
      const ts = await nactiSkript();
      if (this.id !== null) return;
      this.id = ts.render(this.kontejner, {
        sitekey: this.sitekey, action: "chat", appearance: "interaction-only", language: "cs",
        "refresh-expired": "auto",
        callback: (t) => this._predej(t),
        "expired-callback": () => { this.token = null; },
        "error-callback": () => { this.token = null; },
      });
    } catch { /* skript se nenačetl */ }
  }

  // Token byl odeslán (a možná spotřebován): zahodit a v pozadí vyžádat nový.
  zahod() {
    this.token = null;
    if (this.id !== null && window.turnstile) {
      try { window.turnstile.reset(this.id); } catch { /* nic */ }
    }
  }

  // Nový token pro opakované odeslání po 428; null = nepovedlo se (výpadek, uživatel výzvu nepotvrdil).
  async novy() {
    this.token = null;
    try {
      await this.priprav();
      if (this.id === null) return null;
      const cekani = new Promise((ok) => {
        this.cekaji.push(ok);
        setTimeout(() => ok(null), CEKANI_MS);
      });
      window.turnstile.reset(this.id);
      return await cekani;
    } catch {
      return null;
    }
  }
}
