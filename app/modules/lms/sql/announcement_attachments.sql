ALTER TABLE lms_announcements
ADD COLUMN IF NOT EXISTS attachment_asset_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
