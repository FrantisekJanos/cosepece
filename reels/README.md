# Reels (hlidac_reels)

Krátká videa z hlavních nálezů Hlídače zákonů na Facebook a Instagram. Video se vyrenderuje lokálně,
na serveru se zařadí do fronty a člověk ho v Telegramu schválí. Teprve potom ho bot zveřejní.

- `reels-enqueue`: zkontroluje video (ffprobe), zkopíruje ho do `/media/videos/` a zařadí do fronty (tabulka v PostgreSQL webu).
- `reels-bot`: pošle čekající video do Telegramu ke schválení, po schválení ho zveřejní na FB a IG a výsledek ohlásí.
- Render (`render.py`, Playwright a numpy) do kontejneru nepatří, běží lokálně: `pip install -e ".[render,dev]"`.

## Kontejner

Služba `reels` v `docker/docker-compose.yml` (obraz z `reels/Dockerfile`, výchozí příkaz `reels-bot`).
Data jsou ve volume `reelsmedia`, v kontejneru `/media`:

- `videos/`: videa ve frontě (název = sha256 obsahu),
- `public/`: dočasná veřejná kopie pod náhodným tokenem, jen po dobu publikace na Instagram. Instagram si video
  stahuje sám z veřejné adresy, proto web připojuje stejný volume jen pro čtení a vydává
  `https://cosepece.cz/reels-media/<token>.mp4` (jen soubory `*.mp4`, bez výpisu složky). Po publikaci se kopie smaže;
  `Cache-Control: no-store` brání tomu, aby ji CDN vydávala i potom.

Volume `reelsmedia` musí vytvořit compose prázdný: Docker do něj při prvním připojení zkopíruje `/media` z obrazu
i s vlastníkem (uid 10001). Předem vytvořený nebo rootem naplněný volume zápis bota rozbije.
Před prvním `docker/redeploy.sh` s touto službou musí být proměnné `META_*` a `REELS_*` v `docker/.env`,
jinak kontejner `reels` skončí chybou a stále se restartuje.

## Proměnné prostředí

Hodnoty patří jen do `docker/.env` na serveru (vzor `docker/.env.example`), nikdy do gitu.

| Proměnná | Význam |
|---|---|
| `META_SYSTEM_TOKEN` | systémový token Meta (Business portfolio) s oprávněními stránky a Instagramu |
| `META_PAGE_ID` | id stránky na Facebooku |
| `META_IG_USER_ID` | id účtu Instagram |
| `META_GRAPH_VERSION` | verze Graph API, výchozí `v23.0` |
| `REELS_TELEGRAM_TOKEN` | token Telegram bota |
| `REELS_TELEGRAM_CHAT_ID` | id chatu pro schvalování (číslo) |
| `REELS_PUBLIC_BASE` | veřejná adresa složky s kopiemi, výchozí `https://cosepece.cz/reels-media` |
| `REELS_MEDIA_DIR` | složka s daty, v kontejneru `/media` |
| `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` | databáze webu (nastaví compose), lokálně stačí `DATABASE_URL` |

Bez povinných proměnných bot skončí s chybou a vypíše, které chybí (hodnoty nevypisuje).

## Zařazení videa do fronty

Video a text popisku musí být uvnitř kontejneru, například přes `docker compose cp`:

```bash
cd ~/hlidac-zakonu
docker compose -f docker/docker-compose.yml cp reel.mp4 reels:/tmp/reel.mp4
docker compose -f docker/docker-compose.yml cp popisek.txt reels:/tmp/popisek.txt
docker compose -f docker/docker-compose.yml exec reels \
  reels-enqueue --video /tmp/reel.mp4 --caption-file /tmp/popisek.txt --ct 221 --format v3
```

`--ct` (číslo sněmovního tisku) a `--format` (označení formátu videa) jsou nepovinné údaje pro přehled ve frontě. Stejné video (stejný sha256) se do fronty
nezařadí dvakrát. `docker compose cp` ukládá soubory jako root, uživatel `app` je musí umět číst (práva aspoň 0644).
Po zařazení je smaž: `docker compose -f docker/docker-compose.yml exec -u root reels rm /tmp/reel.mp4 /tmp/popisek.txt`.

## Telegram

Bot posílá do nastaveného chatu video ke schválení s popiskem a tlačítky:

- **Schválit**: zveřejní video na FB i IG a pošle odkazy,
- **Upravit popisek**: nový text se pošle jako odpověď na zprávu (funguje i odpověď přímo na video nebo náhled),
- **Zamítnout**: video se nezveřejní,
- **Zkusit znovu**: u položky, jejíž publikace selhala nebo skončila nejistě (nejdřív dohledá, co už na sítích je).

Příkazy:

- `/fronta`: počty položek podle stavů a k neúspěšným položkám znovu tlačítko Zkusit znovu,
- `/dalsi`: pošle další video ze fronty ke schválení.

Zprávy z jiných chatů bot ignoruje.

## Testy

```bash
pip install -e ".[dev]"
pytest -q -m "not render"                       # bez Chromia
DATABASE_URL=postgresql://… REELS_TEST_DB=1 pytest -q   # i testy nad PostgreSQL
```
