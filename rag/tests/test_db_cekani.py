"""Čekání na databázi při startu běhu na pozadí (db.connect_wait)."""
import psycopg
import pytest

from hlidac_rag import db


def _spojeni(monkeypatch, selhani: int) -> list[int]:
    """Podstrčí psycopg.connect, které prvních `selhani` pokusů odmítne; vrací počítadlo pokusů."""
    pokusy = [0]

    def connect(*args, **kwargs):
        pokusy[0] += 1
        if pokusy[0] <= selhani:
            raise psycopg.OperationalError("connection refused")
        return "spojeni"

    monkeypatch.setattr(psycopg, "connect", connect)
    return pokusy


def test_ceka_dokud_databaze_neodpovi(monkeypatch):
    pokusy = _spojeni(monkeypatch, selhani=2)
    assert db.connect_wait(timeout=5, step=0) == "spojeni"
    assert pokusy[0] == 3


def test_po_vyprseni_casu_vyhodi_puvodni_chybu(monkeypatch):
    pokusy = _spojeni(monkeypatch, selhani=10)
    with pytest.raises(psycopg.OperationalError):
        db.connect_wait(timeout=0, step=0)
    assert pokusy[0] == 1


def test_connect_neceka(monkeypatch):
    pokusy = _spojeni(monkeypatch, selhani=1)
    with pytest.raises(psycopg.OperationalError):
        db.connect()
    assert pokusy[0] == 1
