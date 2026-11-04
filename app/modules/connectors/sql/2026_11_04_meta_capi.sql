-- Meta Conversions API. Separate tables, so nothing in the CRM depends on them.
CREATE TABLE IF NOT EXISTS meta_capi_consents (
    consent_id BIGSERIAL PRIMARY KEY,
    lead_id INTEGER REFERENCES crm_leads(lead_id) ON DELETE CASCADE,
    email VARCHAR(255),  -- Only until the lead is matched, then cleared.
    phone VARCHAR(50),   -- Only until the lead is matched, then cleared.
    form VARCHAR(30) NOT NULL,
    wording_version VARCHAR(20) NOT NULL,
    fbc VARCHAR(255),
    fbp VARCHAR(255),
    consented_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_meta_capi_consents_lead ON meta_capi_consents (lead_id);

CREATE TABLE IF NOT EXISTS meta_capi_events (
    event_id BIGSERIAL PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES crm_leads(lead_id) ON DELETE CASCADE,
    event_name VARCHAR(30) NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error VARCHAR(240),
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (lead_id, event_name)
);
CREATE INDEX IF NOT EXISTS idx_meta_capi_events_due ON meta_capi_events (status, next_attempt_at);
