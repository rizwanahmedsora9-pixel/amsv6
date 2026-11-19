-- 0017_activity_log.sql
-- Logs section redesign: activity_event + activity_session, plus the presence
-- columns the Users tab needs.  Safe to retry (IF NOT EXISTS everywhere).
--
-- activity_event / activity_session are normally created by db.create_all()
-- from models/activity.py; the CREATE statements here keep an upgraded
-- database identical to a freshly bootstrapped one.

CREATE TABLE IF NOT EXISTS activity_event (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at     DATETIME NOT NULL,
    category        VARCHAR(20) NOT NULL,
    result          VARCHAR(20) NOT NULL DEFAULT 'success',
    module          VARCHAR(60),
    action          VARCHAR(120) NOT NULL,
    entity_type     VARCHAR(60),
    entity_id       INTEGER,
    entity_label    VARCHAR(200),
    amount_minor    BIGINT,
    summary         VARCHAR(500),
    before_json     TEXT,
    after_json      TEXT,
    changed_json    TEXT,
    user_id         INTEGER,
    username        VARCHAR(80),
    role            VARCHAR(20),
    ip              VARCHAR(80),
    session_sid     VARCHAR(40),
    request_id      VARCHAR(40),
    method          VARCHAR(10),
    path            VARCHAR(300),
    status_code     INTEGER,
    duration_ms     INTEGER,
    bucket_minute   VARCHAR(12),
    hits            INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS ix_activity_event_occurred_at  ON activity_event (occurred_at);
CREATE INDEX IF NOT EXISTS ix_activity_event_category     ON activity_event (category);
CREATE INDEX IF NOT EXISTS ix_activity_event_result       ON activity_event (result);
CREATE INDEX IF NOT EXISTS ix_activity_event_module       ON activity_event (module);
CREATE INDEX IF NOT EXISTS ix_activity_event_entity       ON activity_event (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS ix_activity_event_user_time    ON activity_event (username, occurred_at);
CREATE INDEX IF NOT EXISTS ix_activity_event_cat_time     ON activity_event (category, occurred_at);
CREATE INDEX IF NOT EXISTS ix_activity_event_bucket       ON activity_event (bucket_minute);
CREATE INDEX IF NOT EXISTS ix_activity_event_sid          ON activity_event (session_sid);

CREATE TABLE IF NOT EXISTS activity_session (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_sid      VARCHAR(40) NOT NULL UNIQUE,
    user_id           INTEGER NOT NULL,
    username          VARCHAR(80),
    role              VARCHAR(20),
    ip                VARCHAR(80),
    user_agent        VARCHAR(300),
    current_path      VARCHAR(300),
    current_label     VARCHAR(200),
    first_seen_at     DATETIME NOT NULL,
    last_heartbeat_at DATETIME,
    last_input_at     DATETIME,
    visible           BOOLEAN DEFAULT 1,
    ended_at          DATETIME,
    beat_count        INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_activity_session_sid   ON activity_session (activity_sid);
CREATE INDEX IF NOT EXISTS ix_activity_session_user  ON activity_session (user_id);
CREATE INDEX IF NOT EXISTS ix_activity_session_beat  ON activity_session (last_heartbeat_at);
CREATE INDEX IF NOT EXISTS ix_activity_session_ended ON activity_session (ended_at);

-- Presence columns on the existing session table (the "seen in last 45 min"
-- badge could not tell working from idle; these make the distinction real).
ALTER TABLE user_login_session ADD COLUMN last_active_at DATETIME;
ALTER TABLE user_login_session ADD COLUMN activity_state VARCHAR(20);
