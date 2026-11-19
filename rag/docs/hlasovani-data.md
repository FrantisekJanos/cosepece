# Hlasování v DB: data a párování s tisky

Průzkum pro nástroje MCP `hlasovani_k_tisku(ct)` a `get_hlasovani(id_hlasovani)`. Stav: produkční DB (19. 11. 2026),
849 hlasování k 52 tiskům; pro srovnání otevřená data psp.cz `hl-2025ps.zip` (2 702 hlasování 10. období) a `schuze.zip`.
Tabulky plní web (`app/Votes.php`) každou hodinu znovu celé (DELETE + INSERT v transakci), jen pro aktuální období.

## Sloupce a hodnoty

| Sloupec | Význam (ověřeno na datech) |
|---|---|
| `id_hlasovani` | id hlasování psp.cz; odkaz `https://www.psp.cz/sqw/hlasy.sqw?G={id_hlasovani}` |
| `schuze`, `cislo` | číslo schůze a pořadové číslo hlasování ve schůzi; dvojice je v období jedinečná. „Hlasování č. 287“ v textu je `cislo`, ne id |
| `datum` | `RRRR-MM-DD HH:MM` (všech 849 má 16 znaků), čas podle psp.cz |
| `pro`, `proti`, `zdrzel`, `nehlasoval`, `prihlaseno` | počty z psp.cz; nepřihlášení = 200 − `prihlaseno` |
| `kvorum` | nejmenší počet hlasů pro přijetí. 836× = `prihlaseno / 2 + 1` (celočíselně), 13× = 101 (nadpoloviční většina všech poslanců, např. setrvání na zákoně vráceném Senátem 88449 nebo prezidentem 88667) |
| `vysledek` | v datech jen `A` = přijato (324) a `R` = zamítnuto (525). `druh_hlasovani` (web ho neukládá) je v psp.cz u všech `N` |
| `nazev` | `nazev_dlouhy` z psp.cz (max. 70 znaků; `nazev_kratky` je v datech vždy prázdný): krátký název **tisku/bodu**, ne návrhu, o kterém se hlasuje. U tisku je stejný pro všechna hlasování (nejvýš 2 varianty: s `**` a bez) |
| `krok` | 1 = hlasování je uvedeno u kroku historie tisku (`hist.unl` → `kroky.id_hlasovani`), 43 hlasování: „Odesíláno do Senátu / souhlas“ 31× (závěrečné hlasování 3. čtení), „Prezident / setrváno“ 7× (přehlasování Senátu), po 1× „Prezident / souhlas“, „Prezident / pozm. přijat“, „Sbírka zákonů / setrváno“, „KONEC / nepřijat“, „2. čtení / vrácen“. 0 = spárováno přes bod schůze (806): jakékoli hlasování v bodu, i procedurální |

- **Zmatečná hlasování** psp.cz eviduje zvlášť (`zmatecne.unl` v `hl-2025ps.zip`), web je nenačítá a `vysledek` u nich zůstává `A`/`R`.
  V tabulce je 11 zmatečných: 87071, 87371, 87408, 87409, 87577, 87578, 87939, 88044, 88273, 88277, 88574. Proto
  `vysledek` nepočítat z `pro >= kvorum` (88574: pro 51, kvórum 33, `R`). Předsedající hlášené „zmatečné“ 88862 (č. 273)
  v seznamu psp.cz není.
- **Zpochybnění** data neoznačují: 88024 (č. 183, 100 : 78) → námitka 88025 (č. 184) → opakování 88026 (č. 185, 99 : 75,
  `krok` = 1), zjištěno jen ze stenozáznamu `10/24/264/4`.
- **`**` v názvu**: 752 z 2 702 hlasování (668 na konci, 84 uprostřed místo mezery, např. „…poškozují**ozonovou vrstvu - EU“).
  Nejspíš zalomení řádku (neověřeno). Komentář ve `Votes::normName` („hvězdičky značí hlasování k PN“) data nepotvrzují:
  88666 (veto) `**` má, 3. čtení tisku 77 (88009–88026) ne.

## Kluby (`hlasovani_kluby`)

- `klub` = zkratka klubu z `organy.unl` ke dni hlasování: `ANO2011`, `MS` (Motoristé sobě), `SPD`, `STAN`, `Piráti`, `TOP09`,
  `ODS`, `KDU-ČSL` (u všech 849 hlasování) a `Nezařaz` (8 hlasování). Uživatel píše „ANO“, „Motoristé“, „TOP 09“: nástroj má
  přijímat aliasy (viz vada E20 u `projevy_k_tisku(klub="ANO")`).
- 8–9 řádků na hlasování. Součty sedí u všech 849: `pro` a `proti` přesně; klubové `zdrzel` = `zdrzel + nehlasoval`
  (kódy C, F, K); `nepritomen` = 200 − `prihlaseno` (kódy @, M, W); součet řádků klubu = 200.

## Jak web páruje hlasování s tiskem a kde chybuje

1. `krok`: hlasování z historie tisku (43). 2. bod schůze (`bod_schuze.unl`: schůze + číslo bodu → tisk) a zároveň stejný
   název tisku po `normName` (malá písmena, mezery, ořez `*` na konci) (806).

- **Chybějící hlasování.** 163 hlasování z bodů s tiskem se nespárovalo kvůli jinému názvu; asi 128 z nich je varianta názvu
  tisku (`- EU**`, `**` uprostřed, „Vl. n. z.“ proti „Vl.n.z.“, zkratky; volná shoda prvních 25 znaků bez interpunkce,
  ručně ověřeno ze stenozáznamu u 86426, 86816, 87089). Tisk 17 (rozpočet 2026) nemá v tabulce žádné hlasování
  (chybí 86426–86429), tisku 69 chybí 20 (86816–…), 58 15, 14 9, 70 6 (87074–87077). Další se liší víc: tisk 52
  „Vl.n.z. o urč. provoz. za účel shrom. důkazů v trest řízení“ proti „Vl. n. z. o urč. provozoven za účel shrom.
  důkazů**v trestním…“ (87089–87092, 88701–88703).
- **Dvojznačný bod.** `bod_schuze.unl` obsahuje i pozvánku (`pozvanka` = 1) s jiným číslováním. 34. schůze bod 49 je
  tisk 311 (schválený pořad) i tisk 4 (pozvánka), oba „Novela z. o daních z příjmů“. 88655–88661 připadla tisku 311 jen
  pořadím řádků (stenozáznam u 88655–88658 potvrzuje 311). Stejný krátký název má 27 skupin tisků (např. 7 tisků „daně z příjmů“).
- **Procedurální hlasování v bodu** patří k tisku: u 231 (30 hlasování) jsou č. 273–280 o pevném čase hlasování a jeho
  revokaci (23. 10.), 285 znovuotevření rozpravy, 286 vrácení k dopracování, 287 zamítnutí, 288 přikázání garančnímu výboru
  (`10/34/477/11`); č. 281–282 a 289–302 neověřeno. Hlasování o pořadu schůze (bod 0) se nepárují vůbec.
- `steno.projev.ct` párování nenahradí: u sloučené rozpravy 76/77 se v 11 hlasováních liší od webu, hlasování o pořadu
  mají `ct` bodu, ve kterém zazněla. Web (podle názvu, „- související“ = 77) má `ct` 76 u 86572–86575, 88449, 88666 a
  `ct` 77 u 86577–86581, 88450, 88667. Opačný tisk uvádí steno u těch 11: 86572–86575, 86577–86581, 88450, 88667.
  Každé `id_hlasovani` (PK) patří právě jednomu `ct`, takže `hlasovani_k_tisku(76)` vrátí jen ta s `ct` 76.

## Porovnání s eval otázkou E21 (`rag/eval/otazky.json`)

| Tisk | Hlasování | Data | E21 |
|---|---|---|---|
| 231 | 88874/88875/88876 = 34. schůze č. 285/286/287, 24. 10. 2026 04:38–04:39 | 81 : 96, přihlášeno 177, `R`; č. 287 proti ANO2011 69, SPD 14, MS 13; pro ODS 23, STAN 21, Piráti 16, KDU-ČSL 14, TOP09 7 | sedí |
| 311 | 89028 = 36. schůze č. 3, 13. 11. 2026 13:17 (zkrácení lhůty pro 3. čtení na 7 dní, `10/36/26/13`) | přihlášeno 100, pro 77 (ANO2011 55, SPD 12, MS 10), proti 23, `A`, `krok` 0 | sedí |
| 88 | 86697 = 9. schůze č. 6, 13. 2. 2026 10:56, `krok` 1 | přihlášeno 136, pro 130, proti 0, `A` | sedí |
| 76 | 88449, 30. schůze č. 32, 8. 9. 2026 (přehlasování Senátu, `krok` 1) | 104 : 68, kvórum 101, `A` | doplněno |
| 77 | 88450, 30. schůze č. 33, 8. 9. 2026 (přehlasování Senátu, `krok` 1) | 104 : 69, kvórum 101, `A` | doplněno |
| 76 | 88666, 35. schůze č. 4, 14. 10. 2026 (veto, čl. 50 odst. 2 Ústavy, `krok` 0) | 104 : 66, kvórum 101, `A` | doplněno |
| 77 | 88667, 35. schůze č. 5, 14. 10. 2026 (veto, `krok` 0) | 104 : 70, kvórum 101, `A` | doplněno |

Ke E20: usnesení o pevném čase hlasování je 88866 (34. schůze č. 277, 23. 10. 2026 19:46), 88 : 63, `A` (`10/34/446/2`).

## PN ↔ hlasování ve 3. čtení: nepárovat

- `nazev` číslo ani písmeno PN neobsahuje. Vzorek: 3. čtení tisku 77 (24. schůze, 1. 7. 2026, č. 168–185, id 88009–88026)
  má u všech 18 hlasování `nazev` „Návrh zákona o státních zaměstnancích - související“. `krok` značí jen závěrečná hlasování.
- Písmeno zazní jen ve stenozáznamu, v projevu zpravodaje před hlasováním: „pod písmenem B kolegy … Juchelky“ (88012,
  č. 171), „C1 – kolegyně Ožanová“ (88013), „pod písmenem A … výborový … s výjimkou A7“ (88017), „E1, E2 a E4 jedním
  hlasováním“ (88021), „G2 nehlasovatelný“. Písmena jsou ze souhrnného přehledu PN (sněmovní tisk N/x), který v DB není.
- Číslo sněmovního dokumentu (`pn.cislo`) zaznívá ve 2. čtení („hlásím se ke sněmovnímu dokumentu 2303“); z 16 projevů
  s odkazem na hlasování a číslem dokumentu není žádný o PN.
- Párování přes autora selže: Ožanová má k tisku 77 pět PN (809–813) pro C1–C5 v neznámém pořadí, E3 (Nacher) a F (Berki)
  k tisku 77 v `pn` žádný PN nemají, A je výborový návrh bez dokumentu.
- **Rozhodnutí:** nástroj PN nepáruje. Spolehlivě umí vrátit klíče projevů, které na hlasování odkazují (kontext s písmenem
  si model přečte přes `get_projev`).

## `steno.projev.hlasovani`

- Hodnoty jsou `id_hlasovani` (parametr `G` z odkazů `hlasy.sqw?G=` ve stenozáznamu, `RE_VOTE` v `psp/parser.py`).
  Ověřeno: 2 698 různých hodnot, všechny v rozsahu id psp.cz 86327–89028; projev `10/34/477/11` s `{88874,88875,88876,88877}`
  hlásí „Hlasování číslo 285 … 287“ (= `cislo`). Na produkci schéma `steno` ve stejné DB je.
- 846 z 849 hlasování z tabulky má aspoň jeden projev; bez odkazu 88666, 88819, 87942.
- Pole obsahuje všechny odkazy v projevu: i pozdější zmínky (`10/10/225/2` zmiňuje 86816 den po hlasování) a hromadné
  výčty (až 51 id v jednom projevu u pořadu schůze). Nástroj `get_hlasovani` řadí projevy nejdřív ze schůze hlasování, pak podle data a pořadí
  (schůze se překrývají, čísla schůzí nejdou časově po sobě). Na produkci 19. 11. 2026 byl první projev ze schůze
  hlasování u 846 z 846 hlasování, která mají projev (heuristika, ne záruka).

## Velikost dat (limit odpovědi 60 000 znaků)

- Nejvíc hlasování na tisk: 171 (tisk 67), 135 (94), 88 (189); nejvíc na tisk a schůzi 129 (tisk 94, 10. schůze).
- JSON (`ensure_ascii=False`, kompaktní), naměřeno na produkci:
  - řádek hlasování se všemi sloupci, textem výsledku a URL, bez klubů: průměr 322, max. 354 znaků (s `nazev` v řádku);
  - kluby v plné podobě (`[{klub, pro, proti, zdrzel, nepritomen}, …]`): průměr 613, max. 690 znaků;
  - kluby kompaktně (`{"ANO2011": [pro, proti, zdrzel, nepritomen], …}`): průměr 198, max. 224 znaků;
  - obálka seznamu (`ct`, `nazev` jako pole, `celkem`, `vraceno`, `limit`, URL tisku, prázdné `hlasovani`): 210 znaků.
- Všech 171 hlasování tisku 67 bez klubů = 53 553 znaků + obálka. Odvozeně: 150 řádků bez klubů ≈ 150 × 354 + 210
  ≈ 53 300 znaků, rezerva jen asi 11 % pod 60 000; s kompaktními kluby (≈ 580 znaků na řádek) se vejde asi 100 řádků.
  Strop `MAX_LIMIT_HLASOVANI` je 70 řádků na stránku: nejhorší možný řádek podle schématu webu (krok „stav / akce“
  243 znaků, 10 klubů, trojmístná čísla) dává asi 770 znaků, 70 řádků s hlavičkou 53 928 znaků (pod 54 000, rezerva
  pod limitem 60 000). Naměřené maximum na produkci: 31 803 znaků u `hlasovani_k_tisku(67, limit=80)` (starší strop 80).
