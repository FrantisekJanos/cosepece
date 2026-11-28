"""Relace, otisk IP a cookie (spec 11.1, 15, 4.2)."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import pytest

from hlidac_rag.chat import relace
from hlidac_rag.chat.relace import (
    BETA_PLATNOST,
    cookie_sid,
    ip_z_hlavicky,
    otisk_ip,
    over_beta,
    podepis_beta,
)

SUL = b"testovaci-sul-jen-pro-testy-0001"
DEN = date(2026, 11, 28)


# --- otisk IP ---

def test_otisk_ma_32_hex_znaku():
    o = otisk_ip("203.0.113.7", SUL, DEN)
    assert re.fullmatch(r"[0-9a-f]{32}", o)


def test_ipv4_se_pocita_po_adrese():
    assert otisk_ip("203.0.113.7", SUL, DEN) == otisk_ip("203.0.113.7", SUL, DEN)
    assert otisk_ip("203.0.113.7", SUL, DEN) != otisk_ip("203.0.113.8", SUL, DEN)


def test_ipv6_se_pocita_po_64():
    a = otisk_ip("2001:db8:1:2:aaaa::1", SUL, DEN)
    b = otisk_ip("2001:db8:1:2:ffff:ffff:ffff:ffff", SUL, DEN)
    jina_64 = otisk_ip("2001:db8:1:3::1", SUL, DEN)
    assert a == b
    assert a != jina_64


def test_ipv6_zapis_nehraje_roli():
    assert otisk_ip("2001:DB8:0:0::1", SUL, DEN) == otisk_ip("2001:db8::5", SUL, DEN)


def test_ipv4_mapovana_v_ipv6_je_ipv4():
    """`::ffff:a.b.c.d` je tatáž IPv4; jinak by všechny IPv4 v jedné /64 sdílely otisk."""
    assert otisk_ip("::ffff:203.0.113.7", SUL, DEN) == otisk_ip("203.0.113.7", SUL, DEN)
    assert otisk_ip("::ffff:203.0.113.7", SUL, DEN) != otisk_ip("::ffff:203.0.113.8", SUL, DEN)


def test_otisk_se_meni_se_dnem_a_soli():
    o = otisk_ip("203.0.113.7", SUL, DEN)
    assert o != otisk_ip("203.0.113.7", SUL, DEN + timedelta(days=1))
    assert o != otisk_ip("203.0.113.7", b"jina-sul-jen-pro-testy-000000002", DEN)


@pytest.mark.parametrize("spatna", ["", "neni-ip", "1.2.3", "300.1.1.1", "1.2.3.4, 5.6.7.8"])
def test_otisk_odmitne_neplatnou_ip(spatna):
    with pytest.raises(ValueError):
        otisk_ip(spatna, SUL, DEN)


def test_otisk_odmitne_prazdnou_sul():
    with pytest.raises(ValueError):
        otisk_ip("203.0.113.7", b"", DEN)


# --- IP z hlavičky ---

def test_ip_z_hlavicky_vrati_adresu_od_caddy():
    assert ip_z_hlavicky("203.0.113.7") == "203.0.113.7"
    assert ip_z_hlavicky(" 2001:db8::1 ") == "2001:db8::1"


@pytest.mark.parametrize("hodnota", [None, "", "   ", "neni-ip", "unknown"])
def test_chybejici_nebo_neplatna_hlavicka_je_chyba(hodnota):
    """Bez adresy od Caddy služba IP nehádá (žádný fallback na adresu spojení ani sdílený koš)."""
    with pytest.raises(relace.ChybiIP):
        ip_z_hlavicky(hodnota)


def test_retezec_adres_se_odmitne():
    """Caddy hlavičku přepisuje jedinou adresou z `CF-Connecting-IP`. Víc adres znamená, že se do ní dostala
    hodnota od klienta (změněný Caddyfile): první adresu by si klient zvolil sám."""
    with pytest.raises(relace.ChybiIP):
        ip_z_hlavicky("1.1.1.1, 203.0.113.7")


def test_chybiip_je_valueerror():
    assert issubclass(relace.ChybiIP, ValueError)


def test_nazev_hlavicky():
    assert relace.HLAVICKA_IP == "x-forwarded-for"


# --- cookie relace ---

def test_cookie_ma_strict_httponly_secure():
    sid = "a" * 32
    c = cookie_sid(sid)
    assert c == f"chat_sid={sid}; Path=/chat-api; HttpOnly; Secure; SameSite=Strict; Max-Age=2592000"


@pytest.mark.parametrize("spatne", ["", "abc", "a" * 31, "A" * 32, "a" * 31 + ";", "a" * 32 + "\r\nX: y"])
def test_cookie_odmitne_neplatne_id(spatne):
    """Do hlavičky Set-Cookie jde jen id ve tvaru z `zaloz_relaci` (32 hex), nic jiného."""
    with pytest.raises(ValueError):
        cookie_sid(spatne)


def test_platne_sid():
    assert relace.platne_sid("0123456789abcdef" * 2)
    assert not relace.platne_sid(None)
    assert not relace.platne_sid("x" * 32)


# --- cookie chat_beta ---

TAJ = b"beta-tajemstvi-jen-pro-testy-001"
TED = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)


def test_beta_cookie_projde():
    h = podepis_beta(TAJ, TED)
    assert over_beta(h, TAJ, TED)
    assert over_beta(h, TAJ, TED + BETA_PLATNOST - timedelta(seconds=1))


def test_beta_cookie_podvrh_neprojde():
    h = podepis_beta(TAJ, TED)
    assert not over_beta(h, b"jine-tajemstvi-jen-pro-testy-002", TED)
    assert not over_beta("x", TAJ, TED)
    assert not over_beta("", TAJ, TED)
    assert not over_beta(None, TAJ, TED)
    # změněná platnost s původním podpisem
    verze, do, podpis = h.split(".")
    assert not over_beta(f"{verze}.{int(do) + 10**6}.{podpis}", TAJ, TED)
    # změněný podpis
    jiny = ("0" if podpis[0] != "0" else "1") + podpis[1:]
    assert not over_beta(f"{verze}.{do}.{jiny}", TAJ, TED)
    assert not over_beta(h + "x", TAJ, TED)


def test_beta_cookie_vyprsi():
    h = podepis_beta(TAJ, TED)
    assert not over_beta(h, TAJ, TED + BETA_PLATNOST)


def test_beta_s_prazdnym_tajemstvim_nejde():
    with pytest.raises(ValueError):
        podepis_beta(b"", TED)
    assert not over_beta(podepis_beta(TAJ, TED), b"", TED)


def test_beta_bez_ted_pouzije_hodiny():
    assert over_beta(podepis_beta(TAJ), TAJ)
