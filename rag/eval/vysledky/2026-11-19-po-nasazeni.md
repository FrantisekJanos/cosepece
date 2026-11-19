# Eval MCP 2026-11-19

| Otázka | Volání | Výsledek | ms | znaků | Detail |
|---|---|---|---|---|---|
| E01 | najdi_osobu | ok | 285 | 231 |  |
| E01 | projevy_recnika | ok | 84 | 189 |  |
| E01 | projevy_recnika | ok | 36 | 971 |  |
| E02 | tisk_podle_sbirky | ok | 31 | 279 |  |
| E02 | get_tisk | ok | 43 | 2926 |  |
| E03 | seznam_tisku | ok | 113 | 1619 |  |
| E03 | najdi_osobu | ok | 102 | 128 |  |
| E03 | projevy_recnika | ok | 39 | 1018 |  |
| E04 | get_tisk | ok | 91 | 2339 |  |
| E04 | hledej_v_projevech | ok | 53 | 1168 |  |
| E04 | get_projev | ok | 40 | 4589 |  |
| E05 | get_tisk | ok | 39 | 2935 |  |
| E05 | pn_k_tisku | ok | 44 | 1203 |  |
| E05 | get_projev | ok | 34 | 4970 |  |
| E06 | seznam_tisku | ok | 38 | 12858 |  |
| E08 | get_tisk | ok | 40 | 3365 |  |
| E08 | pn_k_tisku | ok | 38 | 10680 |  |
| E08 | get_pn | ok | 35 | 2019 |  |
| E09 | tisky_menici_zakon | ok | 44 | 2045 |  |
| E10 | tisky_menici_zakon | FAIL | 50 | 220 | tisky.*.ct: cesta v odpovědi neexistuje |
| E10 | hledej_v_textech | ok | 90 | 3778 |  |
| E11 | get_tisk | ok | 42 | 4773 |  |
| E11 | hledej_v_textech | FAIL | 45 | 2047 | (celá odpověď): neobsahuje 'přede dnem nabytí účinnosti' |
| E12 | get_tisk | ok | 35 | 3385 |  |
| E12 | osnova_textu | ok | 81 | 3916 |  |
| E12 | get_cel | ok | 37 | 1098 |  |
| E13 | get_tisk | ok | 39 | 4055 |  |
| E13 | pn_k_tisku | ok | 53 | 8935 |  |
| E14 | hledej_v_projevech | ok | 102 | 7806 |  |
| E15 | get_pn | ok | 77 | 2071 |  |
| E15 | osnova_textu | ok | 37 | 3757 |  |
| E15 | get_cel | ok | 44 | 4373 |  |
| E16 | get_pn | ok | 33 | 344 |  |
| E16 | osnova_textu | FAIL | 36 | 154 | je_chyba: očekáváno False, odpověď je isError |
| E17 | pn_k_tisku | ok | 36 | 3644 |  |
| E17 | hledej_v_textech | ok | 36 | 216 |  |
| E18 | hledej_v_projevech | ok | 40 | 873 |  |
| E18 | osnova_textu | ok | 221 | 15563 |  |
| E18 | osnova_textu | FAIL | 37 | 97 | je_chyba: očekáváno False, odpověď je isError |
| E20 | get_tisk | ok | 42 | 2354 |  |
| E20 | hledej_v_projevech | ok | 125 | 8037 |  |
| E20 | get_projev | ok | 38 | 1494 |  |
| E23 | zmeny_projevu | ok | 58 | 584 |  |
| E23 | zmeny_projevu | ok | 39 | 27 |  |
| E23 | zmeny_projevu | ok | 36 | 27 |  |
| E23 | stav_dat | ok | 82 | 669 |  |

Souhrn: ok: 42/46, medián znaků: 2046, max znaků: 15563

<!-- souhrn: {"ok": 42, "celkem": 46, "median_znaku": 2046, "max_znaku": 15563} -->
