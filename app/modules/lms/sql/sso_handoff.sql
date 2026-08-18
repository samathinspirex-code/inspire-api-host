-- Required once for secure CMS -> LMS single sign-on.
CREATE TABLE IF NOT EXISTS sso_tickets (
    ticket_id   SERIAL PRIMARY KEY,
    user_id     INT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    ticket_hash VARCHAR(64) NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sso_tickets_user_id ON sso_tickets(user_id);
