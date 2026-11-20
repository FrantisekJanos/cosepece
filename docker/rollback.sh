#!/usr/bin/env bash
# Návrat k verzi, která běžela před posledním nasazením (commit z docker/.previous-commit, zapisuje redeploy.sh).
# Spuštění na serveru: ~/hlidac-zakonu/docker/rollback.sh
# Další ./redeploy.sh bez parametru nasadí znovu aktuální main.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -s .previous-commit ]; then
  echo "Předchozí verze není zaznamenaná (docker/.previous-commit chybí)." >&2
  exit 1
fi
echo "Vracím verzi $(cat .previous-commit)"
ROLLBACK=1 SKIP_PREFLIGHT=1 exec ./redeploy.sh "$(cat .previous-commit)"
