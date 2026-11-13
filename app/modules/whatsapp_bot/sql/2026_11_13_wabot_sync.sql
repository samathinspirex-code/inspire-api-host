-- CRM -> WhatsApp Bot: which bot chats have been copied into CRM leads.
-- The bot's own database is never written to; this is the CRM's record.
CREATE TABLE IF NOT EXISTS wabot_sync_state (
    bot_session_id     varchar(64) PRIMARY KEY,
    bot_handover_id    varchar(64),
    crm_lead_id        integer REFERENCES crm_leads(lead_id) ON DELETE SET NULL,
    handover_synced_at timestamptz NOT NULL DEFAULT now(),
    last_message_at    timestamptz,
    updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS wabot_sync_state_handover_idx ON wabot_sync_state (bot_handover_id);

CREATE TABLE IF NOT EXISTS wabot_sync_meta (
    key        varchar(40) PRIMARY KEY,
    value      jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);
