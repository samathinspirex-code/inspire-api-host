-- Conditional offer letters and the documents students upload through the offer link.
-- Safe to run more than once. offer_service also creates these on first use.
CREATE TABLE IF NOT EXISTS crm_offer_letters (
    offer_id SERIAL PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES crm_leads(lead_id) ON DELETE CASCADE,
    token VARCHAR(64) NOT NULL UNIQUE,
    programme VARCHAR(255) NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    sent_by_user_id INTEGER,
    origin VARCHAR(30) NOT NULL DEFAULT 'crm',
    email_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    email_error TEXT
);
CREATE INDEX IF NOT EXISTS ix_crm_offer_letters_lead ON crm_offer_letters (lead_id, sent_at DESC);

CREATE TABLE IF NOT EXISTS crm_lead_documents (
    document_id SERIAL PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES crm_leads(lead_id) ON DELETE CASCADE,
    offer_id INTEGER REFERENCES crm_offer_letters(offer_id) ON DELETE SET NULL,
    document_type VARCHAR(30) NOT NULL,
    s3_key TEXT NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    size_bytes INTEGER NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_crm_lead_documents_lead ON crm_lead_documents (lead_id, uploaded_at DESC);
