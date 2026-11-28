"""Relace, otisk IP a cookie (spec 11.1, 15, 4.2)."""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone

import pytest

from hlidac_rag.chat import relace
from hlidac_rag.chat.relace import (
    BETA_PLATNOST,
    cookie_sid,
    ip_z_hlavicky,
    otisk_ip,
    otisk_se_soli,
    over_beta,
    podepis_beta,
)

SUL = b"testovaci-sul-jen-pro-testy-0001"
DEN = date(2026, 11, 28)


# --- otisk IP (čistý výpočet se solí) ---

def test_otisk_ma_32_hex_znaku():
    o = otisk_se_soli("203.0.113.7", SUL)
    assert re.fullmatch(r"[0-9a-f]{32}", o)


def test_ipv4_se_pocita_po_adrese():
    assert otisk_se_soli("203.0.113.7", SUL) == otisk_se_soli("203.0.113.7", SUL)
    assert otisk_se_soli("203.0.113.7", SUL) != otisk_se_soli("203.0.113.8", SUL)


def test_ipv6_se_pocita_po_64():
    a = otisk_se_soli("2001:db8:1:2:aaaa::1", SUL)
    b = otisk_se_soli("2001:db8:1:2:ffff:ffff:ffff:ffff", SUL)
    jina_64 = otisk_se_soli("2001:db8:1:3::1", SUL)
    assert a == b
    assert a != jina_64


def test_ipv6_zapis_nehraje_roli():
    assert otisk_se_soli("2001:DB8:0:0::1", SUL) == otisk_se_soli("2001:db8::5", SUL)


def test_ipv4_mapovana_v_ipv6_je_ipv4():
    """`::ffff:a.b.c.d` je tatáž IPv4; jinak by všechny IPv4 v jedné /64 sdílely otisk."""
    assert otisk_se_soli("::ffff:203.0.113.7", SUL) == otisk_se_soli("203.0.113.7", SUL)
    assert otisk_se_soli("::ffff:203.0.113.7", SUL) != otisk_se_soli("::ffff:203.0.113.8", SUL)


def test_otisk_se_meni_se_soli():
    assert otisk_se_soli("203.0.113.7", SUL) != otisk_se_soli("203.0.113.7", b"jina-sul-jen-pro-testy-000000002")


@pytest.mark.parametrize("spatna", ["", "neni-ip", "1.2.3", "300.1.1.1", "1.2.3.4, 5.6.7.8", "fe80::1%eth0"])
def test_otisk_odmitne_neplatnou_ip(spatna):
    with pytest.raises(ValueError):
        otisk_se_soli(spatna, SUL)


def test_otisk_odmitne_prazdnou_sul():
    with pytest.raises(ValueError):
        otisk_se_soli("203.0.113.7", b"")


def test_den_prahy():
    assert relace.den_prahy(datetime(2026, 11, 28, 23, 30, tzinfo=timezone.utc)) == date(2026, 11, 29)
    with pytest.raises(ValueError):
        relace.den_prahy(datetime(2026, 11, 28, 12, 0))


# --- otisk IP se solí dne z DB ---

D1 = date(2031, 6, 15)


@pytest.fixture
def conn():
    from hlidac_rag import db

    c = db.connect()
    db.migrate(c)
    c.autocommit = True
    c.execute("DELETE FROM chat.sul_dne WHERE den >= '2031-01-01'")
    yield c
    c.execute("DELETE FROM chat.sul_dne WHERE den >= '2031-01-01'")
    c.close()


@pytest.mark.pg
@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)")
def test_otisk_se_meni_se_dnem_a_soli(conn):
    """Dvě relace téhož dne dají stejný otisk, jiný den jiný; sůl dne je náhodná, ne odvozená z data."""
    from hlidac_rag import db

    druhe = db.connect()
    druhe.autocommit = True
    try:
        a = otisk_ip(conn, "203.0.113.7", D1)
        assert re.fullmatch(r"[0-9a-f]{32}", a)
        assert otisk_ip(druhe, "203.0.113.7", D1) == a
        assert otisk_ip(conn, "203.0.113.7", D1 + timedelta(days=1)) != a
        sul = conn.execute("SELECT sul FROM chat.sul_dne WHERE den = %s", (D1,)).fetchone()[0]
        assert len(bytes(sul)) == 32
        assert otisk_se_soli("203.0.113.7", bytes(sul)) == a
        # jiná sůl téhož dne (po smazání) dá jiný otisk: z data se nic nepřepočítá
        conn.execute("DELETE FROM chat.sul_dne WHERE den = %s", (D1,))
        assert otisk_ip(conn, "203.0.113.7", D1) != a
    finally:
        druhe.close()


@pytest.mark.pg
@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)")
def test_neplatna_ip_nezalozi_sul(conn):
    with pytest.raises(ValueError):
        otisk_ip(conn, "neni-ip", D1)
    assert conn.execute("SELECT count(*) FROM chat.sul_dne WHERE den = %s", (D1,)).fetchone()[0] == 0


@pytest.mark.pg
@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="bez DATABASE_URL (PostgreSQL)")
def test_uklid_smaze_sul_po_48_h(conn):
    from zoneinfo import ZoneInfo

    from hlidac_rag.chat import uloziste

    ted = datetime.now(ZoneInfo("Europe/Prague"))
    dnes = ted.date()
    for d in range(4):
        uloziste.sul_dne(conn, dnes - timedelta(days=d))
    vysledek = uloziste.uklid(conn, ted)
    zbyle = {r[0] for r in conn.execute("SELECT den FROM chat.sul_dne WHERE den >= %s", (dnes - timedelta(days=3),))}
    assert zbyle == {dnes, dnes - timedelta(days=1)}
    assert vysledek["soli"] >= 2


# --- IP z hlavičky ---

def test_ip_z_hlavicky_vrati_adresu_od_caddy():
    assert ip_z_hlavicky("203.0.113.7") == "203.0.113.7"
    assert ip_z_hlavicky(" 2001:db8::1 ") == "2001:db8::1"


@pytest.mark.parametrize("hodnota", [None, "", "   ", "neni-ip", "unknown", "fe80::1%eth0", "fe80::1%25eth0"])
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
    # ne-ASCII v podpisu (compare_digest by vyhodil TypeError) a horní index místo číslice (int() ValueError)
    assert not over_beta(f"{verze}.{do}.é" + podpis[1:], TAJ, TED)
    assert not over_beta("v1.123.é" + "0" * 63, TAJ, TED)
    assert not over_beta("v1.².abc", TAJ, TED)
    assert not over_beta(f"{verze}.{do}.{podpis.upper()}", TAJ, TED)
    assert not over_beta(f"{verze}.{do}.{podpis[:-1]}", TAJ, TED)


def test_beta_cookie_vyprsi():
    h = podepis_beta(TAJ, TED)
    assert not over_beta(h, TAJ, TED + BETA_PLATNOST)


def test_beta_s_prazdnym_tajemstvim_nejde():
    with pytest.raises(ValueError):
        podepis_beta(b"", TED)
    assert not over_beta(podepis_beta(TAJ, TED), b"", TED)


def test_beta_bez_ted_pouzije_hodiny():
    assert over_beta(podepis_beta(TAJ), TAJ)
