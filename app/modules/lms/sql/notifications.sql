CREATE TABLE IF NOT EXISTS lms_announcements (
    announcement_id BIGSERIAL PRIMARY KEY,
    audience_type VARCHAR(20) NOT NULL CHECK (audience_type IN ('all', 'course', 'class')),
    audience_id BIGINT,
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    importance VARCHAR(20) NOT NULL DEFAULT 'normal' CHECK (importance IN ('normal', 'important', 'urgent')),
    publish_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL DEFAULT 'scheduled' CHECK (status IN ('draft', 'scheduled', 'published', 'expired', 'cancelled')),
    email_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_by BIGINT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (audience_type = 'all' OR audience_id IS NOT NULL),
    CHECK (expires_at IS NULL OR expires_at > publish_at)
);
CREATE INDEX IF NOT EXISTS idx_lms_announcements_publish ON lms_announcements(status, publish_at);

CREATE TABLE IF NOT EXISTS lms_notifications (
    notification_id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    event_key VARCHAR(255) NOT NULL,
    notification_type VARCHAR(40) NOT NULL,
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    action_url TEXT,
    importance VARCHAR(20) NOT NULL DEFAULT 'normal' CHECK (importance IN ('normal', 'important', 'urgent')),
    scheduled_for TIMESTAMPTZ NOT NULL,
    read_at TIMESTAMPTZ,
    email_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    email_status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (email_status IN ('pending', 'sent', 'failed', 'disabled')),
    email_attempts INTEGER NOT NULL DEFAULT 0,
    email_sent_at TIMESTAMPTZ,
    email_provider_id VARCHAR(255),
    email_error VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lms_notification_user_event UNIQUE (user_id, event_key)
);
CREATE INDEX IF NOT EXISTS idx_lms_notifications_user ON lms_notifications(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lms_notifications_delivery ON lms_notifications(email_status, scheduled_for);
