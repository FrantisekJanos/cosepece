"""Další nástroje MCP serveru, každý okruh ve vlastním modulu.

Modul v tomto balíčku (jméno bez úvodního podtržítka) definuje funkci
`register(server, call, annotations)`, která nad `server` zaregistruje své nástroje:

    def register(server, call, annotations):
        @server.tool(annotations=annotations)
        def muj_nastroj(ct: int) -> dict:
            \"\"\"Popis pro klienta.\"\"\"
            return call("muj_nastroj", _muj_nastroj, ct)

`call(name, fn, *args)` je `mcp_server._call`: transakce jen pro čtení, limit času a velikosti odpovědi,
zápis do mcp.volani; `fn(conn, *args)` dostane spojení jako první argument. ValueError = čitelná chyba klienta.
Vlastní tabulky patří do `hlidac_rag/schema_<okruh>.sql` (idempotentní, spouští se po schema.sql).
"""
