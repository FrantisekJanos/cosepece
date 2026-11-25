## 1. `cesta` – Cesta zákona

**Divák pochopí:** jak rychle návrh Sněmovnou šel, kde se zrychlilo proti běžnému postupu a co jednací řád dovoluje.

| Scéna | Obsah |
|---|---|
| `s-c-osa` | Až 6 kroků s daty, mezi nimi odstup ve dnech („+21 dní“, „týž den“), sporné kroky červeně, dole celkový počet dní |
| `s-c-norm` | „Běžně × tady“: až 3 řádky (pruh běžné lhůty, pruh skutečnosti, paragraf), razítko ZKRÁTILI / § 90/2 jen s klíčem důvodu, věta o tom, co je legální (`ferove`) |
| `s-dny` | Sloupcový graf PN po dnech, den konce podrobné rozpravy červeně (volitelné, `pn_dny`) |

**Data:** `cesta.kroky[{date,label,hot}]`, `cesta.dni`, `cesta.dni_popis` ← `get_tisk(ct,["kroky"])` (tabulka `kroky`); `cesta.normalne[{label,par,normal,actual,unit}]` ← `reasons` (`deadline_cut` nese „30 dní místo 60“, `short_gap` počet dní) + pevná tabulka lhůt z `docs/jednaci-rad-postup.md`; `pn_dny` ← `get_tisk(ct,["pn"])` pole `cas` (tabulka `pn`).

**Automatizace hned:** celé. Kroky, odstupy, počty PN po dnech i lhůty jsou strukturovaná data. Texty kroků jdou ze slovníku stav+akce → popisek. Titulek a perex šablonou („{nazev}: výbory měly {actual} dní místo {normal}“) nebo z AI rozboru webu.
**AI potřeba:** žádná (nanejvýš zkrácení názvu).
**Rizika:** názvy akcí v datech jsou technické („změna lhůty pozmň. návrhu“, „nesouhlas s RJ“) a slovník je může špatně přeložit; odstup dní není vždy zákonná lhůta (tisk 174: 6 dní vs. minimum 7, viz `zdroje.md`); delší než běžná lhůta se musí ukázat stejně poctivě jako kratší (174: výbory 112 dní).

## 2. `zmena` – Co se doopravdy mění

**Divák pochopí:** co přesně se v zákoně přepisuje (dnes × po novele), doslova z textu, proč to předkladatel chce a na co si dát pozor.

| Scéna | Obsah |
|---|---|
| `s-z-1` až `s-z-3` | Místo v textu (§ / článek), popisek, „DNES“ (přeškrtnuto) × „PO NOVELE“, výstřižek s doslovnou citací, zdroj (tisk, článek) |
| `s-z-proc` | „Proč to navrhují“ (důvodová zpráva nebo úvodní slovo, se zdrojem) × „Na co se dívat“ (se zdrojem) |

**Data:** `zmena.body[{kde,co,pred,po,bez_pred,citace,zdroj,url}]` ← `osnova_textu`, `get_cel`, `hledej_v_textech("nahrazuje", ct=…)` (tabulky textů po celcích); `zmena.proc{text,zdroj}` ← důvodová zpráva (`vcetne_duvodove=True`) nebo `get_projev` úvodního slova; `zmena.pozor{text,zdroj}` ← `get_tisk(ct,["rozbor"])` (`rozbor.ai`) a důvody skóre. Bod bez citace nebo zdroje šablona nezařadí.

**Automatizace hned:** novelizační body tvaru „číslo/slova „X“ se nahrazují „Y““ jdou vytáhnout regulárním výrazem včetně citace a místa. Citace AI rozboru (`rozbor.ai.citace`) jsou už ověřené proti textu.
**AI potřeba:** výběr 1–3 nejdůležitějších změn, lidský popisek („lhůta pro zahájení řízení“), věta „proč“ z důvodové zprávy. Vhodné pro stávající AI frontu (Haiku + ověření Sonnetem) s povinnou doslovnou citací.
**Rizika:** MCP nemá platné znění zákona, takže „dnes“ známe jen z nahrazovaných slov (u 221 „10“ → jednotku „let“ dává až důvodová zpráva); u nových paragrafů jen „v zákoně není“. AI může vybrat nepodstatnou změnu nebo špatně pojmenovat význam. „Na co se dívat“ je hodnocení: musí mít zdroj a nesmí tvrdit motiv.

## 3. `rozprava` – Rozprava v kostce

**Divák pochopí:** kdo mluvil a kolik, proč to předkladatel chce, jaké byly hlavní námitky a co na ně předkladatel odpověděl.

| Scéna | Obsah |
|---|---|
| `s-r-kdo` | Počet vystoupení a řečníků, pruhy po klubech (jedna šedá), kolik stenozáznamů je neautorizovaných |
| `s-r-pro` | „Proč to předkladatel chce“: až 3 body, u každého řečník a datum |
| `s-r-proti` | „Hlavní námitky“: až 3 body, řečníci, šedé štítky klubů, poznámka (např. kdo v principu souhlasí) |
| `s-r-odp` | „Odpověď předkladatele“: až 3 body (volitelné) |

Každá scéna se shrnutím má štítek „shrnutí AI“, neautorizovaný projev štítek „neautorizováno“, závěr „Shrnutí AI z N projevů, odkazy na cosepece.cz“.

**Data:** `rozprava.projevu`, `recniku`, `kluby[{klub,projevu}]`, `neautorizovano` ← `projevy_k_tisku(ct)` (tabulka projevů; počty jsou čisté SQL); `pro/namitky/odpovedi.body[{text,zdroj,kluby,url,klic,autorizovano}]` ← `get_projev(klic)`. Šablona ukáže shrnutí jen tehdy, když má doložené body předkladatel i námitky; bod bez `url` vypadne.

**Automatizace hned:** scéna s čísly.
**AI potřeba:** celé shrnutí argumentů. Doporučený postup je MAP/REDUCE z `docs/prompt-rozbor-rozpravy-v1.md` (položky `argument` se `smer` a `predmet`, párování námitka–odpověď), Reel pak bere 2–3 nejčastější položky každé strany a odkaz na projev.
**Rizika:** nejvyšší ze všech formátů. Výběr tří bodů z desítek projevů je redakční rozhodnutí (ukázka 311 vznikla ze 6 ze 40 projevů a přiznává to). Parafráze může posunout smysl, výrok řečníka není fakt („odhad je nejistý“ je námitka, ne zjištění). Neautorizované projevy se mohou změnit (`zmeny_projevu`). Bez lidské kontroly nepouštět.

## 4. `pn` – Kdo co přidal

**Divák pochopí:** kolik pozměňovacích návrhů přišlo, od koho a kdy, zda sahají do jiných zákonů a co nejvýraznější z nich navrhují.

| Scéna | Obsah |
|---|---|
| `s-p-kolik` | Počet PN, počet autorů, lístky (pozdní červeně), 5 nejčastějších autorů se šedým štítkem klubu |
| `s-dny` | PN po dnech nebo měsících (volitelné) |
| `s-p-jine` | Jiné zákony, do kterých PN sahají (číslo Sb., název, čísla PN), razítko PŘÍLEPEK jen s klíčem `pn_other_law` (volitelné) |
| `s-p-top` | Až 3 PN: číslo, autor, klub, titulek; štítek „titulky: AI“ (volitelné) |

**Data:** `pn.count`, `authors`, `autori[{jmeno,klub,count}]` ← `pn_k_tisku(ct)` + `najdi_osobu` (klub); `late_count`, `pn_dny` ← `pn[].cas` + `kroky`; `jine_zakony[{sb,nazev,pn}]` ← `pn[].laws`, `zakony_z_pn`; `top[{cislo,autor,klub,titulek,url}]` ← `pn[].titulek`, `ai_score`. U videa k vybraným PN (`podklady/zadani.md`) systém přepíše autora a klub zadaných PN v `top` údaji ze serveru (klub ke dni podání) a doplní pole `pn_autor`; model `pn_autor` nepíše.

**Automatizace hned:** celé. Titulky PN už web má (AI fronta), jen se přebírají a označí.
**AI potřeba:** žádná nová.
**Rizika:** výběr „top“ podle `ai_score` zvýrazní některé autory; pořadí musí být pravidlové a stejné pro všechny kluby. Samotný počet PN není prohřešek (opoziční PN jsou běžný nástroj), proto věta „O pozměňovacích návrzích se hlasuje až ve 3. čtení“. Zákon z `laws` nemusí být přílepek (115: 45/2013 mění už sám tisk); razítko jen s `pn_other_law`.

