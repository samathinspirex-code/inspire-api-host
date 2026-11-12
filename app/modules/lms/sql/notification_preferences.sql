-- Per-user switches for LMS bell notifications and emails, one row per category.
-- A missing row means the category is on for both channels.
CREATE TABLE IF NOT EXISTS lms_notification_preferences (
    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    category VARCHAR(40) NOT NULL,
    in_app_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    email_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, category)
);
