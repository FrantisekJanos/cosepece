-- Schéma před převodem na Laravel, SQLite.
CREATE TABLE alerts (
            alert_key TEXT PRIMARY KEY,
            kind VARCHAR(20) NOT NULL,
            ct INTEGER NOT NULL,
            event_at VARCHAR(19) NOT NULL,
            created_at VARCHAR(19) NOT NULL,
            title VARCHAR(255) NOT NULL,
            body TEXT NOT NULL,
            watched INTEGER NOT NULL DEFAULT 0,
            posted INTEGER NOT NULL DEFAULT 0,
            score INTEGER NOT NULL DEFAULT 0
        );
CREATE TABLE kroky (
            id_hist INTEGER PRIMARY KEY,
            id_tisk INTEGER NOT NULL,
            ct INTEGER NOT NULL,
            datum VARCHAR(19),
            typ_stavu INTEGER,
            stav VARCHAR(120),
            akce_id INTEGER,
            akce VARCHAR(120)
        );
CREATE TABLE meta (
            k TEXT PRIMARY KEY,
            v TEXT
        );
CREATE TABLE pn (
            id_dokument INTEGER PRIMARY KEY,
            cislo INTEGER NOT NULL,
            ct INTEGER NOT NULL,
            id_osoba INTEGER,
            osoba VARCHAR(120),
            cas VARCHAR(19)
        );
CREATE TABLE pn_seen (
            id_dokument INTEGER PRIMARY KEY
        );
CREATE TABLE pn_text (
            id_dokument INTEGER PRIMARY KEY,
            ct INTEGER NOT NULL,
            cislo INTEGER NOT NULL,
            docx_url VARCHAR(255),
            text TEXT,
            chars INTEGER NOT NULL DEFAULT 0,
            laws TEXT,
            signals TEXT,
            rule_score INTEGER NOT NULL DEFAULT 0,
            ai_json TEXT,
            ai_score INTEGER,
            fetched_at VARCHAR(19),
            ai_at VARCHAR(19),
            error TEXT
        , ai_model VARCHAR(60), ai_prompt INTEGER);
CREATE TABLE post_log (
            alert_key TEXT NOT NULL,
            network VARCHAR(20) NOT NULL,
            status VARCHAR(10) NOT NULL,
            at VARCHAR(19) NOT NULL,
            error TEXT
        );
CREATE TABLE tisk_text (
            ct INTEGER PRIMARY KEY,
            docx_url VARCHAR(255),
            text TEXT,
            chars INTEGER NOT NULL DEFAULT 0,
            signals TEXT,
            ai_json TEXT,
            ai_score INTEGER,
            fetched_at VARCHAR(19),
            ai_at VARCHAR(19),
            error TEXT
        , ai_model VARCHAR(60), ai_prompt INTEGER);
CREATE TABLE tisky (
            id_tisk INTEGER PRIMARY KEY,
            ct INTEGER NOT NULL,
            druh INTEGER NOT NULL,
            typ_navrhu VARCHAR(20) NOT NULL,
            navrhovatel VARCHAR(255),
            nazev VARCHAR(255),
            uplny_nazev TEXT,
            predlozeno VARCHAR(19),
            faze VARCHAR(60),
            posledni VARCHAR(19),
            p90 INTEGER NOT NULL DEFAULT 0,
            p90ok INTEGER NOT NULL DEFAULT 0,
            zl INTEGER NOT NULL DEFAULT 0,
            pn_count INTEGER NOT NULL DEFAULT 0,
            pn_last VARCHAR(19),
            zakony TEXT,
            sbirka VARCHAR(20),
            score INTEGER NOT NULL DEFAULT 0,
            reasons TEXT
        , vyhlaseno VARCHAR(10));
CREATE INDEX idx_alerts_event ON alerts (event_at);
CREATE INDEX idx_kroky_tisk ON kroky (id_tisk);
CREATE INDEX idx_pn_ct ON pn (ct);
CREATE INDEX idx_pn_text_ct ON pn_text (ct);
CREATE INDEX idx_tisky_ct ON tisky (ct);