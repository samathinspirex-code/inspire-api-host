CREATE TABLE IF NOT EXISTS cms_activity_log (
    activity_log_id SERIAL PRIMARY KEY,
    actor_user_id INTEGER,
    actor_name VARCHAR(255) NOT NULL,
    actor_email VARCHAR(255),
    action VARCHAR(255) NOT NULL,
    module VARCHAR(100) NOT NULL,
    request_method VARCHAR(10) NOT NULL,
    request_path TEXT NOT NULL,
    result VARCHAR(32) NOT NULL DEFAULT 'Success',
    is_sensitive BOOLEAN NOT NULL DEFAULT FALSE,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cms_activity_log_occurred_at
    ON cms_activity_log(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_cms_activity_log_module
    ON cms_activity_log(module);
