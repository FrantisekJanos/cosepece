#!/usr/bin/env bash
# Redeploy produkce na serveru: stáhne main z GitHubu, sestaví kontejnery, novou verzi ještě před výměnou
# otestuje a po výměně ověří. Když ověření po výměně selže, vrátí předchozí verzi.
# Spuštění na serveru: ~/hlidac-zakonu/docker/redeploy.sh [commit]   (bez parametru origin/main)
# config.php a .env v docker/ git ignoruje, data jsou ve volumech appdata a pgdata.
set -euo pipefail
cd "$(dirname "$0")/.."
TARGET=${1:-origin/main}
PREVIOUS=$(git rev-parse HEAD)
git fetch --quiet origin main
git reset --hard "$TARGET"
CURRENT=$(git rev-parse HEAD)
# release pro Sentry: krátký otisk nasazeného commitu (compose ho předá kontejnerům)
export SENTRY_RELEASE=$(git rev-parse --short HEAD)
cd docker
# předchozí verze pro návrat (docker/rollback.sh): commit do souboru a dosavadní obrazy označit jako :previous,
# aby je úklid nesmazal a návrat nemusel všechno stavět znovu
if [ "$PREVIOUS" != "$CURRENT" ]; then
  echo "$PREVIOUS" > .previous-commit
  for image in $(docker compose config --images | grep -v / || true); do
    if docker image inspect "$image" >/dev/null 2>&1; then
      docker image tag "$image" "$image:previous"
    fi
  done
fi
# --pull: i bezpečnostní aktualizace základních obrazů (php:8.3-apache, python)
docker compose build --pull
# kontrola nové verze před výměnou: testy uvnitř nového obrazu webu, bez sítě a bez provozních dat.
# Když neprojdou, kontejnery se nevymění a dál běží předchozí verze.
WEB_IMAGE=$(docker compose config --images | grep -E -- '-web$' | head -n 1 || true)
if [ -n "$WEB_IMAGE" ] && [ "${SKIP_PREFLIGHT:-0}" != 1 ]; then
  echo "Testy v novém obrazu ($WEB_IMAGE)…"
  if ! docker run --rm --network none --entrypoint php "$WEB_IMAGE" /var/www/html/tests/run.php | tail -n 1 | grep -q 'VŠE OK'; then
    echo "CHYBA: testy v novém obrazu neprošly. Kontejnery se nevyměnily, běží dál předchozí verze." >&2
    exit 1
  fi
fi
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
# nová verze po výměně neodpovídá: vrátit předchozí (jen jednou, ne při samotném návratu)
if [ "$FAIL" = 1 ] && [ "${ROLLBACK:-0}" != 1 ] && [ "$PREVIOUS" != "$CURRENT" ]; then
  echo "CHYBA: nová verze po spuštění neodpovídá, vracím předchozí ($PREVIOUS)." >&2
  ROLLBACK=1 SKIP_PREFLIGHT=1 "$0" "$PREVIOUS" || echo "CHYBA: návrat k předchozí verzi se nepovedl, je potřeba ruční zásah." >&2
  exit 1
fi
exit "$FAIL"
