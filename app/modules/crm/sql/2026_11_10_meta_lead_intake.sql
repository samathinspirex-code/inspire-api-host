-- Meta lead intake and the Meta assignment setting. Separate tables, so CRM pages never depend on them.
CREATE TABLE IF NOT EXISTS crm_source_assignment (
    source_group VARCHAR(30) PRIMARY KEY,
    counsellor_ids INTEGER[] NOT NULL DEFAULT '{}',
    last_counsellor_id INTEGER,
    last_assigned_on DATE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS crm_meta_lead_cursors (
    form_id VARCHAR(40) PRIMARY KEY,
    last_created BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS crm_meta_lead_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    running_until TIMESTAMPTZ,
    last_run_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_error VARCHAR(240),
    forms_watched INTEGER
);

-- Each Meta lead is created once, even if two runs overlap.
CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_leads_social_lead_id ON crm_leads (social_lead_id) WHERE social_lead_id IS NOT NULL;
