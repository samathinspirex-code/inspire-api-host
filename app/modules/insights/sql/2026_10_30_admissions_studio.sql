ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS referral_code VARCHAR(40);

CREATE INDEX IF NOT EXISTS ix_crm_leads_referral_code ON crm_leads (referral_code);

CREATE TABLE IF NOT EXISTS partner_links (
    link_id SERIAL PRIMARY KEY,
    name VARCHAR(160) NOT NULL,
    partner_type VARCHAR(80) NOT NULL,
    platform VARCHAR(40) NOT NULL DEFAULT 'all',
    programme VARCHAR(255),
    campaign VARCHAR(160),
    commission NUMERIC(12, 2) NOT NULL DEFAULT 0,
    code VARCHAR(40) NOT NULL UNIQUE,
    url TEXT NOT NULL,
    created_by INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS partner_link_clicks (
    click_id SERIAL PRIMARY KEY,
    code VARCHAR(40) NOT NULL,
    platform VARCHAR(40),
    utm_source VARCHAR(120),
    utm_medium VARCHAR(120),
    utm_campaign VARCHAR(160),
    ip_hash VARCHAR(64),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_partner_clicks_code ON partner_link_clicks (code);

CREATE TABLE IF NOT EXISTS connectors (
    connector_id SERIAL PRIMARY KEY,
    platform VARCHAR(40) NOT NULL,
    name VARCHAR(160) NOT NULL,
    account_id VARCHAR(160),
    owner VARCHAR(120),
    status VARCHAR(40) NOT NULL DEFAULT 'awaiting_api',
    created_by INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS whatsapp_lines (
    line_id SERIAL PRIMARY KEY,
    counsellor_user_id INTEGER,
    phone_number VARCHAR(40) NOT NULL,
    display_name VARCHAR(160),
    phone_number_id VARCHAR(80),
    created_by INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS crm_arena_claims (
    claim_id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    quest_id VARCHAR(40) NOT NULL,
    claim_date DATE NOT NULL,
    xp INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, quest_id, claim_date)
);
