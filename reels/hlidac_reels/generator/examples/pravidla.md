# Pravidla obsahu (závazná)

- Hodnotí se **postup, ne strana ani osoba**. Žádné stranické barvy, klub jen šedým štítkem, žádné přezdívky ani hodnocení lidí.
- Jen doložená fakta. Každý údaj dohledatelný (patka: tisk, psp.cz, datum dat; `zdroje.md`).
- **Žádné vytržené citace bez kontextu** (rozhodnutí majitele po variantě „předkladatel“). Citace jen jako doklad konkrétního tvrzení (text zákona, PN), ne jako „co kdo řekl“.
- **Osoby nejmenuj.** Ve volném textu (titulek, perex, háček, scény, popisek) nepiš jména osob: spolupodepsaných, řečníků, ministrů ani nikoho jiného. Jediná výjimka je autor PN nebo předkladatel ze zadání, a to jen celým jménem ve tvaru ze zadání s klubem a dovětkem („Jan Novák (ANO) a 2 další poslanci“), a řečník v úvodu projevu níže. Místo „autoři uvádějí“, „podle autorů“ piš „odůvodnění PN uvádí“, „podle odůvodnění“.
- **Výrok nikdy nepřisuzuj osobě.** Nepiš „X řekl“, „X tvrdí“, „X upozornil“, „podle X“, „X: „…““ ani „předkladatelé zdůvodňují“. Řečník v projevu často cituje nebo převypráví někoho jiného a nástroj `over_citaci` to nepozná. Platí pro osoby, role (poslanec, ministryně, předkladatel, autor, mluvčí, opozice) i kluby. Sloveso mluvení nebo hodnocení (uvádí, tvrdí, varuje, odmítá, reaguje…) jen s dokumentem nebo institucí na začátku věty („Návrh uvádí…“, „Odůvodnění uvádí…“, „ČNB varuje…“).
- Text z projevu uveď jen takto: „V projevu J. Nováka 12. 11. zaznělo: „…““ (doslovně, ověřeno přes `over_citaci`, cituj `nalezeny_text`) nebo „V projevu J. Nováka 12. 11. zaznělo, že …“ (převyprávění). U neautorizovaného záznamu přidej „(neautorizovaný záznam)“ hned za datum. Ke každému takovému místu zapiš položku do pole karty `citace_projevu`: `[{"klic", "recnik", "datum" (RRRR-MM-DD), "url" (stenozáznam psp.cz), "autorizovano", "doslovne", "text"}]`. Video autopilota smí citovat jen autorizovaný projev.
- Citace projevu nepatří do titulku, háčku ani do prvního odstavce popisku (ten jde zkrácený na Threads a X).
- U dokumentu piš, co v něm stojí: „Důvodová zpráva uvádí…“, „V textu PN stojí…“. Ne „předkladatel chce“, ne „podle předkladatelů“.
- Uvozovky jen pro doslovný text zákona, návrhu nebo PN, který je v kartě jako `citace` nebo `quote`, a pro citaci projevu výše.
- **Více navrhovatelů:** hned za jméno (a klub) prvního napiš „a N dalších poslanců“ podle údaje v zadání (`spolu` u PN) nebo v `predkladatel` (`dalsi_text`), ve stejné větě, i v prvním odstavci popisku: „PN 2376 podal Jan Novák (ANO) a 2 další poslanci“. Když zadání u PN uvádí, že počet spolupodepsaných nejde určit, napiš za jméno „podle evidence psp.cz“ („Jan Novák (ANO, podle evidence psp.cz)“). U předkladatele s neznámým počtem „a další poslanci“. Nikdy jen první jméno. Počet neodhaduj.
- Motiv nepřipisuj: žádné „aby“, „chce“, „chtějí“, „snaží se“ u osob, stran a vlády, ani „obešli“, „protlačili“, „potichu“, „účelově“, „záměrně“. Popisuj, co se stalo a kdy.
- Popisek videa, které stojí na rozboru AI (titulky AI, přílepek, závažnost), musí říct, že jde o AI („shrnutí AI“, „titulky napsala AI webu cosepece.cz“); hodnocení AI v prvním odstavci popisku označ v témže odstavci.
- Shrnutí od AI je ve videu viditelně označené („shrnutí AI“, „Shrnutí AI z N projevů“). Neautorizovaný stenozáznam má štítek „neautorizováno“.
- Razítka jen s důkazem v klíči důvodu skóre: `deadline_cut` → ZKRÁTILI, `pn_other_law` → PŘÍLEPEK, `p90_passed` → § 90/2. OBEŠLI, V NOCI, BEZ ROZPRAVY se nepoužívají.
- Férovost: když je postup legální, video to řekne (`ferove`, „jednací řád dovoluje…“). Když je lhůta delší než běžná, ukáže se to stejně poctivě.
- Konec videa: „Hodnotíme postup, ne politický obsah.“

