#!/bin/bash
# AI rozbor pro web na hostingu. Spouští se na počítači s Claude Code (Mac nebo Linux),
# pravidelně přes launchd (deploy/cz.hlidac.ai.plist) nebo cron.
#   1. stáhne z hostingu (ai_api.php) texty čekající na rozbor, posoudí je přes `claude -p` (Haiku, předplatné)
#   2. nejzávažnější nálezy znovu posoudí silnější model (Sonnet)
#   3. výsledky pošle zpět; hosting ověří citace a přepočítá skóre
#   4. vysvětlení PN „v praxi“ (Haiku, u PN s nálezem znovu Sonnet); hosting je pustí jen přes pojistky
# Nastavení je v deploy/ai-remote.env (vzor deploy/ai-remote.env.example).

set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$DIR/deploy/ai-remote.env"
LOG="$DIR/data/ai-remote.log"
LOCK="$DIR/data/ai-remote.lock"
mkdir -p "$DIR/data"

if [ ! -f "$ENV_FILE" ]; then
  echo "Chybí $ENV_FILE – zkopírujte deploy/ai-remote.env.example a vyplňte" >&2
  exit 1
fi
# shellcheck source=/dev/null
. "$ENV_FILE"

# launchd a cron mají chudé PATH: doplníme obvyklá umístění php a claude
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
PHP_BIN="${PHP_BIN:-$(command -v php)}"
if [ -z "$PHP_BIN" ] || [ -z "$(command -v claude)" ]; then
  echo "$(date '+%F %T') chybí php nebo claude v PATH" >> "$LOG"
  exit 1
fi
# bez USER/LOGNAME nenajde Claude Code přihlášení v klíčence („Not logged in“)
export USER="${USER:-$(id -un)}"
export LOGNAME="${LOGNAME:-$USER}"
# předplatné, ne API: klíč z prostředí by se účtoval zvlášť
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN

# jen jeden běh najednou (první dohánění historie trvá i hodiny)
if ! mkdir "$LOCK" 2>/dev/null; then
  # zámek starší 6 hodin = po pádu, uvolníme ho
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +360 2>/dev/null)" ]; then
    rmdir "$LOCK" && mkdir "$LOCK" || exit 0
  else
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

STATUS=0
{
  echo "=== $(date '+%F %T') start"
  # token přes prostředí, ne jako argument (argumenty procesu vidí ostatní uživatelé v ps)
  export HLIDAC_TOKEN
  "$PHP_BIN" "$DIR/bin/ai.php" --remote="$HLIDAC_URL" --limit="${AI_LIMIT:-20}" --all || STATUS=1
  "$PHP_BIN" "$DIR/bin/ai.php" --remote="$HLIDAC_URL" --limit="${AI_LIMIT:-20}" --all --verify || STATUS=1
  # vysvětlení „v praxi“ až po rozboru: procedurální nálezy mají přednost. Bez --all: jedna dávka za běh,
  # aby dohánění starších PN (stovky textů) neblokovalo rozbor nových; jednorázově lze dohnat ručně s --all
  "$PHP_BIN" "$DIR/bin/ai.php" --remote="$HLIDAC_URL" --limit="${AI_PRAXE_LIMIT:-20}" --praxe || STATUS=1
  "$PHP_BIN" "$DIR/bin/ai.php" --remote="$HLIDAC_URL" --limit="${AI_PRAXE_LIMIT:-20}" --praxe --verify || STATUS=1
  # chyba některé fáze = nenulový návratový kód (launchd/cron ji pak ukáže), i když druhá fáze proběhla
  echo "=== $(date '+%F %T') konec$([ "$STATUS" = 0 ] || echo ' s chybami')"
} >> "$LOG" 2>&1

# log držíme malý
if [ "$(wc -l < "$LOG")" -gt 5000 ]; then
  tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
exit "$STATUS"
