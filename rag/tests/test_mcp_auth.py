"""AuthMiddleware: neplatná hlavička Authorization vrací 401, ne výjimku (Sentry COSEPECE-RAG-2)."""
import anyio

from hlidac_rag.mcp_server import AuthMiddleware


async def _zavolej(headers):
    async def app(scope, receive, send):  # pragma: no cover - k aplikaci se požadavek nesmí dostat
        raise AssertionError("požadavek bez platného tokenu prošel")

    poslano = []

    async def send(msg):
        poslano.append(msg)

    async def receive():
        return {"type": "http.request", "body": b""}

    scope = {"type": "http", "path": "/mcp", "method": "POST", "headers": headers}
    await AuthMiddleware(app)(scope, receive, send)
    return poslano[0]["status"]


def test_hlavicka_mimo_ascii_vraci_401():
    # bajt 0xdd na pozici 10 jako v hlášení ze Sentry
    assert anyio.run(_zavolej, [(b"authorization", b"Bearer abc\xdd\xff")]) == 401


def test_bez_hlavicky_vraci_401():
    assert anyio.run(_zavolej, []) == 401
