-- Automatic lead priority: manual choices can hold, and unanswered calls lower the score.
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS priority_locked BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS unanswered_calls INTEGER NOT NULL DEFAULT 0;
