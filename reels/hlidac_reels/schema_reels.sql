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
-- otisk šířky, která opravdu odešla do Telegramu (schválit jde jen ta, kterou vlastník viděl)
ALTER TABLE reel_queue ADD COLUMN IF NOT EXISTS telegram_landscape_sha256 text;

-- Drobný stav služby (např. offset Telegramu).
CREATE TABLE IF NOT EXISTS reel_state (
    key   text PRIMARY KEY,
    value text
);

-- Sada variant z generátoru (část 2, 2026-11-20): úlohy generátoru a vazba položek fronty na sadu.
CREATE TABLE IF NOT EXISTS reel_jobs (
    id            bigserial PRIMARY KEY,
    source        text NOT NULL CHECK (source IN ('manual','auto_pick','fresh')),
    ct            integer,
    format_hint   text,
    reason_keys   text,  -- JSON seznam klíčů v době založení (fresh)
    status        text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','claimed','done','failed','expired','cancelled')),
    claimed_at    timestamptz,
    claim_token   text,
    attempts      integer NOT NULL DEFAULT 0,
    error         text,
    set_id        bigint,
    created_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    notified_at   timestamptz
);
-- Video k vybraným PN (/pn, 2026-11-24): čísla PN seřazená a bez duplicit; NULL = celý tisk.
ALTER TABLE reel_jobs ADD COLUMN IF NOT EXISTS focus_pn integer[];
-- Pro jeden tisk a jednu množinu PN nejvýš jedna aktivní úloha (úloha k celému tisku a k jeho PN běží vedle sebe).
-- Původní index jen nad ct se ruší.
DROP INDEX IF EXISTS reel_jobs_active_ct;
CREATE UNIQUE INDEX IF NOT EXISTS reel_jobs_active_ct_pn ON reel_jobs (ct, COALESCE(focus_pn, '{}'::integer[]))
    WHERE status IN ('queued','claimed') AND ct IS NOT NULL;

ALTER TABLE reel_queue ADD COLUMN IF NOT EXISTS set_id   bigint;  -- reel_jobs.id
ALTER TABLE reel_queue ADD COLUMN IF NOT EXISTS variant  smallint;
ALTER TABLE reel_queue ADD COLUMN IF NOT EXISTS template text;
-- Nové stavy expired a withdrawn: CHECK se vytvoří znovu (idempotentně, i nad starou produkční tabulkou).
ALTER TABLE reel_queue DROP CONSTRAINT IF EXISTS reel_queue_status_check;
ALTER TABLE reel_queue ADD CONSTRAINT reel_queue_status_check
    CHECK (status IN ('queued','pending','approved','publishing','published','rejected','failed','unknown',
                      'expired','withdrawn'));
