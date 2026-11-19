"""Načítání modulů nástrojů (hlidac_rag.nastroje) a pořadí souborů schématu."""
from hlidac_rag import db


def test_schema_files_zacina_hlavnim_schematem():
    soubory = db.schema_files()
    assert soubory[0] == "schema.sql"
    assert soubory[1:] == sorted(soubory[1:])
    assert all(s.startswith("schema_") and s.endswith(".sql") for s in soubory[1:])


def test_moduly_nastroju_se_registruji():
    from hlidac_rag import mcp_server, nastroje
    import pkgutil
    cekane = sorted(m.name for m in pkgutil.iter_modules(nastroje.__path__) if not m.name.startswith("_"))
    assert mcp_server.MODULY_NASTROJU == cekane
