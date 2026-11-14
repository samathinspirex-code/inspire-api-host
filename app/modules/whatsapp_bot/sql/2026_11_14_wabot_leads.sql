-- CRM -> WhatsApp Bot: bot leads mirrored into CRM leads (the bot itself is never written to).
-- bot_values is the last set of values copied from the bot, so a later bot change can
-- update the CRM field without overwriting what a counsellor typed.
CREATE TABLE IF NOT EXISTS wabot_lead_state (
    bot_lead_id    varchar(64) PRIMARY KEY,
    crm_lead_id    integer REFERENCES crm_leads(lead_id) ON DELETE SET NULL,
    bot_values     jsonb NOT NULL DEFAULT '{}'::jsonb,
    bot_updated_at timestamptz,
    synced_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS wabot_lead_state_crm_idx ON wabot_lead_state (crm_lead_id);
