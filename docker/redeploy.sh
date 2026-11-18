#!/usr/bin/env bash
# Redeploy produkce na serveru (účet alan): stáhne main z GitHubu a přestaví kontejnery.
# Spuštění: ssh alan@5.75.228.104 '~/hlidac-zakonu/docker/redeploy.sh'
# config.php a .env v docker/ git ignoruje, data jsou ve volumech appdata a pgdata.
set -euo pipefail
cd "$(dirname "$0")/.."
git fetch --quiet origin main
git reset --hard origin/main
cd docker
docker compose up -d --build
docker image prune -f >/dev/null
docker compose ps --format '{{.Service}} {{.Status}}'
curl -s -o /dev/null -w 'web: %{http_code}\n' http://172.18.0.1:8100/
