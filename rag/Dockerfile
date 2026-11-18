# Služba hlidac_rag: ingest stenoprotokolů (rag-ingest) a MCP server (mcp)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY hlidac_rag ./hlidac_rag
RUN pip install --no-cache-dir . && useradd --system --uid 10001 app
USER app

CMD ["python", "-m", "hlidac_rag.mcp_server"]
