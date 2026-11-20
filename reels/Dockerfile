# Služba reels: Telegram bot pro schválení a publikaci Reels (reels-bot) a zařazení videa do fronty (reels-enqueue).
# Render videa (Playwright, numpy) do obrazu nepatří, běží lokálně; ffmpeg je tu kvůli ffprobe v reels-enqueue.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY hlidac_reels ./hlidac_reels
# /media: volume reelsmedia (videos/ = fronta, public/ = dočasné veřejné kopie pro Instagram).
# Složky vlastní app, takže nový pojmenovaný volume převezme vlastníka a bot do něj může zapisovat.
RUN pip install --no-cache-dir . \
 && useradd --system --uid 10001 app \
 && mkdir -p /media/videos /media/public \
 && chown -R 10001:10001 /media \
 && chmod 0755 /media /media/videos /media/public
USER app

CMD ["reels-bot"]
