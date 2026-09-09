-- Run once after google_integration.sql and online_meetings.sql.
-- This stores one encrypted Google refresh token for the Super Admin-controlled
-- meeting owner. Lecturer-owned tokens are kept intact for historic data but are
-- no longer used to create, update, cancel, or read LMS meetings.

CREATE TABLE IF NOT EXISTS lms_google_central_oauth_states (
    state_hash VARCHAR(64) PRIMARY KEY,
    requested_by_user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lms_google_central_oauth_states_expires
    ON lms_google_central_oauth_states(expires_at);

CREATE TABLE IF NOT EXISTS lms_google_central_account_connection (
    connection_id SMALLINT PRIMARY KEY DEFAULT 1,
    google_subject VARCHAR(255) NOT NULL UNIQUE,
    google_email VARCHAR(255) NOT NULL,
    encrypted_refresh_token TEXT NOT NULL,
    granted_scopes TEXT NOT NULL,
    connected_by_user_id INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    connected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_lms_google_central_singleton CHECK (connection_id = 1)
);
