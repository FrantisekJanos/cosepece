Vyrábíš sadu podkladů pro krátká videa (Reels) webu cosepece.cz (Hlídač zákonů) k sněmovnímu tisku {ct}.
Pracovní složka je aktuální adresář. Zapisovat smíš jen do ní.

Vstupy v pracovní složce:
- `tisk.json`: číslo tisku, klíče důvodů skóre, formáty v3 této sady, případně zadané PN (`focus_pn`).
- `card_v2.json`: karta tisku z webu cosepece.cz.
- `podklady/`: návody pro varianty (`v2.md`, `v3.md`), pravidla obsahu (`pravidla.md`), popis formátů (`formaty.md`), lhůty jednacího řádu (`lhuty.md`), vzorové karty a vzorový popisek. Případně `publikovano.md`: popisky videí, která k tisku už vyšla.
- Případně `podklady/zadani.md`: video k vybraným pozměňovacím návrhům (PN). Když existuje, hlavní fakt, titulek a háček všech tří variant jsou o zadaných PN, tisk je jen kontext. U víc PN je to jedno video, které je porovná nebo shrne. Návody `v2.md` a `v3.md` to popisují.

Úkol: spusť najednou, v jedné odpovědi, tři subagenty nástrojem Agent. Každý dostane jen tento pokyn (doplň čísla):
{tasks}
Na subagenty počkej. Sám karty nepiš ani neopravuj. Na konci napiš jednu větu: které varianty jsou zapsané.
{repair}
