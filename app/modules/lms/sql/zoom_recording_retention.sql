-- Zoom cloud retention: a recording is removed from Zoom storage only after it
-- has been published to Vimeo, and never before the retention window expires.
-- The delay leaves a recoverable copy in Zoom while the Vimeo transcode settles.
ALTER TABLE lms_zoom_recordings ADD COLUMN IF NOT EXISTS zoom_published_at TIMESTAMPTZ;
ALTER TABLE lms_zoom_recordings ADD COLUMN IF NOT EXISTS zoom_cloud_deleted_at TIMESTAMPTZ;
ALTER TABLE lms_zoom_recordings ADD COLUMN IF NOT EXISTS zoom_cloud_delete_error TEXT;

-- Existing published rows become eligible from their publish time.
UPDATE lms_zoom_recordings SET zoom_published_at = COALESCE(zoom_published_at, updated_at)
WHERE status = 'published' AND zoom_published_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_lms_zoom_recordings_retention
  ON lms_zoom_recordings (zoom_published_at)
  WHERE zoom_cloud_deleted_at IS NULL;

-- Retention period in days, configurable per deployment.
ALTER TABLE lms_zoom_settings ADD COLUMN IF NOT EXISTS cloud_retention_days SMALLINT NOT NULL DEFAULT 2
  CHECK (cloud_retention_days BETWEEN 0 AND 30);
