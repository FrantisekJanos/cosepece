#!/bin/bash
# Zabalí projekt pro nasazení: ../hlidac-zakonu.zip (nebo cesta v 1. argumentu).
#   bin/package.sh [cesta.zip] [--bez-dat]
# Výchozí balík obsahuje i databázi data/hlidac.sqlite s už staženými texty a rozborem AI (čistá kopie
# přes VACUUM INTO), aby se na hostingu nemuselo vše stahovat a posuzovat znovu. Neobsahuje tajné ani
# místní soubory: config.php, deploy/ai-remote.env, logy, zálohy, cache, .git, testovací data.
set -eu
DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT=""
WITH_DATA=1
for arg in "$@"; do
  case "$arg" in
    --bez-dat) WITH_DATA=0 ;;
    *) OUT="$arg" ;;
  esac
done
OUT="${OUT:-$(dirname "$DIR")/hlidac-zakonu.zip}"
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT" ;; esac
rm -f "$OUT"
cd "$DIR"
zip -qr "$OUT" . \
  -x '.git/*' '.claude/*' '*.DS_Store' 'config.php' 'data/*' 'deploy/ai-remote.env' \
     'tests/fixtures/*' '.hlidac_import.tgz' '*.zip' 'docker/.env' 'docker/config.php' 'pristupy.md'
zip -q "$OUT" data/.htaccess

if [ "$WITH_DATA" = 1 ] && [ -f data/hlidac.sqlite ]; then
  # konzistentní a zmenšená kopie databáze (i když zrovna běží web nebo AI)
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  mkdir -p "$TMP/data"
  sqlite3 data/hlidac.sqlite "VACUUM INTO '$TMP/data/hlidac.sqlite'"
  # chybové texty mohou obsahovat URL s tokenem (starší verze je nemaskovaly) – do balíku nepatří
  sqlite3 "$TMP/data/hlidac.sqlite" "UPDATE post_log SET error = NULL; UPDATE pn_text SET error = 'chyba (text odstraněn při balení)' WHERE error IS NOT NULL; UPDATE tisk_text SET error = 'chyba (text odstraněn při balení)' WHERE error IS NOT NULL; UPDATE pn_text_archiv SET error = 'chyba (text odstraněn při balení)' WHERE error IS NOT NULL; UPDATE tisk_text_archiv SET error = 'chyba (text odstraněn při balení)' WHERE error IS NOT NULL; VACUUM;"
  (cd "$TMP" && zip -q "$OUT" data/hlidac.sqlite)
  echo "Databáze přibalena: $(sqlite3 "$TMP/data/hlidac.sqlite" "SELECT (SELECT COUNT(*) FROM tisky) || ' tisků, ' || ((SELECT COUNT(*) FROM tisk_text WHERE ai_at IS NOT NULL) + (SELECT COUNT(*) FROM pn_text WHERE ai_at IS NOT NULL)) || ' rozborů AI, poslední synchronizace ' || (SELECT v FROM meta WHERE k = 'last_sync')")"
fi
echo "Hotovo: $OUT"
unzip -l "$OUT" | tail -1
