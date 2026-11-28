import collections
import json
from pathlib import Path

import pytest

from eval.chat.kontroly import ZAKAZANE_ODVOLANI, nacti_scenare, zkontroluj

SCENARE = Path(__file__).resolve().parents[1] / "eval" / "chat" / "scenare.json"


def test_must_bez_diakritiky_a_velikosti():
    v = zkontroluj({"must": ["zamítnut"]}, "Návrh byl ZAMITNUT [3].", [], 0.0)
    assert v.ok


def test_must_not_a_re():
    v = zkontroluj({"must_not": ["volte"], "must_re": [r"\[\d+\]"]}, "Volte stranu X.", [], 0.0)
    assert not v.ok and len(v.selhani) == 2


def test_cituje_a_nastroje_a_cena():
    t = {"cituje": True, "nastroje_any": ["get_pn", "pn_k_tisku"], "max_cena_usd": 0.06}
    assert zkontroluj(t, "Ano [2].", ["get_pn"], 0.05).ok
    assert zkontroluj(t, "Ano.", ["get_tisk"], 0.07).selhani == ["cituje", "nastroje_any", "max_cena_usd"]


def test_odvolani_je_zakazane_v_kazdem_tahu():
    assert not zkontroluj({}, "Omlouvám se, v předchozí odpovědi jsem to uvedl bez opory.", [], 0.0).ok


def test_sada_je_platna_a_uplna():
    s = nacti_scenare(SCENARE)
    sady = collections.Counter(x["sada"] for x in s)
    assert len(s) >= 45 and len({x["id"] for x in s}) == len(s)
    assert sady["zaklad"] >= 25 and all(sady[k] >= 3 for k in
        ["kontext", "pamet", "poctivost", "citace", "injekce", "neutralita"]) and sady["naklady"] >= 1
    assert all(x["rucne"].strip() for x in s)


# Doplňující testy chování kontrol a validace

def test_cituje_false_znamena_bez_odkazu():
    assert zkontroluj({"cituje": False}, "Obecně platí, že…", [], 0.0).ok
    assert zkontroluj({"cituje": False}, "Podle zdroje [1].", [], 0.0).selhani == ["cituje"]
    assert zkontroluj({"cituje": None}, "Podle zdroje [1].", [], 0.0).ok


def test_oprava_uzivatele_neni_odvolani():
    # „bez opory“ samo o sobě smí zaznít, když bot opravuje tvrzení uživatele
    assert zkontroluj({}, "Tvrzení, že návrh prošel, je bez opory v datech [2].", [], 0.0).ok
    assert all(f == f.lower() for f in ZAKAZANE_ODVOLANI)


def test_must_re_bez_diakritiky_a_nastroje_s_predponou_mcp():
    assert zkontroluj({"must_re": [r"zamitnut"]}, "Byl zamítnut.", [], 0.0).ok
    assert zkontroluj({"nastroje_any": ["get_pn"]}, "x", ["mcp__cosepece__get_pn"], 0.0).ok


def test_poradi_selhani_a_popis():
    v = zkontroluj({"must": ["prijat"], "must_not": ["zamitnut"]}, "Návrh byl zamítnut. Beru zpět.", [], 0.0)
    assert v.selhani == ["must: prijat", "must_not: zamitnut", "odvolani: beru zpět"]


def _zapis(tmp_path, data):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


ZAKLAD = {"id": "X1", "sada": "zaklad", "kontext": None, "tahy": [{"text": "Otázka?"}], "rucne": "zdroj"}


def test_validace_prijme_nepovinne_klice(tmp_path):
    s = dict(ZAKLAD, jen_endpoint=True, kour=True, kontext={"typ": "tisk", "ct": 221})
    assert nacti_scenare(_zapis(tmp_path, [s]))[0]["kour"] is True


@pytest.mark.parametrize("zmena, hlaska", [
    ({"sada": "jina"}, "sada"),
    ({"rucne": " "}, "rucne"),
    ({"tahy": []}, "tahy"),
    ({"tahy": [{"text": "x", "must": "ne-seznam"}]}, "must"),
    ({"tahy": [{"text": "x", "must_re": ["("]}]}, "must_re"),
    ({"tahy": [{"text": "x", "cituje": "ano"}]}, "cituje"),
    ({"tahy": [{"text": "x", "max_cena_usd": "0.06"}]}, "max_cena_usd"),
    ({"tahy": [{"text": "x", "neznamy": 1}]}, "neznamy"),
    ({"kontext": {"ct": 221}}, "typ"),
    ({"kour": "ano"}, "kour"),
    ({"navic": 1}, "navic"),
])
def test_validace_odmitne_chybny_format(tmp_path, zmena, hlaska):
    with pytest.raises(ValueError, match=hlaska):
        nacti_scenare(_zapis(tmp_path, [dict(ZAKLAD, **zmena)]))


def test_validace_odmitne_duplicitni_id(tmp_path):
    with pytest.raises(ValueError, match="X1"):
        nacti_scenare(_zapis(tmp_path, [ZAKLAD, ZAKLAD]))
