"""Kontroly rozpočtu a výběr modelu (spec 10.2, 11.1, 11.3)."""
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from hlidac_rag.chat.cenik import CENY
from hlidac_rag.chat.config import nacti
from hlidac_rag.chat.rozpocet import (
    over_modely, prisny_rezim, vyber_model, zbyva_otazek, zkontroluj, znovu_v,
)
from hlidac_rag.chat.uloziste import Utrata

N = nacti({"CHAT_ZAPNUTO": "1"})
PRAHA = ZoneInfo("Europe/Prague")


def U(**kw):
    zaklad = dict(den=0.0, hodina=0.0, mesic=0.0, relace=0.0, ip=0.0, zprav_min=0, zprav_den=0)
    return Utrata(**{**zaklad, **kw})


@pytest.mark.parametrize("u, kod", [
    (U(den=1.94), None), (U(den=1.95), "limit_den"),            # den tvrdě: útrata + 0,06 ≤ 2,00
    (U(hodina=0.44), None), (U(hodina=0.45), "limit_hodina"),   # 25 % dne ($0,50) za 60 min
    (U(relace=0.24), None), (U(relace=0.25), "limit_relace"),   # relace: útrata < strop
    (U(ip=0.49), None), (U(ip=0.50), "limit_ip"),
    (U(mesic=59.95), "limit_den"), (U(zprav_min=3), "limit_relace"), (U(zprav_den=10), "limit_relace"),
])
def test_hranice(u, kod):
    assert zkontroluj(N, u) == kod


def test_poradi_vyhodnoceni():
    u = U(den=1.95, hodina=0.45, relace=0.25, ip=0.5)
    assert zkontroluj(N, u) == "limit_den"
    assert zkontroluj(N, U(hodina=0.45, relace=0.25, ip=0.5)) == "limit_hodina"
    assert zkontroluj(N, U(relace=0.25, ip=0.5)) == "limit_relace"


def test_vypnuto():
    assert zkontroluj(replace(N, zapnuto=False), U()) == "vypnuto"


def test_vyssi_rozpocet_jen_z_konfigurace():
    n5 = nacti({"CHAT_ZAPNUTO": "1", "CHAT_ROZPOCET_DEN_USD": "5", "CHAT_PODIL_HODINA": "0.2",
                "CHAT_STROP_TAHU_USD": "0.10"})
    assert zkontroluj(n5, U(den=0.95, hodina=0.89)) is None
    assert zkontroluj(n5, U(hodina=0.91)) == "limit_hodina"


def test_nouzovy_model():
    n = replace(N, model_nouzovy="claude-haiku-5-5", prah_nouze=0.6)
    assert vyber_model(n, U(den=1.19)) == "claude-sonnet-5-5"
    assert vyber_model(n, U(den=1.21)) == "claude-haiku-5-5"
    assert vyber_model(N, U(den=1.99)) == "claude-sonnet-5-5"   # bez nouzového modelu


def test_zbyva_otazek():
    assert zbyva_otazek(N, U(relace=0.17)) == 2   # (0,25 − 0,17) / 0,04, dolů
    assert zbyva_otazek(N, U(relace=0.30)) == 0


def test_prisny_rezim():
    assert prisny_rezim(N, U(den=1.01)) and prisny_rezim(N, U(ip=0.26)) and not prisny_rezim(N, U(den=0.8))


def test_znovu_v():
    ted = datetime(2026, 11, 28, 10, 30, tzinfo=PRAHA)
    assert znovu_v(N, U(), ted) is None
    assert znovu_v(N, U(hodina=0.45), ted) == ted + timedelta(minutes=60)
    pulnoc = datetime(2026, 11, 29, 0, 0, tzinfo=PRAHA)
    for u in (U(den=1.95), U(relace=0.25), U(ip=0.5), U(mesic=60)):
        assert znovu_v(N, u, ted) == pulnoc


def test_znovu_v_pulnoc_pri_zmene_casu():
    ted = datetime(2026, 10, 24, 23, 0, tzinfo=PRAHA)   # další den (25. 10.) má 25 hodin
    r = znovu_v(N, U(ip=0.5), ted)
    assert r == datetime(2026, 10, 25, 0, 0, tzinfo=PRAHA)


def test_znovu_v_vyzaduje_pasmo():
    with pytest.raises(ValueError):
        znovu_v(N, U(ip=0.5), datetime(2026, 11, 28, 10, 0))


def test_over_modely():
    over_modely(N, CENY)
    over_modely(replace(N, model_nouzovy="claude-haiku-5-5"), CENY)
    with pytest.raises(ValueError, match="CHAT_MODEL"):
        over_modely(replace(N, model="claude-neznamy"), CENY)
    with pytest.raises(ValueError, match="CHAT_MODEL_NOUZOVY"):
        over_modely(replace(N, model_nouzovy="claude-neznamy"), CENY)
