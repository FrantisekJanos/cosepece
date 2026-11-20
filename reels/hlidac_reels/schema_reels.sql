-- Fronta reelů k publikaci (Telegram schválení, Instagram, Facebook a Threads).
CREATE TABLE IF NOT EXISTS reel_queue (
    id                     bigserial PRIMARY KEY,
    source                 text NOT NULL DEFAULT 'ukazka',
    ct                     integer,
    format                 text,
    video_path             text NOT NULL,
    video_sha256           text NOT NULL,
    caption                text NOT NULL,
    status                 text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','pending','approved','publishing','published','rejected','failed','unknown')),
    telegram_video_msg_id  bigint,
    telegram_msg_id        bigint,
    ig_container_id        text,
    ig_media_id            text,
    ig_permalink           text,
    fb_video_id            text,
    fb_permalink           text,
    public_token           text,
    error                  text,
    attempts               integer NOT NULL DEFAULT 0,
    created_at             timestamptz NOT NULL DEFAULT now(),
    sent_at                timestamptz,
    decided_at             timestamptz,
    published_at           timestamptz,
    UNIQUE (video_sha256),
    UNIQUE (public_token)
);
CREATE INDEX IF NOT EXISTS reel_queue_status_idx ON reel_queue (status, id);

-- Threads (2026-11-20): v produkci už tabulka existuje, sloupce se doplní na konec.
-- Pořadí sloupců nevadí, queue.py vybírá sloupce jménem (stejné pořadí jako pole Reel).
ALTER TABLE reel_queue ADD COLUMN IF NOT EXISTS threads_container_id text;
ALTER TABLE reel_queue ADD COLUMN IF NOT EXISTS threads_media_id     text;
ALTER TABLE reel_queue ADD COLUMN IF NOT EXISTS threads_permalink    text;

-- Video na šířku 16:9 (2026-11-20): volitelné, pro Threads; FB a IG dostanou 9:16.
ALTER TABLE reel_queue ADD COLUMN IF NOT EXISTS video_landscape_path      text;
ALTER TABLE reel_queue ADD COLUMN IF NOT EXISTS video_landscape_sha256    text;
ALTER TABLE reel_queue ADD COLUMN IF NOT EXISTS telegram_landscape_msg_id bigint;

-- Drobný stav služby (např. offset Telegramu).
CREATE TABLE IF NOT EXISTS reel_state (
    key   text PRIMARY KEY,
    value text
);
