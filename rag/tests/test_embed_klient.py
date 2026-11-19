"""Klient OpenAI embeddings nad falešným transportem (žádné skutečné volání API)."""
from __future__ import annotations

import json

import httpx2 as httpx
import pytest

from hlidac_rag.embed import klient as modul
from hlidac_rag.embed.klient import EmbedChyba, Klient

KLIC = "sk-test-123"


@pytest.fixture(autouse=True)
def bez_cekani(monkeypatch):
    monkeypatch.setattr(modul, "_spi", lambda s: None)


def _odpoved(vstupy: list[str], poradi=None) -> dict:
    data = [{"object": "embedding", "index": i, "embedding": [float(i), float(len(t))]} for i, t in enumerate(vstupy)]
    if poradi:
        data = [data[i] for i in poradi]
    return {"data": data, "model": "x", "usage": {"prompt_tokens": 7, "total_tokens": 7}}


def _klient(handler) -> tuple[Klient, list]:
    pozadavky = []

    def h(req: httpx.Request) -> httpx.Response:
        pozadavky.append(req)
        return handler(req, json.loads(req.content))
    return Klient(KLIC, transport=httpx.MockTransport(h)), pozadavky


def test_poradi_podle_index():
    k, _ = _klient(lambda req, body: httpx.Response(200, json=_odpoved(body["input"], poradi=[2, 0, 1])))
    vektory, tokenu = k.embed(["a", "bb", "ccc"])
    assert vektory == [[0.0, 1.0], [1.0, 2.0], [2.0, 3.0]] and tokenu == 7


def test_pozadavek_ma_model_dimenze_a_klic():
    k, poz = _klient(lambda req, body: httpx.Response(200, json=_odpoved(body["input"])))
    k.embed(["a"])
    body = json.loads(poz[0].content)
    assert body["model"] == "text-embedding-3-large" and body["dimensions"] == 1024 and body["input"] == ["a"]
    assert poz[0].headers["authorization"] == f"Bearer {KLIC}"
    assert str(poz[0].url) == "https://api.openai.com/v1/embeddings"


def test_davky_po_128():
    k, poz = _klient(lambda req, body: httpx.Response(200, json=_odpoved(body["input"])))
    vektory, tokenu = k.embed([f"t{i}" for i in range(300)])
    assert [len(json.loads(p.content)["input"]) for p in poz] == [128, 128, 44]
    assert len(vektory) == 300 and tokenu == 21


def test_prazdny_vstup_bez_volani():
    k, poz = _klient(lambda req, body: httpx.Response(500))
    assert k.embed([]) == ([], 0) and poz == []


def test_orez_7000():
    k, poz = _klient(lambda req, body: httpx.Response(200, json=_odpoved(body["input"])))
    k.embed(["x" * 9000])
    assert len(json.loads(poz[0].content)["input"][0]) == 7000


def test_retry_429_pak_ok():
    odpovedi = iter([429, 429, 200])

    def h(req, body):
        kod = next(odpovedi)
        return httpx.Response(200, json=_odpoved(body["input"])) if kod == 200 else httpx.Response(kod, headers={"Retry-After": "1"})
    k, poz = _klient(h)
    vektory, _ = k.embed(["a"])
    assert len(poz) == 3 and vektory == [[0.0, 1.0]]


def test_retry_after_respektuje(monkeypatch):
    spanky = []
    monkeypatch.setattr(modul, "_spi", spanky.append)
    odpovedi = iter([httpx.Response(429, headers={"Retry-After": "7"}), None])

    def h(req, body):
        r = next(odpovedi)
        return r or httpx.Response(200, json=_odpoved(body["input"]))
    k, _ = _klient(h)
    k.embed(["a"])
    assert spanky == [7.0]


def test_trvala_chyba_embedchyba():
    k, poz = _klient(lambda req, body: httpx.Response(500, text="server error"))
    with pytest.raises(EmbedChyba):
        k.embed(["a"])
    assert len(poz) == 5


def test_chyba_400_bez_opakovani():
    k, poz = _klient(lambda req, body: httpx.Response(400, json={"error": {"message": "bad input"}}))
    with pytest.raises(EmbedChyba, match="400"):
        k.embed(["a"])
    assert len(poz) == 1


def test_sitova_chyba_embedchyba():
    def h(req, body):
        raise httpx.ConnectError("spojení odmítnuto")
    k, poz = _klient(h)
    with pytest.raises(EmbedChyba):
        k.embed(["a"])
    assert len(poz) == 5


def test_klic_neni_v_chybe():
    # API v chybě 401 vrací část klíče; text výjimky ho nesmí obsahovat
    k, _ = _klient(lambda req, body: httpx.Response(401, json={"error": {"message": f"Incorrect API key provided: {KLIC}"}}))
    with pytest.raises(EmbedChyba) as e:
        k.embed(["a"])
    assert KLIC not in str(e.value) and KLIC not in repr(e.value)
    assert KLIC not in repr(k) and KLIC not in str(k)


def test_spatny_pocet_vektoru_embedchyba():
    k, _ = _klient(lambda req, body: httpx.Response(200, json=_odpoved(body["input"][:1])))
    with pytest.raises(EmbedChyba):
        k.embed(["a", "b"])


def test_z_prostredi_bez_klice_none(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert Klient.z_prostredi() is None
    monkeypatch.setenv("OPENAI_API_KEY", "  ")
    assert Klient.z_prostredi() is None


def test_z_prostredi_cte_model_a_dimenzi(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KLIC)
    monkeypatch.setenv("EMBED_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("EMBED_DIM", "512")
    k = Klient.z_prostredi(timeout=3)
    assert k.model == "text-embedding-3-small" and k.dim == 512 and k.timeout == 3
    monkeypatch.delenv("EMBED_MODEL")
    monkeypatch.delenv("EMBED_DIM")
    k = Klient.z_prostredi()
    assert k.model == "text-embedding-3-large" and k.dim == 1024
