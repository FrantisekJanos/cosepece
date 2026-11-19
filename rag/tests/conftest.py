"""Společné pojistky testů."""
import pytest


@pytest.fixture(autouse=True)
def bez_openai(monkeypatch):
    """Testy nikdy nevolají skutečné API OpenAI: klíč z prostředí shellu zmizí a cache embeddingů dotazů se vyprázdní.
    Test, který klienta potřebuje, si ho podstrčí sám (falešný transport)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from hlidac_rag.embed import hledani

    hledani.vycisti_cache()
    yield
    hledani.vycisti_cache()
