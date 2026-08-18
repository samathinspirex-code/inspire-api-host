-- Run once after the existing LMS scripts.
-- OAuth client secrets remain in the API environment and are not stored here.
CREATE TABLE IF NOT EXISTS lms_google_integration_settings (
    settings_id SMALLINT PRIMARY KEY DEFAULT 1,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    workspace_domain VARCHAR(255),
    embed_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    calendar_sync_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    attendance_sync_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    attendance_threshold_percentage SMALLINT NOT NULL DEFAULT 50
        CHECK (attendance_threshold_percentage BETWEEN 1 AND 100),
    default_access_type VARCHAR(20) NOT NULL DEFAULT 'restricted',
    updated_by INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_lms_google_settings_singleton CHECK (settings_id = 1),
    CONSTRAINT ck_lms_google_settings_access_type
        CHECK (default_access_type IN ('open', 'trusted', 'restricted'))
);

INSERT INTO lms_google_integration_settings (settings_id)
VALUES (1)
ON CONFLICT (settings_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS lms_google_oauth_states (
    state_hash VARCHAR(64) PRIMARY KEY,
    lecturer_user_id INTEGER NOT NULL REFERENCES lms_lecturer_profiles(user_id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_google_oauth_states_lecturer
    ON lms_google_oauth_states(lecturer_user_id);
CREATE INDEX IF NOT EXISTS idx_google_oauth_states_expires
    ON lms_google_oauth_states(expires_at);

CREATE TABLE IF NOT EXISTS lms_google_account_connections (
    lecturer_user_id INTEGER PRIMARY KEY REFERENCES lms_lecturer_profiles(user_id) ON DELETE CASCADE,
    google_subject VARCHAR(255) NOT NULL UNIQUE,
    google_email VARCHAR(255) NOT NULL,
    encrypted_refresh_token TEXT NOT NULL,
    granted_scopes TEXT NOT NULL,
    connected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
