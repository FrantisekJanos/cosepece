#!/usr/bin/env bash
# Redeploy produkce na serveru: stáhne main z GitHubu a přestaví kontejnery.
# Spuštění na serveru: ~/hlidac-zakonu/docker/redeploy.sh
# config.php a .env v docker/ git ignoruje, data jsou ve volumech appdata a pgdata.
set -euo pipefail
cd "$(dirname "$0")/.."
git fetch --quiet origin main
git reset --hard origin/main
# release pro Sentry: krátký otisk nasazeného commitu (compose ho předá kontejnerům)
export SENTRY_RELEASE=$(git rev-parse --short HEAD)
cd docker
# --pull: i bezpečnostní aktualizace základních obrazů (php:8.3-apache, python)
docker compose build --pull
docker compose up -d
docker image prune -f >/dev/null
docker compose ps --format '{{.Service}} {{.Status}}'
# chybějící BIND v .env nesmí skript ukončit (set -e + pipefail); kontrola musí skončit 200
BIND=$(grep -E '^BIND=' .env 2>/dev/null | cut -d= -f2 || true)
MCP_BIND=$(grep -E '^MCP_BIND=' .env 2>/dev/null | cut -d= -f2 || true)
check() {
  # kontejnery po startu chvíli nabíhají: až 30 s opakujeme
  local name=$1 url=$2 code=000
  for _ in $(seq 1 15); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "$url" || true)
    [ "$code" = 200 ] && break
    sleep 2
  done
  echo "$name: $code"
  [ "$code" = 200 ]
}
FAIL=0
check web "http://${BIND:-127.0.0.1:8100}/" || FAIL=1
check mcp "http://${MCP_BIND:-127.0.0.1:8102}/health" || FAIL=1
exit "$FAIL"
