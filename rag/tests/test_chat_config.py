import pytest

from hlidac_rag.chat.cenik import cena_usd, nacti_cenik
from hlidac_rag.chat.config import nacti


def test_vychozi_je_sonnet_za_dolar():
    n = nacti({})
    assert (n.model, n.effort, n.rozpocet_den, n.strop_tahu, n.strop_relace, n.strop_ip) == \
        ("claude-sonnet-5-5", "low", 2.00, 0.06, 0.25, 0.50)
    assert (n.podil_hodina, n.max_kol, n.rozpocet_vysledku_tokenu, n.max_tokenu_odpovedi) == (0.25, 3, 12000, 1200)
    assert (n.historie_max_tokenu, n.max_tahu_konverzace, n.max_znaku_zpravy, n.rozpocet_mesic) == (6000, 6, 500, 60.0)
    assert (n.zprav_relace_min, n.zprav_relace_den, n.karta_jako) == (3, 10, "tool_result")
    assert (n.zapnuto, n.rezim, n.model_nouzovy, n.turnstile_zprav, n.turnstile_hodin) == (False, "skryte", "", 5, 6.0)


def test_profil_haiku_se_vybere_podle_modelu():
    n = nacti({"CHAT_MODEL": "claude-haiku-5-5"})
    assert (n.strop_tahu, n.strop_relace, n.strop_ip, n.podil_hodina) == (0.02, 0.06, 0.15, 0.15)
    assert (n.max_kol, n.rozpocet_vysledku_tokenu, n.historie_max_tokenu, n.turnstile_zprav) == (5, 40000, 20000, 10)


def test_env_prebije_profil():
    n = nacti({"CHAT_MODEL": "claude-haiku-5-5", "CHAT_ROZPOCET_DEN_USD": "5", "CHAT_STROP_RELACE_USD": "0.08"})
    assert (n.rozpocet_den, n.strop_relace, n.strop_tahu) == (5.0, 0.08, 0.02)


def test_neplatna_hodnota_selze_pri_startu():
    with pytest.raises(ValueError, match="CHAT_MAX_KOL"):
        nacti({"CHAT_MAX_KOL": "hodne"})


def test_neznamy_model_bere_profil_sonnetu():
    n = nacti({"CHAT_MODEL": "claude-neco"})
    assert (n.model, n.strop_tahu, n.max_kol) == ("claude-neco", 0.06, 3)


def test_zapnuto_a_enumy():
    assert nacti({"CHAT_ZAPNUTO": "1", "CHAT_REZIM": "verejne", "CHAT_KARTA_JAKO": "blok"}).zapnuto is True
    with pytest.raises(ValueError, match="CHAT_REZIM"):
        nacti({"CHAT_REZIM": "tajne"})
    with pytest.raises(ValueError, match="CHAT_ZAPNUTO"):
        nacti({"CHAT_ZAPNUTO": "mozna"})


@pytest.mark.parametrize("hodnota", ["nan", "inf", "-inf", "-0.01"])
def test_float_odmitne_nan_inf_a_zaporne(hodnota):
    for jmeno in ("CHAT_ROZPOCET_DEN_USD", "CHAT_STROP_TAHU_USD", "CHAT_PODIL_HODINA", "CHAT_TURNSTILE_HODIN"):
        with pytest.raises(ValueError, match=jmeno):
            nacti({jmeno: hodnota})


def test_pocty_kol_a_tahu_alespon_jedna_ostatni_int_nezaporne():
    for jmeno in ("CHAT_MAX_KOL", "CHAT_MAX_TAHU_KONVERZACE"):
        with pytest.raises(ValueError, match=jmeno):
            nacti({jmeno: "0"})
    with pytest.raises(ValueError, match="CHAT_ZPRAV_RELACE_DEN"):
        nacti({"CHAT_ZPRAV_RELACE_DEN": "-1"})
    assert nacti({"CHAT_MAX_KOL": "1", "CHAT_STROP_TAHU_USD": "0"}).max_kol == 1


def test_effort_jen_ze_seznamu():
    assert nacti({"CHAT_EFFORT": "xhigh"}).effort == "xhigh"
    with pytest.raises(ValueError, match="CHAT_EFFORT"):
        nacti({"CHAT_EFFORT": "vysoky"})


def test_hlaska_boolu_vypisuje_prijimane_hodnoty():
    with pytest.raises(ValueError) as e:
        nacti({"CHAT_ZAPNUTO": "mozna"})
    zprava = str(e.value)
    assert "CHAT_ZAPNUTO" in zprava
    assert all(h in zprava for h in ("1", "true", "ano", "0", "false", "ne", "off"))


def test_cena_sonnet():
    assert cena_usd("claude-sonnet-5-5", vstup=1000, vystup=100) == pytest.approx(0.003)
    assert cena_usd("claude-sonnet-5-5", 0, 0, cache_cteni=10000, cache_zapis=4000) == pytest.approx(0.011)


def test_cena_haiku_a_neznamy_model():
    assert cena_usd("claude-haiku-5-5", 10000, 1000) == pytest.approx(0.0015)
    with pytest.raises(KeyError):
        cena_usd("claude-neco", 1, 1)


def test_cenik_z_env():
    c = nacti_cenik({"CHAT_CENIK": '{"claude-haiku-5-5": [0.2, 1.0, 0.02, 0.25]}'})
    assert cena_usd("claude-haiku-5-5", 1_000_000, 0, cenik=c) == pytest.approx(0.2)


def test_neplatny_cenik_selze():
    with pytest.raises(ValueError, match="CHAT_CENIK"):
        nacti_cenik({"CHAT_CENIK": "{nejson"})
