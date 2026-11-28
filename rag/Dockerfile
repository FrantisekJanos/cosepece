# Služba hlidac_rag: ingest stenoprotokolů (rag-ingest), MCP server (mcp) a chatbot (chat)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY hlidac_rag ./hlidac_rag
RUN pip install --no-cache-dir . && useradd --system --uid 10001 app
# kouřový eval chatu pro bránu v docker/redeploy.sh (python -m eval.chat.chat_eval z /app); zbytek eval/ do obrazu nejde
COPY eval/__init__.py ./eval/__init__.py
COPY eval/chat ./eval/chat
USER app

CMD ["python", "-m", "hlidac_rag.mcp_server"]
