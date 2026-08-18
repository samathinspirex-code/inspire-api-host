-- Replace email login OTP with RFC 6238 authenticator credentials.
CREATE TABLE IF NOT EXISTS authenticator_credentials (
    user_id INTEGER PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    encrypted_secret TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    last_used_step BIGINT,
    failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
    locked_until TIMESTAMPTZ,
    verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS authenticator_setup_tokens (
    token_id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_by INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_authenticator_setup_user
    ON authenticator_setup_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_authenticator_setup_expiry
    ON authenticator_setup_tokens(expires_at);

CREATE TABLE IF NOT EXISTS authenticator_recovery_codes (
    recovery_code_id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    code_hash VARCHAR(64) NOT NULL UNIQUE,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_authenticator_recovery_user
    ON authenticator_recovery_codes(user_id);

-- Email OTP rows contain only short-lived codes and are no longer used.
DROP TABLE IF EXISTS login_otps;
