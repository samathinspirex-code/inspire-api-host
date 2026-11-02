-- Apply after 2026_10_30_admissions_studio.sql. No credentials are stored here.
BEGIN;
ALTER TABLE connectors ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMPTZ;
ALTER TABLE connectors ADD COLUMN IF NOT EXISTS last_error VARCHAR(240);
ALTER TABLE connectors ADD COLUMN IF NOT EXISTS report JSONB;
COMMIT;
