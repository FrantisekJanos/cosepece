"""Relace, otisk IP a cookie chatbota (spec 11.1, 15, 4.2).

IP klienta se čte jen z hlavičky `X-Forwarded-For`, kterou Caddy přepíše jedinou adresou z `CF-Connecting-IP`
(`header_up X-Forwarded-For {http.request.header.CF-Connecting-IP}`, blok `/chat-api/*` za matcherem `@cf`).
Hodnotu od klienta Caddy nepropustí, protože hlavičku nastavuje, ne doplňuje. Chybí-li adresa, je neplatná nebo
jich je víc (Caddyfile se změnil a do hlavičky se dostala hodnota od klienta), `ip_z_hlavicky` vyhodí `ChybiIP`
a služba požadavek odmítne. IP se nehádá z adresy spojení (to je vždy Caddy) a nesdílí se žádný „neznámý“ koš.

Otisk IP: HMAC-SHA256 s náhodnou solí pražského dne (`chat.sul_dne`, `uloziste.sul_dne`), zkrácený na 32 hex
znaků. IPv4 po adrese, IPv6 po síti /64, IPv4 zapsaná jako `::ffff:a.b.c.d` jako IPv4. Sůl vzniká v DB při prvním
použití dne a úklid ji po 48 h smaže; pak otisk nejde přepočítat ani výčtem všech IPv4 (statická sůl v `.env`
by to dovolila zpětně pro libovolný den). Čitelná IP se nikam neukládá.

Cookie `chat_sid` nese náhodné id relace z `uloziste.zaloz_relaci` (32 hex). Podepsaná není: id je náhodné
(128 bitů) a samo nic neopravňuje, relaci server dohledá v DB. Cookie `chat_beta` (skrytý režim) podepsaná je,
protože ji server nikde neeviduje: `v1.<platnost do, unix s>.<HMAC-SHA256>`, klíč `CHAT_BETA_KLIC` (jen pro ni).
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from . import uloziste

HLAVICKA_IP = "x-forwarded-for"   # nastavuje Caddy z CF-Connecting-IP, nic jiného se nečte
COOKIE_SID = "chat_sid"
COOKIE_BETA = "chat_beta"
SID_PLATNOST_S = 30 * 24 * 3600
BETA_PLATNOST = timedelta(days=30)
_BETA_VERZE = "v1"
_SID = re.compile(r"[0-9a-f]{32}")
_PODPIS = re.compile(r"[0-9a-f]{64}")


class ChybiIP(ValueError):
    """Hlavička s IP od Caddy chybí nebo nemá tvar jedné platné adresy."""


def ip_z_hlavicky(xff: str | None) -> str:
    """IP klienta z hlavičky `X-Forwarded-For`, kterou nastavuje Caddy. Jinak `ChybiIP`.

    Přijme jen jedinou platnou adresu (Caddy hlavičku přepisuje). Řetězec víc adres se odmítne, protože jeho
    první prvek by mohl pocházet od klienta."""
    if xff is None:
        raise ChybiIP("chybí hlavička s IP")
    hodnota = xff.strip()
    if not hodnota or "," in hodnota or "%" in hodnota:
        raise ChybiIP("hlavička s IP nemá tvar jedné adresy")
    try:
        return str(ipaddress.ip_address(hodnota))
    except ValueError:
        raise ChybiIP("hlavička s IP nemá platnou adresu") from None


def _sit(ip: str) -> str:
    """Klíč pro limit: IPv4 adresa, IPv6 síť /64. Adresa se zónou (`fe80::1%eth0`) je ValueError."""
    if "%" in ip:
        raise ValueError("adresa se zónou")
    a = ipaddress.ip_address(ip.strip())
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped is not None:
        a = a.ipv4_mapped
    if isinstance(a, ipaddress.IPv4Address):
        return str(a)
    return str(ipaddress.IPv6Network((a, 64), strict=False))


def den_prahy(ted: datetime) -> date:
    """Pražský den, ke kterému patří sůl otisku (stejný den jako limity útraty)."""
    if ted.tzinfo is None:
        raise ValueError("ted musí mít časové pásmo")
    return ted.astimezone(ZoneInfo(uloziste.TZ)).date()


def otisk_se_soli(ip: str, sul: bytes) -> str:
    """HMAC-SHA256(sůl, adresa nebo síť /64), 32 hex znaků. Neplatná IP nebo prázdná sůl: ValueError."""
    if not sul:
        raise ValueError("prázdná sůl")
    return hmac.new(sul, _sit(ip).encode(), hashlib.sha256).hexdigest()[:32]


def otisk_ip(conn: psycopg.Connection, ip: str, den: date) -> str:
    """Otisk IP se solí dne `den` z DB (vznikne při prvním použití dne). Neplatná IP: ValueError."""
    _sit(ip)   # neplatná adresa nezaloží sůl
    return otisk_se_soli(ip, uloziste.sul_dne(conn, den))


def platne_sid(sid: str | None) -> bool:
    """Má id relace tvar z `zaloz_relaci` (32 malých hex znaků)?"""
    return isinstance(sid, str) and _SID.fullmatch(sid) is not None


def cookie_sid(id: str) -> str:
    """Hodnota hlavičky Set-Cookie pro relaci; id jiného tvaru než 32 hex je ValueError (žádná injekce hlavičky)."""
    if not platne_sid(id):
        raise ValueError("neplatné id relace")
    return f"{COOKIE_SID}={id}; Path=/chat-api; HttpOnly; Secure; SameSite=Strict; Max-Age={SID_PLATNOST_S}"


def _podpis_beta(do: int, tajemstvi: bytes) -> str:
    return hmac.new(tajemstvi, f"{COOKIE_BETA}|{_BETA_VERZE}|{do}".encode(), hashlib.sha256).hexdigest()


def _unix(ted: datetime | None) -> int:
    if ted is None:
        return int(time.time())
    if ted.tzinfo is None:
        raise ValueError("ted musí mít časové pásmo")
    return int(ted.timestamp())


def podepis_beta(tajemstvi: bytes, ted: datetime | None = None) -> str:
    """Hodnota cookie `chat_beta` platná `BETA_PLATNOST` od `ted` (výchozí teď)."""
    if not tajemstvi:
        raise ValueError("prázdné tajemství")
    do = _unix(ted) + int(BETA_PLATNOST.total_seconds())
    return f"{_BETA_VERZE}.{do}.{_podpis_beta(do, tajemstvi)}"


def over_beta(hodnota: str | None, tajemstvi: bytes, ted: datetime | None = None) -> bool:
    """Platí cookie `chat_beta`? Podpis se porovnává v konstantním čase, prošlá nebo podvržená neplatí."""
    if not tajemstvi or not isinstance(hodnota, str):
        return False
    casti = hodnota.split(".")
    if (len(casti) != 3 or casti[0] != _BETA_VERZE or not (casti[1].isascii() and casti[1].isdigit())
            or len(casti[1]) > 12 or _PODPIS.fullmatch(casti[2]) is None):
        return False
    do = int(casti[1])
    if not hmac.compare_digest(casti[2], _podpis_beta(do, tajemstvi)):
        return False
    return _unix(ted) < do
