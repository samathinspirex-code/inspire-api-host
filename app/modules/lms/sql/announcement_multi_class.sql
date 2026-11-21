-- Lets one announcement reach several chosen classes.
ALTER TABLE lms_announcements
ADD COLUMN IF NOT EXISTS audience_class_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
