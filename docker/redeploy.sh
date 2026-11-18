#!/usr/bin/env bash
# Redeploy produkce na serveru: stáhne main z GitHubu a přestaví kontejnery.
# Spuštění na serveru: ~/hlidac-zakonu/docker/redeploy.sh
# config.php a .env v docker/ git ignoruje, data jsou ve volumech appdata a pgdata.
set -euo pipefail
cd "$(dirname "$0")/.."
git fetch --quiet origin main
git reset --hard origin/main
cd docker
docker compose up -d --build
docker image prune -f >/dev/null
docker compose ps --format '{{.Service}} {{.Status}}'
BIND=$(grep -E '^BIND=' .env | cut -d= -f2)
curl -s -o /dev/null -w 'web: %{http_code}\n' "http://${BIND:-127.0.0.1:8100}/"
