"""Krok embeddingů v run.py bez databáze a bez API."""
from __future__ import annotations

from hlidac_rag import run


def test_bez_klice_preskoci(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert run.krok_embeddingy(object(), 1000) == {"ok": False, "duvod": "bez OPENAI_API_KEY"}


def test_predava_strop_a_vysledek(monkeypatch):
    volano = {}

    def falesny_indexuj(conn, klient, max_tokenu):
        volano.update(conn=conn, klient=klient, max_tokenu=max_tokenu)
        return {"zdroju": 2, "useku": 3}
    monkeypatch.setattr(run, "_klient_z_prostredi", lambda: "klient")
    monkeypatch.setattr(run, "indexuj", falesny_indexuj)
    r = run.krok_embeddingy("conn", 1234)
    assert r == {"ok": True, "zdroju": 2, "useku": 3}
    assert volano == {"conn": "conn", "klient": "klient", "max_tokenu": 1234}


def test_chyba_indexeru_je_ok_false(monkeypatch):
    def falesny_indexuj(conn, klient, max_tokenu):
        return {"zdroju": 0, "chyba": "OpenAI vrátilo HTTP 500"}
    monkeypatch.setattr(run, "_klient_z_prostredi", lambda: "klient")
    monkeypatch.setattr(run, "indexuj", falesny_indexuj)
    assert run.krok_embeddingy("conn", 1)["ok"] is False


def test_vychozi_strop_z_prostredi(monkeypatch):
    monkeypatch.setenv("EMBED_MAX_TOKENS_PER_RUN", "500")
    assert run._vychozi_strop() == 500
    monkeypatch.delenv("EMBED_MAX_TOKENS_PER_RUN")
    assert run._vychozi_strop() == 3_000_000



def test_strop_nula_neni_vychozi(monkeypatch):
    monkeypatch.delenv("EMBED_MAX_TOKENS_PER_RUN", raising=False)
    ap_args = run._parser().parse_args(["--jen-embeddingy", "--embed-max-tokenu", "0"])
    assert run._strop(ap_args) == 0
    assert run._strop(run._parser().parse_args([])) == run._vychozi_strop()
