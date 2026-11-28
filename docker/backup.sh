#!/usr/bin/env bash
# Denní záloha databáze (pg_dump, vlastní formát). Spuštění z cronu, např. 15 3 * * * ~/hlidac-zakonu/docker/backup.sh
# Zálohy jdou do BACKUP_DIR (výchozí ~/backups), ponechá se posledních KEEP_DAYS dní.
set -euo pipefail
cd "$(dirname "$0")"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
mkdir -p "$BACKUP_DIR"
out="$BACKUP_DIR/hlidac-$(date +%F-%H%M).dump"
# schéma chat (konverzace, otisky IP) do zálohy nepatří: spec chatbota 15, texty konverzací žijí jen 30 dní
docker compose exec -T db pg_dump -U hlidac -Fc --exclude-schema=chat hlidac > "$out.tmp"
mv "$out.tmp" "$out"
find "$BACKUP_DIR" -name 'hlidac-*.dump' -mtime +"$KEEP_DAYS" -delete
echo "záloha: $out ($(du -h "$out" | cut -f1))"
