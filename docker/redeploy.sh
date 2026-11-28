#!/usr/bin/env bash
# Redeploy produkce na serveru: stáhne main z GitHubu, sestaví kontejnery, novou verzi ještě před výměnou
# otestuje a po výměně ověří. Když ověření webu nebo MCP po výměně selže, vrátí předchozí verzi. Služba chat jde až
# po nich a její selhání (start, /chat-api/zdravi, kouřový eval) nasazení nevrací: chat se jen vypne.
# SKIP_CHAT_EVAL=1 přeskočí kouřový eval (stojí ~0,40 USD).
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
# služba chat se spouští až po ověření webu a MCP (níže): její selhání nesmí zastavit ani vrátit nasazení webu
docker compose up -d $(docker compose config --services | grep -vx chat || true)
docker image prune -f >/dev/null
docker compose ps --format '{{.Service}} {{.Status}}'
# hodnota proměnné z .env; chybějící nesmí skript ukončit (set -e + pipefail)
env_hodnota() { grep -E "^$1=" .env 2>/dev/null | tail -n 1 | cut -d= -f2- || true; }
BIND=$(env_hodnota BIND)
MCP_BIND=$(env_hodnota MCP_BIND)
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

# chat (spec chatbota 14.2 a 16): start, /chat-api/zdravi, potom kouřový eval přes živý web. Jakékoli selhání chatu
# jen vypíše varování a chat vypne; web a MCP běží dál a návratový kód redeploye se nemění.
chat_varuj() { echo "VAROVÁNÍ (chat): $*" >&2; }
if [ "$FAIL" = 1 ]; then
  chat_varuj "web nebo MCP neodpovídá, chat se nespouští ani nevyměňuje."
elif docker compose config --services | grep -qx chat; then
  CHAT_BIND=$(env_hodnota CHAT_BIND)
  CHAT_ZAPNUTO=$(env_hodnota CHAT_ZAPNUTO | tr '[:upper:]' '[:lower:]')
  if ! docker compose up -d chat; then
    chat_varuj "služba nenastartovala (web a MCP běží dál)."
  elif ! check chat "http://${CHAT_BIND:-127.0.0.1:8103}/chat-api/zdravi"; then
    # nejčastěji chybí role chat_ro/chat_rw nebo jejich hesla: zastavit, ať se kontejner neopakovaně nerestartuje
    chat_varuj "neodpovídá na /chat-api/zdravi, zastavuji ho (docker compose logs chat)."
    docker compose stop chat || true
  elif [ "${ROLLBACK:-0}" = 1 ] || [ "${SKIP_CHAT_EVAL:-0}" = 1 ]; then
    echo "chat: kouřový eval přeskočen"
  elif ! [[ "$CHAT_ZAPNUTO" =~ ^(1|true|ano|yes|on)$ ]]; then   # stejné hodnoty jako config.py
    echo "chat: vypnutý (CHAT_ZAPNUTO), kouřový eval se nespouští"
  else
    # 10 tahů přes https://cosepece.cz (Cloudflare, Caddy, Turnstile obchází jen klíč CHAT_EVAL_KLIC), ~0,40 USD
    echo "chat: kouřový eval…"
    # strop 15 minut: zaseknutý běh (kód 124) se počítá jako nedoběhnutý, ne jako selhání kvality
    if timeout 900 docker compose run --rm --no-deps -T chat \
        python -m eval.chat.chat_eval --sada kour --beta-z-prostredi; then
      echo "chat: kouřový eval prošel"
    else
      KOD=$?
      if [ "$KOD" = 1 ]; then
        # selhání kontrol kvality (neutralita, injekce, citace): chat se sám vypne (CHAT_ZAPNUTO=0 v .env)
        chat_varuj "kouřový eval neprošel, vypínám chat (CHAT_ZAPNUTO=0 v docker/.env; zapnutí: oprava, CHAT_ZAPNUTO=1, docker compose up -d chat)."
        sed -i 's/^CHAT_ZAPNUTO=.*/CHAT_ZAPNUTO=0/' .env
        if ! docker compose up -d chat; then
          chat_varuj "restart s CHAT_ZAPNUTO=0 selhal, zastavuji chat."
          docker compose stop chat || true
        fi
      else
        # 2 = služba nedostupná, limit nebo strop ceny evalu: kvalita neověřena, chat zůstává zapnutý
        chat_varuj "kouřový eval nedoběhl (kód $KOD: služba nedostupná, limit nebo strop ceny), chat zůstává zapnutý; zkontroluj ručně."
      fi
    fi
  fi
fi
exit "$FAIL"
