// Systémové hlášky panelu (spec 11.3): text doslova z tabulky, odkazy, zda blokují pole zprávy a zda je widget
// opakuje sám. Hlášky nejsou zprávy bota: neukládají se do historie a model je nevidí.

const ODKAZ_HLEDAT = {text: "Hledat na webu", href: "/hledat"};
const ODKAZ_MCP = {text: "Připojte si data do vlastního AI asistenta (MCP)", href: "/mcp"};

function tvar(n, jedna, dve, pet) {
  return n === 1 ? jedna : n >= 2 && n <= 4 ? dve : pet;
}

function odkazyLimitu(ct) {
  const out = [ODKAZ_HLEDAT];
  if (Number.isInteger(ct) && ct > 0) {
    out.push({text: "Tisk na psp.cz", href: `https://www.psp.cz/sqw/historie.sqw?o=10&t=${ct}`});
  }
  out.push(ODKAZ_MCP);
  return out;
}

function zaMinut(znovu_v, ted) {
  const cas = Date.parse(znovu_v || "");
  if (!Number.isFinite(cas)) return "za chvíli";
  const n = Math.max(1, Math.ceil((cas - ted) / 60000));
  return `za ${n} ${tvar(n, "minutu", "minuty", "minut")}`;
}

function h(text, {odkazy = [], blokuje = false, opakovat = false, akce = null} = {}) {
  return {text, odkazy, blokuje, opakovat, akce};
}

// kod: kód ze služby (tělo chyby, /stav, událost `chyba`) nebo vlastní kód widgetu
// data: {znovu_v?, zbyva_otazek?, ct?, otazek?}; ted = ms od epochy (pro testy)
export function hlaska(kod, data = {}, ted = Date.now()) {
  const d = data || {};
  switch (kod) {
    case "limit_den":
      return h("Dnešní kapacita asistenta je vyčerpaná. Běží na malém denním rozpočtu, znovu odpoví zítra.",
        {odkazy: odkazyLimitu(d.ct), blokuje: true});
    case "limit_hodina":
      return h(`Asistent má teď hodně dotazů. Zkuste to znovu ${zaMinut(d.znovu_v, ted)}.`, {blokuje: true});
    case "limit_relace": {
      const n = d.otazek;
      const kolik = Number.isInteger(n) && n > 0 ? `${n} ${tvar(n, "otázku", "otázky", "otázek")}` : "hodně otázek";
      return h(`Dnes jste položili ${kolik}, víc jich asistent na jednoho návštěvníka denně nezvládne. Zítra pokračujte.`,
        {blokuje: true});
    }
    case "limit_ip":
      return h("Z vaší sítě dnes přišlo hodně dotazů (může jít o školu nebo firmu se společnou adresou). Zkuste to zítra.",
        {odkazy: odkazyLimitu(d.ct), blokuje: true});
    case "limit_konverzace":
      return h("Tahle konverzace je dlouhá. Začněte novou, kontext stránky převezmu.", {blokuje: true, akce: "novy_chat"});
    case "obsazeno":
      return h("Asistent je vytížený, zkouším to znovu…", {opakovat: true});
    case "obsazeno_trvale":
      return h("Asistent je teď vytížený. Zkuste to za chvíli.");
    case "turnstile":
      return h("Potvrďte prosím, že nejste robot. Zpráva se pak odešle sama.");
    case "overeni_nedostupne":
      return h("Ověření, že nejste robot, teď nefunguje. Zkuste to za chvíli.");
    case "vypnuto":
      return h("Asistent je dočasně vypnutý.", {blokuje: true});
    case "odmitnuto":
      return h("Na tuto otázku asistent neodpoví. Zkuste se zeptat jinak, k tisku, zákonu nebo hlasování.");
    case "tah_bezi":
      return h("Předchozí odpověď ještě běží, možná v jiné kartě. Počkejte, až doběhne, a zeptejte se znovu.");
    case "konverzace_nedostupna":
      return h("Předchozí konverzace už není k dispozici.");
    case "spojeni":
      return h("Spojení s asistentem se přerušilo. Zkuste to znovu.");
    default:
      return h("Něco se nepovedlo. Zkuste to prosím znovu.");
  }
}

// Řádek pod polem zprávy, když relaci zbývají 2 otázky nebo méně (spec 11.3); jinak prázdný řetězec.
export function zbyvaText(n) {
  if (!Number.isInteger(n) || n > 2 || n < 0) return "";
  if (n === 0) return "Dnes už nezbývá žádná otázka";
  return n === 1 ? "Dnes zbývá 1 otázka" : `Dnes zbývají ${n} otázky`;
}
